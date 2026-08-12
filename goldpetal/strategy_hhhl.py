"""S12_HHHL30 — Higher-high / lower-low candle breakout (paper).

Rules (same as backtest_hhhl_candles.py):
  LONG  entry: high > prev_high AND close > open
        exit:  high < prev_high AND close < open
  SHORT entry: low  < prev_low  AND close < open
        exit:  low  > prev_low  AND close > open

Defaults from hist paper sweep: 30m bars, min_range=5, no_flip=True.
Session hours use global MARKET_OPEN/CLOSE in run_strategy.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from strategy import Position, SignalResult

IST = ZoneInfo("Asia/Kolkata")


@dataclass
class HhhlConfig:
    bar_minutes: int = 30
    min_range: float = 5.0
    no_flip: bool = True
    allow_long: bool = True
    allow_short: bool = True


class HhhlCandleStrategy:
    name = "S12_HHHL30"

    def __init__(self, cfg: HhhlConfig | None = None) -> None:
        self.cfg = cfg or HhhlConfig()
        self.position: Position = "flat"
        self.entry_price: float | None = None
        self.last_skip: str | None = None
        self.prev_o = self.prev_h = self.prev_l = self.prev_c = None
        self._bar_key: datetime | None = None
        self._bar_o = self._bar_h = self._bar_l = self._bar_c = None
        self._bar_n = 0

    @property
    def status_line(self) -> str:
        c = self.cfg
        return (
            f"TF={c.bar_minutes}m min_range={c.min_range:.0f} "
            f"no_flip={c.no_flip} L={c.allow_long} S={c.allow_short} "
            f"pos={self.position}"
        )

    def _floor_bar(self, ts: datetime) -> datetime:
        ts = ts.astimezone(IST)
        midnight = ts.replace(hour=0, minute=0, second=0, microsecond=0)
        mins = int((ts - midnight).total_seconds() // 60)
        block = (mins // max(1, self.cfg.bar_minutes)) * max(1, self.cfg.bar_minutes)
        return midnight + timedelta(minutes=block)

    def on_tick(
        self, now: datetime, ltp: float, message: dict[str, Any] | None = None
    ) -> SignalResult | None:
        del message  # OHLC from LTP only
        key = self._floor_bar(now)
        px = float(ltp)
        if self._bar_key is None:
            self._bar_key = key
            self._bar_o = self._bar_h = self._bar_l = self._bar_c = px
            self._bar_n = 1
            return None

        if key == self._bar_key:
            assert self._bar_h is not None and self._bar_l is not None
            self._bar_h = max(self._bar_h, px)
            self._bar_l = min(self._bar_l, px)
            self._bar_c = px
            self._bar_n += 1
            return None

        # Bar closed → decide on completed candle, then start new bar
        result = self._on_bar_close(
            float(self._bar_o or px),
            float(self._bar_h or px),
            float(self._bar_l or px),
            float(self._bar_c or px),
        )
        self._bar_key = key
        self._bar_o = self._bar_h = self._bar_l = self._bar_c = px
        self._bar_n = 1
        return result

    def on_bar_row(self, row: dict[str, Any]) -> SignalResult | None:
        """Offline / paper-sim path from OHLC row."""
        return self._on_bar_close(
            float(row["open"]),
            float(row["high"]),
            float(row["low"]),
            float(row["close"]),
        )

    def _on_bar_close(self, o: float, h: float, l: float, c: float) -> SignalResult | None:
        prev = (self.prev_o, self.prev_h, self.prev_l, self.prev_c)
        self.prev_o, self.prev_h, self.prev_l, self.prev_c = o, h, l, c
        if any(x is None for x in prev):
            return None
        assert self.prev_h is not None  # for type checkers after assign
        _po, ph, pl, _pc = prev
        assert ph is not None and pl is not None

        range_pts = h - l
        want_long = (
            self.cfg.allow_long and h > float(ph) and c > o
        )
        want_short = (
            self.cfg.allow_short and l < float(pl) and c < o
        )
        exit_long = h < float(ph) and c < o
        exit_short = l > float(pl) and c > o
        can_enter = range_pts >= self.cfg.min_range

        if self.position == "long":
            if exit_long or (want_short and not self.cfg.no_flip):
                self.position = "flat"
                self.entry_price = None
                if want_short and not self.cfg.no_flip and can_enter:
                    self.position = "short"
                    self.entry_price = c
                    return SignalResult(
                        action="SHORT",
                        position_after="short",
                        price_delta=None,
                        net=None,
                        net_delta=None,
                        prev_net_delta=None,
                        reason=(
                            f"s12 flip short LL+red range={range_pts:.1f} "
                            f"H={h:.1f}<prevH={float(ph):.1f}"
                        ),
                    )
                return SignalResult(
                    action="CLOSE",
                    position_after="flat",
                    price_delta=None,
                    net=None,
                    net_delta=None,
                    prev_net_delta=None,
                    reason=f"s12 exit long LH+red range={range_pts:.1f}",
                )
            self.last_skip = "hold_long"
            return None

        if self.position == "short":
            if exit_short or (want_long and not self.cfg.no_flip):
                self.position = "flat"
                self.entry_price = None
                if want_long and not self.cfg.no_flip and can_enter:
                    self.position = "long"
                    self.entry_price = c
                    return SignalResult(
                        action="BUY",
                        position_after="long",
                        price_delta=None,
                        net=None,
                        net_delta=None,
                        prev_net_delta=None,
                        reason=(
                            f"s12 flip long HH+green range={range_pts:.1f} "
                            f"L={l:.1f}>prevL={float(pl):.1f}"
                        ),
                    )
                return SignalResult(
                    action="CLOSE",
                    position_after="flat",
                    price_delta=None,
                    net=None,
                    net_delta=None,
                    prev_net_delta=None,
                    reason=f"s12 exit short HL+green range={range_pts:.1f}",
                )
            self.last_skip = "hold_short"
            return None

        # flat
        if not can_enter:
            self.last_skip = f"min_range {range_pts:.1f}<{self.cfg.min_range}"
            return None
        if want_long and not want_short:
            self.position = "long"
            self.entry_price = c
            return SignalResult(
                action="BUY",
                position_after="long",
                price_delta=None,
                net=None,
                net_delta=None,
                prev_net_delta=None,
                reason=f"s12 long HH+green range={range_pts:.1f} H={h:.1f}>prevH={float(ph):.1f}",
            )
        if want_short and not want_long:
            self.position = "short"
            self.entry_price = c
            return SignalResult(
                action="SHORT",
                position_after="short",
                price_delta=None,
                net=None,
                net_delta=None,
                prev_net_delta=None,
                reason=f"s12 short LL+red range={range_pts:.1f} L={l:.1f}<prevL={float(pl):.1f}",
            )
        self.last_skip = "no_signal"
        return None


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "y"}


def hhhl_from_env() -> HhhlCandleStrategy:
    load = None
    try:
        from dotenv import load_dotenv

        load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
    except Exception:
        pass
    del load
    cfg = HhhlConfig(
        bar_minutes=int(os.getenv("S12_BAR_MINUTES", "30")),
        min_range=float(os.getenv("S12_MIN_RANGE", "5")),
        no_flip=_env_flag("S12_NO_FLIP", True),
        allow_long=_env_flag("S12_ALLOW_LONG", True),
        allow_short=_env_flag("S12_ALLOW_SHORT", True),
    )
    return HhhlCandleStrategy(cfg)
