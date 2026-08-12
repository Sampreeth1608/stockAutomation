"""Bot process ops for the Streamlit desk (status / restart supervise)."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def _pgrep(pattern: str) -> list[dict[str, Any]]:
    try:
        proc = subprocess.run(
            ["pgrep", "-af", pattern],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception as exc:
        return [{"error": str(exc)}]
    rows: list[dict[str, Any]] = []
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) < 2:
            continue
        pid_s, cmd = parts[0], parts[1]
        # skip the pgrep itself / grep noise
        if "pgrep" in cmd:
            continue
        try:
            pid = int(pid_s)
        except ValueError:
            continue
        rows.append({"pid": pid, "cmd": cmd})
    return rows


def bot_status() -> dict[str, Any]:
    supervise = [r for r in _pgrep("supervise.sh") if "supervise.sh" in r.get("cmd", "")]
    runner = [r for r in _pgrep("run_strategy.py") if "run_strategy.py" in r.get("cmd", "")]
    desk = [r for r in _pgrep("streamlit") if "streamlit" in r.get("cmd", "").lower()]
    log = ROOT / "data" / "strategy_run.log"
    tail = ""
    if log.exists():
        try:
            lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
            tail = "\n".join(lines[-30:])
        except OSError:
            tail = ""
    health: dict[str, Any] = {}
    health_path = ROOT / "data" / "control" / "bot_health.json"
    if health_path.exists():
        try:
            import json

            health = json.loads(health_path.read_text(encoding="utf-8"))
        except Exception:
            health = {}
    # DB open vs RAM mismatch hint
    mismatches: list[str] = []
    try:
        from position_safety import INTRADAY_RESTORE, last_open_position

        ram = (health.get("positions") or {}) if isinstance(health, dict) else {}
        # health may use short keys S5 or full names
        for name in INTRADAY_RESTORE:
            open_pos = last_open_position(name)
            short = name.split("_")[0]  # S5, S8, S12…
            ram_pos = ram.get(name) or ram.get(short) or "flat"
            if open_pos is None:
                db_pos = "flat"
            else:
                db_pos = open_pos.side
            if str(ram_pos) != str(db_pos) and not (
                str(ram_pos) == "flat" and db_pos == "flat"
            ):
                # Only flag when DB open but RAM flat (classic orphan) or opposite
                if db_pos != "flat" and str(ram_pos) == "flat":
                    mismatches.append(f"{name}: DB={db_pos} RAM=flat (orphan)")
                elif db_pos == "flat" and str(ram_pos) != "flat":
                    mismatches.append(f"{name}: DB=flat RAM={ram_pos}")
    except Exception as exc:
        mismatches.append(f"mismatch_check_error:{exc}")
    return {
        "ok": True,
        "supervise": supervise,
        "run_strategy": runner,
        "desk": desk,
        "running": bool(supervise and runner),
        "log_tail": tail,
        "cwd": str(ROOT),
        "health": health,
        "mismatches": mismatches,
    }


def _kill_patterns(patterns: list[str]) -> list[int]:
    killed: list[int] = []
    for pat in patterns:
        for row in _pgrep(pat):
            pid = int(row["pid"])
            cmd = row.get("cmd", "")
            if "pgrep" in cmd:
                continue
            # Never kill the Streamlit desk itself from restart.
            if "streamlit" in cmd.lower() or "analytics.app" in cmd:
                continue
            try:
                os.kill(pid, signal.SIGKILL)
                killed.append(pid)
            except ProcessLookupError:
                pass
            except PermissionError:
                pass
    return killed


def restart_bot(*, start_if_stopped: bool = True) -> dict[str, Any]:
    """Kill supervise/run_strategy and start supervise.sh again."""
    killed = _kill_patterns(["run_strategy.py", "supervise.sh"])
    time.sleep(1.0)
    started = False
    pid = None
    err = ""
    if start_if_stopped:
        log = ROOT / "data" / "supervise.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        try:
            with log.open("a", encoding="utf-8") as fh:
                proc = subprocess.Popen(
                    ["bash", "./supervise.sh"],
                    cwd=str(ROOT),
                    stdout=fh,
                    stderr=fh,
                    start_new_session=True,
                    env={**os.environ, "PYTHONUNBUFFERED": "1"},
                )
            pid = proc.pid
            started = True
        except Exception as exc:
            err = str(exc)
    time.sleep(2.0)
    status = bot_status()
    return {
        "ok": started and status.get("running"),
        "killed": killed,
        "started_supervise_pid": pid,
        "error": err or None,
        "status": status,
    }


def stop_bot() -> dict[str, Any]:
    killed = _kill_patterns(["run_strategy.py", "supervise.sh"])
    time.sleep(0.5)
    return {"ok": True, "killed": killed, "status": bot_status()}
