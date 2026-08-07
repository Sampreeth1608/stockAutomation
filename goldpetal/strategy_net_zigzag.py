"""S8_NET_ZIGZAG — best paper research params so far.

Theory (user style, simplified / proven better than nested pullback):
  - NET = TBQ − TSQ, IMB% = |NET| / max(TBQ, TSQ) * 100
  - While NET>0 and IMB>=min → long-only zigzags
  - While NET<0 and IMB>=min → short-only zigzags
  - Enter when bias is set and flat; re-enter after exit if bias intact
  - Exit: TP / SL / supporting-qty weaken vs entry / NET flip
  - Defaults from hist sweep: imb10 / weaken10 / TP25 / SL20
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from strategy import Action, Position, SignalResult

Bias = Literal["BULL", "BEAR", "NEUTRAL"]


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
    # Optional depth confirm (bid1-5 / sell1-5) — OFF by default (best was without)
    require_depth: bool = False
    depth_ratio: float = 1.15


class NetZigzagStrategy:
    name = "S8_NET_ZIGZAG"

    def __init__(self, cfg: NetZigzagConfig | None = None) -> None:
        self.cfg = cfg or NetZigzagConfig()
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

    @property
    def status_line(self) -> str:
        c = self.cfg
        return (
            f"imb>={c.min_imb_pct:.0f}% weaken>={c.weaken_pct:.0f}% "
            f"TP={c.tp_points:.0f} SL={c.sl_points:.0f} "
            f"fee_gate={c.use_fee_gate} depth={c.require_depth} "
            f"bias={self.bias} pos={self.position}"
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

    def _update_bias(self, tbq: float, tsq: float) -> Bias:
        net, imb = net_imbalance(tbq, tsq)
        self.last_net = net
        self.last_imb = imb
        self.last_tbq = tbq
        self.last_tsq = tsq
        if imb < self.cfg.min_imb_pct:
            # Soft imbalance: keep prior bias only if NET sign still agrees
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

    def _depth_ok(self, side: Position, message: dict[str, Any]) -> bool:
        if not self.cfg.require_depth:
            return True
        from depth import depth_buy_sell_sums

        buy, sell, _ = depth_buy_sell_sums(message)
        r = self.cfg.depth_ratio
        if side == "long":
            return buy >= r * max(sell, 1e-9)
        return sell >= r * max(buy, 1e-9)

    def _open(self, side: Position, ltp: float) -> None:
        self.position = side
        self.entry_price = float(ltp)
        self.entry_tbq = self.last_tbq
        self.entry_tsq = self.last_tsq

    def _close(self) -> None:
        self.position = "flat"
        self.entry_price = None
        self.entry_tbq = None
        self.entry_tsq = None

    def _manage(self, ltp: float) -> SignalResult | None:
        assert self.entry_price is not None
        ep = float(self.entry_price)
        side = self.position
        move = (ltp - ep) if side == "long" else (ep - ltp)

        def done(reason: str) -> SignalResult:
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

    def on_tick(
        self, now: datetime, ltp: float, message: dict[str, Any]
    ) -> SignalResult | None:
        del now
        self._tick_i += 1
        qs = self._parse_qty(message)
        if qs is None:
            self.last_skip = "no_tbq_tsq"
            return None
        tbq, tsq = qs
        self._update_bias(tbq, tsq)

        if self._tick_i % max(1, self.cfg.every_n_ticks) != 0:
            return None

        if self.position != "flat" and self.entry_price is not None:
            return self._manage(float(ltp))

        if self.cfg.use_fee_gate and self.cfg.tp_points < self.cfg.fee_be_points:
            self.last_skip = "fee_gate"
            return None

        if self.bias == "BULL" and self.last_net > 0:
            if not self._depth_ok("long", message):
                self.last_skip = "depth_block_long"
                return None
            self._open("long", float(ltp))
            return SignalResult(
                action="BUY",
                position_after="long",
                price_delta=None,
                net=self.last_net,
                net_delta=None,
                prev_net_delta=None,
                reason=(
                    f"zigzag_long bias=BULL net={self.last_net:.0f} "
                    f"imb={self.last_imb:.1f}% tbq={tbq:.0f} tsq={tsq:.0f}"
                ),
            )

        if self.bias == "BEAR" and self.last_net < 0:
            if not self._depth_ok("short", message):
                self.last_skip = "depth_block_short"
                return None
            self._open("short", float(ltp))
            return SignalResult(
                action="SHORT",
                position_after="short",
                price_delta=None,
                net=self.last_net,
                net_delta=None,
                prev_net_delta=None,
                reason=(
                    f"zigzag_short bias=BEAR net={self.last_net:.0f} "
                    f"imb={self.last_imb:.1f}% tbq={tbq:.0f} tsq={tsq:.0f}"
                ),
            )

        self.last_skip = f"wait bias={self.bias}"
        return None


def net_zigzag_from_env() -> NetZigzagStrategy:
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
        min_imb_pct=_f("S8_MIN_IMB_PCT", 10.0),
        weaken_pct=_f("S8_WEAKEN_PCT", 10.0),
        tp_points=_f("S8_TP_POINTS", 25.0),
        sl_points=_f("S8_SL_POINTS", 20.0),
        every_n_ticks=int(_f("S8_EVERY_N_TICKS", 1)),
        use_fee_gate=_b("S8_USE_FEE_GATE", False),
        fee_be_points=_f("S8_FEE_BE_POINTS", 50.0),
        require_depth=_b("S8_REQUIRE_DEPTH", False),
        depth_ratio=_f("S8_DEPTH_RATIO", 1.15),
    )
    return NetZigzagStrategy(cfg)
