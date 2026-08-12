#!/usr/bin/env bash
# Keep the strategy running; restart automatically if it exits.
set -u
cd "$(dirname "$0")"
mkdir -p data
LOG="data/strategy_run.log"

echo "[$(date '+%F %T')] supervisor starting" | tee -a "$LOG"

while true; do
  echo "[$(date '+%F %T')] launching run_strategy.py" | tee -a "$LOG"
  PYTHONUNBUFFERED=1 python run_strategy.py 2>&1 | tee -a "$LOG"
  code=${PIPESTATUS[0]}
  echo "[$(date '+%F %T')] run_strategy exited code=${code}; restarting in 5s" | tee -a "$LOG"
  sleep 5
done
