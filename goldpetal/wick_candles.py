"""Candle wick lengths → long / short (S14 candidate).

  upper wick = high − max(open, close)
  lower wick = min(open, close) − low

  LONG  when lower wick is longer than upper wick (demand defended the low)
  SHORT when upper wick is longer than lower wick (supply defended the high)
  Equal wicks → no new signal (hold if already in a trade)

Exit is the opposite wick winning on a later candle. Same-candle re-entry
is allowed when the exit bar itself prints the other side.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WickMeasure:
    upper: float
    lower: float
    body: float
    range_pts: float

    @property
    def dominant(self) -> str | None:
        if self.lower > self.upper:
            return "long"
        if self.upper > self.lower:
            return "short"
        return None


def wick_measure(open_: float, high: float, low: float, close: float) -> WickMeasure:
    o, h, l, c = float(open_), float(high), float(low), float(close)
    body_top = max(o, c)
    body_bot = min(o, c)
    upper = max(0.0, h - body_top)
    lower = max(0.0, body_bot - l)
    return WickMeasure(
        upper=upper,
        lower=lower,
        body=abs(c - o),
        range_pts=max(0.0, h - l),
    )


def wick_side(
    open_: float,
    high: float,
    low: float,
    close: float,
    *,
    min_diff: float = 0.0,
    min_frac: float = 0.0,
    min_body_ratio: float = 0.0,
    min_range: float = 0.0,
) -> str | None:
    """Return 'long' | 'short' | None for this candle's wicks."""
    m = wick_measure(open_, high, low, close)
    if min_range > 0 and m.range_pts < min_range:
        return None
    if m.dominant is None:
        return None
    winner = m.lower if m.dominant == "long" else m.upper
    loser = m.upper if m.dominant == "long" else m.lower
    if winner - loser < float(min_diff):
        return None
    if min_frac > 0:
        if m.range_pts <= 0 or (winner / m.range_pts) < float(min_frac):
            return None
    if min_body_ratio > 0:
        if winner < float(min_body_ratio) * max(m.body, 1e-9):
            return None
    return m.dominant
