"""S8_NET_ZIGZAG — NET bias zigzags with gated re-entry.

Core:
  NET = TBQ − TSQ, IMB% = |NET| / max(TBQ, TSQ) * 100
  Long-only while BULL; short-only while BEAR.
  Exit: TP / SL / supporting-qty weaken / bias flip.

Entry (anti-spam — immediate re-entry after SL was blowing up paper EV):
  - require IMB rising-edge (cross above min_imb) OR pullback-resume
  - cooldown ticks after any exit
  - after SL: must see IMB fall below min then re-cross (reset)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from strategy import Action, Position, SignalResult

Bias = Literal["BULL", "BEAR", "NEUTRAL"]
IST = ZoneInfo("Asia/Kolkata")


def net_imbalance(tbq: float, tsq: float) -> tuple[float, float]:
    net = float(tbq) - float(tsq)
    denom = max(float(tbq), float(tsq), 1e-9)
    return net, abs(net) / denom * 100.0


@dataclass
class NetZigzagConfig:
    min_imb_pct: float = 10.0
    weaken_pct: float = 10.0
    tp_points: float = 25.0
    sl_points: float = 20.0
    every_n_ticks: int = 1
    use_fee_gate: bool = False
    fee_be_points: float = 50.0
    require_depth: bool = False
    depth_ratio: float = 1.15
    # Re-entry controls
    cooldown_ticks: int = 40
    pullback_points: float = 8.0
    resume_points: float = 5.0
    # "edge" = IMB cross above min; "pullback" = only after dip+resume; "both"
    # "always" = enter whenever flat + strong bias (30m bar paper: +₹42k row)
    entry_mode: str = "both"
    # 0 = tick mode; 30 = decide only on 30m bar close (MTF paper path)
    bar_minutes: int = 0


class NetZigzagStrategy:
    name = "S8_NET_ZIGZAG"

    def __init__(
        self,
        cfg: NetZigzagConfig | None = None,
        *,
        name: str | None = None,
    ) -> None:
        self.cfg = cfg or NetZigzagConfig()
        if name:
            self.name = name
        self.position: Position = "flat"
        self.bias: Bias = "NEUTRAL"
        self.last_net: float = 0.0
        self.last_imb: float = 0.0
        self.last_tbq: float = 0.0
        self.last_tsq: float = 0.0
        self.entry_price: float | None = None
        self.entry_tbq: float | None = None
        self.entry_tsq: float | None = None
        self._tick_i = 0
        self.last_skip: str | None = None

        self._prev_below: bool = True  # arm first IMB rising-edge
        self._edge_latched: bool = False  # true on the tick IMB crosses up
        self._cooldown_until: int = 0
        self._need_reset: bool = False  # after SL
        self._extreme: float | None = None  # high in bull / low in bear
        self._pullback_ext: float | None = None
        self._in_pullback: bool = False
        self._last_exit_reason: str = ""

        # bar builder (when bar_minutes > 0)
        self._bar_key: datetime | None = None
        self._bar_o = self._bar_h = self._bar_l = self._bar_c = None
        self._bar_tbq = self._bar_tsq = 0.0
        self._bar_n = 0

    @property
    def status_line(self) -> str:
        c = self.cfg
        tf = f"{c.bar_minutes}m" if c.bar_minutes and c.bar_minutes > 0 else "tick"
        return (
            f"TF={tf} imb>={c.min_imb_pct:.0f}% weaken>={c.weaken_pct:.0f}% "
            f"TP={c.tp_points:.0f} SL={c.sl_points:.0f} "
            f"cd={c.cooldown_ticks} mode={c.entry_mode} "
            f"bias={self.bias} pos={self.position}"
        )

    def _floor_bar(self, ts: datetime) -> datetime:
        ts = ts.astimezone(IST)
        midnight = ts.replace(hour=0, minute=0, second=0, microsecond=0)
        mins = int((ts - midnight).total_seconds() // 60)
        block = (mins // self.cfg.bar_minutes) * self.cfg.bar_minutes
        return midnight + timedelta(minutes=block)

    def _parse_qty(self, message: dict[str, Any]) -> tuple[float, float] | None:
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

        strong = imb >= self.cfg.min_imb_pct
        self._edge_latched = bool(self._prev_below and strong)
        if self._edge_latched and self._need_reset:
            self._need_reset = False
        self._prev_below = not strong

        if not strong:
            if self.bias == "BULL" and net > 0:
                return self.bias
            if self.bias == "BEAR" and net < 0:
                return self.bias
            self.bias = "NEUTRAL"
            return self.bias

        if net > 0:
            self.bias = "BULL"
        elif net < 0:
            self.bias = "BEAR"
        else:
            self.bias = "NEUTRAL"
        return self.bias

    def _imb_edge(self) -> bool:
        """True on the tick IMB crosses from below min to >= min."""
        return self._edge_latched

    def _depth_ok(self, side: Position, message: dict[str, Any]) -> bool:
        if not self.cfg.require_depth:
            return True
        from depth import depth_buy_sell_sums

        buy, sell, _ = depth_buy_sell_sums(message)
        r = self.cfg.depth_ratio
        if side == "long":
            return buy >= r * max(sell, 1e-9)
        return sell >= r * max(buy, 1e-9)

    def _update_pullback(self, ltp: float) -> None:
        if self.bias == "BULL":
            if self._extreme is None or ltp > self._extreme:
                self._extreme = ltp
                self._in_pullback = False
                self._pullback_ext = None
            elif self._extreme - ltp >= self.cfg.pullback_points:
                self._in_pullback = True
                if self._pullback_ext is None or ltp < self._pullback_ext:
                    self._pullback_ext = ltp
        elif self.bias == "BEAR":
            if self._extreme is None or ltp < self._extreme:
                self._extreme = ltp
                self._in_pullback = False
                self._pullback_ext = None
            elif ltp - self._extreme >= self.cfg.pullback_points:
                self._in_pullback = True
                if self._pullback_ext is None or ltp > self._pullback_ext:
                    self._pullback_ext = ltp
        else:
            self._extreme = ltp
            self._in_pullback = False
            self._pullback_ext = None

    def _pullback_resume(self, ltp: float, side: Position) -> bool:
        if not self._in_pullback or self._pullback_ext is None:
            return False
        if side == "long":
            return (ltp - self._pullback_ext) >= self.cfg.resume_points
        return (self._pullback_ext - ltp) >= self.cfg.resume_points

    def _entry_allowed(self, ltp: float, side: Position) -> tuple[bool, str]:
        if self._tick_i < self._cooldown_until:
            return False, "cooldown"
        mode = (self.cfg.entry_mode or "both").lower()
        # "always" (S10 / MTF +₹42k): keep trading after SL — never lock out.
        if mode == "always":
            self._need_reset = False
        elif self._need_reset:
            return False, "need_sl_reset"
        if self.last_imb < self.cfg.min_imb_pct:
            return False, "imb_soft"

        edge = self._imb_edge()
        pb = self._pullback_resume(ltp, side)

        if mode == "always":
            return True, "always"
        if mode == "edge":
            return (edge, "imb_edge" if edge else "wait_edge")
        if mode == "pullback":
            return (pb, "pullback_resume" if pb else "wait_pullback")
        # both: either
        if edge:
            return True, "imb_edge"
        if pb:
            return True, "pullback_resume"
        return False, "wait_edge_or_pullback"

    def _open(self, side: Position, ltp: float) -> None:
        self.position = side
        self.entry_price = float(ltp)
        self.entry_tbq = self.last_tbq
        self.entry_tsq = self.last_tsq
        self._extreme = float(ltp)
        self._pullback_ext = None
        self._in_pullback = False
        self._edge_latched = False
        self._need_reset = False

    def _close(self, reason: str) -> None:
        self.position = "flat"
        self.entry_price = None
        self.entry_tbq = None
        self.entry_tsq = None
        self._last_exit_reason = reason
        self._cooldown_until = self._tick_i + max(0, self.cfg.cooldown_ticks)
        mode = (self.cfg.entry_mode or "both").lower()
        if reason.startswith("sl") and mode != "always":
            # edge/both: must see IMB go soft then re-cross before next entry
            self._need_reset = True
            self._prev_below = False
        elif reason.startswith("sl") and mode == "always":
            # S10: SL closes the trade only — do not stop further entries
            self._need_reset = False
        self._in_pullback = False
        self._pullback_ext = None

    def _manage(self, ltp: float) -> SignalResult | None:
        assert self.entry_price is not None
        ep = float(self.entry_price)
        side = self.position
        move = (ltp - ep) if side == "long" else (ep - ltp)

        def done(reason: str) -> SignalResult:
            self._close(reason)
            return SignalResult(
                action="CLOSE",
                position_after="flat",
                price_delta=(ltp - ep) if side == "long" else (ep - ltp),
                net=self.last_net,
                net_delta=None,
                prev_net_delta=None,
                reason=reason,
            )

        if side == "long" and self.bias == "BEAR":
            return done(f"flip_to_BEAR net={self.last_net:.0f} imb={self.last_imb:.1f}%")
        if side == "short" and self.bias == "BULL":
            return done(f"flip_to_BULL net={self.last_net:.0f} imb={self.last_imb:.1f}%")

        if side == "long" and self.entry_tbq and self.entry_tbq > 0:
            drop = (self.entry_tbq - self.last_tbq) / self.entry_tbq * 100.0
            if drop >= self.cfg.weaken_pct:
                return done(f"tbq_weaken {drop:.1f}%>= {self.cfg.weaken_pct:.0f}%")
        if side == "short" and self.entry_tsq and self.entry_tsq > 0:
            drop = (self.entry_tsq - self.last_tsq) / self.entry_tsq * 100.0
            if drop >= self.cfg.weaken_pct:
                return done(f"tsq_weaken {drop:.1f}%>= {self.cfg.weaken_pct:.0f}%")

        if move >= self.cfg.tp_points:
            return done(f"tp +{move:.1f}>={self.cfg.tp_points:.0f}")
        if move <= -self.cfg.sl_points:
            return done(f"sl {move:.1f}<=-{self.cfg.sl_points:.0f}")
        return None

    def _decide(
        self, ltp: float, tbq: float, tsq: float, message: dict[str, Any]
    ) -> SignalResult | None:
        """One decision step after bias/pullback already updated and tick counted."""
        if self.position != "flat" and self.entry_price is not None:
            return self._manage(float(ltp))

        if self.cfg.use_fee_gate and self.cfg.tp_points < self.cfg.fee_be_points:
            self.last_skip = "fee_gate"
            return None

        if self.bias == "BULL" and self.last_net > 0:
            ok, why = self._entry_allowed(float(ltp), "long")
            if not ok:
                self.last_skip = why
            elif not self._depth_ok("long", message):
                self.last_skip = "depth_block_long"
            else:
                self._open("long", float(ltp))
                return SignalResult(
                    action="BUY",
                    position_after="long",
                    price_delta=None,
                    net=self.last_net,
                    net_delta=None,
                    prev_net_delta=None,
                    reason=(
                        f"zigzag_long {why} net={self.last_net:.0f} "
                        f"imb={self.last_imb:.1f}% tbq={tbq:.0f} tsq={tsq:.0f}"
                    ),
                )

        if self.bias == "BEAR" and self.last_net < 0:
            ok, why = self._entry_allowed(float(ltp), "short")
            if not ok:
                self.last_skip = why
            elif not self._depth_ok("short", message):
                self.last_skip = "depth_block_short"
            else:
                self._open("short", float(ltp))
                return SignalResult(
                    action="SHORT",
                    position_after="short",
                    price_delta=None,
                    net=self.last_net,
                    net_delta=None,
                    prev_net_delta=None,
                    reason=(
                        f"zigzag_short {why} net={self.last_net:.0f} "
                        f"imb={self.last_imb:.1f}% tbq={tbq:.0f} tsq={tsq:.0f}"
                    ),
                )

        return None

    def _step(
        self, ltp: float, tbq: float, tsq: float, message: dict[str, Any]
    ) -> SignalResult | None:
        self._tick_i += 1
        self._update_bias(tbq, tsq)
        self._update_pullback(float(ltp))
        return self._decide(ltp, tbq, tsq, message)

    def _on_tick_bar(
        self, now: datetime, ltp: float, message: dict[str, Any]
    ) -> SignalResult | None:
        """Accumulate ticks; decide only when a bar closes (MTF +₹42k path)."""
        qs = self._parse_qty(message)
        if qs is None:
            self.last_skip = "no_tbq_tsq"
            return None
        tbq_f, tsq_f = qs
        px = float(ltp)
        key = self._floor_bar(now)
        sig: SignalResult | None = None

        if self._bar_key is None:
            self._bar_key = key

        if key != self._bar_key:
            if self._bar_c is not None:
                msg = {
                    "total_buy_quantity": self._bar_tbq,
                    "total_sell_quantity": self._bar_tsq,
                }
                sig = self._step(
                    float(self._bar_c),
                    float(self._bar_tbq),
                    float(self._bar_tsq),
                    msg,
                )
            self._bar_key = key
            self._bar_o = self._bar_h = self._bar_l = self._bar_c = px
            self._bar_tbq = tbq_f
            self._bar_tsq = tsq_f
            self._bar_n = 1
            return sig

        if self._bar_o is None:
            self._bar_o = self._bar_h = self._bar_l = self._bar_c = px
            self._bar_tbq = tbq_f
            self._bar_tsq = tsq_f
            self._bar_n = 1
            return None

        self._bar_h = max(float(self._bar_h), px)
        self._bar_l = min(float(self._bar_l), px)
        self._bar_c = px
        self._bar_tbq = tbq_f
        self._bar_tsq = tsq_f
        self._bar_n += 1
        return None

    def on_tick(
        self, now: datetime, ltp: float, message: dict[str, Any]
    ) -> SignalResult | None:
        if self.cfg.bar_minutes and self.cfg.bar_minutes > 0:
            return self._on_tick_bar(now, ltp, message)

        qs = self._parse_qty(message)
        if qs is None:
            self.last_skip = "no_tbq_tsq"
            return None
        tbq, tsq = qs
        self._tick_i += 1
        self._update_bias(tbq, tsq)
        self._update_pullback(float(ltp))
        if self._tick_i % max(1, self.cfg.every_n_ticks) != 0:
            return None
        return self._decide(float(ltp), tbq, tsq, message)


def net_zigzag_from_env():
    """Factory: ALIGN (default) = price∩TBQ/TSQ tick logic; else classic zigzag."""
    try:
        from dotenv import load_dotenv

        load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
    except Exception:
        pass

    logic = (
        os.getenv("S8_LOGIC", "").strip().lower()
        or os.getenv("S8_ENTRY_MODE", "align").strip().lower()
    )
    # New default path: remove 30m bar, use align flow
    if logic in {"align", "flow", "price_book", ""}:
        from strategy_s8_align import align_s8_from_env

        return align_s8_from_env()

    def _f(name: str, default: float) -> float:
        return float(os.getenv(name, str(default)))

    def _b(name: str, default: bool) -> bool:
        raw = os.getenv(name)
        if raw is None:
            return default
        return raw.strip().lower() in {"1", "true", "yes", "y"}

    # Legacy zigzag (edge/both/always + optional bar minutes)
    bar_m = int(_f("S8_BAR_MINUTES", 0))
    default_mode = "always" if bar_m > 0 else "both"
    default_cd = 0 if bar_m > 0 else 40
    mode = os.getenv("S8_ENTRY_MODE", default_mode).strip().lower()
    cfg = NetZigzagConfig(
        min_imb_pct=_f("S8_MIN_IMB_PCT", 10.0),
        weaken_pct=_f("S8_WEAKEN_PCT", 10.0),
        tp_points=_f("S8_TP_POINTS", 25.0),
        sl_points=_f("S8_SL_POINTS", 20.0),
        every_n_ticks=int(_f("S8_EVERY_N_TICKS", 1)),
        use_fee_gate=_b("S8_USE_FEE_GATE", False),
        fee_be_points=_f("S8_FEE_BE_POINTS", 50.0),
        require_depth=_b("S8_REQUIRE_DEPTH", False),
        depth_ratio=_f("S8_DEPTH_RATIO", 1.15),
        cooldown_ticks=int(_f("S8_COOLDOWN_TICKS", default_cd)),
        pullback_points=_f("S8_PULLBACK_POINTS", 8.0),
        resume_points=_f("S8_RESUME_POINTS", 5.0),
        entry_mode=mode,
        bar_minutes=bar_m,
    )
    return NetZigzagStrategy(cfg)


def s10_legacy30_from_env() -> NetZigzagStrategy:
    """S10: legacy 30m bar-close zigzag with entry_mode=always (MTF +₹42k row).

    Independent of S8 ALIGN. Override with S10_* env vars.
    """
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

    cfg = NetZigzagConfig(
        min_imb_pct=_f("S10_MIN_IMB_PCT", 10.0),
        weaken_pct=_f("S10_WEAKEN_PCT", 10.0),
        tp_points=_f("S10_TP_POINTS", 25.0),
        sl_points=_f("S10_SL_POINTS", 20.0),
        every_n_ticks=int(_f("S10_EVERY_N_TICKS", 1)),
        use_fee_gate=_b("S10_USE_FEE_GATE", False),
        fee_be_points=_f("S10_FEE_BE_POINTS", 50.0),
        require_depth=_b("S10_REQUIRE_DEPTH", False),
        depth_ratio=_f("S10_DEPTH_RATIO", 1.15),
        cooldown_ticks=int(_f("S10_COOLDOWN_TICKS", 0)),
        pullback_points=_f("S10_PULLBACK_POINTS", 8.0),
        resume_points=_f("S10_RESUME_POINTS", 5.0),
        entry_mode=os.getenv("S10_ENTRY_MODE", "always").strip().lower(),
        bar_minutes=int(_f("S10_BAR_MINUTES", 30)),
    )
    return NetZigzagStrategy(cfg, name="S10_LEGACY30")
