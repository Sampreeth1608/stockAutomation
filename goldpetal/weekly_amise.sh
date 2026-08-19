#!/usr/bin/env bash
# AMISE observe loop (market state + guardian + similar-state memory).
# Pass --lab to run the research factory (slow). --propose writes Lab
# pending rows only. Never ENABLE. Keep DRY_RUN=true.
#
#   ./weekly_amise.sh
#   ./weekly_amise.sh --lab
#   ./weekly_amise.sh --lab --propose
set -euo pipefail
cd "$(dirname "$0")"
PY="${PYTHON:-python3}"
if [[ -x ./venv/bin/python ]]; then
  PY=./venv/bin/python
fi
echo "AMISE weekly — research only. Does not ENABLE. Keep DRY_RUN=true."
exec "$PY" amise.py --db data/ticks.db "$@"
