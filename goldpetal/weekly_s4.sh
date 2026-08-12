#!/usr/bin/env bash
# Weekly S4 overnight ML improve + control-panel proposal.
# Cron (Sunday 18:20 IST):  20 18 * * 0 cd ~/goldpetal && ./weekly_s4.sh >> data/s4_ml/cron.log 2>&1
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p data/s4_ml data/models data/control
PY="${PYTHON:-python3}"
if [[ -x ./venv/bin/python ]]; then
  PY=./venv/bin/python
fi
NOTE="week-$(date +%V)"
exec "$PY" evolve_s4_ml.py train \
  --model-dir data/models \
  --late-minutes 30 \
  --budget-note "$NOTE" \
  "$@"
