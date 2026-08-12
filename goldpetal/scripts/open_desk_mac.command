#!/bin/bash
# Double-click this in Finder to open the Gold Petal desk (sync + Streamlit).
cd "$(dirname "$0")/.." || exit 1
exec ./scripts/open_desk_mac.sh
