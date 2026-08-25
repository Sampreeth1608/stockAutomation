#!/usr/bin/env bash
# Print gcloud steps to lower S16 VM RAM (and what you can do about disk).
# Run on the Mac (gcloud), not on the VM. Does not Arm live.
# Default: print only. --apply-ram stops the VM, sets e2-small, starts it.
# Never --apply-ram while Gold Petal is in session (09:00–23:30 IST).
set -euo pipefail
PROJECT="${GP_VM_PROJECT:-sampreethlovestory}"
ZONE="${GP_VM_ZONE:-asia-south1-c}"
VM="${GP_VM_NAME:-sampreeth-love-story}"
# 2 GB. Do not use e2-micro (1 GB) — bot + desk + Linux will OOM.
MACHINE="${GP_MACHINE_TYPE:-e2-small}"

echo "Do this AFTER flatten (23:30) and VM stop (23:55), or on Saturday/Sunday."
echo "Do NOT stop the VM while S16 is live in session."
echo
echo "Target RAM: $MACHINE = 2 GB. 4 GB (e2-medium) only if the desk stays on this VM."
echo "Stopped VM: no vCPU/RAM charge. Boot disk still bills 24/7."
echo
echo "=== 0) See what you have now ==="
echo "gcloud compute instances describe $VM --project=$PROJECT --zone=$ZONE --format='yaml(name,status,machineType,disks)'"
echo "gcloud compute disks list --project=$PROJECT --filter=\"zone:($ZONE)\" --format='table(name,sizeGb,type,users,status)'"
echo
echo "=== 1) Lower RAM (easy). VM must be STOPPED. ==="
echo "gcloud compute instances stop $VM --project=$PROJECT --zone=$ZONE"
echo "gcloud compute instances set-machine-type $VM --project=$PROJECT --zone=$ZONE --machine-type=$MACHINE"
echo "gcloud compute instances start $VM --project=$PROJECT --zone=$ZONE"
echo
echo "Console: Compute Engine → VM instances → $VM → STOP → EDIT →"
echo "Machine configuration → E2 → e2-small (2 vCPU, 2 GB) → Save → START."
echo
echo "=== 2) Disk bill (hard). GCP cannot shrink a boot disk in place. ==="
echo "Making room inside Ubuntu (rm ticks.db, apt clean) does NOT lower the GCP bill."
echo "Billing is the provisioned size (30 GB stays 30 GB even if df shows 8 GB used)."
echo
echo "You MAY delete a disk only if Users/In use by is EMPTY (not attached to $VM)."
echo "Do not delete the boot disk attached to $VM. Monday will not boot."
echo
echo "# extra unused disk only — replace DISK_NAME after you list disks:"
echo "# gcloud compute disks delete DISK_NAME --project=$PROJECT --zone=$ZONE"
echo
echo "Old snapshots also bill:"
echo "gcloud compute snapshots list --project=$PROJECT --format='table(name,diskSizeGb,storageBytes,status,creationTimestamp)'"
echo
echo "To actually use a smaller boot disk you must clone onto a NEW smaller disk"
echo "(Sunday job). Snapshot restore cannot be smaller than the original disk."
echo "If the boot disk is already 20–30 GB, leave it. Shrinking 10 GB is not worth it."
echo
echo "=== Do not ==="
echo "Do not use e2-micro / 1 GB."
echo "Do not delete the boot disk."
echo "Do not open 22 or 8501 to the internet."
echo "Do not Arm live from here."

if [[ "${1:-}" != "--apply-ram" ]]; then
  echo
  echo "Printed only. After hours, re-run with --apply-ram to stop → e2-small → start."
  exit 0
fi

echo
echo "Applying RAM: stop $VM → $MACHINE → start …"
gcloud compute instances stop "$VM" --project="$PROJECT" --zone="$ZONE"
gcloud compute instances set-machine-type "$VM" \
  --project="$PROJECT" --zone="$ZONE" --machine-type="$MACHINE"
gcloud compute instances start "$VM" --project="$PROJECT" --zone="$ZONE"
echo "Machine type is $MACHINE. Confirm @reboot brought S16 up (supervise + desk)."
echo "Disk size is unchanged. GCP cannot shrink the boot disk in place."
