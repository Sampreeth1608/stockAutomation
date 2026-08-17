"""S14 / S15 — 30m wick (paper).

S14 formula (nothing else):

  upper = high − max(open, close)
  lower = min(open, close) − low
  lower > upper → LONG
  upper > lower → SHORT
  upper = lower → skip

Same candle: if the dominating wick changes, close and open that side (FLIP).
If a trade is exited, open the side the wick dominates — do not stay flat.

Next candle (in a position or flat): wait 2 minutes from that candle's open.
  open = high (high never left open) → close long, open SHORT
  open = low  (low never left open)  → close existing, open LONG
  open = high and open = low (flat)  → skip this 2-minute rule

No range skip. No bald-body rule. No frac50 / pin2.

S15 is unchanged: last-minute bald body only, HOLD.

Actions are on the in-progress 30m OHLC from ticks.db (fast SQL).
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from strategy import Position, SignalResult
from wick_candles import wick_exit_strict, wick_measure, wick_side

IST = ZoneInfo("Asia/Kolkata")


@dataclass
class WickConfig:
    bar_minutes: int = 30
    min_range: float = 0.0
    confirm_minutes: int = 1
    nowick_eps: float = 1.0
    nowick_body: bool = True
    nowick_only: bool = False
    min_diff: float = 0.0
    min_frac: float = 0.0
    min_body_ratio: float = 0.0
    entry_strict: bool = False
    exit_strict: bool = False
    reenter: bool = False
    allow_long: bool = True
    allow_short: bool = True
    # S14: trade as soon as the wick side changes (not last minute only).
    wick_anytime: bool = False
    # S14: next-candle open=high / open=low confirm, minutes from bar open.
    open_hold_minutes: float = 0.0


class WickCandleStrategy:
    """Live 30m wick. ``name`` is S14_WICK30_STRICT or S15_WICK30_NOWICK."""

    def __init__(
        self,
        name: str,
        cfg: WickConfig | None = None,
        *,
        seed: bool = True,
    ) -> None:
        self.name = name
        self.cfg = cfg or WickConfig()
        self.position: Position = "flat"
        self.entry_price: float | None = None
        self.last_skip: str | None = None
        self._bar_key: datetime | None = None
        self._bar_o = self._bar_h = self._bar_l = self._bar_c = None
        self._bar_n = 0
        self._decided_this_bar = False
        self._open_hold_done = False
        self._open_hold_armed = False
        self._last_wick_seen: str | None = None
        if seed:
            self.seed_from_ticks()

    @property
    def preset_label(self) -> str:
        if self.cfg.nowick_only:
            base = "nowick"
        elif self.cfg.wick_anytime and self.cfg.reenter:
            base = "raw_flip"
        elif self.cfg.entry_strict and self.cfg.exit_strict:
            base = "strict"
        elif self.cfg.exit_strict:
            base = "raw_strict"
        else:
            base = "raw"
        if self.cfg.reenter and not base.endswith("_flip"):
            return f"{base}_flip"
        return base

    @property
    def bar_debug(self) -> str:
        if self._bar_o is None or self._bar_h is None or self._bar_l is None:
            return "bar=none"
        m = wick_measure(
            float(self._bar_o),
            float(self._bar_h),
            float(self._bar_l),
            float(self._bar_c if self._bar_c is not None else self._bar_o),
        )
        return (
            f"U={m.upper:.1f} L={m.lower:.1f} "
            f"dom={m.dominant or '-'} O={self._bar_o:.1f} H={self._bar_h:.1f} "
            f"L={self._bar_l:.1f} C={self._bar_c}"
        )

    @property
    def status_line(self) -> str:
        c = self.cfg
        return (
            f"TF={c.bar_minutes}m {self.preset_label} "
            f"confirm={c.confirm_minutes}m open_hold={c.open_hold_minutes:.0f}m "
            f"wick_anytime={c.wick_anytime} reenter={c.reenter} "
            f"nowick_only={c.nowick_only} "
            f"{'FLIP same-candle reverse' if c.reenter else 'HOLD no-reverse'} "
            f"{self.bar_debug} skip={self.last_skip or '-'} pos={self.position}"
        )

    def _floor_bar(self, ts: datetime) -> datetime:
        ts = ts.astimezone(IST)
        midnight = ts.replace(hour=0, minute=0, second=0, microsecond=0)
        mins = int((ts - midnight).total_seconds() // 60)
        block = (mins // max(1, self.cfg.bar_minutes)) * max(1, self.cfg.bar_minutes)
        return midnight + timedelta(minutes=block)

    def _in_confirm_window(self, now: datetime, bar_key: datetime) -> bool:
        bar_end = bar_key + timedelta(minutes=max(1, self.cfg.bar_minutes))
        start = bar_end - timedelta(minutes=max(1, self.cfg.confirm_minutes))
        return start <= now < bar_end

    def release_decision_lock(self) -> None:
        """Allow another confirm-window attempt (e.g. after entry gate reject)."""
        self._decided_this_bar = False

    def _reset_bar(self, key: datetime, px: float, *, first_tick: datetime) -> None:
        self._bar_key = key
        self._bar_o = self._bar_h = self._bar_l = self._bar_c = px
        self._bar_n = 1
        self._decided_this_bar = False
        self._last_wick_seen = None
        hold_m = float(self.cfg.open_hold_minutes)
        if hold_m > 0:
            window_end = key + timedelta(minutes=hold_m)
            self._open_hold_armed = first_tick < window_end
            self._open_hold_done = first_tick >= window_end
        else:
            self._open_hold_armed = False
            self._open_hold_done = True

    def seed_from_ticks(
        self, db_path: Path | None = None, *, now: datetime | None = None
    ) -> None:
        """Hydrate the in-progress 30m OHLC from ticks.db (indexed received_at)."""
        try:
            db = db_path or (Path(__file__).resolve().parent / "data" / "ticks.db")
            if not db.exists():
                return
            now = (now or datetime.now(IST)).astimezone(IST)
            cur_key = self._floor_bar(now)
            start = cur_key.strftime("%Y-%m-%dT%H:%M:%S")
            con = sqlite3.connect(str(db))
            try:
                rows = con.execute(
                    "SELECT received_at, ltp FROM ticks "
                    "WHERE ltp IS NOT NULL AND received_at >= ? "
                    "ORDER BY received_at ASC, id ASC",
                    (start,),
                ).fetchall()
            finally:
                con.close()
            o = h = l = c = None
            n = 0
            first_ts: datetime | None = None
            for raw_ts, ltp in rows:
                ts = _parse_ts(raw_ts)
                if ts is None:
                    continue
                if self._floor_bar(ts) != cur_key:
                    continue
                px = float(ltp)
                if o is None:
                    o = h = l = c = px
                    n = 1
                    first_ts = ts
                    continue
                h = max(h, px)
                l = min(l, px)
                c = px
                n += 1
            if o is None:
                return
            self._reset_bar(cur_key, float(o), first_tick=first_ts or now)
            self._bar_h = float(h)
            self._bar_l = float(l)
            self._bar_c = float(c)
            self._bar_n = max(1, n)
        except Exception:
            return

    def on_tick(
        self, now: datetime, ltp: float, message: dict[str, Any] | None = None
    ) -> SignalResult | None:
        del message  # OHLC from LTP only
        now = now.astimezone(IST)
        key = self._floor_bar(now)
        px = float(ltp)

        if self._bar_key is None:
            self._reset_bar(key, px, first_tick=now)
            return None

        if key != self._bar_key:
            self._reset_bar(key, px, first_tick=now)
            return None

        assert self._bar_h is not None and self._bar_l is not None
        self._bar_h = max(self._bar_h, px)
        self._bar_l = min(self._bar_l, px)
        self._bar_c = px
        self._bar_n += 1

        o = float(self._bar_o or px)
        h = float(self._bar_h)
        l = float(self._bar_l)
        c = float(self._bar_c or px)

        if float(self.cfg.open_hold_minutes) > 0 and not self._open_hold_done:
            window_end = key + timedelta(minutes=float(self.cfg.open_hold_minutes))
            if now >= window_end:
                self._open_hold_done = True
                if self._open_hold_armed:
                    hold_side = _open_hold_side(o, h, l)
                    if hold_side is not None:
                        self._last_wick_seen = wick_measure(o, h, l, c).dominant
                        result = self._flip_to(
                            hold_side, o, h, l, c, why=_open_hold_reason(hold_side)
                        )
                        if result is not None:
                            return result
                        self.last_skip = "open_hold_same_side"
                    else:
                        self.last_skip = "open_hold_none"

        if self.cfg.wick_anytime:
            want = wick_measure(o, h, l, c).dominant
            if want is None:
                self.last_skip = "equal_wick"
                return None
            if want == self._last_wick_seen:
                self.last_skip = f"wick_unchanged_{want}"
                return None
            result = self._flip_to(want, o, h, l, c, why="wick")
            self._last_wick_seen = want
            return result

        if self._decided_this_bar:
            return None
        if not self._in_confirm_window(now, key):
            self.last_skip = "watching"
            return None

        result = self._decide(o, h, l, c)
        if result is not None and result.action in {"BUY", "SHORT", "CLOSE"}:
            self._decided_this_bar = True
        return result

    def on_bar_row(self, row: dict[str, Any]) -> SignalResult | None:
        """Offline path from a completed OHLC row (wick only; no 2-minute rule)."""
        o = float(row["open"])
        h = float(row["high"])
        l = float(row["low"])
        c = float(row["close"])
        if self.cfg.wick_anytime:
            want = wick_measure(o, h, l, c).dominant
            if want is None:
                self.last_skip = "equal_wick"
                return None
            result = self._flip_to(want, o, h, l, c, why="wick")
            if result is not None:
                self._last_wick_seen = want
            return result
        return self._decide(o, h, l, c)

    def _wick_kwargs(self) -> dict[str, Any]:
        c = self.cfg
        return {
            "min_diff": c.min_diff,
            "min_frac": c.min_frac,
            "min_body_ratio": c.min_body_ratio,
            "min_range": 0.0,
            "nowick_eps": c.nowick_eps,
            "nowick_body": c.nowick_body,
            "nowick_only": c.nowick_only,
        }

    def _flip_to(
        self,
        side: str,
        o: float,
        h: float,
        l: float,
        c: float,
        *,
        why: str,
    ) -> SignalResult | None:
        """Close whatever is open and take ``side`` (BUY or SHORT)."""
        if side == "long" and not self.cfg.allow_long:
            return None
        if side == "short" and not self.cfg.allow_short:
            return None
        if side == "long" and self.position == "long":
            self.last_skip = "already_long"
            return None
        if side == "short" and self.position == "short":
            self.last_skip = "already_short"
            return None

        m = wick_measure(o, h, l, c)
        tag = f"{self.name} {self.preset_label}"
        prev = self.position
        if side == "long":
            self.position = "long"
            self.entry_price = c
            action = "BUY"
            extra = f"L={m.lower:.1f}>U={m.upper:.1f}"
        else:
            self.position = "short"
            self.entry_price = c
            action = "SHORT"
            extra = f"U={m.upper:.1f}>L={m.lower:.1f}"
        kind = "FLIP" if prev != "flat" else "enter"
        return SignalResult(
            action=action,
            position_after=self.position,
            price_delta=None,
            net=None,
            net_delta=None,
            prev_net_delta=None,
            reason=(
                f"{tag} {kind} {prev}→{self.position} {why} "
                f"{extra} O={o:.1f} H={h:.1f} L={l:.1f} C={c:.1f}"
            ),
        )

    def _decide(self, o: float, h: float, l: float, c: float) -> SignalResult | None:
        range_pts = h - l

        if self.cfg.entry_strict:
            side = wick_exit_strict(
                o,
                h,
                l,
                c,
                min_range=0.0,
                nowick_eps=self.cfg.nowick_eps,
                nowick_body=self.cfg.nowick_body,
            )
        else:
            side = wick_side(o, h, l, c, **self._wick_kwargs())
        if self.cfg.exit_strict:
            xside = wick_exit_strict(
                o,
                h,
                l,
                c,
                min_range=0.0,
                nowick_eps=self.cfg.nowick_eps,
                nowick_body=self.cfg.nowick_body,
            )
        else:
            xside = side

        if side == "long" and not self.cfg.allow_long:
            side = None
        if side == "short" and not self.cfg.allow_short:
            side = None
        if xside == "long" and not self.cfg.allow_long:
            xside = None
        if xside == "short" and not self.cfg.allow_short:
            xside = None

        tag = f"{self.name} {self.preset_label}"
        m = wick_measure(o, h, l, c)

        if self.position == "long":
            if xside == "short":
                if self.cfg.reenter and side == "short":
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
                            f"{tag} FLIP long→short range={range_pts:.1f} "
                            f"U={m.upper:.1f} L={m.lower:.1f} C={c:.1f}"
                        ),
                    )
                self.position = "flat"
                self.entry_price = None
                return SignalResult(
                    action="CLOSE",
                    position_after="flat",
                    price_delta=None,
                    net=None,
                    net_delta=None,
                    prev_net_delta=None,
                    reason=(
                        f"{tag} HOLD exit long range={range_pts:.1f} "
                        f"U={m.upper:.1f} L={m.lower:.1f} C={c:.1f}"
                    ),
                )
            self.last_skip = "hold_long" if side != "short" else "weak_opposite"
            return None

        if self.position == "short":
            if xside == "long":
                if self.cfg.reenter and side == "long":
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
                            f"{tag} FLIP short→long range={range_pts:.1f} "
                            f"U={m.upper:.1f} L={m.lower:.1f} C={c:.1f}"
                        ),
                    )
                self.position = "flat"
                self.entry_price = None
                return SignalResult(
                    action="CLOSE",
                    position_after="flat",
                    price_delta=None,
                    net=None,
                    net_delta=None,
                    prev_net_delta=None,
                    reason=(
                        f"{tag} HOLD exit short range={range_pts:.1f} "
                        f"U={m.upper:.1f} L={m.lower:.1f} C={c:.1f}"
                    ),
                )
            self.last_skip = "hold_short" if side != "long" else "weak_opposite"
            return None

        if side == "long":
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
                    f"{tag} long last-minute wick range={range_pts:.1f} "
                    f"L={m.lower:.1f}>U={m.upper:.1f} C={c:.1f} O={o:.1f}"
                ),
            )
        if side == "short":
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
                    f"{tag} short last-minute wick range={range_pts:.1f} "
                    f"U={m.upper:.1f}>L={m.lower:.1f} C={c:.1f} O={o:.1f}"
                ),
            )
        self.last_skip = "no_signal"
        return None


def _open_hold_side(open_: float, high: float, low: float) -> str | None:
    """After 2 minutes: O=H → short, O=L → long. Both (flat) → skip."""
    at_high = float(high) == float(open_)
    at_low = float(low) == float(open_)
    if at_high and at_low:
        return None
    if at_high:
        return "short"
    if at_low:
        return "long"
    return None


def _open_hold_reason(side: str) -> str:
    if side == "short":
        return "open=high 2m"
    return "open=low 2m"


def _parse_ts(raw: str) -> datetime | None:
    s = str(raw).strip()
    if not s:
        return None
    if " " in s[:19] and "T" not in s[:19]:
        s = s.replace(" ", "T", 1)
    try:
        dt = datetime.fromisoformat(s[:32])
    except ValueError:
        try:
            dt = datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    return dt.astimezone(IST)


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "y"}


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
    except Exception:
        pass


def wick_strict_from_env() -> WickCandleStrategy:
    """S14 — raw wick FLIP + 2-minute open=high / open=low on the next bar."""
    _load_dotenv()
    cfg = WickConfig(
        bar_minutes=int(os.getenv("S14_BAR_MINUTES", "30")),
        min_range=0.0,
        confirm_minutes=int(os.getenv("S14_CONFIRM_MINUTES", "1")),
        nowick_eps=float(os.getenv("S14_NOWICK_EPS", "1")),
        nowick_body=False,
        nowick_only=False,
        min_diff=0.0,
        min_frac=0.0,
        min_body_ratio=0.0,
        entry_strict=False,
        exit_strict=False,
        allow_long=_env_flag("S14_ALLOW_LONG", True),
        allow_short=_env_flag("S14_ALLOW_SHORT", True),
        reenter=True,
        wick_anytime=True,
        open_hold_minutes=float(os.getenv("S14_OPEN_HOLD_MINUTES", "2")),
    )
    return WickCandleStrategy("S14_WICK30_STRICT", cfg)


def wick_strict_flip_from_env() -> WickCandleStrategy:
    """S14 already FLIPs. Kept so older imports still resolve."""
    s = wick_strict_from_env()
    s.name = "S16_WICK30_STRICT_FLIP"
    return s


def wick_nowick_from_env() -> WickCandleStrategy:
    """S15 — 30m:nowick HOLD (bald body only)."""
    _load_dotenv()
    cfg = WickConfig(
        bar_minutes=int(os.getenv("S15_BAR_MINUTES", "30")),
        min_range=0.0,
        confirm_minutes=int(os.getenv("S15_CONFIRM_MINUTES", "1")),
        nowick_eps=float(os.getenv("S15_NOWICK_EPS", "1")),
        nowick_body=True,
        nowick_only=True,
        min_diff=0.0,
        min_frac=0.0,
        min_body_ratio=0.0,
        exit_strict=False,
        allow_long=_env_flag("S15_ALLOW_LONG", True),
        allow_short=_env_flag("S15_ALLOW_SHORT", True),
        reenter=False,
        wick_anytime=False,
        open_hold_minutes=0.0,
    )
    return WickCandleStrategy("S15_WICK30_NOWICK", cfg)
