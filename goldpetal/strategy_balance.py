"""Strategy 2: trade on sign of total buy qty - total sell qty."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from strategy import Action, BarSnapshot, Position, SignalResult


class BalanceStrategy:
    """BUY while (BP - SP) > 0; SHORT while (BP - SP) < 0."""

    name = "S2_BALANCE"

    def __init__(self) -> None:
        self.position: Position = "flat"

    def on_bar(self, bar: BarSnapshot) -> SignalResult:
        buy_sum = float(bar.bp)
        sell_sum = float(bar.sp)
        net = buy_sum - sell_sum

        if net > 0:
            if self.position == "long":
                action: Action = "HOLD"
                reason = f"buy_sum({buy_sum}) - sell_sum({sell_sum}) = {net} > 0; hold long"
            else:
                action = "BUY"
                self.position = "long"
                reason = f"buy_sum({buy_sum}) - sell_sum({sell_sum}) = {net} > 0; buy"
        elif net < 0:
            if self.position == "short":
                action = "HOLD"
                reason = f"buy_sum({buy_sum}) - sell_sum({sell_sum}) = {net} < 0; hold short"
            else:
                action = "SHORT"
                self.position = "short"
                reason = f"buy_sum({buy_sum}) - sell_sum({sell_sum}) = {net} < 0; short"
        else:
            if self.position == "flat":
                action = "FLAT"
                reason = "buy_sum - sell_sum = 0; stay flat"
            else:
                action = "CLOSE"
                self.position = "flat"
                reason = "buy_sum - sell_sum = 0; close to flat"

        return SignalResult(
            action=action,
            position_after=self.position,
            price_delta=None,
            net=net,
            net_delta=None,
            prev_net_delta=None,
            reason=reason,
        )
