#!/usr/bin/env bash
# Restart the 8787 HTML operator desk only (not supervise).
# Same writes as Streamlit 8501 tab Desk. Use 8501 if 8787 does not open.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8787}"
LOG="data/control_panel.log"
PY="./venv/bin/python"
if [[ ! -x "$PY" ]]; then
  PY="./.venv/bin/python"
fi
if [[ ! -x "$PY" ]]; then
  PY="python3"
fi
mkdir -p data

echo "desk folder: $ROOT"
echo "stopping any old control_panel.py (including ~/goldpetal)…"
pkill -f '[p]ython.*control_panel.py' 2>/dev/null || pkill -f 'control_panel.py' || true
sleep 1

{
  echo
  echo "===== START $(date '+%F %T')  cwd=$ROOT  bind=${HOST}:${PORT} ====="
} >> "$LOG"

nohup "$PY" control_panel.py --host "$HOST" --port "$PORT" >> "$LOG" 2>&1 &
pid=$!
echo "started pid $pid"
sleep 2

if ! kill -0 "$pid" 2>/dev/null; then
  echo "FAILED — process $pid is dead. Last log:"
  tail -n 20 "$LOG" || true
  exit 1
fi

code="$(curl -s -o /tmp/gp-desk-get.html -w '%{http_code}' --max-time 3 "http://127.0.0.1:${PORT}/" || true)"
echo "local GET / → HTTP ${code:-down}"
if [[ "${code:-}" != "200" ]] || ! grep -q "Save strategies" /tmp/gp-desk-get.html 2>/dev/null; then
  echo "FAILED — expected the new desk HTML (Start bot / Save strategies)."
  tail -n 20 "$LOG" || true
  exit 1
fi

echo "desk is UP from $ROOT  (pid $pid)"
echo "If this shell prints 'Terminated', that was the OLD panel. Ignore it."
echo
echo "If 8787 does not open in Chrome, skip it. Use the 8501 tunnel you already have:"
echo "  gcloud compute ssh sampreeth1608@sampreeth-love-story --zone=asia-south1-c -- -N -L 8501:127.0.0.1:8501"
echo "Then Chrome: http://127.0.0.1:8501/  → first tab Desk"
echo "Do not type URLs in this SSH session."
echo
echo "Optional 8787 tunnel:"
echo "  gcloud compute ssh sampreeth1608@sampreeth-love-story --zone=asia-south1-c -- -N -L ${PORT}:127.0.0.1:${PORT}"
echo "Then http://127.0.0.1:${PORT}/  (hard-refresh Cmd+Shift+R)"
