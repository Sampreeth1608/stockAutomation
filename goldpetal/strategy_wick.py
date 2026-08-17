"""S14 / S15 — 30m wick (paper).

S14 formula (nothing else):

  upper = high − max(open, close)
  lower = min(open, close) − low
  lower > upper → LONG
  upper > lower → SHORT
  upper = lower → skip

Wait for the candle to **finish** (first tick of the next bar). On that
**same** closed candle, in this order:

  open = high (high never left open) → SHORT
  open = low  (low never left open)  → LONG
  open = high and open = low (flat)  → skip this check, use the wick
  otherwise → wick: lower>upper LONG, upper>lower SHORT, equal skip

If that side differs from the open trade, close and open it (FLIP).
Do not stay flat after an exit. No +2-minute look while the bar is forming.

No range skip. No bald-body rule. No frac50 / pin2.

S15 is unchanged: last-minute bald body only, HOLD.
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
    # Intra-bar wick FLIP on every tick (off for S14 — too many trades).
    wick_anytime: bool = False
    # S14: wick formula once, on the finished candle (bar roll).
    wick_on_close: bool = False
    # Legacy intra-bar timer (off for S14). Kept so old configs still parse.
    open_hold_minutes: float = 0.0
    # S14: open=high / open=low on the same finished candle as the wick.
    open_hold_on_close: bool = False


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
        elif self.cfg.reenter and (self.cfg.wick_anytime or self.cfg.wick_on_close):
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
            f"confirm={c.confirm_minutes}m "
            f"open_hold_on_close={c.open_hold_on_close} "
            f"wick_on_close={c.wick_on_close} wick_anytime={c.wick_anytime} "
            f"reenter={c.reenter} nowick_only={c.nowick_only} "
            f"{'FLIP on closed bar' if c.wick_on_close else ('FLIP same-candle reverse' if c.reenter else 'HOLD no-reverse')} "
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

    def _seed_position_from_signals(self, db: Path) -> None:
        """Restart must keep the open S14 book so the next bar can FLIP/CLOSE."""
        try:
            from storage import latest_signals

            last = latest_signals(limit=1, strategy=self.name, db_path=db)
            if not last:
                return
            row = last[0]
            action = str(row["action"] or "").upper()
            pos = str(row["position_after"] or "").lower()
            if action in {"BUY", "SHORT"} and pos in {"long", "short"}:
                self.position = pos  # type: ignore[assignment]
                cmp = row["cmp"]
                self.entry_price = float(cmp) if cmp is not None else None
            elif action == "CLOSE" or pos == "flat":
                self.position = "flat"
                self.entry_price = None
        except Exception:
            return

    def seed_from_ticks(
        self, db_path: Path | None = None, *, now: datetime | None = None
    ) -> None:
        """Hydrate the in-progress 30m OHLC from ticks.db (indexed received_at)."""
        try:
            db = db_path or (Path(__file__).resolve().parent / "data" / "ticks.db")
            if not db.exists():
                return
            self._seed_position_from_signals(db)
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
            closed_result = self._maybe_wick_on_closed_bar()
            self._reset_bar(key, px, first_tick=now)
            return closed_result

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

        if self.cfg.wick_on_close:
            if self.last_skip not in {"open_hold_none", "open_hold_same_side"}:
                self.last_skip = "waiting_bar_close"
            return None

        if self._decided_this_bar:
            return None
        if not self._in_confirm_window(now, key):
            self.last_skip = "watching"
            return None

        result = self._decide(o, h, l, c)
        if result is not None and result.action in {"BUY", "SHORT", "CLOSE"}:
            self._decided_this_bar = True
        return result

    def _maybe_wick_on_closed_bar(self) -> SignalResult | None:
        """S14: one wick decision on the bar that just finished."""
        if not self.cfg.wick_on_close:
            return None
        if self._bar_o is None or self._bar_h is None or self._bar_l is None:
            return None
        o = float(self._bar_o)
        h = float(self._bar_h)
        l = float(self._bar_l)
        c = float(self._bar_c if self._bar_c is not None else self._bar_o)
        return self._wick_closed(o, h, l, c)

    def _wick_closed(self, o: float, h: float, l: float, c: float) -> SignalResult | None:
        want, why = s14_bar_decision(
            o, h, l, c, open_hold=self.cfg.open_hold_on_close
        )
        if want is None:
            self.last_skip = why
            return None
        result = self._flip_to(want, o, h, l, c, why=why)
        self._last_wick_seen = want
        if result is None and why.startswith("open="):
            self.last_skip = "open_hold_same_side"
        return result

    def on_bar_row(self, row: dict[str, Any]) -> SignalResult | None:
        """Offline path from a completed OHLC row (same-candle open=high/low + wick)."""
        o = float(row["open"])
        h = float(row["high"])
        l = float(row["low"])
        c = float(row["close"])
        if self.cfg.wick_anytime or self.cfg.wick_on_close:
            return self._wick_closed(o, h, l, c)
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
    """Same closed candle: O=H → short, O=L → long. Both (flat) → skip."""
    at_high = float(high) == float(open_)
    at_low = float(low) == float(open_)
    if at_high and at_low:
        return None
    if at_high:
        return "short"
    if at_low:
        return "long"
    return None


def wick_record_actions(
    prev_position: str, result: Any
) -> list[tuple[str, str]]:
    """DB actions for one wick result. FLIP → CLOSE then BUY/SHORT (Watch closed+opened)."""
    if result is None:
        return []
    action = str(getattr(result, "action", "") or "").upper()
    if action not in {"BUY", "SHORT", "CLOSE"}:
        return []
    prev = str(prev_position or "flat").lower()
    pos_after = str(getattr(result, "position_after", "") or "").lower()
    if action == "BUY":
        pos_after = pos_after or "long"
    elif action == "SHORT":
        pos_after = pos_after or "short"
    else:
        pos_after = pos_after or "flat"
    out: list[tuple[str, str]] = []
    if action in {"BUY", "SHORT"} and prev in {"long", "short"}:
        out.append(("CLOSE", "flat"))
    out.append((action, pos_after))
    return out


def s14_bar_decision(
    o: float,
    h: float,
    l: float,
    c: float,
    *,
    open_hold: bool = True,
) -> tuple[str | None, str]:
    """One finished candle → ('long'|'short'|None, why)."""
    if open_hold:
        hold = _open_hold_side(o, h, l)
        if hold is not None:
            return hold, _open_hold_reason(hold)
    want = wick_measure(o, h, l, c).dominant
    if want is None:
        return None, "equal_wick"
    return want, "wick (bar closed)"


def _open_hold_reason(side: str) -> str:
    if side == "short":
        return "open=high (same candle)"
    return "open=low (same candle)"


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
    """S14 — same closed candle: open=high/low first, else wick FLIP."""
    _load_dotenv()
    hold_raw = os.getenv("S14_OPEN_HOLD_MINUTES", "2")
    try:
        hold_on = float(hold_raw) > 0
    except ValueError:
        hold_on = True
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
        wick_anytime=False,
        wick_on_close=True,
        open_hold_minutes=0.0,
        open_hold_on_close=hold_on,
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
        wick_on_close=False,
        open_hold_minutes=0.0,
        open_hold_on_close=False,
    )
    return WickCandleStrategy("S15_WICK30_NOWICK", cfg)
