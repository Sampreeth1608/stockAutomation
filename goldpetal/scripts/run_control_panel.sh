#!/usr/bin/env bash
# Restart the 8787 control panel only (not supervise / not the bot).
# Default bind 0.0.0.0 so http://<vm-ip>:8787/ works from your laptop.
# Tunnel-only: HOST=127.0.0.1 ./scripts/run_control_panel.sh
set -euo pipefail
cd "$(dirname "$0")/.."
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8787}"
PY="./venv/bin/python"
if [[ ! -x "$PY" ]]; then
  PY="./.venv/bin/python"
fi
if [[ ! -x "$PY" ]]; then
  PY="python3"
fi
mkdir -p data
pkill -f 'control_panel.py' || true
sleep 1
nohup "$PY" control_panel.py --host "$HOST" --port "$PORT" >> data/control_panel.log 2>&1 &
echo "started pid $!  log=data/control_panel.log  bind=${HOST}:${PORT}"
echo "open http://<vm-ip>:${PORT}/  (hard-refresh Ctrl+Shift+R)"
echo "if you use the SSH tunnel instead: http://127.0.0.1:${PORT}/"
sleep 1
tail -n 20 data/control_panel.log || true
