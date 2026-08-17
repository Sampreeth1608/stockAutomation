#!/usr/bin/env bash
# Restart the 8787 control panel only (not supervise / not the bot).
# Default bind 127.0.0.1 — open via SSH tunnel:
#   gcloud compute ssh sampreeth1608@sampreeth-love-story --zone=asia-south1-c -- -N -L 8787:127.0.0.1:8787
#   then http://127.0.0.1:8787/
set -euo pipefail
cd "$(dirname "$0")/.."
HOST="${HOST:-127.0.0.1}"
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
echo "On your Mac:"
echo "  gcloud compute ssh sampreeth1608@sampreeth-love-story --zone=asia-south1-c -- -N -L ${PORT}:127.0.0.1:${PORT}"
echo "Then open http://127.0.0.1:${PORT}/  (hard-refresh Ctrl+Shift+R)"
sleep 1
tail -n 20 data/control_panel.log || true
