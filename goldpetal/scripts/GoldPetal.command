#!/bin/bash
# Gold Petal — standalone Mac app. Put this file on the Desktop and double-click.
# The real station stays on the VM. This only opens a private IAP SSH tunnel + Chrome.
# Run on the Mac. Never on the VM. Keep DRY_RUN=true.
# GoldPetal.command v35 — tunnel first; do not restart the desk on every click.
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
  # -sL follows / → /login (302) so Unlock desk still counts as UP.
  curl -sL -o /dev/null -w '%{http_code}' --max-time 3 "$DESK_URL" || true
}

desk_answers() {
  case "$(desk_http)" in
    200|301|302|401|403) return 0 ;;
    *) return 1 ;;
  esac
}

port_busy() {
  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:8501 -sTCP:LISTEN >/dev/null 2>&1
  else
    return 1
  fi
}

open_browser() {
  echo "Opening Chrome: $DESK_URL"
  if command -v open >/dev/null 2>&1; then
    open -a "Google Chrome" "$DESK_URL" 2>/dev/null || open "$DESK_URL"
  else
    echo "Open Chrome: $DESK_URL"
  fi
}

print_tunnel() {
  echo "  gcloud compute ssh ${VM_USER}@${VM_NAME} --project=${VM_PROJECT} --zone=${VM_ZONE} --tunnel-through-iap -- -N -L 8501:127.0.0.1:8501"
}

hold() {
  echo
  echo "Leave this window open while you use the desk. Press Enter to close."
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

echo "======== Gold Petal v35 ========"
echo "This window is the private IAP tunnel. Leave it open. Closing it drops the desk."
echo "Keep DRY_RUN=true. Do not open port 22 or 8501 to the internet."
echo

if desk_answers || port_busy; then
  echo "Something is already on 8501 — opening Chrome. Do not start a second tunnel."
  open_browser
  echo "Hard-refresh: Cmd+Shift+R"
  echo "If you see Unlock desk, that is the login, not a crash."
  hold
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

echo "Opening private IAP tunnel 8501 → VM localhost."
echo "Chrome opens as soon as 8501 answers (Unlock desk still counts)."
echo "This does not restart the bot and does not restart the desk."
echo

(
  up=0
  for _ in $(seq 1 20); do
    if desk_answers; then
      up=1
      break
    fi
    sleep 1
  done
  if [[ "$up" == "1" ]]; then
    echo "Station UP  $DESK_URL"
  else
    echo "Tunnel still connecting. Opening Chrome anyway — wait, then Cmd+Shift+R."
    echo "If the page stays blank, the station on the VM is down. In another Mac Terminal:"
    echo "  gcloud compute ssh ${VM_USER}@${VM_NAME} --project=${VM_PROJECT} --zone=${VM_ZONE} --tunnel-through-iap --command '$REMOTE_DESK'"
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
  echo "Tunnel exited."
  if desk_answers || port_busy; then
    echo "8501 is already in use on this Mac — a tunnel is probably running in another window."
    open_browser
  else
    echo "If the log said connect to host 8.231.125.120 port 22: Operation timed out,"
    echo "gcloud tried the public IP. Paste this and leave it running:"
    print_tunnel
    echo "Do not open port 22 or 8501 to the internet."
  fi
else
  echo "Tunnel closed."
fi
hold
exit "$tunnel_rc"
