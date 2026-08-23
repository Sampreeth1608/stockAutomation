#!/usr/bin/env bash
# Start S16 bot + desk after the VM boots (08:55 IST schedule).
# Does not Arm live. Does not change DRY_RUN. Desk stays on localhost.
set -u
export HOME="${HOME:-/home/sampreeth1608}"
GP="${GP_DESK_DIR:-$HOME/goldpetal}"
cd "$GP" || exit 1
mkdir -p data
LOG="data/boot_s16_session.log"
echo "[$(date '+%F %T %Z')] boot_s16_session cwd=$GP" >>"$LOG"

if ! pgrep -f 'supervise.sh' >/dev/null 2>&1; then
  if [[ -x ./supervise.sh ]]; then
    nohup ./supervise.sh >>data/supervise.log 2>&1 &
    echo "[$(date '+%F %T')] started supervise pid=$!" >>"$LOG"
  else
    echo "[$(date '+%F %T')] ERROR: no supervise.sh in $GP" >>"$LOG"
  fi
else
  echo "[$(date '+%F %T')] supervise already up" >>"$LOG"
fi

if ! pgrep -f 'control_panel.py' >/dev/null 2>&1; then
  if [[ -x ./scripts/run_desk_vm.sh ]]; then
    GP_QUIET_OPEN=1 ./scripts/run_desk_vm.sh --restart >>"$LOG" 2>&1 || true
    echo "[$(date '+%F %T')] desk restart requested" >>"$LOG"
  fi
else
  echo "[$(date '+%F %T')] desk already up" >>"$LOG"
fi
