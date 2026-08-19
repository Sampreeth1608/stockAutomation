"""S7_FLOW_BRAIN — paper tick pressure brain. Not S16. Not live.

ENABLE_S7 defaults false. Paper 100 lots ≠ live. Stay DRY_RUN.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from flow_brain import BOOK, FlowBrain, classify_message
from strategy import Position, SignalResult


class S7FlowBrainStrategy:
    name = BOOK

    def __init__(
        self,
        *,
        market_open: str = "09:00",
        market_close: str = "23:30",
        min_hold_s: float = 20.0,
        cooldown_s: float = 15.0,
    ) -> None:
        self.market_open = market_open
        self.market_close = market_close
        self.min_hold_s = float(min_hold_s)
        self.cooldown_s = float(cooldown_s)
        self.brain = FlowBrain()
        self.position: Position = "flat"
        self.entry_price: float | None = None
        self._entry_t = 0.0
        self._last_exit_t = -1e18
        self.last_skip: str | None = None
        self.last_state = ""
        self.last_want: str | None = None

    @property
    def status_line(self) -> str:
        snap = self.brain.last
        extra = (
            f"st={snap.state} imb5={snap.flow_imb_5s:+.2f} "
            f"dLTP5={snap.d_ltp_5s:+.1f} exp={int(snap.expanding)} "
            f"atr={snap.atr:.1f}"
            if snap
            else "warming"
        )
        return (
            f"tick LTP+TBQ+TSQ {self.market_open}-{self.market_close} "
            f"{extra} skip={self.last_skip or '-'} pos={self.position}"
        )

    def _hhmm_ok(self, now: datetime) -> bool:
        hhmm = now.strftime("%H:%M")
        return self.market_open <= hhmm <= self.market_close

    def on_tick(
        self, now: datetime, ltp: float, message: dict[str, Any]
    ) -> SignalResult | None:
        if not self._hhmm_ok(now):
            if self.position != "flat" and self.entry_price is not None:
                side = self.position
                self.position = "flat"
                self.entry_price = None
                self._last_exit_t = now.timestamp()
                self.last_skip = "session_flatten"
                return SignalResult(
                    action="CLOSE",
                    position_after="flat",
                    price_delta=None,
                    net=0.0,
                    net_delta=None,
                    prev_net_delta=None,
                    reason=f"session flatten was_{side}",
                )
            self.last_skip = "outside_session"
            return None
        snap = classify_message(self.brain, now, ltp, message)
        if snap is None:
            return None
        self.last_state = snap.state
        self.last_want = snap.want
        t = snap.t
        want = snap.want

        if self.position == "long":
            held = t - self._entry_t
            kill = snap.state in {"bear_cont", "absorb_buy"} or (
                snap.decaying and snap.state != "bull_cont"
            )
            if want == "short" and held >= self.min_hold_s:
                self.position = "short"
                self.entry_price = snap.ltp
                self._entry_t = t
                self.last_skip = None
                return SignalResult(
                    action="SHORT",
                    position_after="short",
                    price_delta=snap.d_ltp_5s,
                    net=snap.flow_imb_5s,
                    net_delta=snap.imb_vel,
                    prev_net_delta=None,
                    reason=f"FLIP short {snap.why}",
                )
            if kill and held >= self.min_hold_s:
                self.position = "flat"
                self.entry_price = None
                self._last_exit_t = t
                self.last_skip = snap.why
                return SignalResult(
                    action="CLOSE",
                    position_after="flat",
                    price_delta=snap.d_ltp_5s,
                    net=snap.flow_imb_5s,
                    net_delta=snap.imb_vel,
                    prev_net_delta=None,
                    reason=f"decay/kill long {snap.why}",
                )
            self.last_skip = f"hold_long {snap.why}"
            return None

        if self.position == "short":
            held = t - self._entry_t
            kill = snap.state in {"bull_cont", "absorb_sell"} or (
                snap.decaying and snap.state != "bear_cont"
            )
            if want == "long" and held >= self.min_hold_s:
                self.position = "long"
                self.entry_price = snap.ltp
                self._entry_t = t
                self.last_skip = None
                return SignalResult(
                    action="BUY",
                    position_after="long",
                    price_delta=snap.d_ltp_5s,
                    net=snap.flow_imb_5s,
                    net_delta=snap.imb_vel,
                    prev_net_delta=None,
                    reason=f"FLIP long {snap.why}",
                )
            if kill and held >= self.min_hold_s:
                self.position = "flat"
                self.entry_price = None
                self._last_exit_t = t
                self.last_skip = snap.why
                return SignalResult(
                    action="CLOSE",
                    position_after="flat",
                    price_delta=snap.d_ltp_5s,
                    net=snap.flow_imb_5s,
                    net_delta=snap.imb_vel,
                    prev_net_delta=None,
                    reason=f"decay/kill short {snap.why}",
                )
            self.last_skip = f"hold_short {snap.why}"
            return None

        if t - self._last_exit_t < self.cooldown_s:
            self.last_skip = "cooldown"
            return None
        if want == "long":
            self.position = "long"
            self.entry_price = snap.ltp
            self._entry_t = t
            self.last_skip = None
            return SignalResult(
                action="BUY",
                position_after="long",
                price_delta=snap.d_ltp_5s,
                net=snap.flow_imb_5s,
                net_delta=snap.imb_vel,
                prev_net_delta=None,
                reason=snap.why,
            )
        if want == "short":
            self.position = "short"
            self.entry_price = snap.ltp
            self._entry_t = t
            self.last_skip = None
            return SignalResult(
                action="SHORT",
                position_after="short",
                price_delta=snap.d_ltp_5s,
                net=snap.flow_imb_5s,
                net_delta=snap.imb_vel,
                prev_net_delta=None,
                reason=snap.why,
            )
        self.last_skip = snap.why
        return None


def s7_from_env() -> S7FlowBrainStrategy:
    from strategy_s18 import _load_dotenv

    _load_dotenv()
    return S7FlowBrainStrategy(
        market_open=os.getenv("MARKET_OPEN", "09:00"),
        market_close=os.getenv("MARKET_CLOSE", "23:30"),
        min_hold_s=float(os.getenv("S7_MIN_HOLD_SEC", "20") or 20),
        cooldown_s=float(os.getenv("S7_COOLDOWN_SEC", "15") or 15),
    )
