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
  echo "→ ticks.db (may take a minute)…"
  pull "data/ticks.db" "$OUT/ticks.db"
else
  echo "→ skipped ticks.db"
fi

echo
echo "Synced → $OUT"
echo "  streamlit run analytics/app.py"
