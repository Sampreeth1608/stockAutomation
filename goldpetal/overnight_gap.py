"""Overnight gap read: today's session → predicted next-open direction.

Paper book OVERNIGHT_GAP. Not S4 daily swing. Not S7. Not live.
Independent of the tick-window mood gate.

Score today's Gold Petal tape (day return, close in range, late-session
return, late TBQ/TSQ imbalance). BUY near MARKET_CLOSE if the read is
bullish for a gap up; SHORT if bearish for a gap down; skip if mixed.
CLOSE in the first minutes after next MARKET_OPEN (default 09:00–09:05).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Iterable

BOOK = "OVERNIGHT_GAP"

# Default Gold Petal session (IST).
DEFAULT_OPEN = "09:00"
DEFAULT_CLOSE = "23:30"
DEFAULT_ENTRY_MINUTES = 15
DEFAULT_EXIT_MINUTES = 5
DEFAULT_LATE_MINUTES = 45
DEFAULT_BUY_PROB = 0.58
DEFAULT_SHORT_PROB = 0.42


def _sigmoid(z: float) -> float:
    z = max(-20.0, min(20.0, z))
    return 1.0 / (1.0 + math.exp(-z))


def _tanh_scale(x: float, scale: float) -> float:
    if scale == 0:
        return 0.0
    return math.tanh(x / scale)


def close_location_value(high: float, low: float, close: float) -> float:
    """Where close sits in the day range. +1 = close at high, -1 = close at low."""
    span = float(high) - float(low)
    if span <= 1e-12:
        return 0.0
    return 2.0 * ((float(close) - float(low)) / span) - 1.0


def depth_imbalance(tbq: float, tsq: float) -> float:
    total = float(tbq) + float(tsq)
    if total <= 0:
        return 0.0
    return (float(tbq) - float(tsq)) / total


def parse_hhmm(value: str) -> time:
    h, m = (value or "00:00").strip().split(":", 1)
    return time(int(h), int(m))


def in_entry_window(
    now: datetime,
    *,
    market_close: time,
    minutes_before: int,
) -> bool:
    close_dt = now.replace(
        hour=market_close.hour,
        minute=market_close.minute,
        second=0,
        microsecond=0,
    )
    start = close_dt - timedelta(minutes=max(1, int(minutes_before)))
    return start <= now <= close_dt


def in_exit_window(
    now: datetime,
    *,
    market_open: time,
    minutes_after: int,
) -> bool:
    open_dt = now.replace(
        hour=market_open.hour,
        minute=market_open.minute,
        second=0,
        microsecond=0,
    )
    end = open_dt + timedelta(minutes=max(1, int(minutes_after)))
    return open_dt <= now <= end


@dataclass(frozen=True)
class GapRead:
    bias: str  # BULLISH | BEARISH | NEUTRAL
    prob_up: float
    day_return: float
    clv: float
    late_ret: float
    late_imb: float
    open: float
    high: float
    low: float
    close: float
    n: int
    reason: str


def score_session(
    *,
    open_px: float,
    high: float,
    low: float,
    close: float,
    late_open: float,
    late_close: float,
    late_imb: float,
    n: int,
    buy_prob: float = DEFAULT_BUY_PROB,
    short_prob: float = DEFAULT_SHORT_PROB,
) -> GapRead | None:
    """Map today's tape to P(next open up). None if the day is not readable."""
    o = float(open_px)
    h = float(high)
    l = float(low)
    c = float(close)
    if n < 20 or o <= 0 or c <= 0 or h < l:
        return None
    day_ret = math.log(c / o)
    clv = close_location_value(h, l, c)
    lo = float(late_open) if late_open > 0 else c
    late_ret = math.log(c / lo) if lo > 0 else 0.0
    imb = float(late_imb)
    z = (
        6.0 * _tanh_scale(day_ret, 0.004)
        + 1.2 * clv
        + 4.0 * _tanh_scale(late_ret, 0.002)
        + 0.9 * _tanh_scale(imb, 0.35)
    )
    prob = _sigmoid(z)
    if prob >= buy_prob:
        bias = "BULLISH"
    elif prob <= short_prob:
        bias = "BEARISH"
    else:
        bias = "NEUTRAL"
    reason = (
        f"gap_read={bias} P(up)={prob:.3f} "
        f"ret={day_ret * 1e4:.1f}bps clv={clv:.2f} "
        f"late_ret={late_ret * 1e4:.1f}bps late_imb={imb:.2f} n={n}"
    )
    return GapRead(
        bias=bias,
        prob_up=prob,
        day_return=day_ret,
        clv=clv,
        late_ret=late_ret,
        late_imb=imb,
        open=o,
        high=h,
        low=l,
        close=c,
        n=n,
        reason=reason,
    )


def score_ticks(
    samples: Iterable[tuple[datetime, float, float, float]],
    *,
    now: datetime,
    late_minutes: int = DEFAULT_LATE_MINUTES,
    buy_prob: float = DEFAULT_BUY_PROB,
    short_prob: float = DEFAULT_SHORT_PROB,
) -> GapRead | None:
    rows = [
        (ts, float(ltp), float(tbq), float(tsq))
        for ts, ltp, tbq, tsq in samples
        if ltp and ltp > 0
    ]
    if len(rows) < 20:
        return None
    rows.sort(key=lambda r: r[0])
    open_px = rows[0][1]
    close = rows[-1][1]
    high = max(r[1] for r in rows)
    low = min(r[1] for r in rows)
    late_start = now - timedelta(minutes=max(5, int(late_minutes)))
    late = [r for r in rows if r[0] >= late_start] or rows[-max(10, len(rows) // 8) :]
    late_open = late[0][1]
    late_imb = sum(depth_imbalance(r[2], r[3]) for r in late) / max(1, len(late))
    return score_session(
        open_px=open_px,
        high=high,
        low=low,
        close=close,
        late_open=late_open,
        late_close=close,
        late_imb=late_imb,
        n=len(rows),
        buy_prob=buy_prob,
        short_prob=short_prob,
    )
