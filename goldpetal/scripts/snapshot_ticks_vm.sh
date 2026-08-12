#!/usr/bin/env bash
# One-shot: make a consistent ticks snapshot on the VM (fixes malformed scp copies).
# Run on Mac:
#   ./scripts/snapshot_ticks_vm.sh
set -euo pipefail
VM="${VM:-sampreeth-love-story}"
ZONE="${ZONE:-asia-south1-c}"
REMOTE_USER="${REMOTE_USER:-sampreeth1608}"
REMOTE="${REMOTE_USER}@${VM}"

gcloud compute ssh "$REMOTE" --zone="$ZONE" --command '
set -e
cd /home/sampreeth1608/goldpetal
echo "=== live DB ==="
ls -lah data/ticks.db data/ticks.db-wal data/ticks.db-shm 2>/dev/null || ls -lah data/ticks.db
echo "=== integrity (may fail if hot-corrupt) ==="
sqlite3 data/ticks.db "PRAGMA integrity_check;" 2>&1 | head -5 || true
echo "=== backup ==="
rm -f data/ticks_snapshot.db
if command -v sqlite3 >/dev/null 2>&1; then
  sqlite3 data/ticks.db ".timeout 30000" ".backup data/ticks_snapshot.db"
else
  ./venv/bin/python -c "
import sqlite3
from pathlib import Path
src, dst = Path(\"data/ticks.db\"), Path(\"data/ticks_snapshot.db\")
if dst.exists(): dst.unlink()
a, b = sqlite3.connect(src), sqlite3.connect(dst)
with b: a.backup(b)
b.close(); a.close()
"
fi
echo "=== snapshot check ==="
sqlite3 data/ticks_snapshot.db "PRAGMA integrity_check;"
sqlite3 data/ticks_snapshot.db "SELECT COUNT(*), MIN(received_at), MAX(received_at) FROM ticks;"
ls -lah data/ticks_snapshot.db
'
