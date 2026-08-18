#!/usr/bin/env bash
# Paper S18 pack learner. Not live. Type RESTART after a promote.
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
