#!/usr/bin/env bash
# Weekly multi-model strategy discovery from ticks → weekend proposal.
# Cron (Sunday 19:00 IST):  0 19 * * 0 cd ~/goldpetal && ./weekly_discover.sh >> data/discover/cron.log 2>&1
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p data/discover data/discover/models data/discover/packs data/control
PY="${PYTHON:-python3}"
if [[ -x ./venv/bin/python ]]; then
  PY=./venv/bin/python
fi
NOTE="week-$(date +%V)"
exec "$PY" discover_strategies.py run \
  --out-dir data/discover \
  --top-k 3 \
  --note "$NOTE" \
  "$@"
