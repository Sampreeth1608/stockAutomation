"""S14 / S15 — 30m wick HOLD (paper).

S14 uses one rule on every candle (entry and exit). No high−low skip.

  if upper ≤ 1 and lower ≤ 1:          # bald
      close > open → LONG
      close < open → SHORT
      close = open → skip
  else:
      winning wick ≥ 0.5 × range → that side
      or winning wick ≥ 2 × body → that side
      else skip

Action is in that 30m candle's last minute — never the next bar's first tick.

HOLD: opposite → CLOSE. Do not reverse on that same candle.
S15 is bald-only (nowick). FLIP is not papered.

Defaults: 30m bars, confirm=1 minute, nowick_eps=1 pt.
Current in-progress 30m OHLC is seeded from ticks.db (fast SQL, not raw_json).
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


class WickCandleStrategy:
    """Live 30m wick HOLD. ``name`` is S14_WICK30_STRICT or S15_WICK30_NOWICK."""

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
        # Lock BUY/SHORT/CLOSE for this candle — HOLD forbids same-bar reverse.
        self._decided_this_bar = False
        if seed:
            self.seed_from_ticks()

    @property
    def preset_label(self) -> str:
        if self.cfg.nowick_only:
            base = "nowick"
        elif self.cfg.entry_strict and self.cfg.exit_strict:
            base = "strict"
        elif self.cfg.exit_strict:
            base = "raw_strict"
        else:
            base = "raw"
        return f"{base}_flip" if self.cfg.reenter else base

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
            f"U={m.upper:.1f} L={m.lower:.1f} bald={int(m.bald(self.cfg.nowick_eps))} "
            f"dom={m.dominant or '-'} O={self._bar_o:.1f} H={self._bar_h:.1f} "
            f"L={self._bar_l:.1f} C={self._bar_c}"
        )

    @property
    def status_line(self) -> str:
        c = self.cfg
        return (
            f"TF={c.bar_minutes}m {self.preset_label} min_range={c.min_range:.0f} "
            f"confirm={c.confirm_minutes}m nowick_eps={c.nowick_eps:.0f} "
            f"entry_strict={c.entry_strict} exit_strict={c.exit_strict} "
            f"reenter={c.reenter} nowick_only={c.nowick_only} "
            f"{'FLIP same-candle reverse' if c.reenter else 'HOLD no-reverse'} {self.bar_debug} "
            f"skip={self.last_skip or '-'} pos={self.position}"
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
                    continue
                h = max(h, px)
                l = min(l, px)
                c = px
                n += 1
            if o is None:
                return
            self._bar_key = cur_key
            self._bar_o = float(o)
            self._bar_h = float(h)
            self._bar_l = float(l)
            self._bar_c = float(c)
            self._bar_n = max(1, n)
            self._decided_this_bar = False
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
            self._bar_key = key
            self._bar_o = self._bar_h = self._bar_l = self._bar_c = px
            self._bar_n = 1
            self._decided_this_bar = False
            return None

        if key != self._bar_key:
            # Candle ended. Do not decide on this new bar's first tick.
            self._bar_key = key
            self._bar_o = self._bar_h = self._bar_l = self._bar_c = px
            self._bar_n = 1
            self._decided_this_bar = False
            return None

        assert self._bar_h is not None and self._bar_l is not None
        self._bar_h = max(self._bar_h, px)
        self._bar_l = min(self._bar_l, px)
        self._bar_c = px
        self._bar_n += 1

        if self._decided_this_bar:
            return None
        if not self._in_confirm_window(now, key):
            self.last_skip = "watching"
            return None

        result = self._decide(
            float(self._bar_o or px),
            float(self._bar_h),
            float(self._bar_l),
            float(self._bar_c or px),
        )
        if result is not None and result.action in {"BUY", "SHORT", "CLOSE"}:
            self._decided_this_bar = True
        return result

    def on_bar_row(self, row: dict[str, Any]) -> SignalResult | None:
        """Offline / paper-sim path from a completed OHLC row."""
        return self._decide(
            float(row["open"]),
            float(row["high"]),
            float(row["low"]),
            float(row["close"]),
        )

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
    """S14 — 30m:strict HOLD (bald / frac50 / pin2 on every candle)."""
    _load_dotenv()
    cfg = WickConfig(
        bar_minutes=int(os.getenv("S14_BAR_MINUTES", "30")),
        min_range=0.0,
        confirm_minutes=int(os.getenv("S14_CONFIRM_MINUTES", "1")),
        nowick_eps=float(os.getenv("S14_NOWICK_EPS", "1")),
        nowick_body=True,
        nowick_only=False,
        min_diff=0.0,
        min_frac=0.0,
        min_body_ratio=0.0,
        entry_strict=True,
        exit_strict=True,
        allow_long=_env_flag("S14_ALLOW_LONG", True),
        allow_short=_env_flag("S14_ALLOW_SHORT", True),
        reenter=False,
    )
    return WickCandleStrategy("S14_WICK30_STRICT", cfg)


def wick_strict_flip_from_env() -> WickCandleStrategy:
    """Same as S14 but FLIP: close and reverse on that same last-minute candle.

    Not loaded by run_strategy until a backtest row is picked as a new S-number.
    """
    s = wick_strict_from_env()
    s.cfg.reenter = True
    s.name = "S16_WICK30_STRICT_FLIP"
    return s


def wick_nowick_from_env() -> WickCandleStrategy:
    """S15 — 30m:nowick HOLD (bald body only)."""
    _load_dotenv()
    cfg = WickConfig(
        bar_minutes=int(os.getenv("S15_BAR_MINUTES", "30")),
        min_range=0.0,  # no H−L skip; not in the wick formula
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
    )
    return WickCandleStrategy("S15_WICK30_NOWICK", cfg)
