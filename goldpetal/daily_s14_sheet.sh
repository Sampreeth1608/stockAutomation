#!/usr/bin/env bash
# Pull Angel/MCX Gold Petal candles onto the S14 chart (data/s14_sheet/).
# Open the chart through the SSH tunnel: http://127.0.0.1:8787/
#
# Cron (weekdays every 30 min, plus once after the 23:30 IST close):
#   ./daily_s14_sheet.sh --install-cron
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p data/s14_sheet

if [[ "${1:-}" == "--install-cron" ]]; then
  here="$(pwd)"
  job="*/30 * * * 1-5 cd ${here} && ./daily_s14_sheet.sh >> ${here}/data/s14_sheet/cron.log 2>&1"
  tmp="$(mktemp)"
  crontab -l 2>/dev/null | grep -v 'daily_s14_sheet.sh' >"$tmp" || true
  echo "$job" >>"$tmp"
  crontab "$tmp"
  rm -f "$tmp"
  echo "installed: $job"
  crontab -l
  exit 0
fi

dow="$(TZ=Asia/Kolkata date +%u)"
if [[ "$dow" -ge 6 ]]; then
  echo "skip weekend $(TZ=Asia/Kolkata date)"
  exit 0
fi

PY="${PYTHON:-python3}"
if [[ -x ./venv/bin/python ]]; then
  PY=./venv/bin/python
elif [[ -x ./.venv/bin/python ]]; then
  PY=./.venv/bin/python
fi
FROM="${S14_SHEET_FROM:-2026-08-02}"
echo "$(TZ=Asia/Kolkata date '+%Y-%m-%d %H:%M:%S IST') pull Angel ${FROM}→now  py=$PY"
exec "$PY" explain_s14_candles.py --tf 30m,1h,1d --from "$FROM" --no-print-bars
