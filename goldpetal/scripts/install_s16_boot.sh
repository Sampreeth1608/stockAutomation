#!/usr/bin/env bash
# Install @reboot so Monday 08:55 VM start brings S16 up before 09:00.
# Run on the VM once. Does not Arm live. Does not stop the VM.
set -euo pipefail
GP="${GP_DESK_DIR:-$HOME/goldpetal}"
BOOT="$GP/scripts/boot_s16_session.sh"
if [[ ! -x "$BOOT" ]]; then
  chmod +x "$BOOT" 2>/dev/null || true
fi
if [[ ! -f "$BOOT" ]]; then
  echo "Missing $BOOT — copy scripts from origin/cursor/s16-only-live-a4b2 first." >&2
  exit 1
fi
chmod +x "$BOOT" "$GP/supervise.sh" 2>/dev/null || true
LINE="@reboot /bin/bash $BOOT"
tmp="$(mktemp)"
crontab -l 2>/dev/null | grep -v 'boot_s16_session.sh' >"$tmp" || true
printf '%s\n' "$LINE" >>"$tmp"
crontab "$tmp"
rm -f "$tmp"
echo "crontab @reboot installed:"
crontab -l | grep boot_s16_session
echo "S16 flatten is still 23:30 IST. VM stop 23:55 is after that. Leftover (if any) squares at 09:00 after this boot."
