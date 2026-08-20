#!/bin/bash
# Double-click in Finder — Gold Petal trading application (Mac).
# The real app runs on the VM. This opens a private tunnel + Chrome.
cd "$(dirname "$0")/.." || exit 1
exec ./scripts/open_desk_mac.sh
