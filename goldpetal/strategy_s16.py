"""S16_HHHL_WICK_1H — paper 1h close-vs-prev (HH/LL or wick), FLIP.

Wait for the 1h candle to **finish** (first tick of the next hour). Then,
on that **same** closed bar vs the previous closed bar:

  C > prevC → HH/LL only (wicks ignored):
    LONG  = higher high AND green
    SHORT = lower low  AND red
  C < prevC → wick only, min_wick_gap=0 (HH/LL ignored):
    LONG  = lower > upper
    SHORT = upper > lower
  C = prevC → skip

FLIP if already the other side. Fill at this bar's close.
No range skip. No bald-body. No open=high/low (that is S14).
Intraday only: first fill is the first finished 1h of the session;
flatten at MARKET_CLOSE (and leftover at next MARKET_OPEN). Never overnight.
Not live-unlocked. Paper 100 lots ≠ live.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from backtest_hhhl_candles import Candle
from s16_hhhl_wick import s16_bar_decision
from strategy import Position, SignalResult
from strategy_wick import _parse_ts
from wick_candles import wick_measure

IST = ZoneInfo("Asia/Kolkata")
S16_NAME = "S16_HHHL_WICK_1H"


@dataclass
class S16Config:
    bar_minutes: int = 60
    min_wick_gap: float = 0.0
    allow_long: bool = True
    allow_short: bool = True
    market_open: str = "09:00"
    market_close: str = "23:30"


class S16HhhlWickStrategy:
    """Live 1h S16: decide once, on the bar that just finished."""

    name = S16_NAME

    def __init__(self, cfg: S16Config | None = None, *, seed: bool = True) -> None:
        self.cfg = cfg or S16Config()
        self.position: Position = "flat"
        self.entry_price: float | None = None
        self.entry_date: str | None = None
        self.last_skip: str | None = None
        self._bar_key: datetime | None = None
        self._bar_o = self._bar_h = self._bar_l = self._bar_c = None
        self._bar_n = 0
        self._prev: Candle | None = None
        if seed:
            self.seed_from_ticks()

    @property
    def bar_debug(self) -> str:
        if self._bar_o is None or self._bar_h is None or self._bar_l is None:
            prev = (
                f"prevC={self._prev.close:.1f}" if self._prev is not None else "prev=none"
            )
            return f"bar=none {prev}"
        m = wick_measure(
            float(self._bar_o),
            float(self._bar_h),
            float(self._bar_l),
            float(self._bar_c if self._bar_c is not None else self._bar_o),
        )
        prev = (
            f"prevC={self._prev.close:.1f}" if self._prev is not None else "prev=none"
        )
        return (
            f"U={m.upper:.1f} L={m.lower:.1f} "
            f"O={self._bar_o:.1f} H={self._bar_h:.1f} "
            f"L={self._bar_l:.1f} C={self._bar_c} {prev}"
        )

    @property
    def status_line(self) -> str:
        c = self.cfg
        return (
            f"TF={c.bar_minutes}m gap={c.min_wick_gap:g} "
            f"C>prev→HH/LL C<prev→wick FLIP on 1h close "
            f"intraday {c.market_open}-{c.market_close} flatten-at-close "
            f"{self.bar_debug} skip={self.last_skip or '-'} pos={self.position}"
        )

    def _floor_bar(self, ts: datetime) -> datetime:
        ts = ts.astimezone(IST)
        midnight = ts.replace(hour=0, minute=0, second=0, microsecond=0)
        mins = int((ts - midnight).total_seconds() // 60)
        block = (mins // max(1, self.cfg.bar_minutes)) * max(1, self.cfg.bar_minutes)
        return midnight + timedelta(minutes=block)

    def _hhmm(self, raw: str) -> tuple[int, int]:
        h, m = raw.strip().split(":")
        return int(h), int(m)

    def _in_session(self, now: datetime) -> bool:
        if now.weekday() >= 5:
            return False
        oh, om = self._hhmm(self.cfg.market_open)
        ch, cm = self._hhmm(self.cfg.market_close)
        t = now.hour * 60 + now.minute
        return (oh * 60 + om) <= t <= (ch * 60 + cm)

    def _bar_started_in_session(self, bar: Candle) -> bool:
        try:
            dt = datetime.strptime(bar.time[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return False
        oh, om = self._hhmm(self.cfg.market_open)
        ch, cm = self._hhmm(self.cfg.market_close)
        t = dt.hour * 60 + dt.minute
        return (oh * 60 + om) <= t < (ch * 60 + cm)

    def _session_flatten_why(self, now: datetime) -> str | None:
        if self.position == "flat":
            return None
        if now.weekday() >= 5:
            return "weekend flatten"
        oh, om = self._hhmm(self.cfg.market_open)
        ch, cm = self._hhmm(self.cfg.market_close)
        t = now.hour * 60 + now.minute
        if t >= ch * 60 + cm:
            return f"session close {self.cfg.market_close}"
        if t < oh * 60 + om:
            return f"preopen leftover before {self.cfg.market_open}"
        today = now.strftime("%Y-%m-%d")
        if self.entry_date and self.entry_date < today:
            return f"overnight leftover {self.entry_date} flatten at {self.cfg.market_open}"
        return None

    def _flatten(self, px: float, why: str) -> SignalResult:
        prev = self.position
        self.position = "flat"
        self.entry_price = None
        self.entry_date = None
        self.last_skip = why
        return SignalResult(
            action="CLOSE",
            position_after="flat",
            price_delta=None,
            net=None,
            net_delta=None,
            prev_net_delta=None,
            reason=f"{self.name} CLOSE {prev}→flat {why}",
        )

    def _maybe_decide_closed(self, closed: Candle | None, now: datetime) -> SignalResult | None:
        if closed is None:
            return None
        result = None
        if (
            self._in_session(now)
            and closed.time[:10] == now.strftime("%Y-%m-%d")
            and self._bar_started_in_session(closed)
        ):
            result = self._decide_closed(closed)
        if self._bar_started_in_session(closed):
            self._prev = closed
        return result

    def release_decision_lock(self) -> None:
        """No intra-bar lock; kept so the runner's entry-gate retry path is a no-op."""
        return None

    def _reset_bar(self, key: datetime, px: float) -> None:
        self._bar_key = key
        self._bar_o = self._bar_h = self._bar_l = self._bar_c = px
        self._bar_n = 1

    def _closed_candle(self) -> Candle | None:
        if self._bar_key is None or self._bar_o is None:
            return None
        return Candle(
            time=self._bar_key.strftime("%Y-%m-%d %H:%M:%S"),
            open=float(self._bar_o),
            high=float(self._bar_h if self._bar_h is not None else self._bar_o),
            low=float(self._bar_l if self._bar_l is not None else self._bar_o),
            close=float(self._bar_c if self._bar_c is not None else self._bar_o),
        )

    def _seed_position_from_signals(self, db: Path) -> None:
        """Restart must keep the open S16 book so the next 1h bar can FLIP/CLOSE."""
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
                tl = str(row["time_label"] or "")
                self.entry_date = tl[:10] if tl else None
            elif action == "CLOSE" or pos == "flat":
                self.position = "flat"
                self.entry_price = None
                self.entry_date = None
        except Exception:
            return

    def seed_from_ticks(
        self, db_path: Path | None = None, *, now: datetime | None = None
    ) -> None:
        """Hydrate the previous closed 1h bar and the in-progress 1h OHLC."""
        try:
            db = db_path or (Path(__file__).resolve().parent / "data" / "ticks.db")
            if not db.exists():
                return
            self._seed_position_from_signals(db)
            now = (now or datetime.now(IST)).astimezone(IST)
            cur_key = self._floor_bar(now)
            prev_key = cur_key - timedelta(minutes=max(1, self.cfg.bar_minutes))
            start = prev_key.strftime("%Y-%m-%dT%H:%M:%S")
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
            prev_ohlc = self._ohlc_from_rows(rows, prev_key)
            cur_ohlc = self._ohlc_from_rows(rows, cur_key)
            if prev_ohlc is not None:
                o, h, l, c, _n = prev_ohlc
                self._prev = Candle(
                    time=prev_key.strftime("%Y-%m-%d %H:%M:%S"),
                    open=o,
                    high=h,
                    low=l,
                    close=c,
                )
            if cur_ohlc is None:
                return
            o, h, l, c, n = cur_ohlc
            self._reset_bar(cur_key, float(o))
            self._bar_h = float(h)
            self._bar_l = float(l)
            self._bar_c = float(c)
            self._bar_n = max(1, n)
        except Exception:
            return

    def _ohlc_from_rows(
        self, rows: list[tuple[Any, Any]], key: datetime
    ) -> tuple[float, float, float, float, int] | None:
        o = h = l = c = None
        n = 0
        for raw_ts, ltp in rows:
            ts = _parse_ts(raw_ts)
            if ts is None or self._floor_bar(ts) != key:
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
            return None
        return float(o), float(h), float(l), float(c), n

    def on_tick(
        self, now: datetime, ltp: float, message: dict[str, Any] | None = None
    ) -> SignalResult | None:
        del message
        now = now.astimezone(IST)
        key = self._floor_bar(now)
        px = float(ltp)
        flatten_why = self._session_flatten_why(now)

        if self._bar_key is None:
            self._reset_bar(key, px)
            if flatten_why:
                return self._flatten(px, flatten_why)
            self.last_skip = "waiting_1h_close"
            return None

        if key != self._bar_key:
            closed = self._closed_candle()
            result = None if flatten_why else self._maybe_decide_closed(closed, now)
            if flatten_why and closed is not None and self._bar_started_in_session(closed):
                self._prev = closed
            self._reset_bar(key, px)
            if flatten_why:
                return self._flatten(px, flatten_why)
            return result

        assert self._bar_h is not None and self._bar_l is not None
        self._bar_h = max(self._bar_h, px)
        self._bar_l = min(self._bar_l, px)
        self._bar_c = px
        self._bar_n += 1
        if flatten_why:
            return self._flatten(px, flatten_why)
        self.last_skip = "waiting_1h_close"
        return None

    def on_bar_row(self, row: dict[str, Any]) -> SignalResult | None:
        """Offline path from a completed OHLC row (same close-vs-prev formula)."""
        cur = Candle(
            time=str(row.get("time") or ""),
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
        )
        result = self._decide_closed(cur)
        self._prev = cur
        return result

    def _decide_closed(self, cur: Candle | None) -> SignalResult | None:
        if cur is None:
            self.last_skip = "no_closed_bar"
            return None
        if self._prev is None:
            self.last_skip = "need_prev_1h"
            return None
        side, why = s16_bar_decision(
            self._prev, cur, min_wick_gap=float(self.cfg.min_wick_gap)
        )
        if side is None:
            self.last_skip = why
            return None
        return self._flip_to(side, cur, why)

    def _flip_to(self, side: str, cur: Candle, why: str) -> SignalResult | None:
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

        m = wick_measure(cur.open, cur.high, cur.low, cur.close)
        prev = self.position
        if side == "long":
            self.position = "long"
            action = "BUY"
            extra = f"L={m.lower:.1f}>U={m.upper:.1f}"
        else:
            self.position = "short"
            action = "SHORT"
            extra = f"U={m.upper:.1f}>L={m.lower:.1f}"
        self.entry_price = float(cur.close)
        self.entry_date = (cur.time[:10] if cur.time else None)
        kind = "FLIP" if prev != "flat" else "enter"
        prev_c = f" prevC={self._prev.close:.1f}" if self._prev is not None else ""
        return SignalResult(
            action=action,
            position_after=self.position,
            price_delta=None,
            net=None,
            net_delta=None,
            prev_net_delta=None,
            reason=(
                f"{self.name} {kind} {prev}→{self.position} {why} "
                f"{extra} O={cur.open:.1f} H={cur.high:.1f} L={cur.low:.1f} "
                f"C={cur.close:.1f}{prev_c}"
            ),
        )


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


def s16_from_env() -> S16HhhlWickStrategy:
    """S16 paper — 1h close-vs-prev, wick gap 0, FLIP at bar close."""
    _load_dotenv()
    cfg = S16Config(
        bar_minutes=int(os.getenv("S16_BAR_MINUTES", "60")),
        min_wick_gap=float(os.getenv("S16_MIN_WICK_GAP", "0")),
        allow_long=_env_flag("S16_ALLOW_LONG", True),
        allow_short=_env_flag("S16_ALLOW_SHORT", True),
        market_open=os.getenv("MARKET_OPEN", "09:00"),
        market_close=os.getenv("MARKET_CLOSE", "23:30"),
    )
    return S16HhhlWickStrategy(cfg)
