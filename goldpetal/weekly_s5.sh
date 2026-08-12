#!/usr/bin/env bash
# Weekly S5 min-edge ML improve + control-panel proposal.
# Cron (Sunday 18:40 IST):  40 18 * * 0 cd ~/goldpetal && ./weekly_s5.sh >> data/s5_ml/cron.log 2>&1
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p data/s5_ml data/models data/control
PY="${PYTHON:-python3}"
if [[ -x ./venv/bin/python ]]; then
  PY=./venv/bin/python
fi
NOTE="week-$(date +%V)"
exec "$PY" evolve_s5_ml.py train \
  --db data/ticks.db \
  --tf 5 \
  --horizon 6 \
  --req-pts 50 \
  --budget-note "$NOTE" \
  "$@"
