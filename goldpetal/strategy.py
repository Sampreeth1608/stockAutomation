"""30-minute net-pressure strategy (signal logic only)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

Position = Literal["flat", "long", "short"]
Action = Literal["BUY", "SHORT", "HOLD", "CLOSE", "FLAT", "WAIT"]

# Exhaustion: large price move from entry + netΔ stretched vs entry netΔ
DEFAULT_EXHAUSTION_PRICE_PCT = 2.0
DEFAULT_EXHAUSTION_NET_FOLD = 3.0
# Divergence: tiny price change but netΔ surges vs previous netΔ
DEFAULT_LITTLE_PRICE_PCT = 0.1
DEFAULT_NET_SURGE_FOLD = 2.0


@dataclass
class BarSnapshot:
    time_label: str
    cmp: float
    bp: float
    sp: float


@dataclass
class SignalResult:
    action: Action
    position_after: Position
    price_delta: Optional[float]
    net: float
    net_delta: Optional[float]
    prev_net_delta: Optional[float]
    reason: str


class PressureStrategy:
    """Trade only on netΔ; asymmetric hold/close for long vs short."""

    def __init__(
        self,
        exhaustion_price_pct: float = DEFAULT_EXHAUSTION_PRICE_PCT,
        exhaustion_net_fold: float = DEFAULT_EXHAUSTION_NET_FOLD,
        little_price_pct: float = DEFAULT_LITTLE_PRICE_PCT,
        net_surge_fold: float = DEFAULT_NET_SURGE_FOLD,
    ) -> None:
        self.exhaustion_price_pct = exhaustion_price_pct
        self.exhaustion_net_fold = exhaustion_net_fold
        self.little_price_pct = little_price_pct
        self.net_surge_fold = net_surge_fold

        self.position: Position = "flat"
        self.prev_cmp: Optional[float] = None
        self.prev_net: Optional[float] = None
        self.prev_net_delta: Optional[float] = None
        self.entry_cmp: Optional[float] = None
        self.entry_net_delta: Optional[float] = None

    def _reset_entry(self) -> None:
        self.entry_cmp = None
        self.entry_net_delta = None

    def _open(self, side: Position, cmp: float, net_delta: float) -> None:
        self.position = side
        self.entry_cmp = cmp
        self.entry_net_delta = net_delta

    def _close(self) -> None:
        self.position = "flat"
        self._reset_entry()

    def _price_move_pct_from_entry(self, cmp: float) -> Optional[float]:
        if self.entry_cmp is None or self.entry_cmp == 0:
            return None
        return abs(cmp - self.entry_cmp) / self.entry_cmp * 100.0

    def _bar_price_change_pct(self, cmp: float) -> Optional[float]:
        if self.prev_cmp is None or self.prev_cmp == 0:
            return None
        return abs(cmp - self.prev_cmp) / self.prev_cmp * 100.0

    def _exhaustion_close(self, cmp: float, net_delta: float) -> Optional[str]:
        if self.position == "flat" or self.entry_cmp is None or self.entry_net_delta is None:
            return None

        move_pct = self._price_move_pct_from_entry(cmp)
        if move_pct is None or move_pct < self.exhaustion_price_pct:
            return None

        entry_nd = self.entry_net_delta
        if self.position == "long":
            # Favorable move up + netΔ still long and >= 3x entry netΔ
            if cmp > self.entry_cmp and entry_nd > 0 and net_delta >= self.exhaustion_net_fold * entry_nd:
                return (
                    f"exhaustion long: price +{move_pct:.2f}% from entry and "
                    f"netΔ {net_delta} >= {self.exhaustion_net_fold}x entry netΔ {entry_nd}"
                )
        elif self.position == "short":
            # Favorable move down + netΔ still short and <= 3x entry netΔ (more negative)
            if cmp < self.entry_cmp and entry_nd < 0 and net_delta <= self.exhaustion_net_fold * entry_nd:
                return (
                    f"exhaustion short: price -{move_pct:.2f}% from entry and "
                    f"netΔ {net_delta} <= {self.exhaustion_net_fold}x entry netΔ {entry_nd}"
                )
        return None

    def _divergence_close(self, cmp: float, net_delta: float) -> Optional[str]:
        """Price barely moved, but netΔ surged a lot vs previous netΔ."""
        if self.position == "flat" or self.prev_net_delta is None:
            return None

        bar_pct = self._bar_price_change_pct(cmp)
        if bar_pct is None or bar_pct >= self.little_price_pct:
            return None

        prev = self.prev_net_delta
        # "Increased a lot" in numeric terms vs previous netΔ.
        surged = False
        if prev == 0:
            surged = abs(net_delta) > 0
        elif prev > 0:
            surged = net_delta >= self.net_surge_fold * prev
        else:
            # For negative prev, "increased a lot" means jumped upward hard
            # (e.g. -800 -> -100 or to positive), OR magnitude surge deeper.
            # User said "net pressure has increased a lot" with little price change.
            # Use absolute surge: |netΔ| >= fold * |prev| AND netΔ > prev (numeric increase)
            # Actually re-read: "net pressure has increased a lot compared to previous"
            # With our numeric convention, increased = net_delta > prev_net_delta.
            # "a lot" = moved by at least fold * abs(prev) upward.
            surged = (net_delta - prev) >= self.net_surge_fold * abs(prev)

        if not surged:
            return None

        return (
            f"divergence: price change only {bar_pct:.3f}% but netΔ surged "
            f"{prev} -> {net_delta}"
        )

    def on_bar(self, bar: BarSnapshot) -> SignalResult:
        net = bar.bp - bar.sp

        if self.prev_cmp is None or self.prev_net is None:
            self.prev_cmp = bar.cmp
            self.prev_net = net
            return SignalResult(
                action="WAIT",
                position_after=self.position,
                price_delta=None,
                net=net,
                net_delta=None,
                prev_net_delta=None,
                reason="baseline bar stored; waiting for next 30-min bar",
            )

        price_delta = bar.cmp - self.prev_cmp
        net_delta = net - self.prev_net
        prev_net_delta = self.prev_net_delta

        action: Action
        reason: str

        if self.position == "flat":
            if net_delta > 0:
                action = "BUY"
                self._open("long", bar.cmp, net_delta)
                reason = "netΔ > 0"
            elif net_delta < 0:
                action = "SHORT"
                self._open("short", bar.cmp, net_delta)
                reason = "netΔ < 0"
            else:
                action = "FLAT"
                reason = "netΔ == 0; stay flat"
        else:
            # Exit checks first (then can reverse on a later bar; same bar stays flat after close)
            exhaustion = self._exhaustion_close(bar.cmp, net_delta)
            divergence = self._divergence_close(bar.cmp, net_delta)

            if exhaustion:
                action = "CLOSE"
                self._close()
                reason = exhaustion + " | next: watch opposite opportunity"
            elif divergence:
                action = "CLOSE"
                self._close()
                reason = divergence + " | next: watch opposite opportunity"
            elif self.position == "long" and prev_net_delta is not None and net_delta < prev_net_delta:
                action = "CLOSE"
                self._close()
                reason = f"long: netΔ decreased ({prev_net_delta} -> {net_delta})"
            elif self.position == "short" and prev_net_delta is not None and net_delta > prev_net_delta:
                action = "CLOSE"
                self._close()
                reason = f"short: netΔ increased ({prev_net_delta} -> {net_delta})"
            elif self.position == "long" and net_delta <= 0:
                action = "CLOSE"
                self._close()
                reason = "long: netΔ no longer positive"
            elif self.position == "short" and net_delta >= 0:
                action = "CLOSE"
                self._close()
                reason = "short: netΔ no longer negative"
            else:
                action = "HOLD"
                reason = "position intact"

        self.prev_cmp = bar.cmp
        self.prev_net = net
        self.prev_net_delta = net_delta

        return SignalResult(
            action=action,
            position_after=self.position,
            price_delta=price_delta,
            net=net,
            net_delta=net_delta,
            prev_net_delta=prev_net_delta,
            reason=reason,
        )
