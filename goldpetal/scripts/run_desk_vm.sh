#!/usr/bin/env bash
# Run Streamlit desk ON the trading VM inside tmux (survives SSH disconnect).
#
#   ./scripts/run_desk_vm.sh --detach   # kill old 8501 and start in tmux
#   ./scripts/run_desk_vm.sh --restart  # same as --detach
#   ./scripts/run_desk_vm.sh --fg       # foreground (no tmux)
#   tmux attach -t gp-desk             # reattach later
#   tmux kill-session -t gp-desk       # stop desk
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR/.."
# shellcheck source=print_open_on_mac.sh
source "$SCRIPT_DIR/print_open_on_mac.sh"
export GP_DESK_LOCAL=1
export GP_DATA_DIR="${GP_DATA_DIR:-$PWD/data}"

if [[ -x ./venv/bin/streamlit ]]; then
  ST=./venv/bin/streamlit
elif command -v streamlit >/dev/null 2>&1; then
  ST=streamlit
else
  echo "Install streamlit: pip install -r requirements-analytics.txt"
  exit 1
fi

CMD=(
  "$ST" run analytics/app.py
  --server.address 127.0.0.1
  --server.port 8501
  --server.headless true
)

mkdir -p data/control

if [[ "${1:-}" == "--fg" ]]; then
  echo "→ desk foreground :8501  data=$GP_DATA_DIR"
  print_open_on_mac
  exec "${CMD[@]}"
fi

start_detached() {
  local session="${TMUX_DESK_SESSION:-gp-desk}"
  tmux kill-session -t "=$session" 2>/dev/null || true
  pkill -f 'streamlit run analytics/app.py' 2>/dev/null || true
  sleep 1
  tmux new-session -d -s "$session" -c "$PWD" \
    "export GP_DESK_LOCAL=1 GP_DATA_DIR='$GP_DATA_DIR'; ${CMD[*]}; echo DESK_EXITED; sleep 5"
  echo "started tmux $session from $PWD"
  print_open_on_mac
}

if [[ "${1:-}" == "--detach" || "${1:-}" == "--restart" ]]; then
  start_detached
  exit 0
fi

SESSION="${TMUX_DESK_SESSION:-gp-desk}"
if ! command -v tmux >/dev/null 2>&1; then
  echo "tmux not found — install with: sudo apt-get install -y tmux"
  echo "Or run: ./scripts/run_desk_vm.sh --fg"
  exit 1
fi

# Stop any stray streamlit on 8501 outside this session
pkill -f 'streamlit run analytics/app.py' 2>/dev/null || true
sleep 1

if tmux has-session -t "=$SESSION" 2>/dev/null; then
  echo "→ tmux session '$SESSION' already running — attaching"
  exec tmux attach -t "$SESSION"
fi

echo "→ starting desk in tmux session '$SESSION' on 127.0.0.1:8501 (localhost only)"
echo "   detach: Ctrl+B then D    reattach: tmux attach -t $SESSION"
print_open_on_mac
tmux new-session -d -s "$SESSION" -c "$PWD" \
  "export GP_DESK_LOCAL=1 GP_DATA_DIR='$GP_DATA_DIR'; ${CMD[*]}; echo DESK_EXITED; sleep 5"
sleep 2
tmux attach -t "$SESSION"
