"""Minimum-edge helpers: only trade when expected move can beat fees."""

from __future__ import annotations

import math
import os
from collections import deque
from dataclasses import dataclass

from charges import charges_from_env, round_trip_charges


@dataclass
class EdgeThresholds:
    min_edge_points: float
    fee_break_even_points: float
    safety_mult: float
    cover_fees: bool

    @property
    def required_points(self) -> float:
        if self.cover_fees:
            return max(self.min_edge_points, self.fee_break_even_points * self.safety_mult)
        return self.min_edge_points


def fee_break_even_points(price: float = 14380.0) -> float:
    """Points needed so gross ₹ PnL covers a typical round-trip fee."""
    from charges import ignore_fees_enabled

    if ignore_fees_enabled():
        return 0.0
    cfg = charges_from_env()
    fee = round_trip_charges(
        side="BUY", entry_price=price, exit_price=price + 1.0, cfg=cfg
    )["charges"]
    point_value = cfg.lot_size * cfg.turnover_mult  # ₹ per point
    if point_value <= 0:
        return float("inf")
    return fee / point_value


def edge_thresholds_from_env(price: float = 14380.0) -> EdgeThresholds:
    try:
        from dotenv import load_dotenv

        load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
    except Exception:
        pass
    from charges import ignore_fees_enabled

    min_edge = float(os.getenv("MIN_EDGE_POINTS", "20"))
    safety = float(os.getenv("EDGE_SAFETY_MULT", "1.25"))
    if ignore_fees_enabled():
        cover = False
        be = 0.0
    else:
        cover = os.getenv("COVER_FEES", "true").strip().lower() in {
            "1",
            "true",
            "yes",
            "y",
        }
        be = fee_break_even_points(price)
    return EdgeThresholds(
        min_edge_points=min_edge,
        fee_break_even_points=be,
        safety_mult=safety,
        cover_fees=cover,
    )


class PointATR:
    """Rolling expected-move proxy in price points from LTP stream.

    Combines short-window volatility with medium-window range so the
    strategy can detect when Gold Petal has room to travel (user floor
    MIN_EDGE_POINTS, or fee break-even when COVER_FEES=true).
    """

    def __init__(self, window: int = 100) -> None:
        self.window = max(40, window)
        self.ltps: deque[float] = deque(maxlen=self.window)
        self.session_hi: float | None = None
        self.session_lo: float | None = None

    def reset_session(self) -> None:
        self.session_hi = None
        self.session_lo = None

    def update(self, ltp: float) -> float | None:
        px = float(ltp)
        self.ltps.append(px)
        if self.session_hi is None or px > self.session_hi:
            self.session_hi = px
        if self.session_lo is None or px < self.session_lo:
            self.session_lo = px

        need = max(20, self.window // 5)
        if len(self.ltps) < need:
            return None

        vals = list(self.ltps)
        hi, lo = max(vals), min(vals)
        span = hi - lo
        diffs = [abs(vals[i] - vals[i - 1]) for i in range(1, len(vals))]
        mean = sum(diffs) / len(diffs)
        var = sum((d - mean) ** 2 for d in diffs) / max(1, len(diffs) - 1)
        std = math.sqrt(var)

        # short expected excursion
        short_exp = max(span * 0.5, std * 8.0, mean * 12.0)

        # medium: full window range (more "will it move N points?" signal)
        mid_exp = span * 0.65

        # session room: how much of today's range is still "open" from mid
        session_exp = 0.0
        if self.session_hi is not None and self.session_lo is not None:
            sess_span = self.session_hi - self.session_lo
            # remaining room toward extremes from current price
            up = self.session_hi - px
            dn = px - self.session_lo
            session_exp = max(sess_span * 0.4, max(up, dn) * 0.9)

        return max(short_exp, mid_exp, session_exp)
