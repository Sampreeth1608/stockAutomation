#!/usr/bin/env bash
# Sync Gold Petal control + analytics snapshots from the GCP VM → Mac.
# Run on your Mac only.
#
#   ./scripts/sync_analytics_mac.sh
#   ./scripts/sync_analytics_mac.sh --skip-db
#   GP_SYNC_DEBUG=1 ./scripts/sync_analytics_mac.sh --skip-db
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${ANALYTICS_DIR:-$ROOT/data/analytics_mac}"
VM="${VM:-sampreeth-love-story}"
ZONE="${ZONE:-asia-south1-c}"
# Must match the Linux account that owns ~/goldpetal on the VM.
# Your Mac login is often "sampreeth"; without this, gcloud SSHes as that
# user and gets Permission denied on /home/sampreeth1608/...
REMOTE_USER="${REMOTE_USER:-sampreeth1608}"
# Absolute path — gcloud scp does not expand ~/
REMOTE_DIR="${REMOTE_DIR:-/home/${REMOTE_USER}/goldpetal}"
# gcloud target: user@instance
REMOTE="${REMOTE_USER}@${VM}"
SKIP_DB=0

for arg in "$@"; do
  case "$arg" in
    --skip-db) SKIP_DB=1 ;;
    -h|--help) sed -n '1,20p' "$0"; exit 0 ;;
  esac
done

mkdir -p "$OUT" "$OUT/control" "$OUT/discover" "$OUT/logs" "$OUT/models"

echo "→ sync $REMOTE ($ZONE) $REMOTE_DIR → $OUT"

pull() {
  local remote="$1"
  local local="$2"
  mkdir -p "$(dirname "$local")"
  if gcloud compute scp --zone="$ZONE" \
      "${REMOTE}:${REMOTE_DIR}/${remote}" "$local" 2>/tmp/gp_scp_err.txt; then
    echo "  ✓ $remote"
  else
    echo "  · missing $remote"
    if [[ "${GP_SYNC_DEBUG:-0}" == "1" ]]; then
      head -3 /tmp/gp_scp_err.txt | sed 's/^/    /'
    fi
  fi
}

pull "data/control/proposals.json" "$OUT/control/proposals.json"
pull "data/control/state.json" "$OUT/control/state.json"
pull "data/control/capital.json" "$OUT/control/capital.json"
pull "data/control/reasoning_latest.json" "$OUT/control/reasoning_latest.json"
pull "data/control/live_orders.jsonl" "$OUT/control/live_orders.jsonl"
pull "data/discover/behavior_report.json" "$OUT/discover/behavior_report.json"
pull "data/discover/latest_report.json" "$OUT/discover/latest_report.json"
pull "data/strategy_run.log" "$OUT/logs/strategy_run.log"
pull "data/models/report.json" "$OUT/models/report.json"
pull "data/models/overnight_report.json" "$OUT/models/overnight_report.json"

# env flags only (no secrets)
TMP_ENV="$(mktemp)"
if gcloud compute scp --zone="$ZONE" \
    "${REMOTE}:${REMOTE_DIR}/.env" "$TMP_ENV" 2>/dev/null; then
  grep -E '^(DRY_RUN|IGNORE_FEES|COVER_FEES|ENABLE_|S4_|S5_|S6_|S8_|S9_|S10_|S11_|ML_|MODE)' \
    "$TMP_ENV" > "$OUT/env.flags" 2>/dev/null || true
  echo "  ✓ env.flags"
fi
rm -f "$TMP_ENV"

if [[ "$SKIP_DB" -eq 0 ]]; then
  echo "→ ticks.db consistent snapshot on VM (hot-copy safe)…"
  # Live scp of ticks.db while the bot writes → "database disk image is malformed".
  # sqlite3 .backup makes a consistent copy even with concurrent writers.
  SNAP_REMOTE="data/ticks_snapshot.db"
  SNAP_LOCAL="$OUT/ticks.db"
  rm -rf "$SNAP_LOCAL" "$OUT/ticks.db-wal" "$OUT/ticks.db-shm"
  BACKUP_CMD=$(cat <<'EOS'
set -e
cd /home/sampreeth1608/goldpetal
rm -f data/ticks_snapshot.db
if command -v sqlite3 >/dev/null 2>&1; then
  sqlite3 data/ticks.db ".timeout 10000" ".backup data/ticks_snapshot.db"
  sqlite3 data/ticks_snapshot.db 'PRAGMA integrity_check;' | head -1
  sqlite3 data/ticks_snapshot.db 'SELECT COUNT(*) FROM ticks;'
else
  ./venv/bin/python - <<'PY'
import sqlite3
from pathlib import Path
src = Path("data/ticks.db")
dst = Path("data/ticks_snapshot.db")
if dst.exists():
    dst.unlink()
con = sqlite3.connect(src)
bck = sqlite3.connect(dst)
with bck:
    con.backup(bck)
bck.close()
con.close()
chk = sqlite3.connect(dst)
print(chk.execute("PRAGMA integrity_check").fetchone()[0])
print(chk.execute("SELECT COUNT(*) FROM ticks").fetchone()[0])
chk.close()
PY
fi
EOS
)
  if gcloud compute ssh "$REMOTE" --zone="$ZONE" --command "$BACKUP_CMD" \
      2>/tmp/gp_backup_err.txt | tee /tmp/gp_backup_out.txt; then
    echo "  ✓ VM snapshot ready"
  else
    echo "  ✗ VM snapshot failed:"
    head -20 /tmp/gp_backup_err.txt | sed 's/^/    /'
    echo "    On VM try: sqlite3 data/ticks.db 'PRAGMA integrity_check;'"
    exit 1
  fi
  pull "$SNAP_REMOTE" "$SNAP_LOCAL"
  if [[ -f "$SNAP_LOCAL" ]]; then
    echo -n "  · local tick count: "
    if command -v sqlite3 >/dev/null 2>&1; then
      sqlite3 "$SNAP_LOCAL" 'SELECT COUNT(*) FROM ticks;' 2>/dev/null \
        || echo "(malformed — see backtest diagnostics)"
    else
      python3 -c "import sqlite3; c=sqlite3.connect('$SNAP_LOCAL'); print(c.execute('select count(*) from ticks').fetchone()[0])" \
        2>/dev/null || echo "(could not query)"
    fi
  else
    echo "  ✗ ticks snapshot missing after scp"
  fi
else
  echo "→ skipped ticks.db"
fi

echo
echo "Synced → $OUT"
echo "  streamlit run analytics/app.py"
