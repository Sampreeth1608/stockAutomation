#!/usr/bin/env bash
# Weekly S11 discovery from ticks. Trains always. ML proposal only if
# after-charges profit (tax excluded) beats the loaded pack, or if none loaded yet.
# Approve → paper on the station, then type RESTART. Not live.
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p data/discover data/discover/models data/discover/packs data/control
PY="${PYTHON:-python3}"
if [[ -x ./venv/bin/python ]]; then
  PY=./venv/bin/python
fi
NOTE="week-$(date +%V)"
export PYTHONUNBUFFERED=1
exec "$PY" -u discover_strategies.py run \
  --out-dir data/discover \
  --top-k 3 \
  --horizon 0 \
  --note "$NOTE" \
  "$@"
