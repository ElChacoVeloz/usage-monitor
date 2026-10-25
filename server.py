#!/usr/bin/env python3
"""AI Usage Monitor — servidor del tablero de cuotas de modelos.

Diseñado para la Nexus 7 (2013): lienzo fijo 1920x1200 (16:10) escalado, tema
oscuro, cero dependencias. Publica UN solo endpoint `GET /api/data` con el uso
de las cuatro suscripciones y sus ventanas:

  * OpenCode Go  — medido: tokens de Hermes (state.db) x precio de lista
  * ChatGPT Plus — vivo: GET chatgpt.com/backend-api/wham/usage (OAuth de Codex)
  * Claude Pro   — vivo si hay ~/.claude/.credentials.json, si no manual
  * Gemini Pro   — vivo si hay ~/.gemini/oauth_creds.json, si no manual

Cada fuente falla sola y lo dice en la tarjeta; nunca se inventa un número.
Cuando un proveedor no publica cuota para un periodo, ese hueco muestra el uso
medido (tokens) en vez de un porcentaje inventado.

Uso:
  python3 server.py                      # http://0.0.0.0:8773
  python3 server.py --once               # imprime el JSON y sale
  python3 server.py --mock --port 8799   # datos de ejemplo, sin credenciales
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent
WEB = BASE / "web"
CONFIG_PATH = BASE / "config.json"

# Cuota de OpenCode Go: endpoint real (el mismo que usa el plugin del proveedor).
OPENCODE_USAGE_URL = "https://opencode.ai/zen/go/v1/usage"
HERMES_ENV = "~/.hermes/.env"
# Cloudflare de opencode.ai banea la firma de Python-urllib (error 1010) con 403;
# con un User-Agent propio y explícito responde 200.
OPENCODE_UA = "HermesAgent/1.0"

SESSION_S = 5 * 3600
WEEK_S = 7 * 86400
MONTH_MIN_S = 20 * 86400       # clasificador de ventanas por duracion
MONTH_MAX_S = 400 * 86400
OFFLINE = False                # efectivo: --offline o config live_read=false
CLI_OFFLINE = False            # --offline en la linea de comandos


# ----------------------------------------------------------------- utilidades

_CFG: dict = {"mtime": 0.0, "data": {}}


def load_config() -> dict:
    """Se relee solo cuando config.json cambia: editar plan/acentos no pide reinicio."""
    try:
        mtime = CONFIG_PATH.stat().st_mtime
    except OSError:
        mtime = 0.0
    if not _CFG["data"] or mtime != _CFG["mtime"]:
        with open(CONFIG_PATH, encoding="utf-8") as fh:
            _CFG["data"] = json.load(fh)
        _CFG["mtime"] = mtime
    return _CFG["data"]


def tz_of(cfg: dict):
    try:
        return ZoneInfo(cfg.get("timezone", "America/Chicago"))
    except Exception:
        return timezone.utc


def short_error(text, limit: int = 90) -> str:
    s = str(text or "").replace("\n", " ").strip()
    s = "".join(ch for ch in s if ch.isprintable())
    return s[:limit] + ("…" if len(s) > limit else "")


def compact(n: float) -> str:
    """1234567 -> 1.2 M"""
    n = float(n or 0)
    for div, suf in ((1e9, "G"), (1e6, "M"), (1e3, "k")):
        if abs(n) >= div:
            v = n / div
            return f"{v:.1f} {suf}" if v < 100 else f"{v:.0f} {suf}"
    return f"{n:.0f}"


def jwt_exp(token: str) -> float:
    try:
        part = token.split(".")[1]
        part += "=" * (-len(part) % 4)
        return float(json.loads(base64.urlsafe_b64decode(part)).get("exp") or 0)
    except Exception:
        return 0.0


def http_json(url: str, *, headers: dict | None = None, body: bytes | None = None,
              method: str | None = None, timeout: int = 20):
    req = urllib.request.Request(url, data=body, headers=headers or {},
                                 method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8", "replace")
    return json.loads(raw) if raw.strip().startswith(("{", "[")) else {}


class Cache:
    """TTL por llave, con ultimo valor bueno para servir marcado como stale."""

    def __init__(self):
        self._d: dict[str, tuple[float, object]] = {}
        self._lock = threading.Lock()

    def get(self, key: str, ttl: float, fn):
        now = time.time()
        with self._lock:
            hit = self._d.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1], False
        try:
            val = fn()
        except Exception:
            if hit:
                return hit[1], True
            raise
        with self._lock:
            self._d[key] = (now, val)
        return val, False

    def bust(self):
        with self._lock:
            self._d.clear()


CACHE = Cache()

# Cache negativo: si una credencial o una API falla, no se reintenta cada 2 min.
NEG: dict[str, tuple[float, str]] = {}


def neg_check(key: str, ttl: float = 600) -> None:
    hit = NEG.get(key)
    if hit and time.time() - hit[0] < ttl:
        raise RuntimeError(hit[1])


def neg_set(key: str, msg: str) -> None:
    NEG[key] = (time.time(), msg)


def neg_clear(key: str) -> None:
    NEG.pop(key, None)


# ------------------------------------------------------- base de uso de Hermes

def open_usage_db(cfg: dict):
    path = os.path.expanduser(cfg.get("hermes_db", "~/.hermes/state.db"))
    if not Path(path).exists():
        raise RuntimeError("no existe ~/.hermes/state.db")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def usage_rows(cfg: dict) -> list[dict]:
    """Filas de session_model_usage normalizadas (una por sesion+modelo)."""
    conn = open_usage_db(cfg)
    try:
        cur = conn.execute("""
            SELECT model, billing_provider, api_call_count, input_tokens,
                   output_tokens, cache_read_tokens, cache_write_tokens,
                   first_seen, last_seen
              FROM session_model_usage
        """)
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


# Precio de lista OpenCode Go (docs console/go, columna Go). "peak" es el precio
# caro de las horas pico: se usa por omision para no esconder consumo.
GO_MODELS = {
    "deepseek-v4.1-flash": {"peak": (0.30, 1.20, 0.006, 0.0), "off": (0.15, 0.60, 0.003, 0.0), "limit": 80},
    "deepseek-v4-pro":     {"peak": (1.32, 3.96, 0.044, 0.0), "off": (0.66, 1.98, 0.022, 0.0), "limit": 15},
    "kimi-k3":             {"peak": (3.00, 15.00, 0.30, 0.0), "off": (3.00, 15.00, 0.30, 0.0), "limit": 15},
    "kimi-k2.7-code":      {"peak": (0.95, 4.00, 0.19, 0.0), "off": (0.95, 4.00, 0.19, 0.0), "limit": 60},
    "glm-5.3":             {"peak": (1.40, 4.40, 0.26, 0.0), "off": (1.40, 4.40, 0.26, 0.0), "limit": 15},
    "glm-5.3-flash":       {"peak": (0.15, 0.50, 0.03, 0.0), "off": (0.15, 0.50, 0.03, 0.0), "limit": 60},
    "minimax-m3":          {"peak": (0.30, 1.20, 0.06, 0.0), "off": (0.30, 1.20, 0.06, 0.0), "limit": 60},
    "qwen3.8-max":         {"peak": (2.00, 6.00, 0.25, 2.50), "off": (2.00, 6.00, 0.25, 2.50), "limit": 15},
    "grok-4.7":            {"peak": (2.00, 6.00, 0.50, 0.0), "off": (2.00, 6.00, 0.50, 0.0), "limit": 15},
    "gpt-6-luna":          {"peak": (0.10, 0.50, 0.01, 0.125), "off": (0.10, 0.50, 0.01, 0.125), "limit": 15},
    "gpt-5.6-luna":        {"peak": (0.20, 1.20, 0.02, 0.25), "off": (0.20, 1.20, 0.02, 0.25), "limit": 15},
    "claude-haiku-5.5":    {"peak": (0.10, 0.50, 0.01, 0.125), "off": (0.10, 0.50, 0.01, 0.125), "limit": 15},
    # Los modelos vistos en Hermes que no estan en la lista Go: precio desconocido
    "gpt-5.6-sol": {"peak": None, "off": None, "limit": None},
    "kimi-k2.6":   {"peak": None, "off": None, "limit": None},
}


MODEL_NAMES = {
    "deepseek-v4.1-flash": "DeepSeek V4.1 Flash",
    "deepseek-v4-pro": "DeepSeek V4 Pro",
    "kimi-k2.6": "Kimi K2.6",
    "kimi-k2.7-code": "Kimi K2.7 Code",
    "kimi-k3": "Kimi K3",
    "gpt-5.6-sol": "GPT-5.6 Sol",
    "gpt-5.6-luna": "GPT-5.6 Luna",
    "gpt-6-luna": "GPT-6 Luna",
    "glm-5.3": "GLM-5.3",
    "glm-5.3-flash": "GLM-5.3 Flash",
    "minimax-m3": "MiniMax M3",
    "qwen3.8-max": "Qwen3.8 Max",
    "grok-4.7": "Grok 4.7",
    "claude-haiku-5.5": "Claude Haiku 5.5",
}


def display_model(model: str) -> str:
    key = (model or "").strip().lower()
    if key in MODEL_NAMES:
        return MODEL_NAMES[key]
    words = [w for w in key.replace("_", "-").split("-") if w]
    return " ".join(w.upper() if len(w) <= 3 else w.capitalize() for w in words)


def go_price(model: str):
    key = (model or "").lower()
    if key in GO_MODELS:
        return GO_MODELS[key]
    for name, spec in GO_MODELS.items():
        if key.startswith(name) or name.startswith(key):
            return spec
    return {"peak": None, "off": None, "limit": None}


def row_cost(row: dict, mode: str = "peak") -> tuple[float, bool]:
    """USD de la fila y si el precio era conocido."""
    spec = go_price(row["model"])
    tbl = spec["peak"] if mode == "peak" else spec["off"]
    if not tbl:
        return 0.0, False
    pin, pout, pcr, pcw = tbl
    usd = (row["input_tokens"] * pin + row["output_tokens"] * pout
           + row["cache_read_tokens"] * pcr + row["cache_write_tokens"] * pcw) / 1e6
    return usd, True


def period_starts(now: datetime) -> dict[str, float]:
    month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return {
        "session": now.timestamp() - SESSION_S,
        "week": now.timestamp() - WEEK_S,
        "month": month.timestamp(),
    }


def next_month(now: datetime) -> datetime:
    y, m = now.year, now.month + 1
    if m > 12:
        y, m = y + 1, 1
    return now.replace(year=y, month=m, day=1, hour=0, minute=0, second=0,
                       microsecond=0)


def provider_of(row: dict) -> str:
    p = (row.get("billing_provider") or "").lower()
    if "opencode" in p or "zen" in p:
        return "opencode"
    if "openai" in p or "codex" in p or "chatgpt" in p:
        return "chatgpt"
    if "anthropic" in p or "claude" in p:
        return "claude"
    if "google" in p or "gemini" in p:
        return "gemini"
    return "otros"


def measured_summary(cfg: dict, now: datetime) -> dict:
    """Tokens (entrada+salida) y llamadas por proveedor y periodo."""
    rows = usage_rows(cfg)
    starts = period_starts(now)
    out = {pid: {k: {"tokens": 0, "calls": 0} for k in starts} for pid in
           ("opencode", "chatgpt", "claude", "gemini", "otros")}
    for row in rows:
        pid = provider_of(row)
        last = float(row["last_seen"] or 0)
        tok = int(row["input_tokens"] or 0) + int(row["output_tokens"] or 0)
        calls = int(row["api_call_count"] or 0)
        for key, start in starts.items():
            if last >= start:
                out[pid][key]["tokens"] += tok
                out[pid][key]["calls"] += calls
    return out


# ------------------------------------------------------------ fuente: opencode

def opencode_token() -> str:
    """La API key de Go vive en el .env de Hermes; nunca se imprime."""
    path = Path(os.path.expanduser(HERMES_ENV))
    if not path.exists():
        raise RuntimeError(f"falta {HERMES_ENV} con OPENCODE_GO_API_KEY")
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == "OPENCODE_GO_API_KEY":
            value = value.strip().strip("'\"")
            if value:
                return value
    raise RuntimeError("OPENCODE_GO_API_KEY vacía en el .env de Hermes")


def opencode_usage() -> dict:
    """Cuota real de OpenCode Go: ventanas rolling / semanal / mensual."""
    return http_json(OPENCODE_USAGE_URL, headers={
        "Authorization": f"Bearer {opencode_token()}",
        "Accept": "application/json",
        "User-Agent": OPENCODE_UA,
    }, timeout=15)


def opencode_top_model(cfg: dict) -> str:
    """Modelo de Go con más consumo (solo informativo para el pie)."""
    best, name = -1.0, None
    for row in usage_rows(cfg):
        if provider_of(row) != "opencode":
            continue
        usd, known = row_cost(row, "peak")
        if known and usd > best:
            best, name = usd, row["model"]
    return name or ""


def source_opencode(cfg: dict, now: datetime, mock: bool,
                    measured: dict | None = None) -> dict:
    pid = "opencode"
    pcfg = cfg["providers"][pid]
    found, err = {}, None

    if mock:
        now_ts = now.timestamp()
        found = {"session": {"pct": 5.0, "reset_at": now_ts + 4 * 3600},
                 "week": {"pct": 14.0, "reset_at": now_ts + 2 * 86400},
                 "month": {"pct": 8.0, "reset_at": now_ts + 20 * 86400}}
    else:
        try:
            if OFFLINE:
                raise RuntimeError("modo offline (no se consultó a OpenCode)")
            neg_check("opencode")
            data = CACHE.get("opencode", 120, opencode_usage)[0]
            neg_clear("opencode")
            usage_blk = data.get("usage") or {}
            for key, field in (("session", "rolling"), ("week", "weekly"),
                               ("month", "monthly")):
                blk = usage_blk.get(field) or {}
                if not isinstance(blk, dict) or blk.get("percent") is None:
                    continue
                found[key] = {"pct": float(blk["percent"]),
                              "reset_at": parse_iso(blk.get("resetsAt"))}
            if not found:
                err = "respuesta de OpenCode sin ventanas conocidas"
        except Exception as exc:
            err = short_error(exc)
            neg_set("opencode", err)

    if found:
        windows = []
        for key, label, verb, sub in (
                ("session", "SESIÓN 5 H", "libera", "ventana móvil de 5 h"),
                ("week", "SEMANA", "libera", "ventana semanal"),
                ("month", "MES", "reinicia", "ventana mensual")):
            q = found.get(key)
            if not q:
                continue
            windows.append({
                "id": key, "label": label, "kind": "quota",
                "pct": round(float(q.get("pct") or 0), 1),
                "reset_at": q.get("reset_at"), "verb": verb,
                "limit_text": "", "sub": sub,
            })
        model = opencode_top_model(cfg)
        note = "cuota Go leída del API"
        return {
            "id": pid, "name": pcfg["name"], "plan": pcfg["plan"],
            "accent": pcfg["accent"], "source_mode": "live",
            "source_label": "EN VIVO", "source_note": note,
            "windows": windows, "hint": "",
            "footer": {"model": display_model(model) if model else "",
                       "note": note},
        }

    # Sin cuota legible: se muestran los tokens medidos, anunciando la causa.
    out = _windows_from_quota(pid, pcfg, now, {}, err, measured,
                              quota_sub="cuota Go", plan_note="plan Go",
                              hint="no se pudo leer la cuota Go por API")
    model = opencode_top_model(cfg)
    if model:
        out["footer"]["model"] = display_model(model)
    return out


# ------------------------------------------------------------- fuente: chatgpt

CODEX_AUTH = "~/.codex/auth.json"
OPENAI_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"


def codex_access(cfg: dict) -> tuple[str, str]:
    """Devuelve (access_token, account_id) refrescando el OAuth si hace falta."""
    path = Path(os.path.expanduser(CODEX_AUTH))
    if not path.exists():
        raise RuntimeError("no hay sesión de Codex (~/.codex/auth.json)")
    data = json.loads(path.read_text(encoding="utf-8"))
    tok = data.get("tokens") or {}
    at, rt = tok.get("access_token"), tok.get("refresh_token")
    aid = tok.get("account_id") or ""
    if not at:
        raise RuntimeError("~/.codex/auth.json sin access_token")
    if jwt_exp(at) - time.time() > 180:
        return at, aid
    if not rt:
        raise RuntimeError("token de Codex vencido y sin refresh_token")
    body = urllib.parse.urlencode({
        "grant_type": "refresh_token", "refresh_token": rt,
        "client_id": OPENAI_CLIENT_ID, "scope": "openid profile email",
    }).encode()
    try:
        resp = http_json("https://auth.openai.com/oauth/token",
                         headers={"Content-Type": "application/x-www-form-urlencoded",
                                  "Accept": "application/json"},
                         body=body, method="POST", timeout=25)
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        if "refresh_token_expired" in detail or exc.code in (400, 401):
            raise RuntimeError("la sesión de Codex venció · hay que correr `codex login`") from None
        raise RuntimeError(f"el refresh de Codex falló (HTTP {exc.code})") from None
    new_at = resp.get("access_token")
    if not new_at:
        raise RuntimeError("el refresh de Codex no devolvió token")
    tok["access_token"] = new_at
    if resp.get("refresh_token"):
        tok["refresh_token"] = resp["refresh_token"]
    if resp.get("id_token"):
        tok["id_token"] = resp["id_token"]
    data["tokens"] = tok
    data["last_refresh"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.chmod(path, 0o600)
    return new_at, aid


def codex_usage(cfg: dict) -> dict:
    at, aid = codex_access(cfg)
    headers = {"Authorization": f"Bearer {at}", "Accept": "application/json",
               "Content-Type": "application/json",
               "User-Agent": "usage-monitor/1.0"}
    if aid:
        headers["ChatGPT-Account-Id"] = aid
    return http_json("https://chatgpt.com/backend-api/wham/usage", headers=headers)


def classify_window(seconds) -> str | None:
    try:
        s = float(seconds)
    except (TypeError, ValueError):
        return "session"
    if s < 2 * 86400:
        return "session"
    if s < MONTH_MIN_S:
        return "week"
    if s < MONTH_MAX_S:
        return "month"
    return None


def window_pct(w: dict) -> float:
    if "used_percent" in w and w["used_percent"] is not None:
        return float(w["used_percent"])
    if "percent_left" in w and w["percent_left"] is not None:
        return 100.0 - float(w["percent_left"])
    return 0.0


def source_chatgpt(cfg: dict, now: datetime, mock: bool,
                   measured: dict | None = None) -> dict:
    pid = "chatgpt"
    pcfg = cfg["providers"][pid]
    found, err = {}, None
    plan = "plus"
    if mock:
        found = {"session": {"pct": 46.0, "reset_at": now.timestamp() + 3 * 3600},
                 "week": {"pct": 71.0, "reset_at": now.timestamp() + 4 * 86400}}
    else:
        try:
            if OFFLINE:
                raise RuntimeError("modo offline (no se consultó a OpenAI)")
            neg_check("chatgpt")
            data = CACHE.get("chatgpt", 120, lambda: codex_usage(cfg))[0]
            neg_clear("chatgpt")
            plan = data.get("plan_type") or plan
            rl = data.get("rate_limit") or {}
            groups = [rl] + [a.get("rate_limit", {}) for a in
                             (data.get("additional_rate_limits") or [])]
            for group in groups[:1]:
                for attr in ("primary_window", "secondary_window"):
                    w = group.get(attr)
                    if not w:
                        continue
                    key = classify_window(w.get("limit_window_seconds"))
                    if key and key not in found:
                        label = None
                        if key == "session" and float(
                                w.get("limit_window_seconds") or 0) >= 4 * 3600:
                            label = "SESIÓN 5 H"
                        elif key == "week":
                            label = "SEMANA"
                        found[key] = {
                            "pct": window_pct(w),
                            "label": label,
                            "reset_at": w.get("reset_at")
                            or (now.timestamp() + (w.get("reset_after_seconds") or 0)),
                        }
        except Exception as exc:
            err = short_error(exc)
            neg_set("chatgpt", err)

    return _windows_from_quota(pid, pcfg, now, found, err, measured,
                               quota_sub="cuota de Codex",
                               plan_note=f"plan {plan}",
                               hint="corre `codex login` para leer la cuota de Codex")


# -------------------------------------------------------------- fuente: claude

CLAUDE_CREDS = "~/.claude/.credentials.json"
CLAUDE_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"


def claude_access() -> str:
    path = Path(os.path.expanduser(CLAUDE_CREDS))
    if not path.exists():
        raise RuntimeError("sin sesión de Claude Code (~/.claude/.credentials.json)")
    data = json.loads(path.read_text(encoding="utf-8"))
    oauth = data.get("claudeAiOauth") or data
    at = oauth.get("accessToken") or oauth.get("access_token")
    rt = oauth.get("refreshToken") or oauth.get("refresh_token")
    exp = oauth.get("expiresAt") or 0
    exp_s = exp / 1000.0 if exp > 1e11 else float(exp or 0)
    if at and (not exp_s or exp_s - time.time() > 120):
        return at
    if not rt:
        raise RuntimeError("token de Claude vencido y sin refresh_token")
    body = json.dumps({
        "grant_type": "refresh_token", "refresh_token": rt,
        "client_id": CLAUDE_CLIENT_ID,
    }).encode()
    resp = http_json("https://platform.claude.com/v1/oauth/token",
                     headers={"Content-Type": "application/json",
                              "Accept": "application/json",
                              "anthropic-beta": "oauth-2025-04-20",
                              "User-Agent": "claude-cli/2.0.0 (external, cli)"},
                     body=body, method="POST", timeout=25)
    new_at = resp.get("access_token")
    if not new_at:
        raise RuntimeError("el refresh de Claude no devolvió token")
    oauth["accessToken"] = new_at
    if resp.get("refresh_token"):
        oauth["refreshToken"] = resp["refresh_token"]
    if resp.get("expires_in"):
        oauth["expiresAt"] = int(time.time() + float(resp["expires_in"])) * 1000
    if "claudeAiOauth" in data:
        data["claudeAiOauth"] = oauth
    else:
        data = oauth
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.chmod(path, 0o600)
    return new_at


def parse_iso(value) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        return v / 1000.0 if v > 1e11 else v
    try:
        txt = str(value).replace("Z", "+00:00")
        return datetime.fromisoformat(txt).timestamp()
    except Exception:
        return None


def claude_usage() -> dict:
    at = claude_access()
    return http_json("https://api.anthropic.com/api/oauth/usage",
                     headers={"Authorization": f"Bearer {at}",
                              "Accept": "application/json",
                              "anthropic-beta": "oauth-2025-04-20",
                              "User-Agent": "usage-monitor/1.0"})


def source_claude(cfg: dict, now: datetime, mock: bool,
                  measured: dict | None = None, manual: dict | None = None) -> dict:
    pid = "claude"
    pcfg = cfg["providers"][pid]
    found, err = {}, None
    if mock:
        found = {"session": {"pct": 12.0, "reset_at": now.timestamp() + 4 * 3600},
                 "week": {"pct": 34.0, "reset_at": now.timestamp() + 2 * 86400}}
    else:
        try:
            if OFFLINE:
                raise RuntimeError("modo offline (no se consultó a Claude)")
            neg_check("claude")
            data = CACHE.get("claude", 300, claude_usage)[0]
            neg_clear("claude")
            # Preferimos limits[] (kind + percent 0-100, sin ambiguedad); si no
            # viene, caemos a five_hour / seven_day con utilization.
            for lim in (data.get("limits") or []):
                kind = str(lim.get("kind") or "").lower()
                pct = lim.get("percent")
                if pct is None:
                    continue
                if kind == "session":
                    key = "session"
                elif kind in ("weekly_all", "seven_day"):
                    key = "week"
                elif kind.startswith("weekly") and "week" not in found:
                    key = "week"
                else:
                    continue
                found.setdefault(key, {"pct": float(pct),
                                       "reset_at": parse_iso(lim.get("resets_at"))})
            for key, field in (("session", "five_hour"), ("week", "seven_day")):
                blk = data.get(field)
                if key in found or not isinstance(blk, dict):
                    continue
                pct = blk.get("utilization")
                if pct is None:
                    pct = blk.get("used_percent")
                pct = float(pct or 0)
                if 0 < pct <= 1.0:      # la API a veces manda fraccion
                    pct *= 100.0
                found[key] = {"pct": pct, "reset_at": parse_iso(blk.get("resets_at"))}
            if "session" in found:
                found["session"].setdefault("label", "SESIÓN 5 H")
            if not found:
                err = "respuesta de Claude sin ventanas conocidas"
        except Exception as exc:
            err = short_error(exc)
            neg_set("claude", err)

    # sin credencial viva se cae a los números manuales del Capitán
    from_manual = False
    if not found and manual:
        for key, blk in (manual.get("windows") or {}).items():
            if blk.get("pct") is not None:
                found[key] = {"pct": float(blk["pct"]),
                              "reset_at": blk.get("reset_at")}
        if found:
            err = None
            from_manual = True

    out = _windows_from_quota(pid, pcfg, now, found, err, measured,
                              quota_sub="cuota de Claude",
                              plan_note="plan Pro",
                              hint="instala Claude Code y entra con tu cuenta "
                                   "(o captura el % con usage-set.py)")
    if from_manual:
        out["source_mode"] = "manual"
        out["source_label"] = "MANUAL"
        out["source_note"] = "capturado a mano en el servidor"
        out["footer"]["note"] = out["source_note"]
    return out


# -------------------------------------------------------------- fuente: gemini

GEMINI_CREDS = "~/.gemini/oauth_creds.json"
GEMINI_CLIENT_ID = ("681255809395-oo8ft2oprdrnp9e3aqf6av3hmdib135j"
                    ".apps.googleusercontent.com")
GEMINI_CLIENT_SECRET = "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"


def gemini_access() -> str:
    path = Path(os.path.expanduser(GEMINI_CREDS))
    if not path.exists():
        raise RuntimeError("sin sesión de Gemini CLI (~/.gemini/oauth_creds.json)")
    data = json.loads(path.read_text(encoding="utf-8"))
    at = data.get("access_token")
    exp = data.get("expiry_date") or 0
    exp_s = exp / 1000.0 if exp > 1e11 else float(exp or 0)
    if at and (not exp_s or exp_s - time.time() > 120):
        return at
    rt = data.get("refresh_token")
    if not rt:
        raise RuntimeError("token de Gemini vencido y sin refresh_token")
    body = urllib.parse.urlencode({
        "grant_type": "refresh_token", "refresh_token": rt,
        "client_id": GEMINI_CLIENT_ID, "client_secret": GEMINI_CLIENT_SECRET,
    }).encode()
    resp = http_json("https://oauth2.googleapis.com/token",
                     headers={"Content-Type": "application/x-www-form-urlencoded",
                              "Accept": "application/json"},
                     body=body, method="POST", timeout=25)
    new_at = resp.get("access_token")
    if not new_at:
        raise RuntimeError("el refresh de Gemini no devolvió token")
    data["access_token"] = new_at
    if resp.get("expires_in"):
        data["expiry_date"] = int(time.time() + float(resp["expires_in"])) * 1000
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.chmod(path, 0o600)
    return new_at


def gemini_usage() -> dict:
    """Cuota diaria de Code Assist: primero el project id, luego los buckets.

    `retrieveUserQuota` sin `project` devuelve vacio en cuentas con proyecto
    asignado, asi que se pide el id con `loadCodeAssist` (mismo token OAuth).
    """
    at = gemini_access()
    headers = {"Authorization": f"Bearer {at}",
               "Accept": "application/json",
               "Content-Type": "application/json",
               "User-Agent": "usage-monitor/1.0"}
    base = "https://cloudcode-pa.googleapis.com/v1internal"
    project = None
    try:
        la = http_json(f"{base}:loadCodeAssist", headers=headers,
                       body=b"{}", method="POST", timeout=25)
        proj = la.get("cloudaicompanionProject")
        project = proj.get("id") if isinstance(proj, dict) else proj
    except Exception:
        project = None
    body = json.dumps({"project": project}).encode() if project else b"{}"
    return http_json(f"{base}:retrieveUserQuota", headers=headers,
                     body=body, method="POST", timeout=25)


def source_gemini(cfg: dict, now: datetime, mock: bool,
                  measured: dict | None = None, manual: dict | None = None) -> dict:
    pid = "gemini"
    pcfg = cfg["providers"][pid]
    found, err = {}, None
    if mock:
        found = {"session": {"pct": 23.0, "reset_at": now.timestamp() + 9 * 3600}}
    else:
        try:
            if OFFLINE:
                raise RuntimeError("modo offline (no se consultó a Gemini)")
            neg_check("gemini")
            data = CACHE.get("gemini", 300, gemini_usage)[0]
            neg_clear("gemini")
            buckets = data.get("buckets") or []
            worst, name = -1.0, None
            for b in buckets:
                frac = b.get("remainingFraction")
                if frac is None:
                    continue
                used = (1.0 - float(frac)) * 100.0
                if used > worst:
                    worst, name = used, b.get("modelId")
            if worst >= 0:
                found["session"] = {"pct": worst, "reset_at": parse_iso(
                    (buckets[0] or {}).get("resetTime")) if buckets else None}
                pcfg = dict(pcfg)
                pcfg["_model"] = name
            else:
                err = "Gemini sin buckets de cuota"
        except Exception as exc:
            err = short_error(exc)
            neg_set("gemini", err)

    if not found and manual:
        from_manual = True
        for key, blk in (manual.get("windows") or {}).items():
            if blk.get("pct") is not None:
                found[key] = {"pct": float(blk["pct"]),
                              "reset_at": blk.get("reset_at")}
        if found:
            err = None
    else:
        from_manual = False

    out = _windows_from_quota(pid, pcfg, now, found, err, measured,
                              quota_sub="cuota diaria de Code Assist",
                              plan_note="plan AI Pro",
                              hint="Google cerró el OAuth de Code Assist para "
                                   "cuentas individuales (migrar a Antigravity) · "
                                   "captura el % con usage-set.py")
    if from_manual:
        out["source_mode"] = "manual"
        out["source_label"] = "MANUAL"
        out["source_note"] = "capturado a mano en el servidor"
        out["footer"]["note"] = out["source_note"]
    if pcfg.get("_model"):
        out["footer"]["model"] = str(pcfg["_model"])
    return out


# --------------------------------------------------------- armado de ventanas

PERIODS = (("session", "HOY"), ("week", "SEMANA"), ("month", "MES"))


def _windows_from_quota(pid: str, pcfg: dict, now: datetime, found: dict,
                        err: str | None, measured: dict | None,
                        quota_sub: str, plan_note: str,
                        hint: str = "") -> dict:
    """Arma los 3 huecos: cuota donde el proveedor la publica, uso medido si no."""
    windows = []
    tokens_total = 0
    for key, label in PERIODS:
        q = found.get(key)
        stat = (measured or {}).get(key) or {"tokens": 0, "calls": 0}
        tokens_total += stat.get("tokens", 0)
        if q:
            windows.append({
                "id": key, "label": q.get("label") or label, "kind": "quota",
                "pct": round(float(q.get("pct") or 0), 1),
                "reset_at": q.get("reset_at"),
                "verb": "reinicia",
                "limit_text": "", "sub": f"{quota_sub} · {plan_note}",
            })
        else:
            windows.append({
                "id": key, "label": label, "kind": "stat",
                "tokens": stat["tokens"], "calls": stat["calls"],
                "tokens_text": compact(stat["tokens"]),
                "sub": "medido aquí · sin cuota publicada",
            })
    has_quota = any(w["kind"] == "quota" for w in windows)

    if has_quota:
        mode, slabel, note = "live", "EN VIVO", quota_sub
    else:
        mode, slabel = "measured", "MEDIDO"
        note = ("sin cuota viva · " + err) if err else "el proveedor no publica cuota por API"

    if not has_quota and tokens_total == 0:
        # Una API caída no es una cuenta sin configurar: mostrar la causa real.
        if err:
            mode = "error"
            slabel = "CUOTA EN PAUSA" if "429" in err else "SIN LECTURA"
            note = "Claude limita consultas; reintentando" if pid == "claude" and "429" in err else "no se pudo leer la cuota"
            message = err
        else:
            mode, slabel = "empty", "SIN DATOS"
            note, message = "sin uso registrado aquí", hint
        return {
            "id": pid, "name": pcfg["name"], "plan": pcfg["plan"],
            "accent": pcfg["accent"], "source_mode": mode,
            "source_label": slabel, "source_note": note,
            "windows": [], "hint": message,
            "footer": {"model": "", "note": note},
        }

    return {
        "id": pid, "name": pcfg["name"], "plan": pcfg["plan"],
        "accent": pcfg["accent"], "source_mode": mode,
        "source_label": slabel, "source_note": note,
        "windows": windows, "hint": hint,
        "footer": {"model": "", "note": note},
    }


# --------------------------------------------------------------- manual.json

def manual_path(cfg: dict) -> Path:
    return BASE / cfg.get("manual_file", "manual.json")


def load_manual(cfg: dict) -> dict:
    path = manual_path(cfg)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_manual(cfg: dict, data: dict) -> None:
    path = manual_path(cfg)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


# ------------------------------------------------------------------- payload

def build_payload(cfg: dict, mock: bool = False) -> dict:
    global OFFLINE
    OFFLINE = CLI_OFFLINE or not cfg.get("live_read", True)
    now = datetime.now(tz_of(cfg))
    try:
        measured = measured_summary(cfg, now)
    except Exception:
        measured = {}
    manual = load_manual(cfg)

    jobs = {
        "opencode": lambda: source_opencode(cfg, now, mock,
                                           (measured.get("opencode") or {})),
        "chatgpt": lambda: source_chatgpt(cfg, now, mock,
                                          (measured.get("chatgpt") or {})),
        "claude": lambda: source_claude(cfg, now, mock,
                                        (measured.get("claude") or {}),
                                        manual.get("claude")),
        "gemini": lambda: source_gemini(cfg, now, mock,
                                        (measured.get("gemini") or {}),
                                        manual.get("gemini")),
    }
    order = [k for k in cfg.get("providers", {}) if cfg["providers"][k].get("enabled")]
    providers = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = {k: pool.submit(_safe(jobs[k])) for k in order}
        for k in order:
            providers.append(futs[k].result())

    totals = {"today": {"tokens": 0, "calls": 0}, "week": {"tokens": 0, "calls": 0},
              "month": {"tokens": 0, "calls": 0}}
    for pid, buckets in measured.items():
        for dst, src in (("today", "session"), ("week", "week"), ("month", "month")):
            b = buckets.get(src) or {}
            totals[dst]["tokens"] += b.get("tokens", 0)
            totals[dst]["calls"] += b.get("calls", 0)

    status = {p["id"]: p["source_mode"] for p in providers}
    return {
        "ok": True,
        "generated_at": now.timestamp(),
        "version": web_version(),
        "now": {
            "date": now.strftime("%A %d %B").capitalize(),
            "clock": now.strftime("%H:%M"),
            "iso": now.isoformat(timespec="seconds"),
        },
        "title": cfg.get("title", "AI Usage"),
        "locale": cfg.get("locale", "es-MX"),
        "refresh_seconds": int(cfg.get("refresh_seconds", 60)),
        "totals": {k: {"tokens": v["tokens"], "calls": v["calls"],
                       "tokens_text": compact(v["tokens"])}
                   for k, v in totals.items()},
        "providers": providers,
        "status": status,
    }


def _safe(fn):
    def wrapper():
        try:
            return fn()
        except Exception as exc:
            return {"id": "?", "name": "?", "plan": "", "accent": "#888",
                    "source_mode": "error", "source_label": "SIN LECTURA",
                    "source_note": short_error(exc), "windows": [],
                    "footer": {"model": "", "note": short_error(exc)}}
    return wrapper


def web_version() -> str:
    h = hashlib.sha1()
    for path in sorted(WEB.rglob("*")):
        if path.is_file():
            st = path.stat()
            h.update(f"{path.name}:{st.st_mtime_ns}:{st.st_size}".encode())
    return h.hexdigest()[:12]


# -------------------------------------------------------------------- server

class Handler(SimpleHTTPRequestHandler):
    cfg: dict = {}
    mock: bool = False

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(WEB), **kw)

    def log_message(self, fmt, *args):
        pass

    def _send_json(self, obj, code: int = 200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        path = self.path.split("?")[0]
        if path == "/":
            # Deja en el journal el motor del kiosco: sirve para saber qué
            # WebView trae la tableta antes de usar CSS que no entienda.
            self.log_message("kiosco UA: %s", self.headers.get("User-Agent", "?"))
        if path == "/api/data":
            try:
                # load_config() relee si config.json cambió: editar plan/acentos/
                # live_read no pide reiniciar el servicio
                self._send_json(build_payload(load_config(), self.mock))
            except Exception as exc:
                self._send_json({"ok": False, "error": short_error(exc, 300)}, 500)
            return
        if path == "/api/bust":
            CACHE.bust()
            NEG.clear()
            self._send_json({"ok": True, "busted": True})
            return
        if path == "/api/manual":
            self._send_json({"ok": True, "manual": load_manual(self.cfg)})
            return
        if path == "/":
            self.path = "/index.html"
        if path.endswith((".html", ".css", ".js", ".webmanifest")) or path == "/":
            CACHE.bust()
        return super().do_GET()

    def end_headers(self):
        if not self.path.startswith("/api/"):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_POST(self):  # noqa: N802
        path = self.path.split("?")[0]
        if path != "/api/manual":
            self._send_json({"ok": False, "error": "ruta desconocida"}, 404)
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            data = json.loads(self.rfile.read(length) or b"{}")
            pid = data.get("provider")
            if pid not in ("claude", "gemini", "chatgpt", "opencode"):
                raise ValueError("provider inválido")
            store = load_manual(self.cfg)
            entry = store.setdefault(pid, {"windows": {}})
            entry.setdefault("windows", {})
            win = data.get("window") or "session"
            entry["windows"][win] = {
                "pct": None if data.get("pct") in (None, "") else float(data["pct"]),
                "reset_at": data.get("reset_at"),
                "note": data.get("note") or "",
            }
            entry["updated"] = time.time()
            save_manual(self.cfg, store)
            CACHE.bust()
            self._send_json({"ok": True, "manual": store})
        except Exception as exc:
            self._send_json({"ok": False, "error": short_error(exc, 200)}, 400)


def main() -> int:
    ap = argparse.ArgumentParser(description="AI Usage Monitor")
    ap.add_argument("--port", type=int, default=8773)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--offline", action="store_true",
                    help="no consulta APIs (solo lo medido y lo manual)")
    ap.add_argument("--once", action="store_true", help="imprime /api/data y sale")
    args = ap.parse_args()

    global CLI_OFFLINE
    CLI_OFFLINE = bool(args.offline)
    OFFLINE = CLI_OFFLINE
    cfg = load_config()
    if args.once:
        print(json.dumps(build_payload(cfg, args.mock), ensure_ascii=False, indent=2))
        return 0

    Handler.cfg = cfg
    Handler.mock = args.mock
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    srv.daemon_threads = True
    print(f"AI Usage Monitor -> http://{args.host}:{args.port}/  "
          f"({'mock' if args.mock else 'real'})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
