"""Bot process ops for the Streamlit desk (status / restart supervise)."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
_STATUS_CACHE: dict[str, Any] = {}


def _status_cache_clear() -> None:
    _STATUS_CACHE.clear()


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


def _tail_file(path: Path, n: int = 30) -> str:
    if not path.exists():
        return ""
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            fh.seek(max(0, size - 65536))
            data = fh.read().decode("utf-8", errors="replace")
        return "\n".join(data.splitlines()[-n:])
    except OSError:
        return ""


def bot_status(*, lite: bool = False) -> dict[str, Any]:
    now = time.time()
    key = "lite" if lite else "full"
    hit = _STATUS_CACHE.get(key)
    if hit and now - float(hit[0]) < 1.2:
        return dict(hit[1])

    rows = _pgrep("supervise.sh|run_strategy.py|collect_ticks.py|streamlit")
    supervise = [r for r in rows if "supervise.sh" in r.get("cmd", "")]
    runner = [r for r in rows if "run_strategy.py" in r.get("cmd", "")]
    desk = [r for r in rows if "streamlit" in r.get("cmd", "").lower()]
    tail = "" if lite else _tail_file(ROOT / "data" / "strategy_run.log", 30)
    health: dict[str, Any] = {}
    health_path = ROOT / "data" / "control" / "bot_health.json"
    if health_path.exists():
        try:
            import json

            health = json.loads(health_path.read_text(encoding="utf-8"))
        except Exception:
            health = {}
    mismatches: list[str] = []
    if not lite:
        try:
            from position_safety import INTRADAY_RESTORE, last_open_position

            ram = (health.get("positions") or {}) if isinstance(health, dict) else {}
            for name in INTRADAY_RESTORE:
                open_pos = last_open_position(name)
                short = name.split("_")[0]
                ram_pos = ram.get(name) or ram.get(short) or "flat"
                if open_pos is None:
                    db_pos = "flat"
                else:
                    db_pos = open_pos.side
                if str(ram_pos) != str(db_pos) and not (
                    str(ram_pos) == "flat" and db_pos == "flat"
                ):
                    if db_pos != "flat" and str(ram_pos) == "flat":
                        mismatches.append(f"{name}: DB={db_pos} RAM=flat (orphan)")
                    elif db_pos == "flat" and str(ram_pos) != "flat":
                        mismatches.append(f"{name}: DB=flat RAM={ram_pos}")
        except Exception as exc:
            mismatches.append(f"mismatch_check_error:{exc}")
    result = {
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
    _STATUS_CACHE[key] = (now, result)
    return dict(result)


def _kill_patterns(patterns: list[str]) -> list[int]:
    killed: list[int] = []
    for pat in patterns:
        for row in _pgrep(pat):
            pid = int(row["pid"])
            cmd = row.get("cmd", "")
            if "pgrep" in cmd:
                continue
            # Never kill the Streamlit desk or the 8787 control panel.
            if "streamlit" in cmd.lower() or "analytics.app" in cmd:
                continue
            if "control_panel.py" in cmd:
                continue
            try:
                os.kill(pid, signal.SIGKILL)
                killed.append(pid)
            except ProcessLookupError:
                pass
            except PermissionError:
                pass
    return killed


def _python() -> str:
    venv = ROOT / "venv" / "bin" / "python"
    if venv.is_file():
        return str(venv)
    alt = ROOT / ".venv" / "bin" / "python"
    if alt.is_file():
        return str(alt)
    return "python3"


def _spawn(cmd: list[str], log_name: str) -> tuple[int | None, str]:
    log = ROOT / "data" / log_name
    log.parent.mkdir(parents=True, exist_ok=True)
    try:
        with log.open("a", encoding="utf-8") as fh:
            proc = subprocess.Popen(
                cmd,
                cwd=str(ROOT),
                stdout=fh,
                stderr=fh,
                start_new_session=True,
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
            )
        return proc.pid, ""
    except Exception as exc:
        return None, str(exc)


def feed_status() -> dict[str, Any]:
    """Angel tick feed: either inside run_strategy, or collect_ticks.py alone."""
    collector = [
        r
        for r in _pgrep("collect_ticks.py")
        if "collect_ticks.py" in r.get("cmd", "") and "control_panel" not in r.get("cmd", "")
    ]
    bot = bot_status(lite=True)
    via_bot = bool(bot.get("running"))
    source = "bot" if via_bot else ("collector" if collector else "off")
    return {
        "ok": True,
        "via_bot": via_bot,
        "collector": collector,
        "running": via_bot or bool(collector),
        "source": source,
        "note": (
            "Bot already owns the Angel feed"
            if via_bot
            else (
                "Feed-only collector (no strategies)"
                if collector
                else "No Angel feed"
            )
        ),
    }


def start_feed() -> dict[str, Any]:
    """Start collect_ticks.py only when the bot is not running (one Angel socket)."""
    if bot_status(lite=True).get("running"):
        return {
            "ok": False,
            "error": "Bot is running — it already has the Angel feed. Stop the bot first for feed-only.",
            "feed": feed_status(),
        }
    if feed_status().get("collector"):
        return {"ok": True, "note": "feed-only already running", "feed": feed_status()}
    pid, err = _spawn([_python(), "collect_ticks.py"], "collect_ticks.log")
    time.sleep(1.0)
    _status_cache_clear()
    return {
        "ok": bool(pid) and not err,
        "started_pid": pid,
        "error": err or None,
        "feed": feed_status(),
    }


def stop_feed() -> dict[str, Any]:
    """Stop feed-only collector. Does not stop the bot (bot feed dies with Stop bot)."""
    killed = _kill_patterns(["collect_ticks.py"])
    time.sleep(0.4)
    _status_cache_clear()
    return {"ok": True, "killed": killed, "feed": feed_status()}


def restart_bot(*, start_if_stopped: bool = True) -> dict[str, Any]:
    """Kill supervise/run_strategy and start supervise.sh again."""
    killed = _kill_patterns(["collect_ticks.py", "run_strategy.py", "supervise.sh"])
    time.sleep(1.0)
    started = False
    pid = None
    err = ""
    if start_if_stopped:
        pid, err = _spawn(["bash", "./supervise.sh"], "supervise.log")
        started = bool(pid) and not err
    time.sleep(2.0)
    _status_cache_clear()
    status = bot_status(lite=True)
    return {
        "ok": started and status.get("running"),
        "killed": killed,
        "started_supervise_pid": pid,
        "error": err or None,
        "status": status,
    }


def start_bot() -> dict[str, Any]:
    """Start supervise if stopped. Does not kill a running bot (use restart)."""
    st = bot_status(lite=True)
    if st.get("running"):
        slim = dict(st)
        slim.pop("log_tail", None)
        return {
            "ok": True,
            "note": "Bot already running. Type RESTART to reload .env.",
            "status": slim,
        }
    return restart_bot(start_if_stopped=True)


def stop_bot() -> dict[str, Any]:
    killed = _kill_patterns(["run_strategy.py", "supervise.sh"])
    time.sleep(0.5)
    _status_cache_clear()
    return {"ok": True, "killed": killed, "status": bot_status(lite=True)}
