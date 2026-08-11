"""S8_ALIGN — price ∩ TBQ ∩ TSQ with separable entry / hold / exit reasoning models.

Entry models (S8_ENTRY_MODEL):
  imb_sign_rise  NET>0 buy / NET<0 short + abs(IMB)_now > abs(IMB)_prev   [default]
  imb_sign       NET sign only (no rising-IMB gate)
  align_widen    bias BULL/BEAR from px∩book widen + NET sign

Hold models (S8_HOLD_MODEL):
  book_rise         TBQ↑ long / TSQ↑ short → HOLD                       [default]
  book_or_support   rising book OR dip/rally supported → HOLD
  off               never hold (exits free to fire)

Exit models (S8_EXIT_MODEL):
  fat_tp_flip    book_drop + gated break/flip + fat TP @ 50t            [default]
  flip_gate_2m   same gates on 2m bars
  hold_fat_flip  stricter break/flip + fatter TP @ 50t
  book_drop      close mainly on TBQ/TSQ drop vs prev
  break_flip     gated break+flip, no fat TP floor

Bundle S8_MODEL=fat_tp_flip still sets exit TF + defaults; override with
S8_ENTRY_MODEL / S8_HOLD_MODEL / S8_EXIT_MODEL.
"""

from __future__ import annotations

import json
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
    min_imb_pct: float = 3.0
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
    # Cap per-step book threshold as fraction of max(TBQ,TSQ).
    book_thr_cap_pct: float = 0.002  # 0.2% of larger book side per step
    # --- stabilize exits ---
    break_min_bars: int = 2
    break_min_adverse: float = 8.0
    break_need_both: bool = False
    break_persist: int = 1
    break_skip_if_supported: bool = True
    break_price_min: float = 1.5
    prefer_fat_tp: bool = False
    fat_tp_min: float = 35.0
    fat_stall_min: float = 30.0
    flip_min_bars: int = 2
    flip_min_adverse: float = 8.0
    flip_block_in_profit: bool = True
    flip_persist: int = 1
    flip_need_widen: bool = False
    bar_minutes: int = 0
    bar_ticks: int = 0
    model_name: str = "align"
    # Reasoning-model names (entry / hold / exit)
    entry_model: str = "imb_sign_rise"
    hold_model: str = "book_rise"
    exit_model: str = "fat_tp_flip"
    # entry_mode: net_sign | align_widen
    entry_mode: str = "net_sign"
    loss_lock_after: int = 5
    loss_lock_cool_bars: int = 10
    require_pullback: bool = False
    require_rising_imb: bool = True
    require_rising_book: bool = False
    hold_while_book_rises: bool = True
    # Also hold while dip_supported (long) / rally_supported (short)
    hold_on_supported: bool = False
    close_on_book_drop: bool = True
    # Ignore tiny TBQ/TSQ noise: require drop >= this % of previous step
    book_drop_min_pct: float = 0.25
    # Require supporting-book drop for this many consecutive steps
    book_drop_persist: int = 1
    # Do NOT book_drop-exit while open P&L >= this (ride trends; default 25pt)
    protect_profit_pts: float = 25.0
    # Optional weekly MLP neural-net entry gate (train_s8_nn_weekly.py)
    require_nn_filter: bool = False
    nn_model_path: str = "data/models/s8_nn_mlp.joblib"
    nn_min_proba: float = 0.55
    nn_lags: int = 3
    # Multi-step reasoner (math/logic/science/planning) — s8_reasoner.py
    require_reasoning: bool = False
    reasoning_min_score: float = 0.45
    reasoning_lots: float = 100.0


class AlignS8Strategy:
    name = "S8_NET_ZIGZAG"

    def __init__(self, cfg: AlignS8Config | None = None) -> None:
        self.cfg = cfg or AlignS8Config()
        self.position: Position = "flat"
        self.bias: Bias = "NEUTRAL"
        self.last_net = 0.0
        self.last_imb = 0.0
        self.prev_imb = 0.0
        self.last_tbq = 0.0
        self.last_tsq = 0.0
        self.last_px = 0.0
        self.tbq_rising = False
        self.tsq_rising = False
        self.tbq_falling = False
        self.tsq_falling = False
        self.imb_rising = False
        self.entry_price: float | None = None
        self.entry_tbq: float | None = None
        self.entry_tsq: float | None = None
        self.active_tp: float | None = None
        self.active_sl: float | None = None
        self.last_skip: str | None = None
        self.last_align: str = "NA"
        self.last_combo: str = "NA"
        self.last_hold_reason: str | None = None
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
        self._bars_in_trade = 0
        self._break_streak = 0
        self._flip_streak = 0
        self._book_drop_streak = 0
        self._cmp_prev_tbq = 0.0
        self._cmp_prev_tsq = 0.0
        # Need ≥25 steps for MLP rolling features (vol_ratio / net_z)
        self._feat_hist: deque[dict[str, float]] = deque(maxlen=120)
        self._nn_bundle: Any = None
        self.last_nn_proba: float | None = None
        self.nn_skip_count = 0
        self.last_reasoning: Any = None
        self.reasoning_skip_count = 0
        # time / count bar accumulators
        self._bar_key: datetime | None = None
        self._bar_c: float | None = None
        self._bar_tbq = 0.0
        self._bar_tsq = 0.0
        self._bar_n = 0
        self._count_n = 0
        self._loss_streak = 0
        self._loss_locked = False
        self._loss_lock_until = 0

    @property
    def status_line(self) -> str:
        c = self.cfg
        tp = self.active_tp if self.active_tp is not None else c.tp_points
        sl = self.active_sl if self.active_sl is not None else c.sl_points
        if c.bar_ticks and c.bar_ticks > 0:
            tf = f"{c.bar_ticks}t"
        elif c.bar_minutes and c.bar_minutes > 0:
            tf = f"{c.bar_minutes}m"
        else:
            tf = "tick"
        lock = (
            f" LOCK({self._loss_streak}/{c.loss_lock_after})"
            if self._loss_locked
            else f" loss={self._loss_streak}/{c.loss_lock_after}"
        )
        rz = " RZ" if c.require_reasoning else ""
        nn = " NN" if c.require_nn_filter else ""
        return (
            f"TF={tf} ALIGN[{c.model_name}] "
            f"E={c.entry_model} H={c.hold_model} X={c.exit_model} "
            f"imb>={c.min_imb_pct:.0f}% TP={tp:.0f} SL={sl:.0f} "
            f"combo={self.last_combo} bias={self.bias} align={self.last_align} "
            f"pos={self.position}{lock}{nn}{rz}"
        )

    def reasoning_line(self) -> str:
        """Human-readable active reasoning models + last multi-step trace."""
        c = self.cfg
        base = (
            f"entry[{c.entry_model}/{c.entry_mode}] "
            f"hold[{c.hold_model}] exit[{c.exit_model}] "
            f"skip={self.last_skip or '-'} hold={self.last_hold_reason or '-'}"
        )
        if self.last_reasoning is not None:
            try:
                return base + " | " + self.last_reasoning.line()
            except Exception:
                return base
        return base

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
        # compare to previous step before overwriting
        self.prev_imb = float(self.last_imb)
        prev_tbq = float(self._prev_tbq) if self._prev_tbq is not None else float(tbq)
        prev_tsq = float(self._prev_tsq) if self._prev_tsq is not None else float(tsq)
        self._cmp_prev_tbq = prev_tbq
        self._cmp_prev_tsq = prev_tsq
        self.imb_rising = imb > self.prev_imb
        self.tbq_rising = float(tbq) > prev_tbq
        self.tsq_rising = float(tsq) > prev_tsq
        self.tbq_falling = float(tbq) < prev_tbq
        self.tsq_falling = float(tsq) < prev_tsq
        self.last_net, self.last_imb = net, imb
        self.last_tbq, self.last_tsq, self.last_px = tbq, tsq, px
        r_bt = px / max(tbq, 1e-9)
        r_st = px / max(tsq, 1e-9)
        self.px_over_tbq, self.px_over_tsq = r_bt, r_st

        if self._prev_px is None:
            self._prev_px, self._prev_tbq, self._prev_tsq = px, tbq, tsq
            self._prev_r_bt, self._prev_r_st = r_bt, r_st
            # No real previous step yet — do not treat as rising (avoids imb>0 vs 0 entry)
            self.imb_rising = False
            self.tbq_rising = False
            self.tsq_rising = False
            self.tbq_falling = False
            self.tsq_falling = False
            self.last_align = "warmup"
            self.last_combo = "warmup"
            return

        dpx = px - float(self._prev_px)
        dtbq = tbq - float(self._prev_tbq)
        dtsq = tsq - float(self._prev_tsq)
        abs_net = max(abs(net), 1e-9)
        thr_net = self.cfg.book_frac_of_net * abs_net
        thr_cap = max(1.0, self.cfg.book_thr_cap_pct * max(tbq, tsq, 1.0))
        thr = max(1.0, min(thr_net, thr_cap))

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
        # Break uses a slightly larger price move than entry noise (break_price_min)
        br_eps = max(self.cfg.price_eps, float(self.cfg.break_price_min))
        px_down_br = dpx < -br_eps
        px_up_br = dpx > br_eps
        if self.cfg.break_need_both:
            self.break_bull = px_down_br and self.tsq_expand and self.tbq_compress
            self.break_bear = px_up_br and self.tbq_expand and self.tsq_compress
        else:
            self.break_bull = px_down_br and (self.tsq_expand or self.tbq_compress)
            self.break_bear = px_up_br and (self.tbq_expand or self.tsq_compress)
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
        self._feat_hist.append(
            {
                "px": float(px),
                "tbq": float(tbq),
                "tsq": float(tsq),
                "imb": float(imb),
                "net": float(net),
            }
        )

    def _nn_allows_entry(self) -> bool:
        """Optional MLP gate from weekly trainer."""
        if not self.cfg.require_nn_filter:
            return True
        try:
            from s8_nn import features_from_hist, load_bundle, predict_edge_proba
        except Exception as exc:  # pragma: no cover
            self.last_skip = f"nn_import_error {exc}"
            return not self.cfg.require_nn_filter
        if self._nn_bundle is None:
            path = self.cfg.nn_model_path
            try:
                self._nn_bundle = load_bundle(path)
            except Exception as exc:
                self.last_skip = f"nn_missing {path}: {exc}"
                return False
        feats = features_from_hist(
            list(self._feat_hist), lags=max(1, int(self.cfg.nn_lags))
        )
        if feats is None:
            self.last_skip = "nn_warmup"
            self.last_nn_proba = None
            return False
        proba = predict_edge_proba(self._nn_bundle, feats)
        self.last_nn_proba = proba
        if proba < float(self.cfg.nn_min_proba):
            self.nn_skip_count += 1
            self.last_skip = (
                f"nn_low_proba {proba:.2f}<{float(self.cfg.nn_min_proba):.2f}"
            )
            return False
        return True

    def _reasoning_allows_entry(self, side: str) -> bool:
        """Optional multi-step math/logic/science/planning gate."""
        if not self.cfg.require_reasoning:
            return True
        try:
            from s8_reasoner import reason_entry
        except Exception as exc:  # pragma: no cover
            self.last_skip = f"reasoning_import_error {exc}"
            return False
        tp, sl = self._tp_sl()
        entry_p = self.last_nn_proba
        hold_p = exit_p = None
        if self.cfg.require_nn_filter and self._nn_bundle is not None:
            try:
                from s8_ml_pipeline import predict_reasoning
                from s8_nn import features_from_hist

                feats = features_from_hist(
                    list(self._feat_hist), lags=max(1, int(self.cfg.nn_lags))
                )
                if feats is not None:
                    scores = predict_reasoning(self._nn_bundle, feats)
                    entry_p = scores.get("entry_edge")
                    hold_p = scores.get("hold_ok")
                    exit_p = scores.get("exit_soon")
                    if entry_p is not None:
                        self.last_nn_proba = float(entry_p)
            except Exception:
                pass
        trace = reason_entry(
            px=float(self.last_px or 0.0),
            net=float(self.last_net),
            imb=float(self.last_imb),
            prev_imb=float(self.prev_imb),
            tp=float(tp),
            sl=float(sl),
            min_imb=float(self.cfg.min_imb_pct),
            imb_rising=bool(self.imb_rising),
            require_rising_imb=bool(self.cfg.require_rising_imb),
            tbq_rising=bool(self.tbq_rising),
            tsq_rising=bool(self.tsq_rising),
            loss_locked=bool(self._loss_locked),
            in_cooldown=self._tick_i < self._cooldown_until,
            lots=float(self.cfg.reasoning_lots),
            entry_proba=entry_p,
            hold_proba=hold_p,
            exit_soon_proba=exit_p,
            min_entry_proba=float(self.cfg.nn_min_proba),
        )
        self.last_reasoning = trace
        want = "ENTER_LONG" if side == "long" else "ENTER_SHORT"
        if trace.action != want or trace.score < float(self.cfg.reasoning_min_score):
            self.reasoning_skip_count += 1
            self.last_skip = f"reasoning:{trace.summary}"
            return False
        return True

    def _book_stops(self) -> tuple[float, float]:
        """SL/TP from supported adverse/impulse history (price∩book)."""
        c = self.cfg
        adv = median(list(self._adverse_supported)) if self._adverse_supported else float("nan")
        imp = median(list(self._impulse_aligned)) if self._impulse_aligned else float("nan")
        tp_floor = float(c.fat_tp_min if c.prefer_fat_tp else c.tp_min)
        if adv == adv and adv > 0:
            sl = clamp(float(adv), c.sl_min, c.sl_max)
        else:
            sl = float(c.sl_points)
        if imp == imp and imp > 0:
            tp = clamp(float(imp), tp_floor, c.tp_max)
        else:
            tp = max(float(c.tp_points), tp_floor)
        if tp < 1.1 * sl:
            tp = clamp(1.1 * sl, tp_floor, c.tp_max)
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
        self._bars_in_trade = 0
        self._break_streak = 0
        self._flip_streak = 0
        self._book_drop_streak = 0
        tp, sl = self._book_stops()
        self.active_tp, self.active_sl = tp, sl

    def _close(self, move: float | None = None) -> None:
        """Flat + short cooldown. Lock entries only after N consecutive losses."""
        if move is not None:
            if float(move) <= 0:
                self._loss_streak += 1
            else:
                self._loss_streak = 0
            if self._loss_streak >= max(1, int(self.cfg.loss_lock_after)):
                self._loss_locked = True
                self._loss_lock_until = self._tick_i + max(
                    1, int(self.cfg.loss_lock_cool_bars)
                )
        self.position = "flat"
        self.entry_price = None
        self.entry_tbq = None
        self.entry_tsq = None
        self.active_tp = None
        self.active_sl = None
        # Short pause only (1 bar default) — NOT a multi-SL lock
        self._cooldown_until = self._tick_i + max(0, self.cfg.cooldown_ticks)
        self._in_pullback = False
        self._pullback_ext = None
        self._bars_in_trade = 0
        self._break_streak = 0
        self._flip_streak = 0
        self._book_drop_streak = 0

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
        self._bars_in_trade += 1
        self._retune_stops_in_trade(px, side)
        tp, sl = self._tp_sl()
        stall_min = (
            float(self.cfg.fat_stall_min)
            if self.cfg.prefer_fat_tp
            else float(self.cfg.stall_min_profit)
        )

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
            self.last_hold_reason = None
            tagged = f"exit[{self.cfg.exit_model}] {reason}"
            self._close(move)
            return SignalResult(
                action="CLOSE",
                position_after="flat",
                price_delta=move,
                net=self.last_net,
                net_delta=None,
                prev_net_delta=None,
                reason=tagged,
            )

        # Hold reasoning model
        book_hold = False
        self.last_hold_reason = None
        hm = (self.cfg.hold_model or "book_rise").strip().lower()
        if hm not in {"off", "none", "0"}:
            if side == "long" and self.cfg.hold_while_book_rises and self.tbq_rising:
                book_hold = True
                self.last_hold_reason = f"hold[{hm}] tbq↑"
            elif side == "short" and self.cfg.hold_while_book_rises and self.tsq_rising:
                book_hold = True
                self.last_hold_reason = f"hold[{hm}] tsq↑"
            elif self.cfg.hold_on_supported:
                if side == "long" and self.dip_supported:
                    book_hold = True
                    self.last_hold_reason = f"hold[{hm}] dip_supported"
                elif side == "short" and self.rally_supported:
                    book_hold = True
                    self.last_hold_reason = f"hold[{hm}] rally_supported"

        # Supporting book dropped vs previous step → exit (noise-filtered).
        # Trend guard: never book_drop-exit while protect_profit_pts in the green
        # (overnight H=off learned presets were cutting 100–200pt runners).
        protect = float(getattr(self.cfg, "protect_profit_pts", 25.0) or 0.0)
        in_protected_profit = protect > 0 and move >= protect
        if in_protected_profit and self.cfg.close_on_book_drop:
            self.last_hold_reason = (
                f"hold[protect] move={move:.1f}>={protect:.0f} skip_book_drop"
            )

        if self.cfg.close_on_book_drop and not book_hold and not in_protected_profit:
            raw_drop = (side == "long" and self.tbq_falling) or (
                side == "short" and self.tsq_falling
            )
            meaningful = False
            drop_pct = 0.0
            if side == "long" and self.tbq_falling:
                prev = max(float(self._cmp_prev_tbq), 1e-9)
                drop_pct = (prev - float(self.last_tbq)) / prev * 100.0
                meaningful = drop_pct >= float(self.cfg.book_drop_min_pct)
            elif side == "short" and self.tsq_falling:
                prev = max(float(self._cmp_prev_tsq), 1e-9)
                drop_pct = (prev - float(self.last_tsq)) / prev * 100.0
                meaningful = drop_pct >= float(self.cfg.book_drop_min_pct)
            if raw_drop and meaningful:
                self._book_drop_streak += 1
            else:
                self._book_drop_streak = 0
            if (
                meaningful
                and self._book_drop_streak >= max(1, int(self.cfg.book_drop_persist))
            ):
                if side == "long":
                    return done(
                        f"tbq_drop {self.last_tbq:.0f}<prev "
                        f"(-{drop_pct:.2f}%) move={move:.1f} "
                        f"combo={self.last_combo}"
                    )
                return done(
                    f"tsq_drop {self.last_tsq:.0f}<prev "
                    f"(-{drop_pct:.2f}%) move={move:.1f} "
                    f"combo={self.last_combo}"
                )
        else:
            self._book_drop_streak = 0

        # Combined break = stop (book says trend failed) — gated to stop noise eats
        raw_break = (side == "long" and self.break_bull) or (
            side == "short" and self.break_bear
        )
        if raw_break:
            self._break_streak += 1
        else:
            self._break_streak = 0

        allow_break = True
        if book_hold:
            allow_break = False
        if self._bars_in_trade < max(1, int(self.cfg.break_min_bars)):
            allow_break = False
        # Never book_break while in profit; wait until adverse >= break_min_adverse
        if move >= 0 or move > -float(self.cfg.break_min_adverse):
            allow_break = False
        if self.cfg.break_skip_if_supported:
            if side == "long" and self.dip_supported:
                allow_break = False
            if side == "short" and self.rally_supported:
                allow_break = False
        if self._break_streak < max(1, int(self.cfg.break_persist)):
            allow_break = False

        if allow_break and side == "long" and self.break_bull:
            return done(
                f"book_break_bull {self.last_combo} move={move:.1f} "
                f"in={self._bars_in_trade} streak={self._break_streak}"
            )
        if allow_break and side == "short" and self.break_bear:
            return done(
                f"book_break_bear {self.last_combo} move={move:.1f} "
                f"in={self._bars_in_trade} streak={self._break_streak}"
            )

        # Bias flip exit — gated like break (was eating trades after break was gated)
        raw_flip = (side == "long" and self.bias == "BEAR" and self.tsq_allows) or (
            side == "short" and self.bias == "BULL" and self.tbq_allows
        )
        if self.cfg.flip_need_widen:
            raw_flip = (side == "long" and self.bias == "BEAR" and self.widen_bear) or (
                side == "short" and self.bias == "BULL" and self.widen_bull
            )
        if raw_flip:
            self._flip_streak += 1
        else:
            self._flip_streak = 0

        allow_flip = True
        if book_hold:
            allow_flip = False
        if self._bars_in_trade < max(1, int(self.cfg.flip_min_bars)):
            allow_flip = False
        if self.cfg.flip_block_in_profit and move >= 0:
            allow_flip = False
        if move > -float(self.cfg.flip_min_adverse):
            allow_flip = False
        if self._flip_streak < max(1, int(self.cfg.flip_persist)):
            allow_flip = False

        if allow_flip and side == "long" and self.bias == "BEAR" and (
            self.widen_bear if self.cfg.flip_need_widen else self.tsq_allows
        ):
            return done(
                f"align_flip_to_BEAR {self.last_align} move={move:.1f} "
                f"in={self._bars_in_trade} streak={self._flip_streak}"
            )
        if allow_flip and side == "short" and self.bias == "BULL" and (
            self.widen_bull if self.cfg.flip_need_widen else self.tbq_allows
        ):
            return done(
                f"align_flip_to_BULL {self.last_align} move={move:.1f} "
                f"in={self._bars_in_trade} streak={self._flip_streak}"
            )

        if not book_hold and side == "long" and self.entry_tbq and self.entry_tbq > 0:
            drop = (self.entry_tbq - self.last_tbq) / self.entry_tbq * 100.0
            if drop >= self.cfg.weaken_pct:
                return done(f"tbq_weaken {drop:.1f}%")
        if not book_hold and side == "short" and self.entry_tsq and self.entry_tsq > 0:
            drop = (self.entry_tsq - self.last_tsq) / self.entry_tsq * 100.0
            if drop >= self.cfg.weaken_pct:
                return done(f"tsq_weaken {drop:.1f}%")

        # Hard SL only if adverse beyond scientific supported depth
        if move <= -sl:
            if book_hold:
                pass
            elif side == "long" and self.dip_supported:
                pass
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

        if (
            move >= stall_min
            and self._bars_since_ext >= self.cfg.stall_bars
            and not self._book_expand_since_ext
            and not book_hold
        ):
            return done(
                f"stall_book_tp +{move:.1f} no_ext×{self._bars_since_ext} "
                f"combo={self.last_combo}"
            )
        return None

    def _try_enter(self, px: float) -> SignalResult | None:
        em = self.cfg.entry_model or "imb_sign_rise"

        def skip(why: str) -> None:
            self.last_skip = f"entry[{em}]:{why}"

        if self._loss_locked:
            if self._tick_i >= self._loss_lock_until:
                self._loss_locked = False
                self._loss_streak = 0
                skip("loss_lock_released")
            else:
                left = self._loss_lock_until - self._tick_i
                skip(f"loss_lock streak={self._loss_streak} left={left}")
                return None
        if self._tick_i < self._cooldown_until:
            skip("cooldown")
            return None
        if self.last_imb < self.cfg.min_imb_pct:
            skip("imb_soft")
            return None
        if self.cfg.require_rising_imb and not self.imb_rising:
            skip(f"imb_not_rising {self.prev_imb:.1f}->{self.last_imb:.1f}")
            return None

        mode = (self.cfg.entry_mode or "net_sign").strip().lower()

        # --- long ---
        want_long = False
        if mode == "align_widen":
            want_long = self.bias == "BULL" and self.last_net > 0
            if want_long and self._tbq_allow_age > self.book_allow_memory:
                skip("tbq_not_allow")
                return None
        else:
            want_long = self.last_net > 0

        if want_long:
            if self.cfg.require_rising_book and not self.tbq_rising:
                skip("tbq_not_rising")
                return None
            if self.cfg.require_pullback and not self._pullback_resume(px, "long"):
                skip("wait_bull_pullback")
                return None
            if not self._nn_allows_entry():
                return None
            if not self._reasoning_allows_entry("long"):
                return None
            self._open("long", px)
            tp, sl = self._tp_sl()
            how = "pullback" if self.cfg.require_pullback else (
                "widen" if mode == "align_widen" else "imb+"
            )
            nn_bit = (
                f" nn={self.last_nn_proba:.2f}"
                if self.last_nn_proba is not None
                and (self.cfg.require_nn_filter or self.cfg.require_reasoning)
                else ""
            )
            rz_bit = ""
            if self.cfg.require_reasoning and self.last_reasoning is not None:
                rz_bit = f" rz={self.last_reasoning.score:.2f}"
            return SignalResult(
                action="BUY",
                position_after="long",
                price_delta=None,
                net=self.last_net,
                net_delta=None,
                prev_net_delta=None,
                reason=(
                    f"entry[{em}] align_long {how} net={self.last_net:.0f} "
                    f"imb={self.last_imb:.1f}% (↑{self.prev_imb:.1f}) "
                    f"TP={tp:.0f} SL={sl:.0f}{nn_bit}{rz_bit}"
                ),
            )

        # --- short ---
        want_short = False
        if mode == "align_widen":
            want_short = self.bias == "BEAR" and self.last_net < 0
            if want_short and self._tsq_allow_age > self.book_allow_memory:
                skip("tsq_not_allow")
                return None
        else:
            want_short = self.last_net < 0

        if want_short:
            if self.cfg.require_rising_book and not self.tsq_rising:
                skip("tsq_not_rising")
                return None
            if self.cfg.require_pullback and not self._pullback_resume(px, "short"):
                skip("wait_bear_pullback")
                return None
            if not self._nn_allows_entry():
                return None
            if not self._reasoning_allows_entry("short"):
                return None
            self._open("short", px)
            tp, sl = self._tp_sl()
            how = "pullback" if self.cfg.require_pullback else (
                "widen" if mode == "align_widen" else "imb-"
            )
            nn_bit = (
                f" nn={self.last_nn_proba:.2f}"
                if self.last_nn_proba is not None
                and (self.cfg.require_nn_filter or self.cfg.require_reasoning)
                else ""
            )
            rz_bit = ""
            if self.cfg.require_reasoning and self.last_reasoning is not None:
                rz_bit = f" rz={self.last_reasoning.score:.2f}"
            return SignalResult(
                action="SHORT",
                position_after="short",
                price_delta=None,
                net=self.last_net,
                net_delta=None,
                prev_net_delta=None,
                reason=(
                    f"entry[{em}] align_short {how} net={self.last_net:.0f} "
                    f"imb={self.last_imb:.1f}% (↑{self.prev_imb:.1f}) "
                    f"TP={tp:.0f} SL={sl:.0f}{nn_bit}{rz_bit}"
                ),
            )

        if mode == "align_widen":
            skip(f"wait bias={self.bias} net={self.last_net:.0f}")
        else:
            skip("wait net=0")
        return None

    def _floor_bar(self, now: datetime) -> datetime:
        minutes = max(1, int(self.cfg.bar_minutes))
        local = now.astimezone(IST) if now.tzinfo else now.replace(tzinfo=IST)
        midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
        mins = int((local - midnight).total_seconds() // 60)
        block = (mins // minutes) * minutes
        from datetime import timedelta

        return midnight + timedelta(minutes=block)

    def _step(self, px: float, tbq: float, tsq: float) -> SignalResult | None:
        """One decision step (tick or bar close)."""
        self._tick_i += 1
        self._update_behaviour(px, tbq, tsq)
        self._update_pullback(px)
        if self._tick_i % max(1, self.cfg.every_n_ticks) != 0:
            return None
        if self.position != "flat" and self.entry_price is not None:
            return self._manage(px)
        return self._try_enter(px)

    def _on_tick_time_bar(
        self, now: datetime, ltp: float, tbq: float, tsq: float
    ) -> SignalResult | None:
        px = float(ltp)
        key = self._floor_bar(now)
        sig: SignalResult | None = None
        if self._bar_key is None:
            self._bar_key = key
        if key != self._bar_key:
            if self._bar_c is not None:
                sig = self._step(float(self._bar_c), float(self._bar_tbq), float(self._bar_tsq))
            self._bar_key = key
            self._bar_c = px
            self._bar_tbq = tbq
            self._bar_tsq = tsq
            self._bar_n = 1
            return sig
        self._bar_c = px
        self._bar_tbq = tbq
        self._bar_tsq = tsq
        self._bar_n += 1
        return None

    def _on_tick_count_bar(
        self, ltp: float, tbq: float, tsq: float
    ) -> SignalResult | None:
        px = float(ltp)
        n_per = max(1, int(self.cfg.bar_ticks))
        if self._bar_c is None:
            self._bar_c = px
            self._bar_tbq = tbq
            self._bar_tsq = tsq
            self._count_n = 1
            return None
        self._bar_c = px
        self._bar_tbq = tbq
        self._bar_tsq = tsq
        self._count_n += 1
        if self._count_n < n_per:
            return None
        sig = self._step(float(self._bar_c), float(self._bar_tbq), float(self._bar_tsq))
        self._bar_c = None
        self._count_n = 0
        return sig

    def on_tick(
        self, now: datetime, ltp: float, message: dict[str, Any]
    ) -> SignalResult | None:
        qs = self._parse_qty(message)
        if qs is None:
            self.last_skip = "no_tbq_tsq"
            return None
        tbq, tsq = qs
        px = float(ltp)
        # Time bars win when configured (don't let leftover tick TF steal decisions)
        if self.cfg.bar_minutes and self.cfg.bar_minutes > 0:
            return self._on_tick_time_bar(now, px, tbq, tsq)
        if self.cfg.bar_ticks and self.cfg.bar_ticks > 0:
            return self._on_tick_count_bar(px, tbq, tsq)
        return self._step(px, tbq, tsq)

    def on_bar_row(self, row: dict[str, Any]) -> SignalResult | None:
        """MTF path: one decision per bar close (same behaviour math)."""
        tbq = float(row.get("tbq_close", row.get("tbq", 0)) or 0)
        tsq = float(row.get("tsq_close", row.get("tsq", 0)) or 0)
        return self._step(float(row["close"]), tbq, tsq)


def _apply_entry_model(name: str, cfg: AlignS8Config) -> AlignS8Config:
    n = (name or "imb_sign_rise").strip().lower()
    if n in {"", "default", "imb_sign_rise", "rise"}:
        cfg.entry_model = "imb_sign_rise"
        cfg.entry_mode = "net_sign"
        cfg.require_rising_imb = True
        cfg.require_rising_book = False
        cfg.require_pullback = False
        return cfg
    if n in {"imb_sign", "sign", "net_sign"}:
        cfg.entry_model = "imb_sign"
        cfg.entry_mode = "net_sign"
        cfg.require_rising_imb = False
        cfg.require_rising_book = False
        cfg.require_pullback = False
        return cfg
    if n in {"align_widen", "widen", "bias"}:
        cfg.entry_model = "align_widen"
        cfg.entry_mode = "align_widen"
        cfg.require_rising_imb = True
        cfg.require_rising_book = False
        cfg.require_pullback = False
        return cfg
    cfg.entry_model = n
    return cfg


def _apply_hold_model(name: str, cfg: AlignS8Config) -> AlignS8Config:
    n = (name or "book_rise").strip().lower()
    if n in {"", "default", "book_rise", "rise"}:
        cfg.hold_model = "book_rise"
        cfg.hold_while_book_rises = True
        cfg.hold_on_supported = False
        return cfg
    if n in {"book_or_support", "support", "rise_or_support"}:
        cfg.hold_model = "book_or_support"
        cfg.hold_while_book_rises = True
        cfg.hold_on_supported = True
        return cfg
    if n in {"off", "none", "0"}:
        cfg.hold_model = "off"
        cfg.hold_while_book_rises = False
        cfg.hold_on_supported = False
        return cfg
    cfg.hold_model = n
    return cfg


def _apply_exit_model(name: str, cfg: AlignS8Config) -> AlignS8Config:
    """Exit/TP reasoning presets (also set TF when named for live use)."""
    n = (name or "fat_tp_flip").strip().lower()
    if n in {"", "default", "fat_tp_flip", "fat50t", "best"}:
        cfg.exit_model = "fat_tp_flip"
        cfg.bar_ticks = 50
        cfg.bar_minutes = 0
        cfg.min_imb_pct = 3.0
        cfg.break_min_bars = 2
        cfg.break_min_adverse = 8.0
        cfg.break_skip_if_supported = True
        cfg.break_price_min = 1.5
        cfg.break_persist = 1
        cfg.break_need_both = False
        cfg.flip_min_bars = 2
        cfg.flip_min_adverse = 8.0
        cfg.flip_block_in_profit = True
        cfg.flip_persist = 1
        cfg.flip_need_widen = False
        cfg.prefer_fat_tp = True
        cfg.fat_tp_min = 35.0
        cfg.fat_stall_min = 30.0
        cfg.tp_points = 45.0
        cfg.tp_min = 35.0
        cfg.cooldown_ticks = 1
        cfg.close_on_book_drop = True
        cfg.book_drop_min_pct = 0.25
        cfg.book_drop_persist = 1
        return cfg
    if n in {"flip_gate_2m", "flip2m", "2m"}:
        cfg.exit_model = "flip_gate_2m"
        cfg.bar_minutes = 2
        cfg.bar_ticks = 0
        cfg.break_min_bars = 2
        cfg.break_min_adverse = 8.0
        cfg.break_skip_if_supported = True
        cfg.break_price_min = 1.5
        cfg.flip_min_bars = 2
        cfg.flip_min_adverse = 8.0
        cfg.flip_block_in_profit = True
        cfg.prefer_fat_tp = False
        cfg.cooldown_ticks = 1
        cfg.close_on_book_drop = True
        cfg.pullback_points = max(5.0, 4.0 + 2 * 0.3)
        cfg.resume_points = max(3.0, 3.0 + 2 * 0.15)
        return cfg
    if n in {"hold_fat_flip", "strict50t"}:
        cfg.exit_model = "hold_fat_flip"
        cfg.bar_ticks = 50
        cfg.bar_minutes = 0
        cfg.break_min_bars = 3
        cfg.break_min_adverse = 12.0
        cfg.break_skip_if_supported = True
        cfg.break_price_min = 2.0
        cfg.break_persist = 2
        cfg.break_need_both = True
        cfg.flip_min_bars = 3
        cfg.flip_min_adverse = 12.0
        cfg.flip_block_in_profit = True
        cfg.flip_persist = 2
        cfg.flip_need_widen = True
        cfg.prefer_fat_tp = True
        cfg.fat_tp_min = 40.0
        cfg.fat_stall_min = 35.0
        cfg.tp_points = 50.0
        cfg.tp_min = 40.0
        cfg.stall_bars = 2
        cfg.cooldown_ticks = 1
        cfg.close_on_book_drop = True
        return cfg
    if n in {"book_drop", "drop"}:
        cfg.exit_model = "book_drop"
        cfg.close_on_book_drop = True
        cfg.break_min_bars = 99
        cfg.flip_min_bars = 99
        cfg.weaken_pct = 90.0
        cfg.prefer_fat_tp = True
        cfg.fat_tp_min = 35.0
        cfg.tp_points = 45.0
        return cfg
    if n in {"break_flip", "gates"}:
        cfg.exit_model = "break_flip"
        cfg.close_on_book_drop = True
        cfg.break_min_bars = 2
        cfg.break_min_adverse = 8.0
        cfg.break_skip_if_supported = True
        cfg.break_price_min = 1.5
        cfg.flip_min_bars = 2
        cfg.flip_min_adverse = 8.0
        cfg.flip_block_in_profit = True
        cfg.prefer_fat_tp = False
        return cfg
    cfg.exit_model = n
    return cfg


def _apply_model_preset(name: str, cfg: AlignS8Config) -> AlignS8Config:
    """Bundle preset: sets model_name + entry/hold/exit defaults + TF."""
    n = (name or "").strip().lower()
    if n in {"", "align", "default"}:
        cfg.model_name = "align"
        cfg = _apply_entry_model("imb_sign_rise", cfg)
        cfg = _apply_hold_model("book_rise", cfg)
        cfg = _apply_exit_model("break_flip", cfg)
        return cfg
    if n in {"fat_tp_flip", "fat50t", "best"}:
        cfg.model_name = "fat_tp_flip"
        cfg.loss_lock_after = 5
        cfg.loss_lock_cool_bars = 10
        cfg = _apply_entry_model("imb_sign_rise", cfg)
        cfg = _apply_hold_model("book_rise", cfg)
        cfg = _apply_exit_model("fat_tp_flip", cfg)
        return cfg
    if n in {"flip_gate_2m", "flip2m", "2m"}:
        cfg.model_name = "flip_gate_2m"
        cfg = _apply_entry_model("imb_sign_rise", cfg)
        cfg = _apply_hold_model("book_rise", cfg)
        cfg = _apply_exit_model("flip_gate_2m", cfg)
        return cfg
    if n in {"hold_fat_flip", "strict50t"}:
        cfg.model_name = "hold_fat_flip"
        cfg = _apply_entry_model("imb_sign_rise", cfg)
        cfg = _apply_hold_model("book_or_support", cfg)
        cfg = _apply_exit_model("hold_fat_flip", cfg)
        return cfg
    if n in {"learned", "s8_learned", "ml"}:
        # Load improved reasoning preset from learn_s8_align.py train
        path = os.getenv(
            "S8_LEARNED_PRESET",
            os.path.join(os.path.dirname(__file__), "data", "s8_presets", "learned_latest.json"),
        )
        cfg.model_name = "learned"
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            skip = {"preset_name", "meta", "created_at"}
            for k, v in data.items():
                if k in skip:
                    continue
                if hasattr(cfg, k):
                    setattr(cfg, k, v)
            cfg.model_name = "learned"
            if int(getattr(cfg, "bar_minutes", 0) or 0) > 0:
                cfg.bar_ticks = 0
            if int(getattr(cfg, "bar_ticks", 0) or 0) > 0:
                cfg.bar_minutes = 0
        except FileNotFoundError:
            # fall back to current paper default if no train output yet
            cfg = _apply_entry_model("imb_sign_rise", cfg)
            cfg = _apply_hold_model("book_rise", cfg)
            cfg = _apply_exit_model("fat_tp_flip", cfg)
            cfg.bar_minutes = 30
            cfg.bar_ticks = 0
            cfg.model_name = "learned_missing"
        return cfg
    cfg.model_name = n
    return cfg


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

    model = os.getenv("S8_MODEL", "fat_tp_flip").strip().lower()

    cfg = AlignS8Config(
        min_imb_pct=_f("S8_MIN_IMB_PCT", 3.0),
        book_frac_of_net=_f("S8_BOOK_FRAC_OF_NET", 0.10),
        pullback_points=_f("S8_PULLBACK_POINTS", 8.0),
        resume_points=_f("S8_RESUME_POINTS", 5.0),
        every_n_ticks=int(_f("S8_EVERY_N_TICKS", 1)),
        cooldown_ticks=int(_f("S8_COOLDOWN_TICKS", 1)),
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
        book_thr_cap_pct=_f("S8_BOOK_THR_CAP_PCT", 0.002),
        break_min_bars=int(_f("S8_BREAK_MIN_BARS", 2)),
        break_min_adverse=_f("S8_BREAK_MIN_ADVERSE", 8.0),
        break_need_both=_b("S8_BREAK_NEED_BOTH", False),
        break_persist=int(_f("S8_BREAK_PERSIST", 1)),
        break_skip_if_supported=_b("S8_BREAK_SKIP_IF_SUPPORTED", True),
        break_price_min=_f("S8_BREAK_PRICE_MIN", 1.5),
        prefer_fat_tp=_b("S8_PREFER_FAT_TP", False),
        fat_tp_min=_f("S8_FAT_TP_MIN", 35.0),
        fat_stall_min=_f("S8_FAT_STALL_MIN", 30.0),
        flip_min_bars=int(_f("S8_FLIP_MIN_BARS", 2)),
        flip_min_adverse=_f("S8_FLIP_MIN_ADVERSE", 8.0),
        flip_block_in_profit=_b("S8_FLIP_BLOCK_IN_PROFIT", True),
        flip_persist=int(_f("S8_FLIP_PERSIST", 1)),
        flip_need_widen=_b("S8_FLIP_NEED_WIDEN", False),
        bar_minutes=int(_f("S8_BAR_MINUTES", 0)),
        bar_ticks=int(_f("S8_BAR_TICKS", 0)),
        loss_lock_after=int(_f("S8_LOSS_LOCK_AFTER", 5)),
        loss_lock_cool_bars=int(_f("S8_LOSS_LOCK_COOL_BARS", 10)),
        require_pullback=_b("S8_REQUIRE_PULLBACK", False),
        require_rising_imb=_b("S8_REQUIRE_RISING_IMB", True),
        require_rising_book=_b("S8_REQUIRE_RISING_BOOK", False),
        hold_while_book_rises=_b("S8_HOLD_WHILE_BOOK_RISES", True),
        hold_on_supported=_b("S8_HOLD_ON_SUPPORTED", False),
        close_on_book_drop=_b("S8_CLOSE_ON_BOOK_DROP", True),
        book_drop_min_pct=_f("S8_BOOK_DROP_MIN_PCT", 0.25),
        book_drop_persist=int(_f("S8_BOOK_DROP_PERSIST", 1)),
        protect_profit_pts=_f("S8_PROTECT_PROFIT_PTS", 25.0),
        require_nn_filter=_b("S8_REQUIRE_NN", False),
        nn_model_path=os.getenv("S8_NN_MODEL_PATH", "data/models/s8_nn_mlp.joblib"),
        nn_min_proba=_f("S8_NN_MIN_PROBA", 0.55),
        nn_lags=int(_f("S8_NN_LAGS", 3)),
        require_reasoning=_b("S8_REASONING", False),
        reasoning_min_score=_f("S8_REASONING_MIN_SCORE", 0.45),
        reasoning_lots=_f("S8_REASONING_LOTS", 100.0),
    )
    cfg = _apply_model_preset(model, cfg)

    # Explicit entry/hold/exit reasoning models (override bundle)
    if os.getenv("S8_ENTRY_MODEL") is not None:
        cfg = _apply_entry_model(os.getenv("S8_ENTRY_MODEL", ""), cfg)
    if os.getenv("S8_HOLD_MODEL") is not None:
        cfg = _apply_hold_model(os.getenv("S8_HOLD_MODEL", ""), cfg)
    if os.getenv("S8_EXIT_MODEL") is not None:
        cfg = _apply_exit_model(os.getenv("S8_EXIT_MODEL", ""), cfg)

    env_ticks = os.getenv("S8_BAR_TICKS")
    env_mins = os.getenv("S8_BAR_MINUTES")
    # TF overrides are mutually exclusive. Explicit minutes must clear fat_tp_flip's 50t.
    if env_mins is not None and str(env_mins).strip() != "":
        m = int(env_mins)
        cfg.bar_minutes = m
        if m > 0:
            cfg.bar_ticks = 0
    if env_ticks is not None and str(env_ticks).strip() != "":
        t = int(env_ticks)
        cfg.bar_ticks = t
        if t > 0:
            cfg.bar_minutes = 0
    # Knobs that must win over S8_MODEL=learned JSON
    if os.getenv("S8_MIN_IMB_PCT") is not None:
        cfg.min_imb_pct = _f("S8_MIN_IMB_PCT", 3.0)
    if os.getenv("S8_REQUIRE_RISING_IMB") is not None:
        cfg.require_rising_imb = _b("S8_REQUIRE_RISING_IMB", True)
    if os.getenv("S8_REQUIRE_RISING_BOOK") is not None:
        cfg.require_rising_book = _b("S8_REQUIRE_RISING_BOOK", False)
    if os.getenv("S8_HOLD_WHILE_BOOK_RISES") is not None:
        cfg.hold_while_book_rises = _b("S8_HOLD_WHILE_BOOK_RISES", True)
    if os.getenv("S8_HOLD_ON_SUPPORTED") is not None:
        cfg.hold_on_supported = _b("S8_HOLD_ON_SUPPORTED", False)
    if os.getenv("S8_CLOSE_ON_BOOK_DROP") is not None:
        cfg.close_on_book_drop = _b("S8_CLOSE_ON_BOOK_DROP", True)
    if os.getenv("S8_BOOK_DROP_MIN_PCT") is not None:
        cfg.book_drop_min_pct = _f("S8_BOOK_DROP_MIN_PCT", 0.25)
    if os.getenv("S8_BOOK_DROP_PERSIST") is not None:
        cfg.book_drop_persist = int(_f("S8_BOOK_DROP_PERSIST", 1))
    if os.getenv("S8_PROTECT_PROFIT_PTS") is not None:
        cfg.protect_profit_pts = _f("S8_PROTECT_PROFIT_PTS", 25.0)
    if os.getenv("S8_REQUIRE_NN") is not None:
        cfg.require_nn_filter = _b("S8_REQUIRE_NN", False)
    if os.getenv("S8_NN_MODEL_PATH") is not None:
        cfg.nn_model_path = os.getenv("S8_NN_MODEL_PATH", cfg.nn_model_path)
    if os.getenv("S8_NN_MIN_PROBA") is not None:
        cfg.nn_min_proba = _f("S8_NN_MIN_PROBA", 0.55)
    if os.getenv("S8_NN_LAGS") is not None:
        cfg.nn_lags = int(_f("S8_NN_LAGS", 3))
    if os.getenv("S8_REASONING") is not None:
        cfg.require_reasoning = _b("S8_REASONING", False)
    if os.getenv("S8_REASONING_MIN_SCORE") is not None:
        cfg.reasoning_min_score = _f("S8_REASONING_MIN_SCORE", 0.45)
    if os.getenv("S8_REASONING_LOTS") is not None:
        cfg.reasoning_lots = _f("S8_REASONING_LOTS", 100.0)
    return AlignS8Strategy(cfg)
