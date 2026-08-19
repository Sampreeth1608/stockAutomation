#!/usr/bin/env bash
# AMISE loop. Default: invent challengers on 3m…daily, improve existing books,
# write Lab pending. Same-TF vs S16/S18; daily also vs S13. Approve on the Lab
# tab names the next slot (S21, S22, …) or overwrites a filled chair. Never
# ENABLE from this script. Never DRY_RUN=false. Weekly is --full.
#
#   ./weekly_amise.sh              # --lab --propose --full  (includes improve)
#   ./weekly_amise.sh --observe    # memory only
#   ./weekly_amise.sh --lab
set -euo pipefail
cd "$(dirname "$0")"
PY="${PYTHON:-python3}"
if [[ -x ./venv/bin/python ]]; then
  PY=./venv/bin/python
fi
echo "AMISE weekly — factory + improve existing books; you Approve. Keep DRY_RUN=true."
if [[ $# -eq 0 ]]; then
  set -- --lab --propose --full
elif [[ "${1:-}" == "--observe" ]]; then
  shift
  set --
fi
exec "$PY" amise.py --db data/ticks.db "$@"
