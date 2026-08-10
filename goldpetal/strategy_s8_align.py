"""S8_ALIGN — price ∩ TBQ ∩ TSQ individual + combined behaviour.

Entry (unchanged idea):
  Bull: price↑ WITH TBQ↑ (ΔTBQ ≥ frac·|NET|) → long on pullback-resume
  Bear: price↓ WITH TSQ↑ → short on pullback-resume

SL / TP (book-driven — NOT bare price range):
  Individually track Δprice, ΔTBQ, ΔTSQ and ratios price/TBQ, price/TSQ.
  Combined:
    widen_bull  = price↑ + TBQ↑   (support expanding with price)
    dip_supported = price↓ + TBQ↑ (natural pullback under demand — allow deeper SL)
    break_bull  = price↓ + TSQ↑ or TBQ compress (real stop)
    same mirrored for shorts.
  SL = typical adverse depth *while support held* (scientific fluctuation under book).
  TP = typical impulse *while aligned*, or combined stall (price stops + book stops expanding).
"""

from __future__ import annotations

import os
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

from range_stops import clamp, median
from strategy import Position, SignalResult

Bias = Literal["BULL", "BEAR", "NEUTRAL"]
IST = ZoneInfo("Asia/Kolkata")


def net_imbalance(tbq: float, tsq: float) -> tuple[float, float]:
    net = float(tbq) - float(tsq)
    denom = max(float(tbq), float(tsq), 1e-9)
    return net, abs(net) / denom * 100.0


@dataclass
class AlignS8Config:
    min_imb_pct: float = 10.0
    book_frac_of_net: float = 0.10
    pullback_points: float = 8.0
    resume_points: float = 5.0
    every_n_ticks: int = 1
    cooldown_ticks: int = 5
    price_eps: float = 0.5
    # Book-driven SL/TP bounds
    sl_min: float = 20.0
    sl_max: float = 55.0
    tp_min: float = 20.0
    tp_max: float = 80.0
    # Fallback if behaviour history empty
    tp_points: float = 40.0
    sl_points: float = 35.0
    # Memory of supported swings
    behaviour_window: int = 30
    # Combined stall: no new extreme + supporting book not expanding
    stall_bars: int = 3
    stall_min_profit: float = 20.0
    # Hard weaken exit if supporting book drops this % from entry
    weaken_pct: float = 20.0
    # Ratio overextension: price/TBQ rising while TBQ flat → take TP sooner
    ratio_overext_pct: float = 0.15  # 15% jump in px/tbq without TBQ expand


class AlignS8Strategy:
    name = "S8_NET_ZIGZAG"

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
        self.last_skip: str | None = None
        self.last_align: str = "NA"
        self.last_combo: str = "NA"
        self.px_over_tbq: float | None = None
        self.px_over_tsq: float | None = None

        self._tick_i = 0
        self._cooldown_until = 0
        self._prev_px: float | None = None
        self._prev_tbq: float | None = None
        self._prev_tsq: float | None = None
        self._prev_r_bt: float | None = None
        self._prev_r_st: float | None = None

        self._extreme: float | None = None
        self._pullback_ext: float | None = None
        self._in_pullback = False
        self._bars_since_ext = 0
        self._book_expand_since_ext = False

        # Behaviour memory (pts)
        w = max(8, self.cfg.behaviour_window)
        self._adverse_supported: deque[float] = deque(maxlen=w)  # dips under TBQ support
        self._impulse_aligned: deque[float] = deque(maxlen=w)  # runs under align
        self._run_anchor: float | None = None  # start of current supported move
        self._dip_anchor: float | None = None  # start of current supported dip

        # Individual flags (latest step)
        self.price_up = self.price_down = False
        self.tbq_expand = self.tbq_compress = False
        self.tsq_expand = self.tsq_compress = False
        self.widen_bull = self.widen_bear = False
        self.dip_supported = self.rally_supported = False
        self.break_bull = self.break_bear = False
        self.tbq_allows = self.tsq_allows = False
        self._tbq_allow_age = 10**9
        self._tsq_allow_age = 10**9
        self.book_allow_memory = 30

    @property
    def status_line(self) -> str:
        c = self.cfg
        tp = self.active_tp if self.active_tp is not None else c.tp_points
        sl = self.active_sl if self.active_sl is not None else c.sl_points
        return (
            f"TF=tick ALIGN-BOOK imb>={c.min_imb_pct:.0f}% "
            f"TP={tp:.0f} SL={sl:.0f} combo={self.last_combo} "
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

    def _update_behaviour(self, px: float, tbq: float, tsq: float) -> None:
        net, imb = net_imbalance(tbq, tsq)
        self.last_net, self.last_imb = net, imb
        self.last_tbq, self.last_tsq, self.last_px = tbq, tsq, px
        r_bt = px / max(tbq, 1e-9)
        r_st = px / max(tsq, 1e-9)
        self.px_over_tbq, self.px_over_tsq = r_bt, r_st

        if self._prev_px is None:
            self._prev_px, self._prev_tbq, self._prev_tsq = px, tbq, tsq
            self._prev_r_bt, self._prev_r_st = r_bt, r_st
            self.last_align = "warmup"
            self.last_combo = "warmup"
            return

        dpx = px - float(self._prev_px)
        dtbq = tbq - float(self._prev_tbq)
        dtsq = tsq - float(self._prev_tsq)
        abs_net = max(abs(net), 1e-9)
        thr = self.cfg.book_frac_of_net * abs_net

        # --- individual ---
        self.price_up = dpx > self.cfg.price_eps
        self.price_down = dpx < -self.cfg.price_eps
        self.tbq_expand = dtbq >= thr and dtbq > 0
        self.tbq_compress = dtbq <= -thr
        self.tsq_expand = dtsq >= thr and dtsq > 0
        self.tsq_compress = dtsq <= -thr
        self.tbq_allows = self.tbq_expand
        self.tsq_allows = self.tsq_expand
        if self.tbq_allows:
            self._tbq_allow_age = 0
        else:
            self._tbq_allow_age += 1
        if self.tsq_allows:
            self._tsq_allow_age = 0
        else:
            self._tsq_allow_age += 1

        # --- combined ---
        self.widen_bull = self.price_up and self.tbq_expand
        self.widen_bear = self.price_down and self.tsq_expand
        self.dip_supported = self.price_down and self.tbq_expand  # pullback under demand
        self.rally_supported = self.price_up and self.tsq_expand  # short squeeze risk / bear pullback
        self.break_bull = self.price_down and (self.tsq_expand or self.tbq_compress)
        self.break_bear = self.price_up and (self.tbq_expand or self.tsq_compress)

        if self.widen_bull:
            self.last_combo = "widen_px↑TBQ↑"
        elif self.dip_supported:
            self.last_combo = "dip_supported"
        elif self.break_bull:
            self.last_combo = "break_bull"
        elif self.widen_bear:
            self.last_combo = "widen_px↓TSQ↑"
        elif self.rally_supported:
            self.last_combo = "rally_vs_TSQ"
        elif self.break_bear:
            self.last_combo = "break_bear"
        else:
            self.last_combo = "flat"

        # Bias from align
        if self.widen_bull and not self.widen_bear:
            self.bias = "BULL"
            self.last_align = "px↑+TBQ↑"
        elif self.widen_bear and not self.widen_bull:
            self.bias = "BEAR"
            self.last_align = "px↓+TSQ↑"
        elif imb < self.cfg.min_imb_pct:
            self.bias = "NEUTRAL"
            self.last_align = "flat"
        elif self.bias == "BULL" and net <= 0:
            self.bias = "NEUTRAL"
            self.last_align = "flat"
        elif self.bias == "BEAR" and net >= 0:
            self.bias = "NEUTRAL"
            self.last_align = "flat"
        else:
            self.last_align = self.last_align if self.last_align != "warmup" else "flat"

        # --- learn scientific swings under support ---
        if self.widen_bull:
            if self._run_anchor is None:
                self._run_anchor = float(self._prev_px)
            if self._dip_anchor is not None:
                depth = float(self._prev_px) - float(self._dip_anchor)
                if depth >= 3:
                    self._adverse_supported.append(depth)
                self._dip_anchor = None
        elif self.dip_supported or (self.price_down and self._tbq_allow_age <= self.book_allow_memory):
            if self._dip_anchor is None:
                self._dip_anchor = float(self._prev_px)
            if self._run_anchor is not None:
                impulse = float(self._prev_px) - float(self._run_anchor)
                if impulse >= 3:
                    self._impulse_aligned.append(impulse)
                self._run_anchor = None
        elif self.break_bull:
            if self._run_anchor is not None:
                impulse = float(self._prev_px) - float(self._run_anchor)
                if impulse >= 3:
                    self._impulse_aligned.append(impulse)
            self._run_anchor = None
            self._dip_anchor = None

        if self.widen_bear:
            if self._run_anchor is None:
                self._run_anchor = float(self._prev_px)
            if self._dip_anchor is not None:  # rally against short = adverse for short
                depth = float(self._dip_anchor) - float(self._prev_px)
                if depth >= 3:
                    self._adverse_supported.append(depth)
                self._dip_anchor = None
        elif self.rally_supported or (self.price_up and self._tsq_allow_age <= self.book_allow_memory):
            if self.bias == "BEAR" or self.widen_bear or self.last_align == "px↓+TSQ↑":
                if self._dip_anchor is None:
                    self._dip_anchor = float(self._prev_px)
                if self._run_anchor is not None:
                    impulse = float(self._run_anchor) - float(self._prev_px)
                    if impulse >= 3:
                        self._impulse_aligned.append(impulse)
                    self._run_anchor = None

        self._prev_px, self._prev_tbq, self._prev_tsq = px, tbq, tsq
        self._prev_r_bt, self._prev_r_st = r_bt, r_st

    def _book_stops(self) -> tuple[float, float]:
        """SL/TP from supported adverse/impulse history (price∩book)."""
        c = self.cfg
        adv = median(list(self._adverse_supported)) if self._adverse_supported else float("nan")
        imp = median(list(self._impulse_aligned)) if self._impulse_aligned else float("nan")
        if adv == adv and adv > 0:
            sl = clamp(float(adv), c.sl_min, c.sl_max)
        else:
            sl = float(c.sl_points)
        if imp == imp and imp > 0:
            tp = clamp(float(imp), c.tp_min, c.tp_max)
        else:
            tp = float(c.tp_points)
        if tp < 1.1 * sl:
            tp = clamp(1.1 * sl, c.tp_min, c.tp_max)
        return round(tp, 1), round(sl, 1)

    def _retune_stops_in_trade(self, px: float, side: Position) -> None:
        """Widen SL if dip is book-supported; tighten/exit signals via manage."""
        assert self.entry_price is not None
        tp, sl = self._book_stops()
        ep = float(self.entry_price)
        adverse = (ep - px) if side == "long" else (px - ep)

        if side == "long":
            if self.dip_supported or (self.price_down and self._tbq_allow_age <= self.book_allow_memory):
                # natural fluctuation under demand — allow at least this adverse
                sl = clamp(max(sl, adverse * 1.05, float(self.active_sl or sl)), self.cfg.sl_min, self.cfg.sl_max)
            if self.widen_bull:
                # trend continuing — keep/raise TP toward fresh impulse
                tp = max(tp, float(self.active_tp or tp))
            # overextension: px/tbq jumped without TBQ expand
            if (
                self._prev_r_bt is not None
                and self.px_over_tbq is not None
                and not self.tbq_expand
                and self.price_up
            ):
                jump = (self.px_over_tbq - float(self._prev_r_bt)) / max(float(self._prev_r_bt), 1e-12)
                if jump >= self.cfg.ratio_overext_pct:
                    # pull TP down to current open profit floor
                    move = px - ep
                    if move >= self.cfg.stall_min_profit:
                        tp = min(tp, max(self.cfg.stall_min_profit, move))
        else:
            if self.rally_supported or (self.price_up and self._tsq_allow_age <= self.book_allow_memory):
                sl = clamp(max(sl, adverse * 1.05, float(self.active_sl or sl)), self.cfg.sl_min, self.cfg.sl_max)
            if self.widen_bear:
                tp = max(tp, float(self.active_tp or tp))

        self.active_tp, self.active_sl = float(tp), float(sl)

    def _update_pullback(self, px: float) -> None:
        if self.bias == "BULL":
            if self._extreme is None or px > self._extreme:
                self._extreme = px
                self._in_pullback = False
                self._pullback_ext = None
                self._bars_since_ext = 0
                self._book_expand_since_ext = False
            else:
                self._bars_since_ext += 1
                if self.tbq_expand:
                    self._book_expand_since_ext = True
                if self._extreme - px >= self.cfg.pullback_points:
                    self._in_pullback = True
                    if self._pullback_ext is None or px < self._pullback_ext:
                        self._pullback_ext = px
        elif self.bias == "BEAR":
            if self._extreme is None or px < self._extreme:
                self._extreme = px
                self._in_pullback = False
                self._pullback_ext = None
                self._bars_since_ext = 0
                self._book_expand_since_ext = False
            else:
                self._bars_since_ext += 1
                if self.tsq_expand:
                    self._book_expand_since_ext = True
                if px - self._extreme >= self.cfg.pullback_points:
                    self._in_pullback = True
                    if self._pullback_ext is None or px > self._pullback_ext:
                        self._pullback_ext = px
        else:
            self._extreme = px
            self._in_pullback = False
            self._pullback_ext = None
            self._bars_since_ext = 0
            self._book_expand_since_ext = False

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
        self._bars_since_ext = 0
        self._book_expand_since_ext = False
        tp, sl = self._book_stops()
        self.active_tp, self.active_sl = tp, sl

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
        self._retune_stops_in_trade(px, side)
        tp, sl = self._tp_sl()

        if side == "long":
            if self._extreme is None or px > self._extreme:
                self._extreme = px
                self._bars_since_ext = 0
                self._book_expand_since_ext = self.tbq_expand
            else:
                self._bars_since_ext += 1
                if self.tbq_expand:
                    self._book_expand_since_ext = True
        else:
            if self._extreme is None or px < self._extreme:
                self._extreme = px
                self._bars_since_ext = 0
                self._book_expand_since_ext = self.tsq_expand
            else:
                self._bars_since_ext += 1
                if self.tsq_expand:
                    self._book_expand_since_ext = True

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

        # Combined break = stop (book says trend failed)
        if side == "long" and self.break_bull:
            return done(f"book_break_bull {self.last_combo} move={move:.1f}")
        if side == "short" and self.break_bear:
            return done(f"book_break_bear {self.last_combo} move={move:.1f}")

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

        # Hard SL only if adverse beyond scientific supported depth
        # If currently dip_supported, skip hard SL (book still buying the dip)
        if move <= -sl:
            if side == "long" and self.dip_supported:
                pass  # allow supported fluctuation
            elif side == "short" and self.rally_supported:
                pass
            else:
                return done(
                    f"sl {move:.1f}<=-{sl:.0f} combo={self.last_combo} "
                    f"advN={len(self._adverse_supported)}"
                )

        if move >= tp:
            return done(
                f"tp +{move:.1f}>={tp:.0f} combo={self.last_combo} "
                f"impN={len(self._impulse_aligned)}"
            )

        # Combined stall TP: price not making extreme AND supporting book not expanding
        if (
            move >= self.cfg.stall_min_profit
            and self._bars_since_ext >= self.cfg.stall_bars
            and not self._book_expand_since_ext
        ):
            return done(
                f"stall_book_tp +{move:.1f} no_ext×{self._bars_since_ext} "
                f"combo={self.last_combo}"
            )
        return None

    def _try_enter(self, px: float) -> SignalResult | None:
        if self._tick_i < self._cooldown_until:
            self.last_skip = "cooldown"
            return None
        if self.last_imb < self.cfg.min_imb_pct:
            self.last_skip = "imb_soft"
            return None

        if self.bias == "BULL" and self.last_net > 0:
            if self._tbq_allow_age > self.book_allow_memory:
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
                    f"align_long pullback {self.last_align}/{self.last_combo} "
                    f"net={self.last_net:.0f} imb={self.last_imb:.1f}% "
                    f"TP={tp:.0f} SL={sl:.0f} "
                    f"advN={len(self._adverse_supported)} impN={len(self._impulse_aligned)}"
                ),
            )

        if self.bias == "BEAR" and self.last_net < 0:
            if self._tsq_allow_age > self.book_allow_memory:
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
                    f"align_short pullback {self.last_align}/{self.last_combo} "
                    f"net={self.last_net:.0f} imb={self.last_imb:.1f}% "
                    f"TP={tp:.0f} SL={sl:.0f} "
                    f"advN={len(self._adverse_supported)} impN={len(self._impulse_aligned)}"
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
        self._update_behaviour(px, tbq, tsq)
        self._update_pullback(px)
        if self._tick_i % max(1, self.cfg.every_n_ticks) != 0:
            return None
        if self.position != "flat" and self.entry_price is not None:
            return self._manage(px)
        return self._try_enter(px)

    def on_bar_row(self, row: dict[str, Any]) -> SignalResult | None:
        """MTF path: one decision per bar close (same behaviour math)."""
        try:
            ts = datetime.strptime(str(row["time"]), "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=IST
            )
        except Exception:
            ts = datetime.now(IST)
        msg = {
            "total_buy_quantity": row.get("tbq_close", row.get("tbq", 0)),
            "total_sell_quantity": row.get("tsq_close", row.get("tsq", 0)),
        }
        # Use close as decision price; behaviour uses bar-to-bar deltas via prev state
        return self.on_tick(ts, float(row["close"]), msg)


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
        sl_min=_f("S8_SL_MIN", 20.0),
        sl_max=_f("S8_SL_MAX", 55.0),
        tp_min=_f("S8_TP_MIN", 20.0),
        tp_max=_f("S8_TP_MAX", 80.0),
        tp_points=_f("S8_TP_POINTS", 40.0),
        sl_points=_f("S8_SL_POINTS", 35.0),
        behaviour_window=int(_f("S8_BEHAVIOUR_WINDOW", 30)),
        stall_bars=int(_f("S8_STALL_BARS", 3)),
        stall_min_profit=_f("S8_STALL_MIN_PROFIT", 20.0),
        weaken_pct=_f("S8_WEAKEN_PCT", 20.0),
        ratio_overext_pct=_f("S8_RATIO_OVEREXT_PCT", 0.15),
    )
    _ = _b  # reserved
    return AlignS8Strategy(cfg)
