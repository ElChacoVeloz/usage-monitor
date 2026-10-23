#!/usr/bin/env bash
# Estado de las tres credenciales que usa el monitor (nunca imprime secretos).
set -uo pipefail

show() { printf '%-34s' "$1"; }

if [ -f "$HOME/.codex/auth.json" ]; then
  show "Codex (~/.codex/auth.json)"; python3 -c "
import json,os;d=json.load(open(os.path.expanduser('~/.codex/auth.json')))
print('OK  plan:', (d.get('tokens') or {}).get('account_id','')[:8]+'…', '| refresh:', d.get('last_refresh','—')[:19])"
else
  show "Codex (~/.codex/auth.json)"; echo "FALTA — corre: codex login --device-auth"
fi

if [ -f "$HOME/.claude/.credentials.json" ]; then
  show "Claude (~/.claude/…)"; python3 -c "
import json,os,datetime;d=json.load(open(os.path.expanduser('~/.claude/.credentials.json')))
o=d.get('claudeAiOauth') or d
exp=o.get('expiresAt') or 0
exp=exp/1000 if exp>1e11 else exp
print('OK  expira:', datetime.datetime.fromtimestamp(exp).strftime('%Y-%m-%d %H:%M') if exp else '—')"
else
  show "Claude (~/.claude/…)"; echo "FALTA — corre: claude auth login"
fi

if [ -f "$HOME/.gemini/oauth_creds.json" ]; then
  show "Gemini (~/.gemini/…)"; python3 -c "
import json,os;d=json.load(open(os.path.expanduser('~/.gemini/oauth_creds.json')))
print('OK  scopes:', len((d.get('scope') or '').split()), '| refresh_token:', 'sí' if d.get('refresh_token') else 'no')"
else
  show "Gemini (~/.gemini/…)"; echo "FALTA — corre: gemini   (y elige Login with Google)"
fi

echo
echo "estado del monitor:"
command -v curl >/dev/null && curl -s http://127.0.0.1:8773/api/data | python3 -c "
import json,sys
try: d=json.load(sys.stdin)
except Exception: print('  (sin respuesta del monitor)'); raise SystemExit
for p in d.get('providers',[]):
    print('  ', p['id'].ljust(9), p['source_label'].ljust(9), (p.get('source_note') or p['footer'].get('note') or '')[:60])"
