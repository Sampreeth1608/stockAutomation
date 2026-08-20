#!/usr/bin/env bash
# Lightweight HTML trading station on 8501 (not Streamlit).
#
#   ./scripts/run_desk_vm.sh --restart
#   ./scripts/run_desk_vm.sh --fg
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR/.."
# shellcheck source=print_open_on_mac.sh
source "$SCRIPT_DIR/print_open_on_mac.sh"
export GP_DESK_LOCAL=1
export GP_DATA_DIR="${GP_DATA_DIR:-$PWD/data}"
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
mkdir -p data/control

CMD=("$PY" control_panel.py --host "$HOST" --port "$PORT")

stop_old() {
  pkill -f 'streamlit run analytics/app.py' 2>/dev/null || true
  pkill -f '[p]ython.*control_panel.py' 2>/dev/null || pkill -f 'control_panel.py' || true
  sleep 1
}

wait_up() {
  local i code
  for i in 1 2 3 4 5 6; do
    code="$(curl -s -o /tmp/gp-desk-get.html -w '%{http_code}' --max-time 2 "http://127.0.0.1:${PORT}/" || true)"
    if [[ "${code:-}" == "200" ]] && grep -q "Save strategies" /tmp/gp-desk-get.html 2>/dev/null; then
      echo "station UP  pid=$(pgrep -f 'control_panel.py' | head -n1)  $PWD  http://127.0.0.1:${PORT}/"
      hdr="$(grep -o 'gp-header-v[0-9]*' /tmp/gp-desk-get.html 2>/dev/null | head -n1 || true)"
      if [[ -n "${hdr:-}" ]]; then
        echo "header ${hdr} (clock must show · ${hdr#gp-header-} after Cmd+Shift+R)"
      else
        echo "WARNING: station.html has no gp-header-v* — copy station.html from origin/cursor/desk-session-header-a4b2"
      fi
      return 0
    fi
    sleep 1
  done
  echo "FAILED — expected station HTML (Gold Petal Station / Save strategies). HTTP ${code:-down}"
  tail -n 25 "$LOG" || true
  return 1
}

if [[ "${1:-}" == "--fg" ]]; then
  stop_old
  echo "→ station foreground :${PORT}"
  print_open_on_mac
  exec "${CMD[@]}"
fi

start_detached() {
  local session="${TMUX_DESK_SESSION:-gp-desk}"
  tmux kill-session -t "=$session" 2>/dev/null || true
  stop_old
  {
    echo
    echo "===== START $(date '+%F %T')  cwd=$PWD  bind=${HOST}:${PORT} ====="
  } >> "$LOG"
  tmux new-session -d -s "$session" -c "$PWD" \
    "export GP_DESK_LOCAL=1 GP_DATA_DIR='$GP_DATA_DIR'; ${CMD[*]} >> '$LOG' 2>&1; echo DESK_EXITED; sleep 5"
  echo "started tmux $session from $PWD"
  wait_up
  if [[ "${GP_QUIET_OPEN:-}" != "1" ]]; then
    print_open_on_mac
  fi
}

if [[ "${1:-}" == "--detach" || "${1:-}" == "--restart" || "${1:-}" == "" ]]; then
  if [[ "${1:-}" == "" ]] && command -v tmux >/dev/null 2>&1; then
    if tmux has-session -t "=${TMUX_DESK_SESSION:-gp-desk}" 2>/dev/null && [[ -t 0 ]]; then
      echo "→ tmux session already running — attaching"
      exec tmux attach -t "${TMUX_DESK_SESSION:-gp-desk}"
    fi
  fi
  start_detached
  exit 0
fi

echo "usage: $0 [--restart|--fg]"
exit 1
