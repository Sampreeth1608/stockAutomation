"""Strategy 2: 1-minute depth balance.

Collect every tick's buy1..buy5 qty and sell1..sell5 qty for one minute,
sum them, then trade on the minute net:

  net = sum(buy1..5 over minute) - sum(sell1..5 over minute)
  net > 0 → BUY / hold long
  net < 0 → SHORT / hold short
  net = 0 → CLOSE / stay flat

Same rules as the old tick-flip S2, but on a 1-minute collection instead of
reacting to every tick (which caused whipsaw).
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from depth import depth_buy_sell_sums
from strategy import Action, BarSnapshot, Position, SignalResult


class BalanceStrategy:
    """BUY while minute depth buy_sum - sell_sum > 0; SHORT while < 0."""

    name = "S2_BALANCE"

    def __init__(self, window_seconds: int = 60) -> None:
        self.position: Position = "flat"
        self.window_seconds = max(5, int(window_seconds))
        self._bucket_start: datetime | None = None
        self._buy_levels = [0.0] * 5
        self._sell_levels = [0.0] * 5
        self._tick_count = 0
        self._last_cmp: float | None = None
        self.last_minute: dict[str, float] | None = None

    @property
    def status_line(self) -> str:
        return f"1-min depth sum window={self.window_seconds}s pos={self.position}"

    def _bucket_floor(self, now: datetime) -> datetime:
        """Floor timestamp to window boundary (default: whole minutes)."""
        epoch = int(now.timestamp())
        floored = epoch - (epoch % self.window_seconds)
        return datetime.fromtimestamp(floored, tz=now.tzinfo)

    def _reset_bucket(self, start: datetime) -> None:
        self._bucket_start = start
        self._buy_levels = [0.0] * 5
        self._sell_levels = [0.0] * 5
        self._tick_count = 0

    def _add_tick(self, details: dict[str, float]) -> None:
        for i in range(5):
            self._buy_levels[i] += float(details.get(f"buy{i+1}_qty", 0.0) or 0.0)
            self._sell_levels[i] += float(details.get(f"sell{i+1}_qty", 0.0) or 0.0)
        self._tick_count += 1

    def _decide(
        self,
        buy_sum: float,
        sell_sum: float,
        details: dict | None = None,
        *,
        ticks: int | None = None,
    ) -> SignalResult:
        net = buy_sum - sell_sum
        detail_txt = ""
        if details:
            detail_txt = (
                f" buy=[{details.get('buy1_qty', 0):.0f},{details.get('buy2_qty', 0):.0f},"
                f"{details.get('buy3_qty', 0):.0f},{details.get('buy4_qty', 0):.0f},"
                f"{details.get('buy5_qty', 0):.0f}]"
                f" sell=[{details.get('sell1_qty', 0):.0f},{details.get('sell2_qty', 0):.0f},"
                f"{details.get('sell3_qty', 0):.0f},{details.get('sell4_qty', 0):.0f},"
                f"{details.get('sell5_qty', 0):.0f}]"
            )
        tick_txt = f" ticks={ticks}" if ticks is not None else ""

        if net > 0:
            if self.position == "long":
                action: Action = "HOLD"
                reason = (
                    f"1m buy_sum({buy_sum:.0f}) - sell_sum({sell_sum:.0f}) = {net:.0f} > 0; "
                    f"hold long{tick_txt}{detail_txt}"
                )
            else:
                action = "BUY"
                self.position = "long"
                reason = (
                    f"1m buy_sum({buy_sum:.0f}) - sell_sum({sell_sum:.0f}) = {net:.0f} > 0; "
                    f"buy{tick_txt}{detail_txt}"
                )
        elif net < 0:
            if self.position == "short":
                action = "HOLD"
                reason = (
                    f"1m buy_sum({buy_sum:.0f}) - sell_sum({sell_sum:.0f}) = {net:.0f} < 0; "
                    f"hold short{tick_txt}{detail_txt}"
                )
            else:
                action = "SHORT"
                self.position = "short"
                reason = (
                    f"1m buy_sum({buy_sum:.0f}) - sell_sum({sell_sum:.0f}) = {net:.0f} < 0; "
                    f"short{tick_txt}{detail_txt}"
                )
        else:
            if self.position == "flat":
                action = "FLAT"
                reason = f"1m buy_sum - sell_sum = 0; stay flat{tick_txt}{detail_txt}"
            else:
                action = "CLOSE"
                self.position = "flat"
                reason = f"1m buy_sum - sell_sum = 0; close to flat{tick_txt}{detail_txt}"

        return SignalResult(
            action=action,
            position_after=self.position,
            price_delta=None,
            net=net,
            net_delta=None,
            prev_net_delta=None,
            reason=reason,
        )

    def _flush_minute(self) -> SignalResult | None:
        """Evaluate accumulated minute and clear bucket totals."""
        if self._tick_count <= 0:
            return None
        buy_sum = float(sum(self._buy_levels))
        sell_sum = float(sum(self._sell_levels))
        details = {
            **{f"buy{i+1}_qty": self._buy_levels[i] for i in range(5)},
            **{f"sell{i+1}_qty": self._sell_levels[i] for i in range(5)},
            "buy_sum": buy_sum,
            "sell_sum": sell_sum,
            "ticks": float(self._tick_count),
        }
        self.last_minute = details
        ticks = self._tick_count
        # clear before decide so state is ready for next bucket
        self._buy_levels = [0.0] * 5
        self._sell_levels = [0.0] * 5
        self._tick_count = 0
        return self._decide(buy_sum, sell_sum, details, ticks=ticks)

    def on_tick(
        self, now: datetime, cmp: float, message: dict[str, Any]
    ) -> SignalResult | None:
        """Accumulate depth qtys; return a signal only when the minute rolls."""
        buy_sum, sell_sum, details = depth_buy_sell_sums(message)
        if (
            buy_sum == 0
            and sell_sum == 0
            and not message.get("best_5_buy_data")
            and not message.get("best_5_sell_data")
        ):
            return None

        bucket = self._bucket_floor(now)
        self._last_cmp = float(cmp)
        result: SignalResult | None = None

        if self._bucket_start is None:
            self._reset_bucket(bucket)
        elif bucket > self._bucket_start:
            # Minute completed → trade on collected sums, then start new minute
            result = self._flush_minute()
            self._bucket_start = bucket

        self._add_tick(details)
        return result

    def on_bar(self, bar: BarSnapshot, details: dict | None = None) -> SignalResult:
        """Direct evaluate (tests / manual). Uses bar.bp/sp as already-summed totals."""
        return self._decide(float(bar.bp), float(bar.sp), details)


def balance_from_env() -> BalanceStrategy:
    try:
        from dotenv import load_dotenv

        load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
    except Exception:
        pass
    window = int(os.getenv("S2_WINDOW_SECONDS", "60"))
    return BalanceStrategy(window_seconds=window)
