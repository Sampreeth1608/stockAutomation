#!/usr/bin/env bash
# Run Streamlit desk ON the trading VM inside tmux (survives SSH disconnect).
#
#   ./scripts/run_desk_vm.sh           # start/attach tmux session "gp-desk"
#   ./scripts/run_desk_vm.sh --fg      # foreground (no tmux)
#   tmux attach -t gp-desk             # reattach later
#   tmux kill-session -t gp-desk       # stop desk
set -euo pipefail
cd "$(dirname "$0")/.."
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
  --server.address 0.0.0.0
  --server.port 8501
  --server.headless true
)

mkdir -p data/control

if [[ "${1:-}" == "--fg" ]]; then
  echo "→ desk foreground :8501  data=$GP_DATA_DIR"
  exec "${CMD[@]}"
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

echo "→ starting desk in tmux session '$SESSION' on :8501"
echo "   detach: Ctrl+B then D    reattach: tmux attach -t $SESSION"
tmux new-session -d -s "$SESSION" -c "$PWD" \
  "export GP_DESK_LOCAL=1 GP_DATA_DIR='$GP_DATA_DIR'; ${CMD[*]}; echo DESK_EXITED; sleep 5"
sleep 2
tmux attach -t "$SESSION"
