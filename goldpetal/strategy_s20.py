"""S20_FADE_HL — paper 1h: buy a bounced low, short a rejected high.

Wait for the 1h to finish. LONG when that hour made a lower low and closed
green. SHORT when it made a higher high and closed red. Inside / outside /
knife / chase → hold. FLIP at that close. Flatten at MARKET_CLOSE. Not live.
Paper 100 lots ≠ live.
"""

from __future__ import annotations

import os

from backtest_hhhl_candles import Candle
from s20_fade_hl import S20_NAME, fade_bounce_decision
from strategy import SignalResult
from strategy_s18 import S18OhlcVolHtfStrategy
from s18_ohlc_vol_htf import VolBar


def _as_candle(bar: VolBar) -> Candle:
    return Candle(bar.time, bar.open, bar.high, bar.low, bar.close)


class S20FadeHlStrategy(S18OhlcVolHtfStrategy):
    name = S20_NAME

    @property
    def bar_debug(self) -> str:
        prev = (
            f"prevH={self._prev.high:.1f} prevL={self._prev.low:.1f}"
            if self._prev
            else "prev=none"
        )
        if self._bar_o is None:
            return f"bar=none {prev}"
        return (
            f"O={self._bar_o:.1f} H={self._bar_h} L={self._bar_l} "
            f"C={self._bar_c} {prev}"
        )

    @property
    def status_line(self) -> str:
        return (
            f"TF={self.bar_minutes}m fade HL FLIP flatten-at-close "
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
        side, why = fade_bounce_decision(_as_candle(self._prev), _as_candle(cur))
        if side is None:
            self.last_skip = why
            return None
        return self._flip_to(side, cur, why)


def s20_from_env() -> S20FadeHlStrategy:
    from strategy_s18 import _load_dotenv

    _load_dotenv()
    return S20FadeHlStrategy(
        bar_minutes=int(os.getenv("S20_BAR_MINUTES", os.getenv("S18_BAR_MINUTES", "60"))),
        market_open=os.getenv("MARKET_OPEN", "09:00"),
        market_close=os.getenv("MARKET_CLOSE", "23:30"),
    )
