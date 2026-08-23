#!/usr/bin/env bash
# Print (or --apply) a Mon–Fri GCP instance schedule for S16 session hours.
# Default: start 08:55 IST, stop 23:55 IST. Weekend stays off.
# Run on the Mac (gcloud), not on the VM. Does not Arm live.
# --apply actually attaches the schedule. Without it, print only.
set -euo pipefail
PROJECT="${GP_VM_PROJECT:-sampreethlovestory}"
ZONE="${GP_VM_ZONE:-asia-south1-c}"
REGION="${GP_VM_REGION:-asia-south1}"
VM="${GP_VM_NAME:-sampreeth-love-story}"
POLICY="${GP_SCHEDULE_NAME:-s16-session-hours}"
START="${GP_START_CRON:-55 8 * * 1-5}"
STOP="${GP_STOP_CRON:-55 23 * * 1-5}"
TZ_NAME="${GP_SCHEDULE_TZ:-Asia/Kolkata}"

echo "S16 is intraday: flatten at 23:30 IST, leftover at next 09:00 if a close is missed."
echo "Stop 23:55 is 25 minutes after flatten. Start 08:55 is 5 minutes before open."
echo "Prefer start 08:45 if Angel login / TOTP is slow: GP_START_CRON='45 8 * * 1-5'"
echo
echo "RAM while the VM is ON (session): 2 GB minimum (e2-small). 4 GB (e2-medium) if the desk stays on this VM. Do not use 1 GB."
echo "Stopped VM: no vCPU/RAM charge. Boot disk still bills. Do not delete the boot disk."
echo
echo "=== 1) See machine + disks (do not delete the boot disk) ==="
echo "gcloud compute instances describe $VM --project=$PROJECT --zone=$ZONE --format='yaml(machineType,disks,status,resourcePolicies)'"
echo "gcloud compute disks list --project=$PROJECT --filter=\"zone:($ZONE)\" --format='table(name,sizeGb,type,users,status)'"
echo
echo "=== 2) Create Mon–Fri schedule (once) ==="
echo "gcloud compute resource-policies create-instance-schedule $POLICY \\"
echo "  --project=$PROJECT --region=$REGION --timezone=$TZ_NAME \\"
echo "  --description='S16 session: 08:55–23:55 IST Mon–Fri' \\"
echo "  --vm-start-schedule='$START' \\"
echo "  --vm-stop-schedule='$STOP'"
echo
echo "=== 3) Attach to the VM ==="
echo "gcloud compute instances add-resource-policies $VM \\"
echo "  --project=$PROJECT --zone=$ZONE --resource-policies=$POLICY"
echo
echo "=== 4) Optional: stop now (Sunday / after 23:55) ==="
echo "gcloud compute instances stop $VM --project=$PROJECT --zone=$ZONE"
echo
echo "=== Do not ==="
echo "Do not delete the boot disk attached to $VM. The VM cannot start Monday without it."
echo "Do not open 22 or 8501 to the internet. Desk stays 127.0.0.1 + IAP tunnel."
echo "Do not Arm live from here."

if [[ "${1:-}" != "--apply" ]]; then
  echo
  echo "Printed only. Re-run with --apply from the Mac when you want gcloud to create+attach."
  exit 0
fi

echo
echo "Applying schedule $POLICY → $VM …"
gcloud compute resource-policies create-instance-schedule "$POLICY" \
  --project="$PROJECT" --region="$REGION" --timezone="$TZ_NAME" \
  --description="S16 session: 08:55–23:55 IST Mon–Fri" \
  --vm-start-schedule="$START" \
  --vm-stop-schedule="$STOP" || echo "(policy may already exist — continuing)"
gcloud compute instances add-resource-policies "$VM" \
  --project="$PROJECT" --zone="$ZONE" --resource-policies="$POLICY"
echo "Schedule attached. Install VM @reboot first (install_s16_boot.sh) or Monday 08:55 comes up with no bot."
