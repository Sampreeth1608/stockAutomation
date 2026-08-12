#!/usr/bin/env bash
# Run Streamlit desk ON the trading VM (local data/ — no Mac sync).
#
#   cd ~/goldpetal
#   ./scripts/run_desk_vm.sh
#
# Open in browser:
#   http://VM_EXTERNAL_IP:8501
# or from Mac (view only): gcloud compute ssh ... -- -L 8501:localhost:8501
set -euo pipefail
cd "$(dirname "$0")/.."
export GP_DESK_LOCAL=1
export GP_DATA_DIR="${GP_DATA_DIR:-$PWD/data}"
# Prefer venv
if [[ -x ./venv/bin/streamlit ]]; then
  PY=./venv/bin/streamlit
elif command -v streamlit >/dev/null 2>&1; then
  PY=streamlit
else
  echo "Install streamlit: pip install streamlit plotly"
  exit 1
fi
mkdir -p data/control
echo "→ desk on :8501  data=$GP_DATA_DIR  (Ctrl+C to stop)"
exec "$PY" run analytics/app.py \
  --server.address 0.0.0.0 \
  --server.port 8501 \
  --server.headless true
