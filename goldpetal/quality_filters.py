"""Math quality gates: fakeout HH/LL, weak S14 wicks, expected-value skip.

These sit on top of each book's own rule. They do not change the S14
open=high / open=low / wick order — they only skip a wick that is too thin.
"""

from __future__ import annotations

import math
import os
from typing import Any


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def hhll_break_ok(
    *,
    side: str,
    o: float,
    h: float,
    l: float,
    c: float,
    prev_h: float,
    prev_l: float,
    min_close_beyond: float = 3.0,
    max_break_wick_frac: float = 0.6,
) -> tuple[bool, str]:
    """Reject wick-only pokes through prior high/low (false breakouts).

    Long: close must finish beyond prev_high, not just a spike.
    Short: close must finish beyond prev_low.
    """
    side = str(side).lower()
    if min_close_beyond < 0:
        min_close_beyond = 0.0
    if side == "long":
        if not (h > prev_h and c > o):
            return False, "not_hh_green"
        beyond = float(c) - float(prev_h)
        if beyond < min_close_beyond:
            return False, f"fakeout_close {beyond:.1f}<{min_close_beyond:.1f}"
        brk = float(h) - float(prev_h)
        wick = float(h) - float(c)
        if brk > 0 and (wick / brk) > max_break_wick_frac:
            return False, f"fakeout_wick {wick / brk:.2f}"
        return True, f"hh_beyond={beyond:.1f}"
    if not (l < prev_l and c < o):
        return False, "not_ll_red"
    beyond = float(prev_l) - float(c)
    if beyond < min_close_beyond:
        return False, f"fakeout_close {beyond:.1f}<{min_close_beyond:.1f}"
    brk = float(prev_l) - float(l)
    wick = float(c) - float(l)
    if brk > 0 and (wick / brk) > max_break_wick_frac:
        return False, f"fakeout_wick {wick / brk:.2f}"
    return True, f"ll_beyond={beyond:.1f}"


def s14_wick_quality(
    o: float,
    h: float,
    l: float,
    c: float,
    why: str,
    *,
    min_gap: float | None = None,
    min_frac: float | None = None,
) -> tuple[bool, str]:
    """Keep open=high/low. Skip only a weak *wick* call (almost equal U/L)."""
    if str(why).startswith("open="):
        return True, "open_hold"
    from wick_candles import wick_measure

    gap_need = _env_float("S14_MIN_WICK_GAP", 3.0) if min_gap is None else float(min_gap)
    frac_need = _env_float("S14_MIN_WICK_FRAC", 0.12) if min_frac is None else float(min_frac)
    m = wick_measure(o, h, l, c)
    gap = abs(m.lower - m.upper)
    if gap_need > 0 and gap < gap_need:
        return False, f"weak_wick_gap {gap:.1f}<{gap_need:.1f}"
    if frac_need > 0 and m.range_pts > 0 and (gap / m.range_pts) < frac_need:
        return False, f"weak_wick_frac {gap / m.range_pts:.2f}<{frac_need:.2f}"
    return True, f"wick_gap={gap:.1f}"


def s8_quality_ok(
    imb_pct: float,
    *,
    min_imb_pct: float = 14.0,
    rising: bool | None = None,
) -> tuple[bool, str]:
    """S8: only take a strong, preferably rising, order-book imbalance."""
    if float(imb_pct) < float(min_imb_pct):
        return False, f"imb {imb_pct:.1f}<{min_imb_pct:.1f}"
    if rising is False:
        return False, "imb_not_rising"
    return True, f"imb={imb_pct:.1f}"


def expected_value(p_win: float, avg_win: float, avg_loss: float) -> float:
    """avg_loss should be <= 0 (after-tax ₹ of losing trades)."""
    p = min(1.0, max(0.0, float(p_win)))
    return p * float(avg_win) + (1.0 - p) * float(avg_loss)


def kelly_fraction(p_win: float, avg_win: float, avg_loss: float) -> float:
    """Kelly fraction from win rate and payoff. 0 when there is no edge."""
    aw = abs(float(avg_win))
    al = abs(float(avg_loss))
    if aw <= 1e-9 or al <= 1e-9:
        return 0.0
    b = aw / al
    p = min(1.0, max(0.0, float(p_win)))
    return p - (1.0 - p) / b


def wilson_lower(wins: int, n: int, z: float = 1.0) -> float:
    """Wilson score interval lower bound — a conservative P(win)."""
    if n <= 0:
        return 0.5
    p = min(1.0, max(0.0, float(wins) / float(n)))
    z2 = float(z) * float(z)
    denom = 1.0 + z2 / float(n)
    centre = p + z2 / (2.0 * float(n))
    margin = float(z) * math.sqrt((p * (1.0 - p) + z2 / (4.0 * float(n))) / float(n))
    return max(0.0, min(1.0, (centre - margin) / denom))


def hour_cycle(hour_frac: float) -> tuple[float, float]:
    ang = 2.0 * math.pi * (float(hour_frac) % 24.0) / 24.0
    return math.sin(ang), math.cos(ang)


def tick_features(
    *,
    strategy: str,
    side: str,
    hour_frac: float,
    weekday: int,
    ltp: float,
    imb_pct: float = 0.0,
) -> dict[str, Any]:
    s, c = hour_cycle(hour_frac)
    side_u = str(side or "").upper()
    if side_u in {"BUY", "LONG"}:
        side_val = 1.0
    elif side_u in {"SHORT", "SELL"}:
        side_val = -1.0
    else:
        side_val = 0.0
    return {
        "strategy": str(strategy or ""),
        "side": side_val,
        "hour": float(hour_frac) % 24.0,
        "hour_sin": s,
        "hour_cos": c,
        "weekday": float(int(weekday) % 7),
        "ltp": float(ltp or 0.0),
        "imb_pct": float(imb_pct or 0.0),
    }
