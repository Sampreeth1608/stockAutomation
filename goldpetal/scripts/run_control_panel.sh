#!/usr/bin/env bash
# Same lite HTML desk as run_desk_vm.sh (port 8501).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR/.."
# shellcheck source=print_open_on_mac.sh
source "$SCRIPT_DIR/print_open_on_mac.sh"
ROOT="$PWD"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8501}"
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
echo "stopping Streamlit and old control_panel.py…"
pkill -f 'streamlit run analytics/app.py' 2>/dev/null || true
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
  echo "FAILED — expected the lite desk HTML (Start bot / Save strategies)."
  tail -n 20 "$LOG" || true
  exit 1
fi

echo "lite desk is UP from $ROOT  (pid $pid)"
echo "If this shell prints 'Terminated', that was the OLD panel. Ignore it."
print_open_on_mac
