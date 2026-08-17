#!/usr/bin/env bash
# Restart 8787 only (not supervise). Listens on all interfaces so BOTH work:
#   tunnel  → http://127.0.0.1:8787/  (gcloud -L 8787:127.0.0.1:8787)
#   public  → http://<vm-ip>:8787/
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
echo "started pid $!  bind=${HOST}:${PORT}  log=data/control_panel.log"
sleep 1
code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "http://127.0.0.1:${PORT}/" || true)"
echo "local GET / → HTTP ${code:-down}"
tail -n 8 data/control_panel.log || true
echo
echo "Mac tunnel (leave running):"
echo "  gcloud compute ssh sampreeth1608@sampreeth-love-story --zone=asia-south1-c -- -N -L ${PORT}:127.0.0.1:${PORT}"
echo "Then open http://127.0.0.1:${PORT}/"
echo "Backup if tunnel is down: http://8.231.125.120:${PORT}/"
