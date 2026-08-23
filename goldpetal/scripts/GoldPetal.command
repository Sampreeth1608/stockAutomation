#!/bin/bash
# Gold Petal — standalone Mac app. Put this file on the Desktop and double-click.
# The real station stays on the VM. This opens a private IAP SSH tunnel + Chrome.
# If 8501 on the VM is down, it starts the station first (not the trading bot).
# Run on the Mac. Never on the VM. Keep DRY_RUN=true.
# GoldPetal.command v64
#
# Port 22 on the VM public IP is closed on purpose. Direct ssh to 8.231.125.120
# times out. Always use --tunnel-through-iap. Do not open 22 or 8501 to the internet.
set -euo pipefail

VM_USER="${GP_VM_USER:-sampreeth1608}"
VM_NAME="${GP_VM_NAME:-sampreeth-love-story}"
VM_ZONE="${GP_VM_ZONE:-asia-south1-c}"
VM_PROJECT="${GP_VM_PROJECT:-sampreethlovestory}"
DESK_URL="${GP_DESK_URL:-http://127.0.0.1:8501/?v=64}"
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

save_strategy_pdf() {
  # Chrome Downloads folder on this Mac. Desk must already answer on 8501.
  local dest="${HOME}/Downloads/goldpetal_all_strategies.pdf"
  local tmp="${dest}.part"
  mkdir -p "${HOME}/Downloads"
  if curl -fsL --max-time 20 "http://127.0.0.1:8501/api/docs/strategies.pdf" -o "$tmp"; then
    if [[ "$(head -c 4 "$tmp" 2>/dev/null)" == "%PDF" ]]; then
      mv "$tmp" "$dest"
      echo "Saved strategy PDF → $dest"
      return 0
    fi
  fi
  rm -f "$tmp"
  echo "Strategy PDF not copied yet. After Unlock desk: Downloads tab → Download strategy PDF."
}

print_tunnel() {
  echo "  gcloud compute ssh ${VM_USER}@${VM_NAME} --project=${VM_PROJECT} --zone=${VM_ZONE} --tunnel-through-iap -- -N -L 8501:127.0.0.1:8501"
}

print_start_desk() {
  echo "  gcloud compute ssh ${VM_USER}@${VM_NAME} --project=${VM_PROJECT} --zone=${VM_ZONE} --tunnel-through-iap --command '$REMOTE_DESK'"
}

hold() {
  echo
  echo "Leave this window open while you use the desk. Press Enter to close."
  read -r _ || true
}

start_station_on_vm() {
  echo "Starting the station on the VM (8501 only — not the trading bot)…"
  echo "Connection refused on the tunnel means 8501 is down on the VM. This starts it."
  set +e
  gcloud compute ssh "${VM_USER}@${VM_NAME}" \
    --project="${VM_PROJECT}" \
    --zone="${VM_ZONE}" \
    --tunnel-through-iap \
    --command "$REMOTE_DESK"
  local rc=$?
  set -e
  if [[ "$rc" -ne 0 ]]; then
    echo
    echo "Desk restart on the VM returned $rc."
    echo "Leave this window open and paste the start command in another Mac Terminal:"
    print_start_desk
  else
    echo "Station start finished on the VM."
  fi
  return 0
}

wait_and_open() {
  local up=0
  local i
  for i in $(seq 1 40); do
    if desk_answers; then
      up=1
      break
    fi
    sleep 1
  done
  if [[ "$up" == "1" ]]; then
    echo "Station UP  $DESK_URL"
    save_strategy_pdf
  else
    echo "8501 still not answering. Chrome may stay blank until the station is up."
    echo "Leave the tunnel window open. In another Mac Terminal:"
    print_start_desk
    echo "Then Chrome: $DESK_URL"
  fi
  open_browser
  echo "Hard-refresh: Cmd+Shift+R"
  echo "Unlock desk is the login page, not a crash."
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

echo "======== Gold Petal v64 ========"
echo "This window is the private IAP tunnel. Leave it open. Closing it drops the desk."
echo "Keep DRY_RUN=true. Do not open port 22 or 8501 to the internet."
echo

if ! command -v gcloud >/dev/null 2>&1; then
  echo "gcloud not found on this Mac. Install Google Cloud SDK, then double-click again."
  echo "Or paste this in Mac Terminal and leave it running:"
  print_tunnel
  echo "Then Chrome: $DESK_URL"
  hold
  exit 1
fi

if desk_answers; then
  echo "Station already on $DESK_URL"
  save_strategy_pdf
  open_browser
  echo "Hard-refresh Chrome: Cmd+Shift+R"
  echo "If you see Unlock desk, that is the login, not a crash."
  hold
  exit 0
fi

if port_busy; then
  echo "A tunnel is already using 8501 on this Mac. Not starting a second one."
  echo "channel … Connection refused means the station is down on the VM."
  start_station_on_vm
  wait_and_open
  hold
  exit 0
fi

start_station_on_vm

echo
echo "Opening private IAP tunnel 8501 → VM localhost."
echo "Leave this window open."
echo

wait_and_open &

set +e
"${TUNNEL_CMD[@]}"
tunnel_rc=$?
set -e

echo
if [[ "$tunnel_rc" -ne 0 ]]; then
  echo "Tunnel exited."
  if desk_answers; then
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
