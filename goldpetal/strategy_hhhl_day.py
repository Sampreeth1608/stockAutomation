"""S13_HHHL_DAY — daily HH/LL signal, S4-style overnight hold (paper).

Same *same-candle* rule as S12, on the **trading day** vs **previous day**:

  During the day, if **today's high > yesterday's high**, watch the day candle.
  In the **last 15 minutes before market close** (configurable), if close > open → LONG overnight.
  Short: today's low < yesterday's low, last-minute close < open → SHORT overnight.
  EXIT after next open (delivery-style).

Does not replace S4 ML overnight — separate paper strategy.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from strategy import Position, SignalResult

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


@dataclass
class HhhlDayConfig:
    min_range: float = 5.0
    # Confirm in the last N minutes of the day candle (default: last 15 minutes).
    entry_minutes_before_close: int = 15
    exit_minutes_after_open: int = 5
    market_open: str = "09:00"
    market_close: str = "23:30"
    allow_long: bool = True
    allow_short: bool = True


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
        self._entered_today = False
        self._exited_today = False
        self.market_open = self._parse_hhmm(self.cfg.market_open)
        self.market_close = self._parse_hhmm(self.cfg.market_close)
        self._load_state()

    @staticmethod
    def _parse_hhmm(value: str) -> time:
        h, m = value.strip().split(":")
        return time(int(h), int(m))

    @property
    def status_line(self) -> str:
        c = self.cfg
        prev = (
            f"prev={self.prev_day.date} H={self.prev_day.high:.0f} L={self.prev_day.low:.0f}"
            if self.prev_day
            else "prev=none"
        )
        return (
            f"daily HH/LL overnight min_range={c.min_range:.0f} "
            f"entry={c.entry_minutes_before_close}m_before_close "
            f"L={c.allow_long} S={c.allow_short} {prev} pos={self.position}"
        )

    def _load_state(self) -> None:
        if not self.state_path.exists():
            self._seed_prev_from_ticks()
            return
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            self._seed_prev_from_ticks()
            return
        self.position = raw.get("side", "flat")  # type: ignore[assignment]
        self.entry_price = raw.get("entry_price")
        self.entry_date = raw.get("entry_date")
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
        if self.prev_day is None:
            self._seed_prev_from_ticks()

    def _seed_prev_from_ticks(self) -> None:
        """Best-effort: load last completed calendar day OHLC from ticks.db."""
        try:
            from mtf_bars import build_rich_bars, load_tick_rows

            db = Path(__file__).resolve().parent / "data" / "ticks.db"
            if not db.exists():
                return
            rows = load_tick_rows(db)
            bars = build_rich_bars(rows, "1d", 1440)
            if len(bars) < 2:
                if bars:
                    b = bars[-1]
                    today = datetime.now(IST).strftime("%Y-%m-%d")
                    if str(b.time)[:10] < today:
                        self.prev_day = DayOhlc(
                            date=str(b.time)[:10],
                            open=float(b.open),
                            high=float(b.high),
                            low=float(b.low),
                            close=float(b.close),
                        )
                return
            # Prefer second-to-last if last bar is "today" (still forming)
            today = datetime.now(IST).strftime("%Y-%m-%d")
            last = bars[-1]
            prev = bars[-2]
            if str(last.time)[:10] == today:
                b = prev
            else:
                b = last
            self.prev_day = DayOhlc(
                date=str(b.time)[:10],
                open=float(b.open),
                high=float(b.high),
                low=float(b.low),
                close=float(b.close),
            )
        except Exception:
            return

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {
            "side": self.position,
            "entry_price": self.entry_price,
            "entry_date": self.entry_date,
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

    def _in_entry_window(self, now: datetime) -> bool:
        close_dt = now.replace(
            hour=self.market_close.hour,
            minute=self.market_close.minute,
            second=0,
            microsecond=0,
        )
        start = close_dt - timedelta(minutes=self.cfg.entry_minutes_before_close)
        return start <= now <= close_dt

    def _in_exit_window(self, now: datetime) -> bool:
        open_dt = now.replace(
            hour=self.market_open.hour,
            minute=self.market_open.minute,
            second=0,
            microsecond=0,
        )
        end = open_dt + timedelta(minutes=self.cfg.exit_minutes_after_open)
        return open_dt <= now <= end

    def _roll_day(self, today: str, px: float) -> None:
        """On calendar day change, seal yesterday as prev_day and start new bar."""
        if self._day is not None and self._day.date != today:
            self.prev_day = self._day
            self._day = DayOhlc(date=today, open=px, high=px, low=px, close=px)
            self._entered_today = False
            self._exited_today = False
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
        self._day.high = max(self._day.high, px)
        self._day.low = min(self._day.low, px)
        self._day.close = px

    def _signal_from_day(self) -> tuple[str | None, str]:
        """Return ('long'|'short'|None, reason) — same-day HH/LL + last-min close."""
        day = self._day
        prev = self.prev_day
        if day is None or prev is None:
            return None, "need_prev_day"
        if day.range_pts < self.cfg.min_range:
            return None, f"min_range {day.range_pts:.1f}<{self.cfg.min_range}"
        # Watch flags: HH/LL must have printed on *this* day candle.
        saw_hh = day.high > prev.high
        saw_ll = day.low < prev.low
        want_long = self.cfg.allow_long and saw_hh and day.close > day.open
        want_short = self.cfg.allow_short and saw_ll and day.close < day.open
        if want_long and want_short:
            return None, "conflict_hh_ll"
        if want_long:
            return (
                "long",
                (
                    f"s13 LONG same-day HH+green dayH={day.high:.1f}>prevH={prev.high:.1f} "
                    f"C={day.close:.1f}>O={day.open:.1f} range={day.range_pts:.1f}"
                ),
            )
        if want_short:
            return (
                "short",
                (
                    f"s13 SHORT same-day LL+red dayL={day.low:.1f}<prevL={prev.low:.1f} "
                    f"C={day.close:.1f}<O={day.open:.1f} range={day.range_pts:.1f}"
                ),
            )
        if saw_hh and not (day.close > day.open):
            return None, "hh_but_not_green_at_close"
        if saw_ll and not (day.close < day.open):
            return None, "ll_but_not_red_at_close"
        return None, "no_hhll_signal"

    def on_tick(self, now: datetime, ltp: float, message: dict[str, Any] | None = None) -> SignalResult | None:
        del message
        now = now.astimezone(IST)
        today = now.strftime("%Y-%m-%d")
        px = float(ltp)
        self._update_day_bar(today, px)

        # EXIT overnight position after next open
        if (
            self.position != "flat"
            and self.entry_date
            and self.entry_date < today
            and self._in_exit_window(now)
            and not self._exited_today
        ):
            side = self.position
            entry_date = self.entry_date
            self.position = "flat"
            self.entry_price = None
            self.entry_date = None
            self._exited_today = True
            self._save_state()
            self.last_signal = "CLOSE"
            return SignalResult(
                action="CLOSE",
                position_after="flat",
                price_delta=None,
                net=None,
                net_delta=None,
                prev_net_delta=None,
                reason=f"s13 delivery exit after open; was_{side} entry_date={entry_date}",
            )

        # ENTRY in last minute(s) of *this* day candle (same-day HH/LL confirm)
        if self.position != "flat":
            self.last_skip = "already_in"
            return None
        if self._entered_today:
            self.last_skip = "entered_today"
            return None
        if not self._in_entry_window(now):
            # Still track watch state for status
            if self._day and self.prev_day:
                if self._day.high > self.prev_day.high:
                    self.last_skip = "watching_hh"
                elif self._day.low < self.prev_day.low:
                    self.last_skip = "watching_ll"
                else:
                    self.last_skip = "outside_entry_window"
            else:
                self.last_skip = "outside_entry_window"
            return None

        side, reason = self._signal_from_day()
        self.last_skip = reason if side is None else None
        if side is None:
            return None

        self.position = side  # type: ignore[assignment]
        self.entry_price = px
        self.entry_date = today
        self._entered_today = True
        self._save_state()
        self.last_signal = "BUY" if side == "long" else "SHORT"
        return SignalResult(
            action="BUY" if side == "long" else "SHORT",
            position_after=side,  # type: ignore[arg-type]
            price_delta=None,
            net=None,
            net_delta=None,
            prev_net_delta=None,
            reason=reason,
        )


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
    )
    return HhhlDayOvernightStrategy(cfg)
