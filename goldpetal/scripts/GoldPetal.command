#!/bin/bash
# Gold Petal — standalone Mac app. Put this file on the Desktop and double-click.
# The real station stays on the VM. This only opens a private SSH tunnel + Chrome.
# Run on the Mac. Never on the VM. Keep DRY_RUN=true.
set -euo pipefail

VM_USER="${GP_VM_USER:-sampreeth1608}"
VM_NAME="${GP_VM_NAME:-sampreeth-love-story}"
VM_ZONE="${GP_VM_ZONE:-asia-south1-c}"
DESK_URL="${GP_DESK_URL:-http://127.0.0.1:8501/}"
REMOTE_DESK="${GP_REMOTE_DESK:-cd ~/goldpetal && GP_QUIET_OPEN=1 ./scripts/run_desk_vm.sh --restart}"

desk_http() {
  curl -s -o /dev/null -w '%{http_code}' --max-time 2 "$DESK_URL" || true
}

open_browser() {
  if command -v open >/dev/null 2>&1; then
    open "$DESK_URL"
  else
    echo "Open Chrome: $DESK_URL"
  fi
}

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This file is the Mac app. You ran it on $(uname -s) $(hostname)."
  echo
  echo "On the VM:  ./scripts/run_desk_vm.sh --restart"
  echo "On the Mac Terminal (prompt must NOT say sampreeth-love-story):"
  echo "  gcloud compute ssh ${VM_USER}@${VM_NAME} --zone=${VM_ZONE} -- -N -L 8501:127.0.0.1:8501"
  echo "Then Chrome: $DESK_URL"
  echo "Do not gcloud from the VM to itself."
  exit 1
fi

echo "======== Gold Petal ========"
echo "Full app = trading station on the VM, private tunnel on this Mac."
echo "Leave this window open. Closing it drops the tunnel. Keep DRY_RUN=true."
echo

code="$(desk_http)"
if [[ "$code" == "200" ]]; then
  echo "Station already on $DESK_URL"
  open_browser
  echo "Hard-refresh Chrome: Cmd+Shift+R"
  echo "This window can stay open if a tunnel is already running elsewhere."
  exit 0
fi

if ! command -v gcloud >/dev/null 2>&1; then
  echo "gcloud not found on this Mac. Install Google Cloud SDK, then double-click again."
  echo "Or paste this in Mac Terminal and leave it running:"
  echo "  gcloud compute ssh ${VM_USER}@${VM_NAME} --zone=${VM_ZONE} -- -N -L 8501:127.0.0.1:8501"
  echo "Then Chrome: $DESK_URL"
  exit 1
fi

echo "Starting the station on the VM (desk restart, not the bot)…"
gcloud compute ssh "${VM_USER}@${VM_NAME}" --zone="${VM_ZONE}" --command "$REMOTE_DESK"

LOG="${TMPDIR:-/tmp}/goldpetal-tunnel.log"
echo "Opening private tunnel 8501 → VM. This can take a minute…"
gcloud compute ssh "${VM_USER}@${VM_NAME}" --zone="${VM_ZONE}" -- \
  -N -L 8501:127.0.0.1:8501 -o ExitOnForwardFailure=yes >"$LOG" 2>&1 &
TUNNEL_PID=$!
trap 'kill "$TUNNEL_PID" 2>/dev/null || true' EXIT

up=0
for _ in $(seq 1 90); do
  if ! kill -0 "$TUNNEL_PID" 2>/dev/null; then
    echo "Tunnel process exited. Log:"
    cat "$LOG" 2>/dev/null || true
    echo
    echo "Paste this in Mac Terminal and leave it running:"
    echo "  gcloud compute ssh ${VM_USER}@${VM_NAME} --zone=${VM_ZONE} -- -N -L 8501:127.0.0.1:8501"
    echo "Then Chrome: $DESK_URL"
    exit 1
  fi
  if [[ "$(desk_http)" == "200" ]]; then
    up=1
    break
  fi
  sleep 1
done

if [[ "$up" == "1" ]]; then
  echo "Station UP  $DESK_URL"
else
  echo "Tunnel still connecting. Opening Chrome anyway — wait a few seconds, then Cmd+Shift+R."
  echo "If Chrome is refused, wait in this window; do not close it."
fi
open_browser
echo "Hard-refresh: Cmd+Shift+R"
echo "Closing this window drops the tunnel."
wait "$TUNNEL_PID"
