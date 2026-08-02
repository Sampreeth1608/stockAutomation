"""30-minute buying/selling pressure strategy (signal logic only)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

Position = Literal["flat", "long", "short"]
Action = Literal["BUY", "SHORT", "HOLD", "CLOSE", "FLAT", "WAIT"]


@dataclass
class BarSnapshot:
    time_label: str
    cmp: float
    bp: float
    sp: float


@dataclass
class SignalResult:
    action: Action
    position_after: Position
    price_delta: Optional[float]
    net: float
    net_delta: Optional[float]
    prev_net_delta: Optional[float]
    reason: str


class PressureStrategy:
    """BUY/SHORT from priceΔ + netΔ alignment; close on mismatch or falling netΔ."""

    def __init__(self) -> None:
        self.position: Position = "flat"
        self.prev_cmp: Optional[float] = None
        self.prev_net: Optional[float] = None
        self.prev_net_delta: Optional[float] = None

    def on_bar(self, bar: BarSnapshot) -> SignalResult:
        net = bar.bp - bar.sp

        if self.prev_cmp is None or self.prev_net is None:
            self.prev_cmp = bar.cmp
            self.prev_net = net
            return SignalResult(
                action="WAIT",
                position_after=self.position,
                price_delta=None,
                net=net,
                net_delta=None,
                prev_net_delta=None,
                reason="baseline bar stored; waiting for next 30-min bar",
            )

        price_delta = bar.cmp - self.prev_cmp
        net_delta = net - self.prev_net
        prev_net_delta = self.prev_net_delta

        action: Action
        reason: str

        if self.position == "flat":
            if price_delta > 0 and net_delta > 0:
                action = "BUY"
                self.position = "long"
                reason = "priceΔ > 0 and netΔ > 0"
            elif price_delta < 0 and net_delta < 0:
                action = "SHORT"
                self.position = "short"
                reason = "priceΔ < 0 and netΔ < 0"
            else:
                action = "FLAT"
                reason = "no aligned entry (priceΔ/netΔ signs do not match)"
        elif self.position == "long":
            if not (price_delta > 0 and net_delta > 0):
                action = "CLOSE"
                self.position = "flat"
                reason = "long mismatch: need priceΔ > 0 and netΔ > 0"
            elif prev_net_delta is not None and net_delta < prev_net_delta:
                action = "CLOSE"
                self.position = "flat"
                reason = f"netΔ decreased ({prev_net_delta} -> {net_delta})"
            else:
                action = "HOLD"
                reason = "long intact; netΔ not decreasing"
        else:  # short
            if not (price_delta < 0 and net_delta < 0):
                action = "CLOSE"
                self.position = "flat"
                reason = "short mismatch: need priceΔ < 0 and netΔ < 0"
            elif prev_net_delta is not None and net_delta < prev_net_delta:
                action = "CLOSE"
                self.position = "flat"
                reason = f"netΔ decreased ({prev_net_delta} -> {net_delta})"
            else:
                action = "HOLD"
                reason = "short intact; netΔ not decreasing"

        self.prev_cmp = bar.cmp
        self.prev_net = net
        self.prev_net_delta = net_delta

        return SignalResult(
            action=action,
            position_after=self.position,
            price_delta=price_delta,
            net=net,
            net_delta=net_delta,
            prev_net_delta=prev_net_delta,
            reason=reason,
        )
