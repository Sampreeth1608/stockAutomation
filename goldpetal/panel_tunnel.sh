#!/usr/bin/env bash
# Prefer this over opening TCP 8787 on the public internet.
#
# On your LAPTOP (not the VM), run:
#   ssh -N -L 8787:127.0.0.1:8787 sampreeth1608@<VM_EXTERNAL_IP>
# Then open: http://127.0.0.1:8787/
#
# On the VM, bind the panel to localhost only:
#   python3 control_panel.py --host 127.0.0.1 --port 8787
#
# You can delete/disable the GCP firewall rule for 8787 after switching.

set -euo pipefail
HOST="${1:-}"
if [[ -z "$HOST" ]]; then
  echo "Usage: $0 <vm-external-ip>"
  echo "Example: $0 34.93.12.45"
  echo "Then open http://127.0.0.1:8787/ in your browser."
  exit 1
fi
echo "Tunneling localhost:8787 → ${HOST}:8787 (Ctrl+C to stop)"
echo "Open http://127.0.0.1:8787/ in your browser."
exec ssh -N -L 8787:127.0.0.1:8787 "sampreeth1608@${HOST}"
