"""Market regime detection from recent LTP / depth."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Deque, Literal

Regime = Literal["TREND", "CHOP", "QUIET", "WIDE_SPREAD", "UNKNOWN"]


@dataclass
class RegimeState:
    regime: Regime
    vol_bps: float
    spread_bps: float
    direction: float  # net return over window
    flips: int
    reason: str


class RegimeDetector:
    """Classify short-horizon environment for strategy gating."""

    def __init__(self, window: int = 60) -> None:
        self.window = window
        self.ltps: Deque[float] = deque(maxlen=window)
        self.spreads: Deque[float] = deque(maxlen=window)
        self.last: RegimeState = RegimeState(
            "UNKNOWN", 0.0, 0.0, 0.0, 0, "warming_up"
        )

    def update(self, ltp: float, spread_bps: float | None = None) -> RegimeState:
        self.ltps.append(float(ltp))
        if spread_bps is not None:
            self.spreads.append(float(spread_bps))

        if len(self.ltps) < max(20, self.window // 3):
            self.last = RegimeState("UNKNOWN", 0.0, 0.0, 0.0, 0, "warming_up")
            return self.last

        prices = list(self.ltps)
        rets = []
        for i in range(1, len(prices)):
            if prices[i - 1]:
                rets.append((prices[i] - prices[i - 1]) / prices[i - 1] * 1e4)  # bps
        if not rets:
            self.last = RegimeState("UNKNOWN", 0.0, 0.0, 0.0, 0, "no_returns")
            return self.last

        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / max(1, len(rets) - 1)
        vol = var ** 0.5
        direction = sum(rets)
        flips = sum(1 for i in range(1, len(rets)) if rets[i] * rets[i - 1] < 0)
        avg_spread = (
            sum(self.spreads) / len(self.spreads) if self.spreads else 0.0
        )

        if avg_spread >= 8.0:  # wide book vs mid
            regime: Regime = "WIDE_SPREAD"
            reason = f"spread_bps={avg_spread:.2f}>=8"
        elif abs(direction) >= 15.0 and flips < len(rets) * 0.35:
            # Smooth directional drift counts as TREND even if vol looks low
            regime = "TREND"
            reason = f"dir={direction:.2f}bps vol={vol:.2f} flips={flips}"
        elif vol < 1.2 and abs(direction) < 8.0:
            regime = "QUIET"
            reason = f"vol_bps={vol:.2f}<1.2"
        elif flips >= len(rets) * 0.45 and abs(direction) < vol * 3:
            regime = "CHOP"
            reason = f"flips={flips}/{len(rets)} vol={vol:.2f}"
        else:
            regime = "TREND"
            reason = f"dir={direction:.2f}bps vol={vol:.2f} flips={flips}"

        self.last = RegimeState(regime, vol, avg_spread, direction, flips, reason)
        return self.last
