#!/usr/bin/env bash
# Research: 31 CR_* strategies (ohlc/wick/prev/vol/htf mixes). All off. Not paper.
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p data/learn/candle_relations
PY="${PYTHON:-python3}"
if [[ -x ./venv/bin/python ]]; then
  PY=./venv/bin/python
fi
exec "$PY" learn_candle_relations.py \
  --db data/ticks.db \
  --tf 1h,1d \
  --higher 1d, \
  --lots 100 \
  --session \
  --fees \
  --out-dir data/learn/candle_relations \
  "$@"
