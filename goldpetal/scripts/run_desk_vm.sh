#!/usr/bin/env bash
# Lightweight HTML trading station on 8501 (not Streamlit).
#
#   ./scripts/run_desk_vm.sh --restart
#   ./scripts/run_desk_vm.sh --fg
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR/.."
# shellcheck source=print_open_on_mac.sh
source "$SCRIPT_DIR/print_open_on_mac.sh"
export GP_DESK_LOCAL=1
export GP_DATA_DIR="${GP_DATA_DIR:-$PWD/data}"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8501}"
if [[ "$HOST" == "0.0.0.0" || "$HOST" == "::" || "$HOST" == "*" || "$HOST" == "[::]" ]]; then
  bind_flag="$(echo "${DESK_BIND_PUBLIC:-}" | tr '[:upper:]' '[:lower:]')"
  if [[ "$bind_flag" != "true" && "$bind_flag" != "1" && "$bind_flag" != "yes" ]]; then
    echo "Refusing HOST=$HOST — desk stays on 127.0.0.1. Open it from the Mac IAP tunnel." >&2
    echo "DESK_BIND_PUBLIC=true overrides (do not)." >&2
    exit 2
  fi
fi
LOG="data/control_panel.log"
PY="./venv/bin/python"
if [[ ! -x "$PY" ]]; then
  PY="./.venv/bin/python"
fi
if [[ ! -x "$PY" ]]; then
  PY="python3"
fi
mkdir -p data/control

CMD=("$PY" control_panel.py --host "$HOST" --port "$PORT")
SYNC_BRANCH="${GP_SYNC_BRANCH:-origin/cursor/live-1lot-test-a4b2}"
REPO_DIR="${GP_REPO_DIR:-$HOME/goldpetal-repo}"

copy_from_origin() {
  local rel="$1"
  local dest="$2"
  git -C "$REPO_DIR" show "${SYNC_BRANCH}:${rel}" > "$dest"
}

ensure_live_orders_cap() {
  # Mixed git-show copies: new live_readiness.py vs old live_orders.py.
  if "$PY" -c 'from live_orders import HARD_LIVE_MAX_LOTS' >/dev/null 2>&1; then
    return 0
  fi
  echo "WARNING: live_orders.py missing HARD_LIVE_MAX_LOTS (mixed VM copy)."
  if [[ ! -d "$REPO_DIR/.git" ]]; then
    echo "Copy ${SYNC_BRANCH}:goldpetal/live_orders.py into $PWD then restart." >&2
    return 0
  fi
  echo "Copying live_orders.py + live_readiness.py from $SYNC_BRANCH"
  git -C "$REPO_DIR" fetch origin || true
  copy_from_origin goldpetal/live_orders.py "$PWD/live_orders.py"
  copy_from_origin goldpetal/live_readiness.py "$PWD/live_readiness.py" || true
}

preflight_import() {
  local err
  if err="$("$PY" -c 'import live_readiness' 2>&1)"; then
    return 0
  fi
  echo "$err" >&2
  echo "Python import failed — station will not boot. Copy matching files from $SYNC_BRANCH." >&2
  return 1
}

preflight_desk() {
  # Bind lock / other boot refuses. A missing DESK_PASSWORD must not block 8501.
  local err
  err="$("$PY" -c 'from desk_http_auth import desk_http_start_error; e=desk_http_start_error(); print(e or "")' 2>/dev/null || true)"
  if [[ -n "${err:-}" ]]; then
    echo "$err" >&2
    echo "Leaving the running station alone." >&2
    return 2
  fi
  return 0
}

stop_old() {
  pkill -f 'streamlit run analytics/app.py' 2>/dev/null || true
  pkill -f '[p]ython.*control_panel.py' 2>/dev/null || pkill -f 'control_panel.py' || true
  sleep 1
}

wait_up() {
  local i code
  for i in 1 2 3 4 5 6; do
    code="$(curl -sL -o /tmp/gp-desk-get.html -w '%{http_code}' --max-time 2 "http://127.0.0.1:${PORT}/" || true)"
    if [[ "${code:-}" == "200" ]] && grep -qE "Save strategies|Unlock desk|gp-desk-login" /tmp/gp-desk-get.html 2>/dev/null; then
      echo "station UP  pid=$(pgrep -f 'control_panel.py' | head -n1)  $PWD  http://127.0.0.1:${PORT}/"
      if grep -q "Unlock desk" /tmp/gp-desk-get.html 2>/dev/null; then
        echo "login gate on — type DESK_PASSWORD, then Cmd+Shift+R"
      fi
      hdr="$(grep -o 'gp-header-v[0-9]*' /tmp/gp-desk-get.html 2>/dev/null | head -n1 || true)"
      if [[ -n "${hdr:-}" ]]; then
        echo "header ${hdr} (clock must show · ${hdr#gp-header-} after Cmd+Shift+R)"
      elif grep -q "Save strategies" /tmp/gp-desk-get.html 2>/dev/null; then
        echo "WARNING: station.html has no gp-header-v* — copy station.html from origin/cursor/live-1lot-test-a4b2"
      fi
      return 0
    fi
    sleep 1
  done
  echo "FAILED — expected station HTML or Unlock desk login. HTTP ${code:-down}"
  tail -n 25 "$LOG" || true
  return 1
}

if [[ "${1:-}" == "--fg" ]]; then
  ensure_live_orders_cap
  preflight_import
  preflight_desk
  stop_old
  echo "→ station foreground :${PORT}"
  print_open_on_mac
  exec "${CMD[@]}"
fi

start_detached() {
  local session="${TMUX_DESK_SESSION:-gp-desk}"
  ensure_live_orders_cap
  if ! preflight_import; then
    return 1
  fi
  if ! preflight_desk; then
    wait_up || true
    return 2
  fi
  tmux kill-session -t "=$session" 2>/dev/null || true
  stop_old
  {
    echo
    echo "===== START $(date '+%F %T')  cwd=$PWD  bind=${HOST}:${PORT} ====="
  } >> "$LOG"
  tmux new-session -d -s "$session" -c "$PWD" \
    "export GP_DESK_LOCAL=1 GP_DATA_DIR='$GP_DATA_DIR'; ${CMD[*]} >> '$LOG' 2>&1; echo DESK_EXITED; sleep 5"
  echo "started tmux $session from $PWD"
  wait_up
  if [[ "${GP_QUIET_OPEN:-}" != "1" ]]; then
    print_open_on_mac
  fi
}

if [[ "${1:-}" == "--detach" || "${1:-}" == "--restart" || "${1:-}" == "" ]]; then
  if [[ "${1:-}" == "" ]] && command -v tmux >/dev/null 2>&1; then
    if tmux has-session -t "=${TMUX_DESK_SESSION:-gp-desk}" 2>/dev/null && [[ -t 0 ]]; then
      echo "→ tmux session already running — attaching"
      exec tmux attach -t "${TMUX_DESK_SESSION:-gp-desk}"
    fi
  fi
  start_detached
  exit 0
fi

echo "usage: $0 [--restart|--fg]"
exit 1
