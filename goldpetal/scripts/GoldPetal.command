#!/bin/bash
# Gold Petal — standalone Mac app. Put this file on the Desktop and double-click.
# The real station stays on the VM. This only opens a private IAP SSH tunnel + Chrome.
# Run on the Mac. Never on the VM. Keep DRY_RUN=true.
#
# Port 22 on the VM public IP is closed on purpose. Direct ssh to 8.231.125.120
# times out. Always use --tunnel-through-iap. Do not open 22 or 8501 to the internet.
set -euo pipefail

VM_USER="${GP_VM_USER:-sampreeth1608}"
VM_NAME="${GP_VM_NAME:-sampreeth-love-story}"
VM_ZONE="${GP_VM_ZONE:-asia-south1-c}"
VM_PROJECT="${GP_VM_PROJECT:-sampreethlovestory}"
DESK_URL="${GP_DESK_URL:-http://127.0.0.1:8501/}"
REMOTE_DESK="${GP_REMOTE_DESK:-cd ~/goldpetal && GP_QUIET_OPEN=1 ./scripts/run_desk_vm.sh --restart}"

TUNNEL_CMD=(gcloud compute ssh "${VM_USER}@${VM_NAME}"
  --project="${VM_PROJECT}"
  --zone="${VM_ZONE}"
  --tunnel-through-iap
  -- -N -L 8501:127.0.0.1:8501
  -o ExitOnForwardFailure=yes
  -o ServerAliveInterval=30)

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

print_tunnel() {
  echo "  gcloud compute ssh ${VM_USER}@${VM_NAME} --project=${VM_PROJECT} --zone=${VM_ZONE} --tunnel-through-iap -- -N -L 8501:127.0.0.1:8501"
}

hold() {
  echo
  echo "Press Enter to close this window."
  read -r _ || true
}

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This file is the Mac app. You ran it on $(uname -s) $(hostname)."
  echo
  echo "On the VM:  ./scripts/run_desk_vm.sh --restart"
  echo "On the Mac Terminal (prompt must NOT say sampreeth-love-story):"
  print_tunnel
  echo "Then Chrome: $DESK_URL"
  echo "Do not gcloud from the VM to itself. Do not ssh to the VM public IP."
  exit 1
fi

echo "======== Gold Petal ========"
echo "Full app = trading station on the VM, private IAP tunnel on this Mac."
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
  print_tunnel
  echo "Then Chrome: $DESK_URL"
  hold
  exit 1
fi

echo "Starting the station on the VM (desk restart, not the bot)…"
set +e
gcloud compute ssh "${VM_USER}@${VM_NAME}" \
  --project="${VM_PROJECT}" \
  --zone="${VM_ZONE}" \
  --tunnel-through-iap \
  --command "$REMOTE_DESK"
start_rc=$?
set -e
if [[ "$start_rc" -ne 0 ]]; then
  echo
  echo "Could not reach the VM to start the desk."
  echo "If you saw: ssh: connect to host … port 22: Operation timed out"
  echo "that is the public IP, not IAP. This launcher already passes --tunnel-through-iap."
  echo "Paste this from a Mac Terminal whose prompt is NOT sampreeth-love-story:"
  print_tunnel
  echo "Do not open port 22 to the internet. Do not bind 8501 on 0.0.0.0."
  hold
  exit "$start_rc"
fi

echo
echo "Opening private IAP tunnel 8501 → VM localhost."
echo "This window is the tunnel. Leave it open. Chrome opens when 8501 answers."
echo

(
  up=0
  for _ in $(seq 1 90); do
    if [[ "$(desk_http)" == "200" ]]; then
      up=1
      break
    fi
    sleep 1
  done
  if [[ "$up" == "1" ]]; then
    echo "Station UP  $DESK_URL"
  else
    echo "Tunnel still connecting. Opening Chrome anyway — wait, then Cmd+Shift+R."
  fi
  open_browser
  echo "Hard-refresh: Cmd+Shift+R"
) &

set +e
"${TUNNEL_CMD[@]}"
tunnel_rc=$?
set -e

echo
if [[ "$tunnel_rc" -ne 0 ]]; then
  echo "Tunnel exited. The desk on the VM can stay up — you only need IAP from this Mac."
  echo "If the log said connect to host 8.231.125.120 port 22: Operation timed out,"
  echo "gcloud tried the public IP. Paste this and leave it running:"
  print_tunnel
  echo "Do not open port 22 or 8501 to the internet."
else
  echo "Tunnel closed."
fi
hold
exit "$tunnel_rc"
