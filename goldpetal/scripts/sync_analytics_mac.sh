#!/usr/bin/env bash
# Sync Gold Petal analytics snapshots from the GCP VM to this Mac.
# Run on your Mac (not on the VM).
#
# Usage:
#   ./scripts/sync_analytics_mac.sh
#   ./scripts/sync_analytics_mac.sh --skip-db          # light: no ticks.db
#   VM=sampreeth-love-story ZONE=asia-south1-c ./scripts/sync_analytics_mac.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${ANALYTICS_DIR:-$ROOT/data/analytics_mac}"
VM="${VM:-sampreeth-love-story}"
ZONE="${ZONE:-asia-south1-c}"
REMOTE_DIR="${REMOTE_DIR:-~/goldpetal}"
SKIP_DB=0

for arg in "$@"; do
  case "$arg" in
    --skip-db) SKIP_DB=1 ;;
    -h|--help)
      sed -n '1,20p' "$0"
      exit 0
      ;;
  esac
done

mkdir -p "$OUT" "$OUT/control" "$OUT/discover" "$OUT/logs"

echo "→ syncing from $VM ($ZONE) : $REMOTE_DIR → $OUT"

# Small / high-value files first
gcloud compute scp \
  --zone="$ZONE" \
  "$VM:$REMOTE_DIR/data/control/proposals.json" \
  "$OUT/control/proposals.json" 2>/dev/null || echo "(no proposals.json yet)"

gcloud compute scp \
  --zone="$ZONE" \
  "$VM:$REMOTE_DIR/data/discover/behavior_report.json" \
  "$OUT/discover/behavior_report.json" 2>/dev/null || echo "(no behavior_report.json yet)"

gcloud compute scp \
  --zone="$ZONE" \
  "$VM:$REMOTE_DIR/data/discover/latest_report.json" \
  "$OUT/discover/latest_report.json" 2>/dev/null || echo "(no latest_report.json yet)"

gcloud compute scp \
  --zone="$ZONE" \
  "$VM:$REMOTE_DIR/data/strategy_run.log" \
  "$OUT/logs/strategy_run.log" 2>/dev/null || echo "(no strategy_run.log yet)"

gcloud compute scp \
  --zone="$ZONE" \
  "$VM:$REMOTE_DIR/.env" \
  "$OUT/env.snapshot" 2>/dev/null || true

# Strip secrets from env snapshot (keep flags only)
if [[ -f "$OUT/env.snapshot" ]]; then
  grep -E '^(DRY_RUN|IGNORE_FEES|COVER_FEES|ENABLE_|S4_|S5_|S8_|S11_|MODE)' \
    "$OUT/env.snapshot" > "$OUT/env.flags" 2>/dev/null || true
  rm -f "$OUT/env.snapshot"
fi

if [[ "$SKIP_DB" -eq 0 ]]; then
  echo "→ copying ticks.db (can take a minute)…"
  gcloud compute scp \
    --zone="$ZONE" \
    "$VM:$REMOTE_DIR/data/ticks.db" \
    "$OUT/ticks.db"
else
  echo "→ skipped ticks.db (--skip-db)"
fi

echo
echo "Synced → $OUT"
echo "Open analytics:"
echo "  cd $ROOT && source ../venv/bin/activate 2>/dev/null || true"
echo "  pip install -r requirements-analytics.txt"
echo "  streamlit run analytics/app.py -- --data-dir $OUT"
