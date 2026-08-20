#!/usr/bin/env bash
# Gold Petal — the trading application (Mac launcher).
#
# The full app is already the VM station (control_panel.py + station.html).
# This script is the Mac "app": start a private SSH tunnel and open it.
# Run on the Mac. Never on the VM. Never open the desk on a public IP. Keep DRY_RUN=true.
#
# Double-click: scripts/GoldPetal.command
# Terminal:     ./scripts/open_desk_mac.sh
set -euo pipefail

VM_USER="${GP_VM_USER:-sampreeth1608}"
VM_NAME="${GP_VM_NAME:-sampreeth-love-story}"
VM_ZONE="${GP_VM_ZONE:-asia-south1-c}"
DESK_URL="${GP_DESK_URL:-http://127.0.0.1:8501/}"
REMOTE_DESK="${GP_REMOTE_DESK:-cd ~/goldpetal && ./scripts/run_desk_vm.sh --restart}"

desk_http() {
  curl -s -o /dev/null -w '%{http_code}' --max-time 2 "$DESK_URL" 2>/dev/null || echo "000"
}

open_browser() {
  if command -v open >/dev/null 2>&1; then
    open "$DESK_URL"
  else
    echo "Open Chrome: $DESK_URL"
  fi
}

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This is the Mac Gold Petal app."
  echo "You are on $(uname -s) $(hostname)."
  echo
  echo "On the VM (this machine):  ./scripts/run_desk_vm.sh --restart"
  echo "On the Mac laptop:         double-click scripts/GoldPetal.command"
  echo "Do not gcloud/ssh from the VM to itself."
  exit 1
fi

echo "======== Gold Petal ========"
echo "Full app = trading station on the VM, private tunnel on this Mac."
echo "Not Google Sheets. Not a public website. Keep DRY_RUN=true."
echo

code="$(desk_http)"
if [[ "$code" == "200" ]]; then
  echo "Station already on $DESK_URL"
  open_browser
  echo "Hard-refresh Chrome: Cmd+Shift+R"
  exit 0
fi

if ! command -v gcloud >/dev/null 2>&1; then
  echo "gcloud not found. Install Google Cloud SDK, then run this again."
  echo "Or paste this in Terminal and leave it running:"
  echo "  gcloud compute ssh ${VM_USER}@${VM_NAME} --zone=${VM_ZONE} -- -N -L 8501:127.0.0.1:8501"
  echo "Then Chrome: $DESK_URL"
  exit 1
fi

echo "Starting the station on the VM (desk restart, not the bot)…"
gcloud compute ssh "${VM_USER}@${VM_NAME}" --zone="${VM_ZONE}" --command "$REMOTE_DESK"

echo "Opening private tunnel 8501 → VM localhost. Leave this window open."
gcloud compute ssh "${VM_USER}@${VM_NAME}" --zone="${VM_ZONE}" -- -N -L 8501:127.0.0.1:8501 &
TUNNEL_PID=$!
trap 'kill "$TUNNEL_PID" 2>/dev/null || true' EXIT

for _ in $(seq 1 25); do
  if [[ "$(desk_http)" == "200" ]]; then
    echo "Station UP  $DESK_URL"
    open_browser
    echo "Hard-refresh: Cmd+Shift+R"
    echo "Closing this window drops the tunnel."
    wait "$TUNNEL_PID"
    exit 0
  fi
  sleep 1
done

echo "Tunnel started but $DESK_URL is not 200 yet."
echo "On the VM: ./scripts/run_desk_vm.sh --restart"
echo "Then Chrome: $DESK_URL"
wait "$TUNNEL_PID"
exit 1
