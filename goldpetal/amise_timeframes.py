"""AMISE factory timeframes — intraday rungs plus the daily swing.

Paper S16/S18 are 1h books. The factory still has to beat S16-style and
S18-style *on the same bar size* it is inventing (5m vs 5m, 1h vs 1h).
Daily genomes beat S13 (and S16 replayed on the day). S13/S16 formulas
are not rewritten.

Bars align from MARKET_OPEN so 1h15 starts 09:00, 10:15, … not 08:45.
A token change (Gold Petal rollover) starts a new bar and resets volume
so we do not stitch two contracts into one candle.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

# User rungs: 3m … 3h45, then the day candle.
LAB_TF_ROWS: tuple[tuple[str, int], ...] = (
    ("3m", 3),
    ("5m", 5),
    ("10m", 10),
    ("15m", 15),
    ("30m", 30),
    ("45m", 45),
    ("1h", 60),
    ("1h15", 75),
    ("1h30", 90),
    ("1h45", 105),
    ("2h", 120),
    ("2h15", 135),
    ("2h30", 150),
    ("2h45", 165),
    ("3h", 180),
    ("3h15", 195),
    ("3h30", 210),
    ("3h45", 225),
    ("1d", 1440),
)


@dataclass(frozen=True)
class LabTF:
    label: str
    minutes: int

    @property
    def daily(self) -> bool:
        return self.minutes >= 1440

    @property
    def flatten_eod(self) -> bool:
        """Intraday books flatten at session close. Daily swing holds overnight."""
        return not self.daily

    @property
    def min_trades(self) -> int:
        if self.daily:
            return 6
        if self.minutes >= 180:
            return 8
        return 20

    @property
    def min_bars(self) -> int:
        if self.daily:
            return 8
        if self.minutes >= 120:
            return 24
        return 40


LAB_TIMEFRAMES: tuple[LabTF, ...] = tuple(LabTF(label, mins) for label, mins in LAB_TF_ROWS)
_BY_LABEL = {t.label: t for t in LAB_TIMEFRAMES}
_BY_MINUTES = {t.minutes: t for t in LAB_TIMEFRAMES}


def _hhmm_mins(raw: str) -> int:
    h, m = str(raw or "09:00").strip().split(":")
    return int(h) * 60 + int(m)


def _from_minutes(mins: int) -> LabTF:
    mins = int(mins)
    found = _BY_MINUTES.get(mins)
    if found is not None:
        return found
    if mins >= 1440:
        return _BY_LABEL["1d"]
    return LabTF(f"{mins}m", mins)


def parse_tf(raw: str | int | None) -> LabTF:
    if raw is None or raw == "":
        return _BY_LABEL["1h"]
    if isinstance(raw, int):
        return _from_minutes(int(raw))
    text = str(raw).strip().lower().replace(" ", "")
    text = (
        text.replace("hours", "h")
        .replace("hour", "h")
        .replace("hrs", "h")
        .replace("hr", "h")
        .replace("minutes", "m")
        .replace("minute", "m")
        .replace("mins", "m")
        .replace("min", "m")
    )
    aliases = {
        "60": "1h",
        "60m": "1h",
        "1h0": "1h",
        "d": "1d",
        "day": "1d",
        "daily": "1d",
        "1day": "1d",
        "1d": "1d",
    }
    text = aliases.get(text, text)
    if text in _BY_LABEL:
        return _BY_LABEL[text]
    compact = text.replace(":", "")
    if compact in _BY_LABEL:
        return _BY_LABEL[compact]
    # 1h:15 / 1h15m / 2h30 / 3h:45m
    m = re.fullmatch(r"(\d+)h:?(\d+)m?", text) or re.fullmatch(r"(\d+)h:?(\d+)m?", compact)
    if m:
        return _from_minutes(int(m.group(1)) * 60 + int(m.group(2)))
    # 2:30min / 3:15 (hours:minutes on the user rungs)
    m = re.fullmatch(r"(\d+):(\d+)m?", text)
    if m:
        return _from_minutes(int(m.group(1)) * 60 + int(m.group(2)))
    if compact.endswith("m") and compact[:-1].isdigit():
        return _from_minutes(int(compact[:-1]))
    if compact.endswith("h") and compact[:-1].isdigit():
        return _from_minutes(int(compact[:-1]) * 60)
    if compact.isdigit():
        return _from_minutes(int(compact))
    return _BY_LABEL["1h"]


def minutes_for_label(raw: str | int | None) -> int:
    return parse_tf(raw).minutes


def floor_session_bar(
    ts: datetime,
    minutes: int,
    *,
    market_open: str = "09:00",
    market_close: str = "23:30",
) -> datetime:
    """Floor to a session-aligned bar. Daily keys at MARKET_OPEN."""
    del market_close
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=IST)
    ts = ts.astimezone(IST)
    minutes = max(1, int(minutes))
    open_m = _hhmm_mins(market_open)
    oh, om = divmod(open_m, 60)
    if minutes >= 1440:
        open_dt = ts.replace(hour=oh, minute=om, second=0, microsecond=0)
        if ts < open_dt:
            open_dt = open_dt - timedelta(days=1)
        while open_dt.weekday() >= 5:
            open_dt -= timedelta(days=1)
        return open_dt
    midnight = ts.replace(hour=0, minute=0, second=0, microsecond=0)
    mins = int((ts - midnight).total_seconds() // 60)
    if mins < open_m:
        prev = midnight - timedelta(seconds=1)
        return floor_session_bar(
            prev, minutes, market_open=market_open, market_close="23:30"
        )
    rel = mins - open_m
    block = (rel // minutes) * minutes
    return midnight + timedelta(minutes=open_m + block)


def daily_bar_close(
    key: datetime,
    *,
    market_close: str = "23:30",
) -> datetime:
    close_m = _hhmm_mins(market_close)
    ch, cm = divmod(close_m, 60)
    return key.replace(hour=ch, minute=cm, second=0, microsecond=0)


def tick_token(row: Any) -> str:
    try:
        tok = row["token"]
    except Exception:
        tok = None
    if tok is None and isinstance(row, dict):
        tok = row.get("token")
    return str(tok or "").strip()


def tick_in_session(
    ts: datetime,
    *,
    market_open: str = "09:00",
    market_close: str = "23:30",
) -> bool:
    if ts.weekday() >= 5:
        return False
    open_m = _hhmm_mins(market_open)
    close_m = _hhmm_mins(market_close)
    t = ts.hour * 60 + ts.minute
    return open_m <= t <= close_m
