"""MCX Gold Petal session: Mon–Fri MARKET_OPEN–MARKET_CLOSE IST.

Weekends are closed. Extra shut days (exchange holidays) go in
MARKET_HOLIDAYS as comma YYYY-MM-DD. Sunday 23 Aug 2026 is a weekend —
no Angel orders, no new ticks.
"""

from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
DEFAULT_OPEN = "09:00"
DEFAULT_CLOSE = "23:30"


def listed_holidays() -> set[str]:
    raw = os.getenv("MARKET_HOLIDAYS") or ""
    return {p.strip()[:10] for p in raw.split(",") if p.strip()}


def _parse_hhmm(value: str) -> tuple[int, int]:
    parts = str(value or "").strip().split(":")
    if len(parts) != 2:
        return 9, 0
    try:
        return max(0, min(23, int(parts[0]))), max(0, min(59, int(parts[1])))
    except ValueError:
        return 9, 0


def market_window() -> tuple[str, str]:
    open_s = (os.getenv("MARKET_OPEN") or DEFAULT_OPEN).strip() or DEFAULT_OPEN
    close_s = (os.getenv("MARKET_CLOSE") or DEFAULT_CLOSE).strip() or DEFAULT_CLOSE
    return open_s, close_s


def session_open(now: datetime | None = None) -> bool:
    """True only in the Gold Petal session. Weekend and listed holidays are off."""
    clock = now or datetime.now(IST)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=IST)
    else:
        clock = clock.astimezone(IST)
    if clock.weekday() >= 5:
        return False
    if clock.strftime("%Y-%m-%d") in listed_holidays():
        return False
    open_s, close_s = market_window()
    oh, om = _parse_hhmm(open_s)
    ch, cm = _parse_hhmm(close_s)
    start = clock.replace(hour=oh, minute=om, second=0, microsecond=0)
    end = clock.replace(hour=ch, minute=cm, second=0, microsecond=0)
    return start <= clock <= end
