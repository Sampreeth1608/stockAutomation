"""Expected fluctuation (bar-range) → TP/SL points.

MTF study: 30m avg range ~30 pts, 1h ~41; 1–5m too small for TP25.
Fixed SL inside noise → death by fees; too wide → bad RR.
Use rolling median bar range as the period's expected move.
"""

from __future__ import annotations

from typing import Sequence


def median(xs: Sequence[float]) -> float:
    if not xs:
        return float("nan")
    ys = sorted(float(x) for x in xs)
    n = len(ys)
    mid = n // 2
    if n % 2:
        return ys[mid]
    return 0.5 * (ys[mid - 1] + ys[mid])


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def expected_range(ranges: Sequence[float], window: int = 20) -> float:
    """Rolling expected move = median of last `window` bar ranges (high-low)."""
    if not ranges:
        return float("nan")
    w = max(1, int(window))
    return median(list(ranges)[-w:])


def tp_sl_from_range(
    exp_range: float,
    *,
    tp_mult: float = 0.85,
    sl_mult: float = 0.55,
    tp_min: float = 12.0,
    tp_max: float = 60.0,
    sl_min: float = 10.0,
    sl_max: float = 40.0,
    fee_be: float = 0.0,
    min_rr: float = 1.2,
) -> tuple[float, float]:
    """Map expected bar range → (tp_points, sl_points).

    SL floored by fee_be when set (don't stop inside round-trip cost).
    TP raised to keep min_rr vs SL when needed.
    """
    if exp_range != exp_range or exp_range <= 0:
        # NaN / empty → caller should fall back to fixed defaults
        return float("nan"), float("nan")

    sl = clamp(sl_mult * exp_range, sl_min, sl_max)
    if fee_be > 0:
        sl = max(sl, float(fee_be))
    tp = clamp(tp_mult * exp_range, tp_min, tp_max)
    if min_rr > 0 and tp < min_rr * sl:
        tp = clamp(min_rr * sl, tp_min, tp_max)
    return round(tp, 1), round(sl, 1)
