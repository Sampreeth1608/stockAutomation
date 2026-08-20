#!/usr/bin/env bash
# Push the dashboard snapshot to Google Sheets (LIVE/MARKET/STRATEGIES/…).
# Needs GOOGLE_SERVICE_ACCOUNT_JSON + GOOGLE_MONITOR_SHEET_ID in .env.
# Does not Unlock live. Prefer every 15–60s, not every tick.
#
# Cron example (every 5 min Mon–Fri 09:00–23:30 IST — adjust if VM TZ is UTC):
#   */5 9-23 * * 1-5 cd /home/sampreeth1608/goldpetal && ./push_monitor_sheet.sh >> data/monitor_sheet/cron.log 2>&1
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p data/monitor_sheet
PY="${PYTHON:-python3}"
if [[ -x ./venv/bin/python ]]; then
  PY=./venv/bin/python
fi
exec "$PY" monitor_sheet.py --out-dir data/monitor_sheet --upload "$@"
