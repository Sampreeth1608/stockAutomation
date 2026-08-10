#!/usr/bin/env bash
# Weekly S8 full ML evolution at scale + Google Sheets CSV pack.
# Cron (Sunday 18:00 IST):  0 18 * * 0 cd ~/goldpetal && ./weekly_s8_nn.sh >> data/s8_nn/cron.log 2>&1
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p data/s8_nn data/models
PY="${PYTHON:-python3}"
if [[ -x ./venv/bin/python ]]; then
  PY=./venv/bin/python
fi
NOTE="week-$(date +%V)"
# Scale walk-forward + reasoner features + multi-step paths + paper safety + Sheets
exec "$PY" evolve_s8_ml.py train \
  --db data/ticks.db \
  --tf 10 \
  --bar-minutes 10 \
  --lots 100 \
  --min-proba 0.55 \
  --scale \
  --folds 5 \
  --budget-note "$NOTE" \
  "$@"
