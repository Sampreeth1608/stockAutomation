#!/usr/bin/env bash
# One-click Gold Petal Mac desk: sync from VM → Streamlit.
#
# Terminal:
#   ./scripts/open_desk_mac.sh
#
# Finder (double-click):
#   open scripts/open_desk_mac.command
#   (first time: chmod +x scripts/open_desk_mac.command)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -d .venv-analytics ]]; then
  echo "Missing .venv-analytics — create once:"
  echo "  python3 -m venv .venv-analytics"
  echo "  source .venv-analytics/bin/activate"
  echo "  pip install -r requirements.txt -r requirements-analytics.txt"
  exit 1
fi

# shellcheck disable=SC1091
source .venv-analytics/bin/activate

echo "→ git pull (current branch)"
git pull --ff-only || true

echo "→ sync from VM"
chmod +x scripts/sync_analytics_mac.sh 2>/dev/null || true
./scripts/sync_analytics_mac.sh

echo "→ Streamlit desk  http://localhost:8501"
echo "   Ctrl+C to stop"
exec streamlit run analytics/app.py --server.headless true
