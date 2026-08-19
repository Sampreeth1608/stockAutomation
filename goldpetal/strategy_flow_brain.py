"""FLOW_BRAIN — paper tick pressure. Not S7_HOURLY. Not S16. Not live.

ENABLE_FLOW_BRAIN defaults false. Paper 100 lots ≠ live. Stay DRY_RUN.
Live defaults stay the v1 pack so the short synthetic BUY test still
matches the unfiltered simulator. Gate packs are opt-in via
FLOW_BRAIN_GATE_PACK (research). Do not ENABLE.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from flow_brain import (
    BOOK,
    GATE_PACKS,
    FlowGates,
    classify_message,
    gated_want,
    kill_long,
    kill_short,
    manage_open,
    s5_targets,
)
from strategy import Position, SignalResult


class FlowBrainLiveStrategy:
    name = BOOK

    def __init__(
        self,
        *,
        market_open: str = "09:00",
        market_close: str = "23:30",
        min_hold_s: float | None = None,
        cooldown_s: float | None = None,
        gates: FlowGates | None = None,
    ) -> None:
        self.gates = gates or FlowGates(name="v1")
        self.market_open = market_open
        self.market_close = market_close
        self.min_hold_s = float(
            self.gates.min_hold_s if min_hold_s is None else min_hold_s
        )
        self.cooldown_s = float(
            self.gates.cooldown_s if cooldown_s is None else cooldown_s
        )
        self.brain = self.gates.brain()
        self.position: Position = "flat"
        self.entry_price: float | None = None
        self._entry_t = 0.0
        self._last_exit_t = -1e18
        self._pending_want: str | None = None
        self._pending_since = 0.0
        self._drop_streak = 0
        self._entry_tp = 0.0
        self._entry_sl = 0.0
        self.last_skip: str | None = None
        self.last_state = ""
        self.last_want: str | None = None

    @property
    def status_line(self) -> str:
        snap = self.brain.last
        extra = (
            f"st={snap.state} imb5={snap.flow_imb_5s:+.2f} "
            f"imb%={snap.imb_pct:.1f} dLTP5={snap.d_ltp_5s:+.1f} "
            f"exp={int(snap.expanding)} atr={snap.atr:.1f} "
            f"s19={snap.s19_1m or '-'}"
            if snap
            else "warming"
        )
        return (
            f"tick LTP+TBQ+TSQ pack={self.gates.name} {self.market_open}-{self.market_close} "
            f"{extra} skip={self.last_skip or '-'} pos={self.position}"
        )

    def _hhmm_ok(self, now: datetime) -> bool:
        hhmm = now.strftime("%H:%M")
        return self.market_open <= hhmm <= self.market_close

    def _want(self, snap: Any) -> str | None:
        return gated_want(snap, self.gates)

    def _enter(self, snap: Any, side: Position, action: str, reason: str) -> SignalResult:
        self.position = side
        self.entry_price = snap.ltp
        self._entry_t = snap.t
        self._pending_want = None
        self._pending_since = 0.0
        self._drop_streak = 0
        self._entry_tp, self._entry_sl = s5_targets(self.gates, snap)
        self.last_skip = None
        return SignalResult(
            action=action,
            position_after=side,
            price_delta=snap.d_ltp_5s,
            net=snap.flow_imb_5s,
            net_delta=snap.imb_vel,
            prev_net_delta=None,
            reason=reason,
        )

    def _close(self, snap: Any, reason: str) -> SignalResult:
        self.position = "flat"
        self.entry_price = None
        self._last_exit_t = snap.t
        self._pending_want = None
        self._pending_since = 0.0
        self._drop_streak = 0
        self.last_skip = snap.why
        return SignalResult(
            action="CLOSE",
            position_after="flat",
            price_delta=snap.d_ltp_5s,
            net=snap.flow_imb_5s,
            net_delta=snap.imb_vel,
            prev_net_delta=None,
            reason=reason,
        )

    def on_tick(
        self, now: datetime, ltp: float, message: dict[str, Any]
    ) -> SignalResult | None:
        if not self._hhmm_ok(now):
            if self.position != "flat" and self.entry_price is not None:
                side = self.position
                self.position = "flat"
                self.entry_price = None
                self._last_exit_t = now.timestamp()
                self._pending_want = None
                self._drop_streak = 0
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
        raw_want = snap.want
        want = self._want(snap)
        self.last_want = want
        t = snap.t
        persist = bool(self.gates.persist_until_opposite)
        allow_flip = bool(self.gates.allow_flip)
        confirm_s = float(self.gates.confirm_s)

        if self.position == "long":
            held = t - self._entry_t
            managed, self._drop_streak = manage_open(
                side="LONG",
                snap=snap,
                entry_px=float(self.entry_price or snap.ltp),
                held=held,
                min_hold=self.min_hold_s,
                g=self.gates,
                drop_streak=self._drop_streak,
                tp_pts=self._entry_tp,
                sl_pts=self._entry_sl,
            )
            if managed is not None:
                return self._close(snap, f"{managed} long {snap.why}")
            opposite = raw_want == "short" or want == "short"
            if opposite and held >= self.min_hold_s:
                closed = self._close(snap, f"opp short {snap.why}")
                if allow_flip and want == "short":
                    return self._enter(snap, "short", "SHORT", f"FLIP short {snap.why}")
                return closed
            if kill_long(snap, persist) and held >= self.min_hold_s:
                return self._close(snap, f"decay/kill long {snap.why}")
            self.last_skip = f"hold_long {snap.why}"
            return None

        if self.position == "short":
            held = t - self._entry_t
            managed, self._drop_streak = manage_open(
                side="SHORT",
                snap=snap,
                entry_px=float(self.entry_price or snap.ltp),
                held=held,
                min_hold=self.min_hold_s,
                g=self.gates,
                drop_streak=self._drop_streak,
                tp_pts=self._entry_tp,
                sl_pts=self._entry_sl,
            )
            if managed is not None:
                return self._close(snap, f"{managed} short {snap.why}")
            opposite = raw_want == "long" or want == "long"
            if opposite and held >= self.min_hold_s:
                closed = self._close(snap, f"opp long {snap.why}")
                if allow_flip and want == "long":
                    return self._enter(snap, "long", "BUY", f"FLIP long {snap.why}")
                return closed
            if kill_short(snap, persist) and held >= self.min_hold_s:
                return self._close(snap, f"decay/kill short {snap.why}")
            self.last_skip = f"hold_short {snap.why}"
            return None

        if t - self._last_exit_t < self.cooldown_s:
            self.last_skip = "cooldown"
            self._pending_want = None
            return None
        if want is None:
            self._pending_want = None
            self.last_skip = snap.why
            return None
        if confirm_s > 0:
            if self._pending_want != want:
                self._pending_want = want
                self._pending_since = t
                self.last_skip = f"confirm {want}"
                return None
            if t - self._pending_since < confirm_s:
                self.last_skip = f"confirm {want}"
                return None
        if want == "long":
            return self._enter(snap, "long", "BUY", snap.why)
        if want == "short":
            return self._enter(snap, "short", "SHORT", snap.why)
        self.last_skip = snap.why
        return None


def flow_brain_from_env() -> FlowBrainLiveStrategy:
    from strategy_s18 import _load_dotenv

    _load_dotenv()
    pack = (os.getenv("FLOW_BRAIN_GATE_PACK") or "v1").strip().lower()
    gates = GATE_PACKS.get(pack) or GATE_PACKS["v1"]
    min_hold: float | None = None
    cooldown: float | None = None
    if gates.name == "v1":
        min_hold = float(os.getenv("FLOW_BRAIN_MIN_HOLD_SEC", "20") or 20)
        cooldown = float(os.getenv("FLOW_BRAIN_COOLDOWN_SEC", "15") or 15)
    return FlowBrainLiveStrategy(
        market_open=os.getenv("MARKET_OPEN", "09:00"),
        market_close=os.getenv("MARKET_CLOSE", "23:30"),
        min_hold_s=min_hold,
        cooldown_s=cooldown,
        gates=gates,
    )
