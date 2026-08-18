#!/usr/bin/env bash
# Score S18 packs from ticks. Writes an ML proposal (after charges, no tax).
# Approve → paper on the station, then type RESTART. Not live.
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p data/learn/s18
PY="${PYTHON:-python3}"
if [[ -x ./venv/bin/python ]]; then
  PY=./venv/bin/python
fi
exec "$PY" learn_s18.py \
  --db data/ticks.db \
  --lots 100 \
  --session \
  --fees \
  --out-dir data/learn/s18 \
  "$@"
