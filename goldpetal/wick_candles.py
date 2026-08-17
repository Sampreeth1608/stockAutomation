"""Candle wick lengths → long / short (S14).

  upper = high − max(open, close)
  lower = min(open, close) − low
  body  = |close − open|
  range = high − low

S14 uses this on every candle (entry and exit). No high−low skip.

  if upper ≤ 1 and lower ≤ 1:          # bald
      close > open → LONG
      close < open → SHORT
      close = open → skip
  else:
      winning wick ≥ 0.5 × range → that side
      or winning wick ≥ 2 × body → that side
      else skip

HOLD: opposite → CLOSE on a later candle; no reverse on that same bar.
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

    def bald(self, eps: float = 1.0) -> bool:
        """True when neither side has a wick beyond eps points."""
        return self.upper <= float(eps) and self.lower <= float(eps)


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


def _body_side(open_: float, close: float) -> str | None:
    if close > open_:
        return "long"
    if close < open_:
        return "short"
    return None


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
    nowick_eps: float = 1.0,
    nowick_body: bool = True,
    nowick_only: bool = False,
) -> str | None:
    """Return 'long' | 'short' | None for this candle's wicks / bald body."""
    m = wick_measure(open_, high, low, close)
    if min_range > 0 and m.range_pts < min_range:
        return None

    bald = m.bald(nowick_eps)
    if nowick_only:
        if not bald or not nowick_body:
            return None
        return _body_side(open_, close)

    if bald:
        if nowick_body:
            return _body_side(open_, close)
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


def wick_exit_strict(
    open_: float,
    high: float,
    low: float,
    close: float,
    *,
    min_range: float = 0.0,
    nowick_eps: float = 1.0,
    nowick_body: bool = True,
) -> str | None:
    """S14 rule on one candle: bald body, or winning wick ≥ 0.5×range, or ≥ 2×body.

    Used for both entry and exit. min_range is unused when 0 (S14 default).
    """
    m = wick_measure(open_, high, low, close)
    if min_range > 0 and m.range_pts < min_range:
        return None
    if m.bald(nowick_eps):
        return _body_side(open_, close) if nowick_body else None
    if m.dominant is None:
        return None
    winner = m.lower if m.dominant == "long" else m.upper
    frac_ok = m.range_pts > 0 and (winner / m.range_pts) >= 0.5
    pin_ok = winner >= 2.0 * max(m.body, 1e-9)
    if frac_ok or pin_ok:
        return m.dominant
    return None
