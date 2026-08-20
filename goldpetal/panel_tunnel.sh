#!/usr/bin/env bash
# Open the desk from your Mac. Do not run this on the VM.
#
#   ./panel_tunnel.sh                 # IAP tunnel to VM localhost:8501
# Direct ssh to the VM public IP is not used (port 22 is closed).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

if curl -s -m 1 -H "Metadata-Flavor: Google" \
    http://metadata.google.internal/computeMetadata/v1/instance/name >/dev/null 2>&1; then
  echo "You are ON the VM. This tunnel runs on your Mac."
  echo "Open a new Terminal.app window and run it there."
  # shellcheck source=scripts/print_open_on_mac.sh
  source "$HERE/scripts/print_open_on_mac.sh"
  print_open_on_mac
  exit 1
fi

HOST="${1:-}"
if [[ -n "$HOST" ]]; then
  echo "Direct ssh to ${HOST}:22 times out if port 22 is closed (this VM)."
  echo "Ignoring that IP. Using IAP instead. Do not open port 22 to the internet."
fi

echo "Leave this Mac window running, then Chrome: http://127.0.0.1:8501/ → Desk"
exec gcloud compute ssh sampreeth1608@sampreeth-love-story \
  --project=sampreethlovestory \
  --zone=asia-south1-c \
  --tunnel-through-iap \
  -- -N -L 8501:127.0.0.1:8501 -o ExitOnForwardFailure=yes -o ServerAliveInterval=30
