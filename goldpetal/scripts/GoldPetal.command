#!/bin/bash
# Gold Petal — standalone Mac app. Put this file on the Desktop and double-click.
# The real station stays on the VM. This only opens a private SSH tunnel + Chrome.
# Run on the Mac. Never on the VM. Keep DRY_RUN=true.
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
  echo "This file is the Mac app. You ran it on $(uname -s) $(hostname)."
  echo
  echo "The fetch you just did was on the VM. /tmp/GoldPetal.command here cannot open Chrome."
  echo "On the VM:  ./scripts/run_desk_vm.sh --restart"
  echo "On the Mac Terminal (prompt must NOT say sampreeth-love-story):"
  echo "  gcloud compute scp ${VM_USER}@${VM_NAME}:/home/${VM_USER}/goldpetal-repo/goldpetal/scripts/GoldPetal.command ~/Desktop/GoldPetal.command --zone=${VM_ZONE}"
  echo "  chmod +x ~/Desktop/GoldPetal.command"
  echo "  open ~/Desktop/GoldPetal.command"
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

echo "Opening private tunnel 8501 → VM localhost."
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
