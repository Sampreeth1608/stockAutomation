"""S19_BODY_CLOSE_1H — 1h aligned green/red + close vs prev.

LONG when the finished hour is green and closed up vs the previous same-session
hour. SHORT when red and closed down. Mixed / doji / equal → hold.
FLIP at that close. Delivery — holds overnight. Only S16 flattens at
MARKET_CLOSE. Live-eligible — you Arm.
"""

from __future__ import annotations

import os

from s19_body_close import S19_NAME, s19_bar_decision
from strategy import SignalResult
from strategy_s18 import S18OhlcVolHtfStrategy
from s18_ohlc_vol_htf import VolBar


class S19BodyCloseStrategy(S18OhlcVolHtfStrategy):
    name = S19_NAME

    @property
    def bar_debug(self) -> str:
        prev = f"prevC={self._prev.close:.1f}" if self._prev else "prev=none"
        if self._bar_o is None:
            return f"bar=none {prev}"
        return (
            f"O={self._bar_o:.1f} H={self._bar_h} L={self._bar_l} "
            f"C={self._bar_c} {prev}"
        )

    @property
    def status_line(self) -> str:
        return (
            f"TF={self.bar_minutes}m aligned body+close FLIP delivery "
            f"{self.market_open}-{self.market_close} "
            f"{self.bar_debug} skip={self.last_skip or '-'} pos={self.position}"
        )

    def _decide_closed(self, cur: VolBar | None) -> SignalResult | None:
        if cur is None:
            self.last_skip = "no_closed_bar"
            return None
        if self._prev is None or not self._same_session_prev(self._prev, cur):
            self.last_skip = "need_prev_1h"
            return None
        side, why = s19_bar_decision(self._prev, cur)
        if side is None:
            self.last_skip = why
            return None
        return self._flip_to(side, cur, why)


def s19_from_env() -> S19BodyCloseStrategy:
    from strategy_s18 import _load_dotenv

    _load_dotenv()
    return S19BodyCloseStrategy(
        bar_minutes=int(os.getenv("S19_BAR_MINUTES", os.getenv("S18_BAR_MINUTES", "60"))),
        market_open=os.getenv("MARKET_OPEN", "09:00"),
        market_close=os.getenv("MARKET_CLOSE", "23:30"),
    )
