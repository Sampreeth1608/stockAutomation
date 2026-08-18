"""S8_NESTED_TREND — session NET bias + nested swing zigzags.

User theory (encoded):
  1) Day / long bias from NET = TBQ − TSQ.
     While NET > 0 (and IMB% strong) → bullish session → long-only zigzags.
     While NET < 0 → bearish session → short-only zigzags.
  2) True trend confirmation:
       price↑ + NET>0 → true uptrend
       price↓ + NET<0 → true downtrend
  3) Nested: inside a session trend, short swings pull back then resume.
     Trade the resume toward higher highs (bull) / lower lows (bear)
     for ~20–30 pt zigzags as long as session NET bias holds.
  4) Exit on TP / SL / supporting-qty weaken / session NET flip.
     Never average against the session bias.
"""

from __future__ import annotations

import os
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from depth import depth_buy_sell_sums
from strategy import Action, Position, SignalResult

Bias = Literal["BULL", "BEAR", "NEUTRAL"]
Swing = Literal["UP", "DOWN", "PULLBACK_BULL", "PULLBACK_BEAR", "FLAT"]


def net_imbalance(tbq: float, tsq: float) -> tuple[float, float]:
    """Return (NET, IMB%). IMB% = |NET| / max(TBQ, TSQ) * 100."""
    net = float(tbq) - float(tsq)
    denom = max(float(tbq), float(tsq), 1e-9)
    imb_pct = abs(net) / denom * 100.0
    return net, imb_pct


def _median(values: deque[float] | list[float]) -> float:
    if not values:
        return 0.0
    xs = sorted(values)
    mid = len(xs) // 2
    if len(xs) % 2:
        return float(xs[mid])
    return float(xs[mid - 1] + xs[mid]) / 2.0


@dataclass
class NestedTrendConfig:
    min_imb_pct: float = 10.0
    weaken_pct: float = 10.0
    tp_points: float = 25.0
    sl_points: float = 20.0
    # Nested swing: pullback then resume
    pullback_points: float = 8.0
    resume_points: float = 5.0
    # Smoothed session NET (EMA) so bias does not flip every tick
    net_ema_alpha: float = 0.08
    # Short-term price slope window (ticks)
    swing_ticks: int = 40
    every_n_ticks: int = 1
    # Optional fee gate (usually OFF for research; 1-lot zigzags rarely cover ~50pt fees)
    use_fee_gate: bool = False
    fee_be_points: float = 50.0
    # --- Microstructure filters (LTQ + bid1-5 / sell1-5) ---
    # Require buy1-5 vs sell1-5 to agree with session bias at entry
    require_depth: bool = False
    depth_ratio: float = 1.15  # long: buy_sum >= ratio * sell_sum
    # Require last_traded_quantity to show participation
    require_ltq: bool = False
    ltq_min: float = 1.0  # absolute floor
    ltq_vs_median: float = 1.0  # LTQ >= median(recent) * this (0=disable median gate)
    ltq_window: int = 50
    # Optional: exit if depth flips hard against position
    depth_exit: bool = False


class NestedTrendStrategy:
    """Tick strategy: session bias + nested zigzag resumes."""

    name = "S8_NESTED_TREND"

    def __init__(self, cfg: NestedTrendConfig | None = None) -> None:
        self.cfg = cfg or NestedTrendConfig()
        self.position: Position = "flat"
        self.session_bias: Bias = "NEUTRAL"
        self.swing: Swing = "FLAT"

        self.net_ema: float | None = None
        self.last_net: float = 0.0
        self.last_imb: float = 0.0
        self.last_tbq: float = 0.0
        self.last_tsq: float = 0.0
        self.last_ltq: float = 0.0
        self.last_buy_sum: float = 0.0
        self.last_sell_sum: float = 0.0
        self.last_depth_ratio: float = 1.0

        self.prices: deque[float] = deque(maxlen=max(10, self.cfg.swing_ticks))
        self.ltqs: deque[float] = deque(maxlen=max(10, self.cfg.ltq_window))
        # Session extreme / pullback tracking
        self.session_extreme: float | None = None  # high in bull, low in bear
        self.pullback_extreme: float | None = None  # low in bull pullback, high in bear

        self.entry_price: float | None = None
        self.entry_tbq: float | None = None
        self.entry_tsq: float | None = None
        self._tick_i = 0
        self.last_skip: str | None = None
        self._trades_this_bias = 0
        self._bias_at_trade: Bias = "NEUTRAL"

    @property
    def status_line(self) -> str:
        c = self.cfg
        micro = []
        if c.require_depth:
            micro.append(f"depth>={c.depth_ratio:.2f}")
        if c.require_ltq:
            micro.append(f"ltq>={c.ltq_min:.0f}/med*{c.ltq_vs_median:.1f}")
        micro_s = (" " + ",".join(micro)) if micro else ""
        return (
            f"imb>={c.min_imb_pct:.0f}% weaken>={c.weaken_pct:.0f}% "
            f"TP={c.tp_points:.0f} SL={c.sl_points:.0f} "
            f"pb={c.pullback_points:.0f}/res={c.resume_points:.0f} "
            f"fee_gate={c.use_fee_gate}{micro_s} "
            f"bias={self.session_bias} swing={self.swing} pos={self.position}"
        )

    def _tbq_tsq(self, message: dict[str, Any]) -> tuple[float, float] | None:
        tbq = message.get("total_buy_quantity")
        tsq = message.get("total_sell_quantity")
        if tbq is None or tsq is None:
            return None
        try:
            return float(tbq), float(tsq)
        except (TypeError, ValueError):
            return None

    def _update_bias(self, tbq: float, tsq: float) -> Bias:
        net, imb = net_imbalance(tbq, tsq)
        self.last_net = net
        self.last_imb = imb
        self.last_tbq = tbq
        self.last_tsq = tsq
        if self.net_ema is None:
            self.net_ema = net
        else:
            a = self.cfg.net_ema_alpha
            self.net_ema = a * net + (1.0 - a) * self.net_ema

        prev = self.session_bias
        # Bias uses smoothed NET sign + live IMB strength gate
        if self.net_ema > 0 and imb >= self.cfg.min_imb_pct:
            self.session_bias = "BULL"
        elif self.net_ema < 0 and imb >= self.cfg.min_imb_pct:
            self.session_bias = "BEAR"
        else:
            # Keep prior bias if NET still same sign but IMB soft; only NEUTRAL on flip-ish
            if self.net_ema is not None and abs(self.net_ema) > 0:
                if self.net_ema > 0 and self.session_bias == "BULL":
                    pass
                elif self.net_ema < 0 and self.session_bias == "BEAR":
                    pass
                else:
                    self.session_bias = "NEUTRAL"
            else:
                self.session_bias = "NEUTRAL"
        if self.session_bias != prev and self.session_bias in {"BULL", "BEAR"}:
            # New session regime → fresh nested-trend cycle
            self._trades_this_bias = 0
            self.session_extreme = None
            self.pullback_extreme = None
        return self.session_bias

    def _price_slope(self) -> float:
        """Positive = rising short-term price, negative = falling."""
        if len(self.prices) < max(5, self.cfg.swing_ticks // 2):
            return 0.0
        older = self.prices[0]
        newer = self.prices[-1]
        return float(newer - older)

    def _update_swing(self, ltp: float) -> Swing:
        slope = self._price_slope()
        bias = self.session_bias
        net = self.last_net

        true_up = slope > 0 and net > 0
        true_down = slope < 0 and net < 0

        if bias == "BULL":
            if self.session_extreme is None or ltp > self.session_extreme:
                self.session_extreme = ltp
                # New highs reset pullback
                if self.swing == "PULLBACK_BULL":
                    self.pullback_extreme = None
                self.swing = "UP" if true_up or net > 0 else "FLAT"
            else:
                drop = self.session_extreme - ltp
                if drop >= self.cfg.pullback_points:
                    self.swing = "PULLBACK_BULL"
                    if self.pullback_extreme is None or ltp < self.pullback_extreme:
                        self.pullback_extreme = ltp
                elif self.swing == "PULLBACK_BULL":
                    # still in pullback until resume
                    if self.pullback_extreme is None or ltp < self.pullback_extreme:
                        self.pullback_extreme = ltp
                elif true_up:
                    self.swing = "UP"
                else:
                    self.swing = "FLAT"
        elif bias == "BEAR":
            if self.session_extreme is None or ltp < self.session_extreme:
                self.session_extreme = ltp
                if self.swing == "PULLBACK_BEAR":
                    self.pullback_extreme = None
                self.swing = "DOWN" if true_down or net < 0 else "FLAT"
            else:
                bounce = ltp - self.session_extreme
                if bounce >= self.cfg.pullback_points:
                    self.swing = "PULLBACK_BEAR"
                    if self.pullback_extreme is None or ltp > self.pullback_extreme:
                        self.pullback_extreme = ltp
                elif self.swing == "PULLBACK_BEAR":
                    if self.pullback_extreme is None or ltp > self.pullback_extreme:
                        self.pullback_extreme = ltp
                elif true_down:
                    self.swing = "DOWN"
                else:
                    self.swing = "FLAT"
        else:
            self.swing = "FLAT"
            self.session_extreme = ltp
            self.pullback_extreme = None

        return self.swing

    def _resume_long(self, ltp: float) -> bool:
        """Bull nested resume: bounced off pullback low enough with NET still +."""
        if self.session_bias != "BULL" or self.last_net <= 0:
            return False
        if self.last_imb < self.cfg.min_imb_pct:
            return False
        if self.swing == "PULLBACK_BULL" and self.pullback_extreme is not None:
            return (ltp - self.pullback_extreme) >= self.cfg.resume_points
        # Also allow first entry on true uptrend after bias establishes
        if self.swing == "UP" and self._price_slope() > 0:
            return True
        return False

    def _resume_short(self, ltp: float) -> bool:
        if self.session_bias != "BEAR" or self.last_net >= 0:
            return False
        if self.last_imb < self.cfg.min_imb_pct:
            return False
        if self.swing == "PULLBACK_BEAR" and self.pullback_extreme is not None:
            return (self.pullback_extreme - ltp) >= self.cfg.resume_points
        if self.swing == "DOWN" and self._price_slope() < 0:
            return True
        return False

    def _update_micro(self, message: dict[str, Any]) -> None:
        buy_sum, sell_sum, _ = depth_buy_sell_sums(message)
        self.last_buy_sum = buy_sum
        self.last_sell_sum = sell_sum
        self.last_depth_ratio = buy_sum / max(sell_sum, 1e-9)

        ltq_raw = message.get("last_traded_quantity")
        try:
            ltq = float(ltq_raw) if ltq_raw is not None else 0.0
        except (TypeError, ValueError):
            ltq = 0.0
        self.last_ltq = ltq
        if ltq > 0:
            self.ltqs.append(ltq)

    def _micro_ok(self, side: Position) -> tuple[bool, str]:
        """Entry gate using bid1-5/sell1-5 + last_traded_quantity."""
        c = self.cfg
        if c.require_depth:
            if side == "long":
                if self.last_buy_sum < c.depth_ratio * max(self.last_sell_sum, 1e-9):
                    return False, (
                        f"depth_block long buy5={self.last_buy_sum:.0f} "
                        f"sell5={self.last_sell_sum:.0f} need>={c.depth_ratio:.2f}x"
                    )
            else:
                if self.last_sell_sum < c.depth_ratio * max(self.last_buy_sum, 1e-9):
                    return False, (
                        f"depth_block short sell5={self.last_sell_sum:.0f} "
                        f"buy5={self.last_buy_sum:.0f} need>={c.depth_ratio:.2f}x"
                    )
        if c.require_ltq:
            if self.last_ltq < c.ltq_min:
                return False, f"ltq_block ltq={self.last_ltq:.0f}<{c.ltq_min:.0f}"
            if c.ltq_vs_median > 0 and len(self.ltqs) >= max(10, c.ltq_window // 2):
                med = _median(self.ltqs)
                need = med * c.ltq_vs_median
                if self.last_ltq < need:
                    return False, f"ltq_block ltq={self.last_ltq:.0f}<med*{c.ltq_vs_median:.1f}={need:.1f}"
        return True, "micro_ok"

    def _open(self, side: Position, ltp: float) -> None:
        self.position = side
        self.entry_price = ltp
        self.entry_tbq = self.last_tbq
        self.entry_tsq = self.last_tsq
        self._trades_this_bias += 1
        self._bias_at_trade = self.session_bias
        # After entry, treat current as new extreme seed for next zigzag
        self.session_extreme = ltp
        self.pullback_extreme = None
        self.swing = "UP" if side == "long" else "DOWN"

    def _close(self) -> None:
        self.position = "flat"
        self.entry_price = None
        self.entry_tbq = None
        self.entry_tsq = None

    def _manage_open(self, ltp: float) -> SignalResult | None:
        assert self.entry_price is not None
        ep = float(self.entry_price)
        side = self.position
        move = ltp - ep
        if side == "short":
            move = -move

        def _close_result(reason: str) -> SignalResult:
            self._close()
            return SignalResult(
                action="CLOSE",
                position_after="flat",
                price_delta=(ltp - ep) if side == "long" else (ep - ltp),
                net=self.last_net,
                net_delta=None,
                prev_net_delta=None,
                reason=reason,
            )

        # Session flip → exit (bias invalid)
        if side == "long" and self.session_bias == "BEAR":
            return _close_result(
                f"session_flip_to_BEAR net={self.last_net:.0f} imb={self.last_imb:.1f}%"
            )
        if side == "short" and self.session_bias == "BULL":
            return _close_result(
                f"session_flip_to_BULL net={self.last_net:.0f} imb={self.last_imb:.1f}%"
            )

        # Supporting qty weaken vs entry level (not peak)
        if side == "long" and self.entry_tbq and self.entry_tbq > 0:
            drop = (self.entry_tbq - self.last_tbq) / self.entry_tbq * 100.0
            if drop >= self.cfg.weaken_pct:
                return _close_result(
                    f"tbq_weaken {drop:.1f}%>= {self.cfg.weaken_pct:.0f}%"
                )
        if side == "short" and self.entry_tsq and self.entry_tsq > 0:
            drop = (self.entry_tsq - self.last_tsq) / self.entry_tsq * 100.0
            if drop >= self.cfg.weaken_pct:
                return _close_result(
                    f"tsq_weaken {drop:.1f}%>= {self.cfg.weaken_pct:.0f}%"
                )

        if move >= self.cfg.tp_points:
            return _close_result(
                f"tp +{move:.1f}>={self.cfg.tp_points:.0f} bias={self.session_bias}"
            )
        if move <= -self.cfg.sl_points:
            return _close_result(
                f"sl {move:.1f}<=-{self.cfg.sl_points:.0f} bias={self.session_bias}"
            )

        # Depth flip exit: book turns against us while in trade
        if self.cfg.depth_exit:
            r = self.cfg.depth_ratio
            if side == "long" and self.last_sell_sum >= r * max(self.last_buy_sum, 1e-9):
                return _close_result(
                    f"depth_exit long sell5={self.last_sell_sum:.0f}>={r:.2f}*buy5"
                )
            if side == "short" and self.last_buy_sum >= r * max(self.last_sell_sum, 1e-9):
                return _close_result(
                    f"depth_exit short buy5={self.last_buy_sum:.0f}>={r:.2f}*sell5"
                )
        return None

    def on_tick(
        self, now: datetime, ltp: float, message: dict[str, Any]
    ) -> SignalResult | None:
        del now  # reserved for time filters later
        self._tick_i += 1
        qs = self._tbq_tsq(message)
        if qs is None:
            self.last_skip = "no_tbq_tsq"
            return None
        tbq, tsq = qs
        self.prices.append(float(ltp))
        self._update_bias(tbq, tsq)
        self._update_swing(float(ltp))
        self._update_micro(message)

        if self._tick_i % max(1, self.cfg.every_n_ticks) != 0:
            return None

        # Manage open first
        if self.position != "flat" and self.entry_price is not None:
            closed = self._manage_open(float(ltp))
            if closed is not None:
                return closed
            return None

        # Entries only when flat
        if self.cfg.use_fee_gate and self.cfg.tp_points < self.cfg.fee_be_points:
            self.last_skip = "fee_gate"
            return None

        # First trade in a bias: allow true trend. Later: only pullback-resumes
        # (zigzag upper-highs / lower-lows through the day).
        allow_first = self._trades_this_bias == 0 and len(self.prices) >= self.cfg.swing_ticks

        if self._resume_long(float(ltp)):
            if self.swing == "PULLBACK_BULL" or (allow_first and self.swing == "UP"):
                ok, micro_why = self._micro_ok("long")
                if not ok:
                    self.last_skip = micro_why
                    return None
                why = "pullback_resume" if self.swing == "PULLBACK_BULL" else "true_uptrend"
                self._open("long", float(ltp))
                return SignalResult(
                    action="BUY",
                    position_after="long",
                    price_delta=None,
                    net=self.last_net,
                    net_delta=None,
                    prev_net_delta=None,
                    reason=(
                        f"nested_long {why} bias=BULL swing={self.swing} "
                        f"net={self.last_net:.0f} imb={self.last_imb:.1f}% "
                        f"buy5={self.last_buy_sum:.0f} sell5={self.last_sell_sum:.0f} "
                        f"ltq={self.last_ltq:.0f}"
                    ),
                )

        if self._resume_short(float(ltp)):
            if self.swing == "PULLBACK_BEAR" or (allow_first and self.swing == "DOWN"):
                ok, micro_why = self._micro_ok("short")
                if not ok:
                    self.last_skip = micro_why
                    return None
                why = "pullback_resume" if self.swing == "PULLBACK_BEAR" else "true_downtrend"
                self._open("short", float(ltp))
                return SignalResult(
                    action="SHORT",
                    position_after="short",
                    price_delta=None,
                    net=self.last_net,
                    net_delta=None,
                    prev_net_delta=None,
                    reason=(
                        f"nested_short {why} bias=BEAR swing={self.swing} "
                        f"net={self.last_net:.0f} imb={self.last_imb:.1f}% "
                        f"buy5={self.last_buy_sum:.0f} sell5={self.last_sell_sum:.0f} "
                        f"ltq={self.last_ltq:.0f}"
                    ),
                )

        self.last_skip = f"wait bias={self.session_bias} swing={self.swing}"
        return None


def nested_trend_from_env() -> NestedTrendStrategy:
    """Load S8 knobs from env. Default ENABLE handled in portfolio."""
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

    cfg = NestedTrendConfig(
        min_imb_pct=_f("S8_MIN_IMB_PCT", 14.0),
        weaken_pct=_f("S8_WEAKEN_PCT", 10.0),
        tp_points=_f("S8_TP_POINTS", 25.0),
        sl_points=_f("S8_SL_POINTS", 20.0),
        pullback_points=_f("S8_PULLBACK_POINTS", 8.0),
        resume_points=_f("S8_RESUME_POINTS", 5.0),
        net_ema_alpha=_f("S8_NET_EMA_ALPHA", 0.08),
        swing_ticks=int(_f("S8_SWING_TICKS", 40)),
        every_n_ticks=int(_f("S8_EVERY_N_TICKS", 1)),
        use_fee_gate=_b("S8_USE_FEE_GATE", True),
        fee_be_points=_f("S8_FEE_BE_POINTS", 50.0),
        require_depth=_b("S8_REQUIRE_DEPTH", False),
        depth_ratio=_f("S8_DEPTH_RATIO", 1.15),
        require_ltq=_b("S8_REQUIRE_LTQ", False),
        ltq_min=_f("S8_LTQ_MIN", 1.0),
        ltq_vs_median=_f("S8_LTQ_VS_MEDIAN", 1.0),
        ltq_window=int(_f("S8_LTQ_WINDOW", 50)),
        depth_exit=_b("S8_DEPTH_EXIT", False),
    )
    if cfg.use_fee_gate:
        cfg.tp_points = max(float(cfg.tp_points), float(cfg.fee_be_points))
    return NestedTrendStrategy(cfg)
