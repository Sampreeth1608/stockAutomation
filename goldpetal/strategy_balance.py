"""Strategy 2: trade on sign of sum(buy1-5 qty) - sum(sell1-5 qty)."""

from __future__ import annotations

from strategy import Action, BarSnapshot, Position, SignalResult


class BalanceStrategy:
    """BUY while depth buy_sum - sell_sum > 0; SHORT while < 0."""

    name = "S2_BALANCE"

    def __init__(self) -> None:
        self.position: Position = "flat"

    def on_bar(self, bar: BarSnapshot, details: dict | None = None) -> SignalResult:
        buy_sum = float(bar.bp)
        sell_sum = float(bar.sp)
        net = buy_sum - sell_sum
        detail_txt = ""
        if details:
            detail_txt = (
                f" buy=[{details.get('buy1_qty', 0)},{details.get('buy2_qty', 0)},"
                f"{details.get('buy3_qty', 0)},{details.get('buy4_qty', 0)},"
                f"{details.get('buy5_qty', 0)}]"
                f" sell=[{details.get('sell1_qty', 0)},{details.get('sell2_qty', 0)},"
                f"{details.get('sell3_qty', 0)},{details.get('sell4_qty', 0)},"
                f"{details.get('sell5_qty', 0)}]"
            )

        if net > 0:
            if self.position == "long":
                action: Action = "HOLD"
                reason = f"depth buy_sum({buy_sum}) - sell_sum({sell_sum}) = {net} > 0; hold long{detail_txt}"
            else:
                action = "BUY"
                self.position = "long"
                reason = f"depth buy_sum({buy_sum}) - sell_sum({sell_sum}) = {net} > 0; buy{detail_txt}"
        elif net < 0:
            if self.position == "short":
                action = "HOLD"
                reason = f"depth buy_sum({buy_sum}) - sell_sum({sell_sum}) = {net} < 0; hold short{detail_txt}"
            else:
                action = "SHORT"
                self.position = "short"
                reason = f"depth buy_sum({buy_sum}) - sell_sum({sell_sum}) = {net} < 0; short{detail_txt}"
        else:
            if self.position == "flat":
                action = "FLAT"
                reason = f"depth buy_sum - sell_sum = 0; stay flat{detail_txt}"
            else:
                action = "CLOSE"
                self.position = "flat"
                reason = f"depth buy_sum - sell_sum = 0; close to flat{detail_txt}"

        return SignalResult(
            action=action,
            position_after=self.position,
            price_delta=None,
            net=net,
            net_delta=None,
            prev_net_delta=None,
            reason=reason,
        )
