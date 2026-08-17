#!/usr/bin/env bash
# Open the desk from your Mac. Do not run this on the VM.
#
#   ./panel_tunnel.sh                 # 8501 (Desk tab)
#   ./panel_tunnel.sh 8.231.125.120   # if gcloud is not on the Mac
set -euo pipefail

if curl -s -m 1 -H "Metadata-Flavor: Google" \
    http://metadata.google.internal/computeMetadata/v1/instance/name >/dev/null 2>&1; then
  echo "You are ON the VM. This tunnel runs on your Mac."
  echo "Open a new Terminal.app window and run it there."
  # shellcheck source=scripts/print_open_on_mac.sh
  source "$(dirname "$0")/scripts/print_open_on_mac.sh"
  print_open_on_mac
  exit 1
fi

HOST="${1:-}"
if [[ -z "$HOST" ]]; then
  echo "Leave this Mac window running, then Chrome: http://127.0.0.1:8501/ → Desk"
  exec gcloud compute ssh sampreeth1608@sampreeth-love-story --zone=asia-south1-c -- -N -L 8501:127.0.0.1:8501
fi

echo "Tunneling Mac:8501 → ${HOST}:8501 (Ctrl+C to stop)"
echo "Chrome: http://127.0.0.1:8501/  → tab Desk"
exec ssh -N -L 8501:127.0.0.1:8501 "sampreeth1608@${HOST}"
