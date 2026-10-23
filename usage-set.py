#!/usr/bin/env python3
"""Pone a mano el uso de un proveedor sin API de cuota (Claude, Gemini, chatgpt).

Ejemplos:
  ./usage-set.py claude session 42 --reset 2026-10-23T03:00
  ./usage-set.py claude week 68
  ./usage-set.py list
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
CFG = json.loads((BASE / "config.json").read_text(encoding="utf-8"))
PATH = BASE / CFG.get("manual_file", "manual.json")
PROVIDERS = tuple(CFG.get("providers", {}).keys()) or ("claude", "gemini")
WINDOWS = ("session", "week", "month")


def load() -> dict:
    if PATH.exists():
        try:
            return json.loads(PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save(data: dict) -> None:
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(PATH)


def parse_reset(txt: str | None):
    if not txt:
        return None
    if txt.isdigit():
        return float(txt)
    try:
        return datetime.fromisoformat(txt.replace("Z", "+00:00")).timestamp()
    except ValueError:
        raise SystemExit(f"no entiendo la fecha {txt!r} (usa ISO: 2026-10-23T03:00)")


def main() -> int:
    ap = argparse.ArgumentParser(description="Uso manual por proveedor")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("set", help="fija el porcentaje de una ventana")
    s.add_argument("provider", choices=PROVIDERS)
    s.add_argument("window", choices=WINDOWS)
    s.add_argument("pct", type=float)
    s.add_argument("--reset", help="cuándo se reinicia (ISO o epoch)")
    s.add_argument("--note", default="")

    sub.add_parser("list", help="muestra lo capturado")

    a = sub.add_parser("clear", help="borra una entrada")
    a.add_argument("provider", choices=PROVIDERS)

    args = ap.parse_args()
    data = load()

    if args.cmd == "set":
        entry = data.setdefault(args.provider, {"windows": {}})
        entry.setdefault("windows", {})
        entry["windows"][args.window] = {
            "pct": args.pct,
            "reset_at": parse_reset(args.reset),
            "note": args.note,
        }
        entry["updated"] = time.time()
        save(data)
        print(f"{args.provider}/{args.window} = {args.pct}% -> {PATH}")
    elif args.cmd == "clear":
        data.pop(args.provider, None)
        save(data)
        print(f"{args.provider} borrado")
    else:
        if not data:
            print("(vacío)")
        for pid, entry in data.items():
            upd = entry.get("updated")
            age = f"hace {(time.time()-upd)/3600:.1f} h" if upd else "sin fecha"
            print(f"{pid}  ({age})")
            for win, blk in (entry.get("windows") or {}).items():
                when = blk.get("reset_at")
                when_txt = (datetime.fromtimestamp(when).strftime("%Y-%m-%d %H:%M")
                            if when else "—")
                print(f"  {win:<8} {blk.get('pct')}%   reinicia {when_txt}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
