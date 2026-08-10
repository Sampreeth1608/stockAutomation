"""S8_ALIGN — tick-level price + TBQ/TSQ combined behaviour.

No 30m bars. Always watch price, TBQ, TSQ individually and together:

  Bullish: price rising WITH TBQ rising (ΔTBQ ≥ frac·|NET|) → long bias
  Bearish: price falling WITH TSQ rising (ΔTSQ ≥ frac·|NET|) → short bias
  Allow entry only when the supporting book side "allows" (TBQ for long, TSQ for short).
  Enter on pullback → resume in the bias direction (bull buy dip / bear sell rally).

Exits:
  SL = scientific expected fluctuation from recent tick mini-ranges
       (fixed 25 is unscientific if the period commonly swings ~40 and recovers)
  TP = range-based target OR stall (price fails to push a new extreme)
"""

from __future__ import annotations

import os
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from range_stops import expected_range, tp_sl_from_range
from strategy import Position, SignalResult

Bias = Literal["BULL", "BEAR", "NEUTRAL"]


def net_imbalance(tbq: float, tsq: float) -> tuple[float, float]:
    net = float(tbq) - float(tsq)
    denom = max(float(tbq), float(tsq), 1e-9)
    return net, abs(net) / denom * 100.0


@dataclass
class AlignS8Config:
    min_imb_pct: float = 10.0
    # |ΔTBQ| or |ΔTSQ| must be ≥ this fraction of |NET| to "allow"
    book_frac_of_net: float = 0.10
    pullback_points: float = 8.0
    resume_points: float = 5.0
    every_n_ticks: int = 1
    cooldown_ticks: int = 5
    # Scientific SL/TP from recent fluctuation (not fixed 25)
    use_range_stops: bool = True
    range_tick_window: int = 60  # ticks per mini-range
    range_lookback: int = 20  # median of last N mini-ranges
    tp_range_mult: float = 0.85
    sl_range_mult: float = 0.90  # SL ~ period noise so a 40pt dip isn't killed by 25
    tp_min: float = 15.0
    tp_max: float = 80.0
    sl_min: float = 15.0
    sl_max: float = 50.0
    range_fee_be: float = 0.0
    range_min_rr: float = 1.1
    # Fallbacks if range not warm
    tp_points: float = 40.0
    sl_points: float = 35.0
    # Stall TP: no new extreme for this many decide-ticks while in profit
    stall_ticks: int = 50
    stall_min_profit: float = 8.0
    # Tiny price eps so flat ticks don't flip signs
    price_eps: float = 0.5
    weaken_pct: float = 15.0  # exit if supporting book shrinks this % from entry


class AlignS8Strategy:
    """Price∩TBQ / Price∩TSQ alignment zigzag with range-based stops."""

    name = "S8_NET_ZIGZAG"  # keep portfolio / signal name

    def __init__(self, cfg: AlignS8Config | None = None) -> None:
        self.cfg = cfg or AlignS8Config()
        self.position: Position = "flat"
        self.bias: Bias = "NEUTRAL"
        self.last_net = 0.0
        self.last_imb = 0.0
        self.last_tbq = 0.0
        self.last_tsq = 0.0
        self.last_px = 0.0
        self.entry_price: float | None = None
        self.entry_tbq: float | None = None
        self.entry_tsq: float | None = None
        self.active_tp: float | None = None
        self.active_sl: float | None = None
        self.last_exp_range: float | None = None
        self.last_skip: str | None = None
        self.last_align: str = "NA"
        self.px_over_tbq: float | None = None
        self.px_over_tsq: float | None = None

        self._tick_i = 0
        self._cooldown_until = 0
        self._prev_px: float | None = None
        self._prev_tbq: float | None = None
        self._prev_tsq: float | None = None

        self._extreme: float | None = None
        self._pullback_ext: float | None = None
        self._in_pullback = False
        self._ticks_since_ext = 0

        # mini-range builder for scientific SL
        self._seg_h: float | None = None
        self._seg_l: float | None = None
        self._seg_n = 0
        self._ranges: deque[float] = deque(maxlen=max(64, self.cfg.range_lookback * 3))

        self.tbq_allows = False
        self.tsq_allows = False
        self.price_up = False
        self.price_down = False
        self._tbq_allow_age = 10**9
        self._tsq_allow_age = 10**9
        self.book_allow_memory = 30  # ticks: supporting book recently allowed

    @property
    def status_line(self) -> str:
        c = self.cfg
        tp = self.active_tp if self.active_tp is not None else c.tp_points
        sl = self.active_sl if self.active_sl is not None else c.sl_points
        rng = f" expR={self.last_exp_range:.0f}" if self.last_exp_range else ""
        return (
            f"TF=tick ALIGN imb>={c.min_imb_pct:.0f}% book≥{c.book_frac_of_net:.0%}·|NET| "
            f"TP={tp:.0f} SL={sl:.0f}~R{rng} "
            f"bias={self.bias} align={self.last_align} pos={self.position}"
        )

    def _parse_qty(self, message: dict[str, Any]) -> tuple[float, float] | None:
        tbq = message.get("total_buy_quantity")
        tsq = message.get("total_sell_quantity")
        if tbq is None or tsq is None:
            return None
        try:
            return float(tbq), float(tsq)
        except (TypeError, ValueError):
            return None

    def _push_range_tick(self, px: float) -> None:
        if self._seg_h is None:
            self._seg_h = self._seg_l = px
            self._seg_n = 1
            return
        self._seg_h = max(self._seg_h, px)
        self._seg_l = min(float(self._seg_l), px)
        self._seg_n += 1
        if self._seg_n >= max(5, self.cfg.range_tick_window):
            self._ranges.append(max(0.0, float(self._seg_h) - float(self._seg_l)))
            self._seg_h = self._seg_l = px
            self._seg_n = 0

    def _set_stops_from_range(self) -> None:
        c = self.cfg
        er = expected_range(list(self._ranges), c.range_lookback)
        self.last_exp_range = float(er) if er == er else None
        if c.use_range_stops and self.last_exp_range is not None:
            tp, sl = tp_sl_from_range(
                self.last_exp_range,
                tp_mult=c.tp_range_mult,
                sl_mult=c.sl_range_mult,
                tp_min=c.tp_min,
                tp_max=c.tp_max,
                sl_min=c.sl_min,
                sl_max=c.sl_max,
                fee_be=c.range_fee_be,
                min_rr=c.range_min_rr,
            )
            if tp == tp and sl == sl:
                self.active_tp = float(tp)
                self.active_sl = float(sl)
                return
        self.active_tp = float(c.tp_points)
        self.active_sl = float(c.sl_points)

    def _update_align(self, px: float, tbq: float, tsq: float) -> None:
        net, imb = net_imbalance(tbq, tsq)
        self.last_net, self.last_imb = net, imb
        self.last_tbq, self.last_tsq, self.last_px = tbq, tsq, px
        self.px_over_tbq = px / max(tbq, 1e-9)
        self.px_over_tsq = px / max(tsq, 1e-9)

        if self._prev_px is None:
            self._prev_px, self._prev_tbq, self._prev_tsq = px, tbq, tsq
            self.last_align = "warmup"
            return

        dpx = px - float(self._prev_px)
        dtbq = tbq - float(self._prev_tbq)
        dtsq = tsq - float(self._prev_tsq)
        abs_net = max(abs(net), 1e-9)
        thr = self.cfg.book_frac_of_net * abs_net

        self.price_up = dpx > self.cfg.price_eps
        self.price_down = dpx < -self.cfg.price_eps
        self.tbq_allows = dtbq >= thr and dtbq > 0
        self.tsq_allows = dtsq >= thr and dtsq > 0
        if self.tbq_allows:
            self._tbq_allow_age = 0
        else:
            self._tbq_allow_age += 1
        if self.tsq_allows:
            self._tsq_allow_age = 0
        else:
            self._tsq_allow_age += 1

        bull = self.price_up and self.tbq_allows
        bear = self.price_down and self.tsq_allows
        # conflict: price up but only TSQ swelling, or price down but only TBQ
        if bull and not bear:
            self.bias = "BULL"
            self.last_align = "px↑+TBQ↑"
        elif bear and not bull:
            self.bias = "BEAR"
            self.last_align = "px↓+TSQ↑"
        elif bull and bear:
            # both books + price noise — keep prior if IMB still strong
            self.last_align = "both"
            if imb < self.cfg.min_imb_pct:
                self.bias = "NEUTRAL"
        else:
            self.last_align = "flat"
            # sticky while IMB strong and net sign matches
            if imb < self.cfg.min_imb_pct:
                self.bias = "NEUTRAL"
            elif self.bias == "BULL" and net <= 0:
                self.bias = "NEUTRAL"
            elif self.bias == "BEAR" and net >= 0:
                self.bias = "NEUTRAL"

        self._prev_px, self._prev_tbq, self._prev_tsq = px, tbq, tsq

    def _update_pullback(self, px: float) -> None:
        if self.bias == "BULL":
            if self._extreme is None or px > self._extreme:
                self._extreme = px
                self._in_pullback = False
                self._pullback_ext = None
                self._ticks_since_ext = 0
            else:
                self._ticks_since_ext += 1
                if self._extreme - px >= self.cfg.pullback_points:
                    self._in_pullback = True
                    if self._pullback_ext is None or px < self._pullback_ext:
                        self._pullback_ext = px
        elif self.bias == "BEAR":
            if self._extreme is None or px < self._extreme:
                self._extreme = px
                self._in_pullback = False
                self._pullback_ext = None
                self._ticks_since_ext = 0
            else:
                self._ticks_since_ext += 1
                if px - self._extreme >= self.cfg.pullback_points:
                    self._in_pullback = True
                    if self._pullback_ext is None or px > self._pullback_ext:
                        self._pullback_ext = px
        else:
            self._extreme = px
            self._in_pullback = False
            self._pullback_ext = None
            self._ticks_since_ext = 0

    def _pullback_resume(self, px: float, side: Position) -> bool:
        if not self._in_pullback or self._pullback_ext is None:
            return False
        if side == "long":
            return (px - self._pullback_ext) >= self.cfg.resume_points
        return (self._pullback_ext - px) >= self.cfg.resume_points

    def _open(self, side: Position, px: float) -> None:
        self.position = side
        self.entry_price = float(px)
        self.entry_tbq = self.last_tbq
        self.entry_tsq = self.last_tsq
        self._extreme = float(px)
        self._pullback_ext = None
        self._in_pullback = False
        self._ticks_since_ext = 0
        self._set_stops_from_range()

    def _close(self) -> None:
        self.position = "flat"
        self.entry_price = None
        self.entry_tbq = None
        self.entry_tsq = None
        self.active_tp = None
        self.active_sl = None
        self._cooldown_until = self._tick_i + max(0, self.cfg.cooldown_ticks)
        self._in_pullback = False
        self._pullback_ext = None

    def _tp_sl(self) -> tuple[float, float]:
        c = self.cfg
        tp = self.active_tp if self.active_tp is not None else c.tp_points
        sl = self.active_sl if self.active_sl is not None else c.sl_points
        return float(tp), float(sl)

    def _manage(self, px: float) -> SignalResult | None:
        assert self.entry_price is not None
        ep = float(self.entry_price)
        side = self.position
        move = (px - ep) if side == "long" else (ep - px)
        tp, sl = self._tp_sl()

        # track extremes while in trade (for stall)
        if side == "long":
            if self._extreme is None or px > self._extreme:
                self._extreme = px
                self._ticks_since_ext = 0
            else:
                self._ticks_since_ext += 1
        else:
            if self._extreme is None or px < self._extreme:
                self._extreme = px
                self._ticks_since_ext = 0
            else:
                self._ticks_since_ext += 1

        def done(reason: str) -> SignalResult:
            self._close()
            return SignalResult(
                action="CLOSE",
                position_after="flat",
                price_delta=move,
                net=self.last_net,
                net_delta=None,
                prev_net_delta=None,
                reason=reason,
            )

        if side == "long" and self.bias == "BEAR" and self.tsq_allows:
            return done(f"align_flip_to_BEAR {self.last_align}")
        if side == "short" and self.bias == "BULL" and self.tbq_allows:
            return done(f"align_flip_to_BULL {self.last_align}")

        if side == "long" and self.entry_tbq and self.entry_tbq > 0:
            drop = (self.entry_tbq - self.last_tbq) / self.entry_tbq * 100.0
            if drop >= self.cfg.weaken_pct:
                return done(f"tbq_weaken {drop:.1f}%")
        if side == "short" and self.entry_tsq and self.entry_tsq > 0:
            drop = (self.entry_tsq - self.last_tsq) / self.entry_tsq * 100.0
            if drop >= self.cfg.weaken_pct:
                return done(f"tsq_weaken {drop:.1f}%")

        if move <= -sl:
            return done(f"sl {move:.1f}<=-{sl:.0f} expR={self.last_exp_range}")
        if move >= tp:
            return done(f"tp +{move:.1f}>={tp:.0f} expR={self.last_exp_range}")
        # Stall: price won't push further — take profit if already ahead
        if (
            move >= self.cfg.stall_min_profit
            and self._ticks_since_ext >= self.cfg.stall_ticks
        ):
            return done(
                f"stall_tp +{move:.1f} no_ext×{self._ticks_since_ext} "
                f"align={self.last_align}"
            )
        return None

    def _try_enter(self, px: float) -> SignalResult | None:
        if self._tick_i < self._cooldown_until:
            self.last_skip = "cooldown"
            return None
        if self.last_imb < self.cfg.min_imb_pct:
            self.last_skip = "imb_soft"
            return None

        # Bull: long when TBQ recently/now allows + pullback resumed up
        if self.bias == "BULL" and self.last_net > 0:
            tbq_ok = self._tbq_allow_age <= self.book_allow_memory
            if not tbq_ok:
                self.last_skip = "tbq_not_allow"
                return None
            if not self._pullback_resume(px, "long"):
                self.last_skip = "wait_bull_pullback"
                return None
            self._open("long", px)
            tp, sl = self._tp_sl()
            return SignalResult(
                action="BUY",
                position_after="long",
                price_delta=None,
                net=self.last_net,
                net_delta=None,
                prev_net_delta=None,
                reason=(
                    f"align_long pullback_resume {self.last_align} "
                    f"net={self.last_net:.0f} imb={self.last_imb:.1f}% "
                    f"TP={tp:.0f} SL={sl:.0f} expR={self.last_exp_range}"
                ),
            )

        # Bear: short when TSQ recently/now allows + rally resumed down
        if self.bias == "BEAR" and self.last_net < 0:
            tsq_ok = self._tsq_allow_age <= self.book_allow_memory
            if not tsq_ok:
                self.last_skip = "tsq_not_allow"
                return None
            if not self._pullback_resume(px, "short"):
                self.last_skip = "wait_bear_pullback"
                return None
            self._open("short", px)
            tp, sl = self._tp_sl()
            return SignalResult(
                action="SHORT",
                position_after="short",
                price_delta=None,
                net=self.last_net,
                net_delta=None,
                prev_net_delta=None,
                reason=(
                    f"align_short pullback_resume {self.last_align} "
                    f"net={self.last_net:.0f} imb={self.last_imb:.1f}% "
                    f"TP={tp:.0f} SL={sl:.0f} expR={self.last_exp_range}"
                ),
            )

        self.last_skip = f"wait bias={self.bias}"
        return None

    def on_tick(
        self, now: datetime, ltp: float, message: dict[str, Any]
    ) -> SignalResult | None:
        qs = self._parse_qty(message)
        if qs is None:
            self.last_skip = "no_tbq_tsq"
            return None
        tbq, tsq = qs
        px = float(ltp)
        self._tick_i += 1
        self._push_range_tick(px)
        self._update_align(px, tbq, tsq)
        self._update_pullback(px)

        if self._tick_i % max(1, self.cfg.every_n_ticks) != 0:
            return None

        if self.position != "flat" and self.entry_price is not None:
            return self._manage(px)
        return self._try_enter(px)


def align_s8_from_env() -> AlignS8Strategy:
    try:
        from dotenv import load_dotenv

        load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
    except Exception:
        pass

    def _f(name: str, default: float) -> float:
        return float(os.getenv(name, str(default)))

    def _b(name: str, default: bool) -> bool:
        raw = os.getenv(name)
        if raw is None:
            return default
        return raw.strip().lower() in {"1", "true", "yes", "y"}

    cfg = AlignS8Config(
        min_imb_pct=_f("S8_MIN_IMB_PCT", 10.0),
        book_frac_of_net=_f("S8_BOOK_FRAC_OF_NET", 0.10),
        pullback_points=_f("S8_PULLBACK_POINTS", 8.0),
        resume_points=_f("S8_RESUME_POINTS", 5.0),
        every_n_ticks=int(_f("S8_EVERY_N_TICKS", 1)),
        cooldown_ticks=int(_f("S8_COOLDOWN_TICKS", 5)),
        use_range_stops=_b("S8_USE_RANGE_STOPS", True),
        range_tick_window=int(_f("S8_RANGE_TICK_WINDOW", 60)),
        range_lookback=int(_f("S8_RANGE_LOOKBACK", 20)),
        tp_range_mult=_f("S8_TP_RANGE_MULT", 0.85),
        sl_range_mult=_f("S8_SL_RANGE_MULT", 0.90),
        tp_min=_f("S8_TP_MIN", 15.0),
        tp_max=_f("S8_TP_MAX", 80.0),
        sl_min=_f("S8_SL_MIN", 15.0),
        sl_max=_f("S8_SL_MAX", 50.0),
        range_fee_be=_f("S8_RANGE_FEE_BE", 0.0),
        tp_points=_f("S8_TP_POINTS", 40.0),
        sl_points=_f("S8_SL_POINTS", 35.0),
        stall_ticks=int(_f("S8_STALL_TICKS", 50)),
        stall_min_profit=_f("S8_STALL_MIN_PROFIT", 8.0),
        weaken_pct=_f("S8_WEAKEN_PCT", 15.0),
    )
    return AlignS8Strategy(cfg)
