"""Apply a few operator commands from the Google Sheet COMMANDS tab.

Python still enforces risk. The sheet is not an order router.

Allowed (when request=YES):
  pause_all / kill_all → trading_enabled=false or emergency_off
  resume_trading / clear_emergency

Refused (always):
  micro_live, unlock_live, dry_run_false, approve_live, start_bot, stop_bot,
  approve_strategy, paper_mode (already DRY_RUN — no-op message)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from control_state import set_emergency, set_trading_enabled

YES = frozenset({"yes", "y", "1", "true", "on"})

COMMAND_SPECS: tuple[dict[str, str], ...] = (
    {
        "command": "pause_all",
        "allowed": "YES",
        "note": "trading_enabled=false. New entries stop. Does not kill the VM process.",
    },
    {
        "command": "resume_trading",
        "allowed": "YES",
        "note": "trading_enabled=true. Does not start the bot. Does not Unlock live.",
    },
    {
        "command": "emergency_off",
        "allowed": "YES",
        "note": "Same as desk Emergency. Blocks all entries.",
    },
    {
        "command": "kill_all",
        "allowed": "YES",
        "note": "Alias of emergency_off. Not a process kill. Not flatten-all-brokers.",
    },
    {
        "command": "clear_emergency",
        "allowed": "YES",
        "note": "Clears emergency_off. Trading master is separate (resume_trading).",
    },
    {
        "command": "paper_mode",
        "allowed": "NO",
        "note": "Already paper while DRY_RUN=true. Sheets cannot set DRY_RUN.",
    },
    {
        "command": "micro_live",
        "allowed": "NO",
        "note": "Refused. Type LIVE on desk 8501 after Unlock. Keep DRY_RUN=true.",
    },
    {
        "command": "unlock_live",
        "allowed": "NO",
        "note": "Refused. Unlock live stays on the desk.",
    },
    {
        "command": "dry_run_false",
        "allowed": "NO",
        "note": "Refused. Sheets will not write DRY_RUN=false.",
    },
    {
        "command": "approve_live",
        "allowed": "NO",
        "note": "Refused. Approve → live stays on the desk.",
    },
    {
        "command": "approve_strategy",
        "allowed": "NO",
        "note": "Refused. Lab/ML Approve stays on desk 8501.",
    },
    {
        "command": "start_bot",
        "allowed": "NO",
        "note": "Refused. Start/stop the process on the desk.",
    },
    {
        "command": "stop_bot",
        "allowed": "NO",
        "note": "Refused. Start/stop the process on the desk.",
    },
)


def _is_yes(raw: Any) -> bool:
    return str(raw or "").strip().lower() in YES


def _allowed_map() -> dict[str, str]:
    return {s["command"]: s["allowed"] for s in COMMAND_SPECS}


def apply_command_rows(
    rows: list[dict[str, Any]] | list[list[str]],
    *,
    state_path: Path | None = None,
) -> dict[str, str]:
    """Run allowed YES requests. Returns command → result text."""
    parsed: list[tuple[str, str]] = []
    if rows and isinstance(rows[0], dict):
        for r in rows:
            parsed.append((str(r.get("command") or "").strip(), str(r.get("request") or "")))
    else:
        # Spreadsheet values: header + data
        body = rows[1:] if rows and str(rows[0][0]).lower() == "command" else rows
        for r in body:
            if not r:
                continue
            cmd = str(r[0] if len(r) > 0 else "").strip()
            req = str(r[1] if len(r) > 1 else "")
            parsed.append((cmd, req))

    allowed = _allowed_map()
    results: dict[str, str] = {}
    for cmd, req in parsed:
        if not cmd:
            continue
        if cmd not in allowed:
            if _is_yes(req):
                results[cmd] = "refused: unknown command"
            continue
        if allowed[cmd] != "YES":
            if _is_yes(req):
                results[cmd] = f"refused: {cmd} is desk-only"
            continue
        if not _is_yes(req):
            continue
        if cmd == "pause_all":
            set_trading_enabled(False, note="sheets COMMANDS pause_all", path=state_path)
            results[cmd] = "applied: trading_enabled=false"
        elif cmd == "resume_trading":
            set_trading_enabled(True, note="sheets COMMANDS resume_trading", path=state_path)
            results[cmd] = "applied: trading_enabled=true"
        elif cmd in {"emergency_off", "kill_all"}:
            set_emergency(True, note=f"sheets COMMANDS {cmd}", path=state_path)
            results[cmd] = "applied: emergency_off=true"
        elif cmd == "clear_emergency":
            set_emergency(False, note="sheets COMMANDS clear_emergency", path=state_path)
            results[cmd] = "applied: emergency cleared"
        else:
            results[cmd] = "refused: not implemented"
    return results
