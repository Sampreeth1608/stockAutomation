#!/usr/bin/env bash
# Copy a matching desk-runtime set from origin into ~/goldpetal.
# Mixed git-show copies (new live_readiness.py + old live_orders.py) kill 8501.
#
#   ./scripts/sync_desk_runtime.sh
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [[ -f "$SCRIPT_DIR/../control_panel.py" ]]; then
  DEST="$(cd "$SCRIPT_DIR/.." && pwd)"
else
  DEST="${GP_DESK_DIR:-$HOME/goldpetal}"
fi
REPO="${GP_REPO_DIR:-$HOME/goldpetal-repo}"
BRANCH="${GP_SYNC_BRANCH:-origin/cursor/live-1lot-test-a4b2}"

FILES=(
  goldpetal/live_orders.py
  goldpetal/live_readiness.py
  goldpetal/desk_http_auth.py
  goldpetal/analytics/desk_auth.py
  goldpetal/control_panel.py
  goldpetal/control_state.py
  goldpetal/desk_data.py
  goldpetal/capital.py
  goldpetal/market_mood.py
  goldpetal/portfolio.py
  goldpetal/run_strategy.py
  goldpetal/trade_analysis.py
  goldpetal/station.html
  goldpetal/lite.html
  goldpetal/login.html
  goldpetal/scripts/run_desk_vm.sh
  goldpetal/scripts/print_open_on_mac.sh
  goldpetal/scripts/sync_desk_runtime.sh
)

if [[ ! -d "$REPO/.git" ]]; then
  echo "No git repo at $REPO — cannot sync desk runtime." >&2
  exit 1
fi

mkdir -p "$DEST/analytics" "$DEST/scripts"
echo "sync desk runtime  $BRANCH  →  $DEST"
git -C "$REPO" fetch origin
for rel in "${FILES[@]}"; do
  dest_rel="${rel#goldpetal/}"
  git -C "$REPO" show "${BRANCH}:${rel}" > "$DEST/$dest_rel"
  echo "  copied $dest_rel"
done
chmod +x "$DEST/scripts/run_desk_vm.sh" "$DEST/scripts/sync_desk_runtime.sh" || true
echo "desk runtime synced"
