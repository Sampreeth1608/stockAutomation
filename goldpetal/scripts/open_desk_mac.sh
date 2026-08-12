#!/usr/bin/env bash
# One-click Gold Petal Mac desk: light sync → Streamlit (fast).
#
# Daily (seconds — skips huge ticks.db):
#   ./scripts/open_desk_mac.sh
#   # or Finder: open_desk_mac.command
#
# Full sync including ticks.db (~10 min when DB is large):
#   ./scripts/open_desk_mac.sh --full
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
FULL=0
for arg in "$@"; do
  case "$arg" in
    --full) FULL=1 ;;
    -h|--help)
      sed -n '1,12p' "$0"
      exit 0
      ;;
  esac
done

if [[ ! -d .venv-analytics ]]; then
  echo "Missing .venv-analytics — create once:"
  echo "  python3 -m venv .venv-analytics && source .venv-analytics/bin/activate"
  echo "  pip install -r requirements.txt -r requirements-analytics.txt"
  exit 1
fi

# shellcheck disable=SC1091
source .venv-analytics/bin/activate

chmod +x scripts/sync_analytics_mac.sh 2>/dev/null || true

if [[ "$FULL" -eq 1 ]]; then
  echo "→ FULL sync (includes ticks.db — may take several minutes)…"
  ./scripts/sync_analytics_mac.sh
else
  echo "→ light sync (proposals/control only — fast)"
  echo "   Tip: already have ticks? keep using light. Need fresh DB: ./scripts/open_desk_mac.sh --full"
  ./scripts/sync_analytics_mac.sh --skip-db
fi

echo "→ Streamlit  http://localhost:8501   (Ctrl+C to stop)"
exec streamlit run analytics/app.py --server.headless true
