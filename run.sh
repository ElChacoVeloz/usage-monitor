#!/usr/bin/env bash
# Arranque manual del monitor (el servicio de usuario lo hace solo en systemd).
set -euo pipefail
cd "$(dirname "$0")"
exec python3 server.py --port 8773 "$@"
