#!/usr/bin/env bash
# Build Gold Petal Sheets pack daily (ZIP under data/sheets_pack/).
# Cron example (every day 23:45 IST — adjust if VM TZ is UTC):
#   45 23 * * 1-5 cd /home/sampreeth1608/goldpetal && ./daily_sheets.sh >> data/sheets_pack/cron.log 2>&1
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p data/sheets_pack
PY="${PYTHON:-python3}"
if [[ -x ./venv/bin/python ]]; then
  PY=./venv/bin/python
fi
exec "$PY" sheets_pack.py --out-dir data/sheets_pack "$@"
