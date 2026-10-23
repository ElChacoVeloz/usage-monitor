#!/usr/bin/env bash
# Instala el monitor como servicio de USUARIO (sin sudo) y verifica que responde.
set -euo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"
UNIT_DIR="$HOME/.config/systemd/user"
UNIT="$UNIT_DIR/usage-monitor.service"
PORT=8773

mkdir -p "$UNIT_DIR"

cat > "$UNIT" <<EOF
[Unit]
Description=AI Usage Monitor (cuotas de OpenCode Go, ChatGPT, Claude, Gemini)
After=network-online.target

[Service]
Type=simple
WorkingDirectory=$DIR
ExecStart=/usr/bin/python3 $DIR/server.py --port $PORT
Restart=always
RestartSec=3
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
EOF

# libera el puerto si quedó un proceso de prueba
if command -v ss >/dev/null; then
  PIDS=$(ss -ltnp 2>/dev/null | grep ":$PORT " | grep -o 'pid=[0-9]*' | cut -d= -f2 || true)
  for pid in $PIDS; do kill "$pid" 2>/dev/null || true; done
  sleep 1
fi

systemctl --user daemon-reload
systemctl --user enable --now usage-monitor.service

# que arranque al encender sin que nadie inicie sesión
loginctl enable-linger "$USER" >/dev/null 2>&1 || true

for i in $(seq 1 20); do
  code=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/api/data" || true)
  [ "$code" = "200" ] && break
  sleep 0.5
done

if [ "${code:-}" != "200" ]; then
  echo "FALLO: /api/data devolvió '$code'. Revisa: journalctl --user -u usage-monitor -n 40"
  exit 1
fi

IP=$(hostname -I 2>/dev/null | awk '{print $1}')
echo "OK: servicio activo (usuario $USER)"
echo "    local   http://127.0.0.1:$PORT/"
[ -n "${IP:-}" ] && echo "    LAN     http://$IP:$PORT/"
echo "    estado  systemctl --user status usage-monitor"
