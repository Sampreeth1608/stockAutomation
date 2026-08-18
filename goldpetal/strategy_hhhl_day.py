"""S13_HHHL_DAY — daily S16 close-vs-prev, last-15m same-day fill (paper).

S16 formula on the **trading day** vs **previous day**. Confirm only in the
last 15 minutes of *this* day (`MARKET_CLOSE` 23:30 → 23:15–23:30). Never
the next day's open (that is S4). Fill at last-15m LTP.

  C > prevC → HH/LL only (wicks ignored):
    LONG  = higher high AND green
    SHORT = lower low  AND red
  C < prevC → wick only, min_wick_gap=0 (HH/LL ignored):
    LONG  = lower > upper
    SHORT = upper > lower
  C = prevC → skip

FLIP if already the other side. Stay in until the opposite signal (or
emergency / live flatten). No min_range. No fakeout close-beyond. No
bald-body. No open=high/low (that is S14).

Does not replace S4 ML overnight — separate paper strategy. S16 1h stays
its own book.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from backtest_hhhl_candles import Candle
from s16_hhhl_wick import s16_bar_decision
from strategy import Position, SignalResult
from wick_candles import wick_measure

IST = ZoneInfo("Asia/Kolkata")
DEFAULT_STATE_PATH = Path(__file__).resolve().parent / "data" / "s13_state.json"


@dataclass
class DayOhlc:
    date: str
    open: float
    high: float
    low: float
    close: float

    @property
    def range_pts(self) -> float:
        return float(self.high - self.low)

    @property
    def green(self) -> bool:
        return self.close > self.open


def _as_candle(bar: DayOhlc) -> Candle:
    return Candle(
        time=f"{bar.date} 00:00:00",
        open=float(bar.open),
        high=float(bar.high),
        low=float(bar.low),
        close=float(bar.close),
    )


@dataclass
class HhhlDayConfig:
    # Unused (old HH/LL-only). Kept so existing .env / tests still construct.
    min_range: float = 5.0
    # Confirm in the last N minutes of the day candle (default: last 15 minutes).
    entry_minutes_before_close: int = 15
    # Unused (old next-open exit). Kept so existing .env / tests still construct.
    exit_minutes_after_open: int = 5
    market_open: str = "09:00"
    market_close: str = "23:30"
    allow_long: bool = True
    allow_short: bool = True
    # Unused: S16 always FLIPs. Kept for old .env / tests.
    no_flip: bool = False
    min_close_beyond: float = 0.0
    max_break_wick_frac: float = 0.6
    min_wick_gap: float = 0.0


class HhhlDayOvernightStrategy:
    name = "S13_HHHL_DAY"

    def __init__(
        self,
        cfg: HhhlDayConfig | None = None,
        *,
        state_path: Path = DEFAULT_STATE_PATH,
    ) -> None:
        self.cfg = cfg or HhhlDayConfig()
        self.state_path = state_path
        self.position: Position = "flat"
        self.entry_price: float | None = None
        self.entry_date: str | None = None
        self.last_skip: str | None = None
        self.last_signal: str | None = None
        self.prev_day: DayOhlc | None = None
        self._day: DayOhlc | None = None
        self._acted_today = False
        self._acted_date: str | None = None
        self.market_open = self._parse_hhmm(self.cfg.market_open)
        self.market_close = self._parse_hhmm(self.cfg.market_close)
        self._load_state()
        # Always re-read ticks so a restart does not freeze today's H/L
        # at the first print of the day (s13_state.json is only a snapshot).
        self._seed_from_ticks()
        if self.prev_day is not None or self._day is not None:
            self._save_state()

    @staticmethod
    def _parse_hhmm(value: str) -> time:
        h, m = value.strip().split(":")
        return time(int(h), int(m))

    @property
    def status_line(self) -> str:
        c = self.cfg
        prev = (
            f"prev={self.prev_day.date} H={self.prev_day.high:.0f} L={self.prev_day.low:.0f} "
            f"C={self.prev_day.close:.0f}"
            if self.prev_day
            else "prev=none"
        )
        today = (
            f"todayH={self._day.high:.0f} todayL={self._day.low:.0f} "
            f"todayO={self._day.open:.0f} todayC={self._day.close:.0f}"
            if self._day
            else "today=none"
        )
        return (
            f"daily S16 C>prev→HH/LL C<prev→wick gap={c.min_wick_gap:g} "
            f"confirm={c.entry_minutes_before_close}m_before_close FLIP "
            f"L={c.allow_long} S={c.allow_short} "
            f"{prev} {today} pos={self.position}"
        )

    def _load_state(self) -> None:
        if not self.state_path.exists():
            return
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        self.position = raw.get("side", "flat")  # type: ignore[assignment]
        self.entry_price = raw.get("entry_price")
        self.entry_date = raw.get("entry_date")
        acted = raw.get("acted_date")
        if isinstance(acted, str) and acted:
            self._acted_date = acted
            today = datetime.now(IST).strftime("%Y-%m-%d")
            self._acted_today = acted == today
        pd = raw.get("prev_day")
        if isinstance(pd, dict) and pd.get("date"):
            self.prev_day = DayOhlc(
                date=str(pd["date"]),
                open=float(pd["open"]),
                high=float(pd["high"]),
                low=float(pd["low"]),
                close=float(pd["close"]),
            )
        td = raw.get("today")
        if isinstance(td, dict) and td.get("date"):
            self._day = DayOhlc(
                date=str(td["date"]),
                open=float(td["open"]),
                high=float(td["high"]),
                low=float(td["low"]),
                close=float(td["close"]),
            )

    def _seed_prev_from_ticks(self) -> None:
        """Back-compat alias."""
        self._seed_from_ticks()

    def _merge_forming_day(self, bar: DayOhlc) -> None:
        """Widen today's range from ticks; keep the session open already recorded."""
        if self._day is None or self._day.date != bar.date:
            self._day = DayOhlc(
                date=bar.date,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
            )
            return
        self._day.high = max(self._day.high, bar.high)
        self._day.low = min(self._day.low, bar.low)
        self._day.close = bar.close

    @staticmethod
    def _ohlc_from_sql(db: Path, day: str) -> DayOhlc | None:
        """Open/high/low/close for one calendar day from ticks.db (fast, indexed)."""
        prefix = f"{day}%"
        con = sqlite3.connect(str(db))
        try:
            first = con.execute(
                "SELECT ltp FROM ticks WHERE ltp IS NOT NULL AND received_at LIKE ? "
                "ORDER BY received_at ASC, id ASC LIMIT 1",
                (prefix,),
            ).fetchone()
            if first is None or first[0] is None:
                return None
            last = con.execute(
                "SELECT ltp FROM ticks WHERE ltp IS NOT NULL AND received_at LIKE ? "
                "ORDER BY received_at DESC, id DESC LIMIT 1",
                (prefix,),
            ).fetchone()
            agg = con.execute(
                "SELECT MIN(ltp), MAX(ltp) FROM ticks "
                "WHERE ltp IS NOT NULL AND received_at LIKE ?",
                (prefix,),
            ).fetchone()
            if last is None or agg is None or agg[0] is None or agg[1] is None:
                return None
            return DayOhlc(
                date=day,
                open=float(first[0]),
                high=float(agg[1]),
                low=float(agg[0]),
                close=float(last[0]),
            )
        finally:
            con.close()

    @staticmethod
    def _prev_trading_day(db: Path, today: str) -> str | None:
        con = sqlite3.connect(str(db))
        try:
            row = con.execute(
                "SELECT received_at FROM ticks "
                "WHERE ltp IS NOT NULL AND received_at < ? "
                "ORDER BY received_at DESC, id DESC LIMIT 1",
                (today,),
            ).fetchone()
            if row is None or not row[0]:
                return None
            return str(row[0])[:10]
        finally:
            con.close()

    def _seed_from_ticks(
        self, db_path: Path | None = None, *, today: str | None = None
    ) -> None:
        """Load previous completed day + hydrate today's forming day from ticks.db."""
        try:
            db = db_path or (Path(__file__).resolve().parent / "data" / "ticks.db")
            if not db.exists():
                return
            today = today or datetime.now(IST).strftime("%Y-%m-%d")
            prev_date = self._prev_trading_day(db, today)
            if prev_date:
                prev_bar = self._ohlc_from_sql(db, prev_date)
                if prev_bar is not None:
                    if self.prev_day is None or self.prev_day.date < prev_date:
                        self.prev_day = prev_bar
                    elif self.prev_day.date == prev_date:
                        self.prev_day.high = max(self.prev_day.high, prev_bar.high)
                        self.prev_day.low = min(self.prev_day.low, prev_bar.low)
            current = self._ohlc_from_sql(db, today)
            if current is not None:
                self._merge_forming_day(current)
        except Exception as exc:
            self.last_skip = f"seed_err:{type(exc).__name__}"
            return

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {
            "side": self.position,
            "entry_price": self.entry_price,
            "entry_date": self.entry_date,
            "acted_date": self._acted_date,
            "prev_day": None,
            "today": None,
        }
        if self.prev_day:
            payload["prev_day"] = {
                "date": self.prev_day.date,
                "open": self.prev_day.open,
                "high": self.prev_day.high,
                "low": self.prev_day.low,
                "close": self.prev_day.close,
            }
        if self._day:
            payload["today"] = {
                "date": self._day.date,
                "open": self._day.open,
                "high": self._day.high,
                "low": self._day.low,
                "close": self._day.close,
            }
        self.state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _in_confirm_window(self, now: datetime) -> bool:
        """True in the last N minutes of *this* day candle (entry and exit)."""
        close_dt = now.replace(
            hour=self.market_close.hour,
            minute=self.market_close.minute,
            second=0,
            microsecond=0,
        )
        start = close_dt - timedelta(minutes=self.cfg.entry_minutes_before_close)
        return start <= now <= close_dt

    def _in_entry_window(self, now: datetime) -> bool:
        """Back-compat alias — confirm window is used for both entry and exit."""
        return self._in_confirm_window(now)

    def _roll_day(self, today: str, px: float) -> None:
        """On calendar day change, seal yesterday as prev_day and start new bar."""
        if self._day is not None and self._day.date != today:
            self.prev_day = self._day
            self._day = DayOhlc(date=today, open=px, high=px, low=px, close=px)
            self._acted_today = False
            self._save_state()
            return
        if self._day is None:
            self._day = DayOhlc(date=today, open=px, high=px, low=px, close=px)
            self._save_state()

    def _update_day_bar(self, today: str, px: float) -> None:
        self._roll_day(today, px)
        assert self._day is not None
        if self._day.date != today:
            self._roll_day(today, px)
        old_h, old_l = self._day.high, self._day.low
        self._day.high = max(self._day.high, px)
        self._day.low = min(self._day.low, px)
        self._day.close = px
        if self._day.high != old_h or self._day.low != old_l:
            self._save_state()

    def release_action_lock(self) -> None:
        """Allow another confirm-window attempt (e.g. after entry gate reject)."""
        self._acted_today = False
        self._acted_date = None

    def _mark_acted(self, today: str) -> None:
        self._acted_today = True
        self._acted_date = today

    def _flip_to(
        self, side: str, px: float, today: str, why: str, cur: Candle
    ) -> SignalResult | None:
        if side == "long" and not self.cfg.allow_long:
            self.last_skip = "long_disabled"
            return None
        if side == "short" and not self.cfg.allow_short:
            self.last_skip = "short_disabled"
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
        self.entry_price = float(px)
        self.entry_date = today
        self._mark_acted(today)
        self._save_state()
        self.last_signal = action
        kind = "FLIP" if prev != "flat" else "enter"
        prev_c = f" prevC={self.prev_day.close:.1f}" if self.prev_day is not None else ""
        return SignalResult(
            action=action,
            position_after=self.position,
            price_delta=None,
            net=None,
            net_delta=None,
            prev_net_delta=None,
            reason=(
                f"s13 {kind} {prev}→{self.position} {why} {extra} "
                f"O={cur.open:.1f} H={cur.high:.1f} L={cur.low:.1f} "
                f"C={cur.close:.1f}{prev_c} last-15m"
            ),
        )

    def _decide(self, px: float, today: str) -> SignalResult | None:
        """S16 close-vs-prev on the forming day vs previous day — confirm window only."""
        day = self._day
        prev = self.prev_day
        if day is None or prev is None:
            self.last_skip = "need_prev_day"
            return None
        side, why = s16_bar_decision(
            _as_candle(prev), _as_candle(day), min_wick_gap=float(self.cfg.min_wick_gap)
        )
        if side is None:
            self.last_skip = why
            return None
        return self._flip_to(side, px, today, why, _as_candle(day))

    def _watch_skip(self) -> None:
        if self._day and self.prev_day:
            if self._day.close > self.prev_day.close:
                self.last_skip = "watching_up_close_hhll"
            elif self._day.close < self.prev_day.close:
                self.last_skip = "watching_down_close_wick"
            else:
                self.last_skip = "watching_equal_close"
        else:
            self.last_skip = "outside_confirm_window"

    def on_tick(
        self, now: datetime, ltp: float, message: dict[str, Any] | None = None
    ) -> SignalResult | None:
        del message
        now = now.astimezone(IST)
        today = now.strftime("%Y-%m-%d")
        px = float(ltp)
        self._update_day_bar(today, px)

        if self._acted_date == today:
            self._acted_today = True
        if self._acted_today:
            self.last_skip = "acted_today"
            return None
        if not self._in_confirm_window(now):
            self._watch_skip()
            return None

        return self._decide(px, today)


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "y"}


def hhhl_day_from_env() -> HhhlDayOvernightStrategy:
    try:
        from dotenv import load_dotenv

        load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
    except Exception:
        pass
    cfg = HhhlDayConfig(
        min_range=float(os.getenv("S13_MIN_RANGE", "5")),
        entry_minutes_before_close=int(os.getenv("S13_ENTRY_MINUTES_BEFORE_CLOSE", "15")),
        exit_minutes_after_open=int(os.getenv("S13_EXIT_MINUTES_AFTER_OPEN", "5")),
        market_open=os.getenv("MARKET_OPEN", "09:00"),
        market_close=os.getenv("MARKET_CLOSE", "23:30"),
        allow_long=_env_flag("S13_ALLOW_LONG", True),
        allow_short=_env_flag("S13_ALLOW_SHORT", True),
        no_flip=_env_flag("S13_NO_FLIP", False),
        min_close_beyond=float(os.getenv("S13_MIN_CLOSE_BEYOND", "0")),
        max_break_wick_frac=float(os.getenv("S13_MAX_BREAK_WICK_FRAC", "0.6")),
        min_wick_gap=float(os.getenv("S13_MIN_WICK_GAP", "0")),
    )
    return HhhlDayOvernightStrategy(cfg)
