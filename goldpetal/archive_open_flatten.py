"""Square leftover Angel on archived books after MARKET_OPEN.

S16 HHHL+wick 1h is the only live book. Other books stay off. If an old
book still has leftover lots, this squares them once the session opens
(09:00 IST, Mon–Fri). If Angel is already flat, no new order.
Does not Arm live. Does not ENABLE archived books.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from control_state import CONTROL_DIR, ensure_control_dir
from live_readiness import LIVE_ELIGIBLE_BOOKS

IST = ZoneInfo("Asia/Kolkata")
STATE_PATH = CONTROL_DIR / "archive_open_flatten.json"
DEFAULT_OPEN = "09:00"
DEFAULT_CLOSE = "23:30"
RETRY_SEC = 30.0
REASON = "archived leftover flatten at MARKET_OPEN — S16 only from here"


def _parse_hhmm(value: str) -> tuple[int, int]:
    parts = str(value or "").strip().split(":")
    if len(parts) != 2:
        return 9, 0
    try:
        return max(0, min(23, int(parts[0]))), max(0, min(59, int(parts[1])))
    except ValueError:
        return 9, 0


def in_session_after_open(
    now: datetime,
    *,
    market_open: str = DEFAULT_OPEN,
    market_close: str = DEFAULT_CLOSE,
) -> bool:
    """True Mon–Fri from MARKET_OPEN through MARKET_CLOSE (IST)."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=IST)
    now = now.astimezone(IST)
    if now.weekday() >= 5:
        return False
    oh, om = _parse_hhmm(market_open)
    ch, cm = _parse_hhmm(market_close)
    t = now.hour * 60 + now.minute
    return (oh * 60 + om) <= t < (ch * 60 + cm)


def is_archived_book(name: str) -> bool:
    n = str(name or "").strip()
    if not n or n == "YOU_MANUAL":
        return False
    return n not in LIVE_ELIGIBLE_BOOKS


def load_archive_open_state(path: Path | None = None) -> dict[str, Any]:
    target = path or STATE_PATH
    if not target.is_file():
        return {"date": "", "done": [], "last_try_unix": 0.0}
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"date": "", "done": [], "last_try_unix": 0.0}
    if not isinstance(raw, dict):
        return {"date": "", "done": [], "last_try_unix": 0.0}
    done = [str(n).strip() for n in (raw.get("done") or []) if str(n).strip()]
    try:
        last = float(raw.get("last_try_unix") or 0)
    except (TypeError, ValueError):
        last = 0.0
    return {
        "date": str(raw.get("date") or ""),
        "done": done,
        "last_try_unix": last,
    }


def save_archive_open_state(state: dict[str, Any], path: Path | None = None) -> None:
    target = path or STATE_PATH
    ensure_control_dir(target)
    target.write_text(
        json.dumps(
            {
                "date": str(state.get("date") or ""),
                "done": list(state.get("done") or []),
                "last_try_unix": float(state.get("last_try_unix") or 0),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def archived_leftover_names(leftover: dict[str, Any] | None) -> list[str]:
    names: list[str] = []
    for raw in (leftover or {}):
        name = str(raw or "").strip()
        if is_archived_book(name) and name not in names:
            names.append(name)
    return names


def square_archived_leftovers_at_open(
    *,
    now: datetime,
    leftover: dict[str, Any] | None,
    square_leftover: Callable[[str, dict[str, Any]], Any],
    market_open: str = DEFAULT_OPEN,
    market_close: str = DEFAULT_CLOSE,
    path: Path | None = None,
    retry_sec: float = RETRY_SEC,
    clock: Callable[[], float] | None = None,
) -> list[dict[str, Any]]:
    """Square each archived leftover once the session is open. Once per book/day.

    Returns rows for the runner to log / record CLOSE. Empty when outside
    hours, already done, or on cooldown.
    """
    if not in_session_after_open(
        now, market_open=market_open, market_close=market_close
    ):
        return []
    today = now.astimezone(IST).strftime("%Y-%m-%d") if now.tzinfo else now.strftime("%Y-%m-%d")
    st = load_archive_open_state(path)
    if str(st.get("date") or "") != today:
        st = {"date": today, "done": [], "last_try_unix": 0.0}
    names = archived_leftover_names(leftover)
    pending = [n for n in names if n not in set(st.get("done") or [])]
    if not pending:
        st["date"] = today
        save_archive_open_state(st, path)
        return []
    now_unix = (clock or time.time)()
    last = float(st.get("last_try_unix") or 0)
    if last and now_unix - last < float(retry_sec):
        return []
    st["last_try_unix"] = now_unix
    out: list[dict[str, Any]] = []
    done = list(st.get("done") or [])
    leftover = leftover or {}
    for name in pending:
        row = leftover.get(name) or {}
        try:
            res = square_leftover(name, row)
        except Exception as exc:
            out.append(
                {
                    "strategy": name,
                    "ok": False,
                    "error": str(exc),
                    "reason": REASON,
                }
            )
            continue
        raw = (
            res.to_dict()
            if hasattr(res, "to_dict")
            else (dict(res) if isinstance(res, dict) else {"ok": True})
        )
        reason = str(raw.get("reason") or "")
        skipped = bool(raw.get("skipped"))
        ok = bool(raw.get("ok")) and not skipped
        already = reason in {"no_fill_leftover", "angel_already_flat"}
        if ok or already:
            if name not in done:
                done.append(name)
            out.append(
                {
                    "strategy": name,
                    "ok": True,
                    "already_flat": already,
                    "leftover_squared": ok and not already,
                    "reason": REASON,
                    "broker": raw,
                    "lots": row.get("lots"),
                    "side": row.get("side"),
                }
            )
        else:
            out.append(
                {
                    "strategy": name,
                    "ok": False,
                    "error": reason or "leftover Angel square skipped",
                    "reason": REASON,
                    "broker": raw,
                }
            )
    st["done"] = done
    save_archive_open_state(st, path)
    return out
