#!/usr/bin/env bash
# AMISE loop. Default: invent challengers and write Lab pending rows.
# Approve on the Lab tab names S21–S24 and papers them. Never ENABLE
# from this script. Never DRY_RUN=false.
#
#   ./weekly_amise.sh              # --lab --propose
#   ./weekly_amise.sh --observe    # memory only
#   ./weekly_amise.sh --lab
set -euo pipefail
cd "$(dirname "$0")"
PY="${PYTHON:-python3}"
if [[ -x ./venv/bin/python ]]; then
  PY=./venv/bin/python
fi
echo "AMISE weekly — factory proposes; you Approve S21–S24. Keep DRY_RUN=true."
if [[ $# -eq 0 ]]; then
  set -- --lab --propose
elif [[ "${1:-}" == "--observe" ]]; then
  shift
  set --
fi
exec "$PY" amise.py --db data/ticks.db "$@"
