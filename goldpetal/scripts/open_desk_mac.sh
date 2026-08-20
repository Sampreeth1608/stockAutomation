#!/usr/bin/env bash
# Terminal entry: same standalone Mac app as GoldPetal.command (Desktop-safe).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
exec "$HERE/GoldPetal.command"
