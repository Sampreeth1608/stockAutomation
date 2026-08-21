"""OVERNIGHT_GAP — close→next-open. Not S4 swing. Not S7.

Read today's tape near MARKET_CLOSE. BUY if the session looks like a gap-up
open tomorrow; SHORT if it looks like a gap-down. CLOSE in the first minutes
after next MARKET_OPEN (default 09:00–09:05 IST). Mood-exempt. ENABLE defaults
true (papers with the slim books). Paper 100 lots ≠ live. Live-eligible —
tick Lots or ₹, then you Arm live. Next-open exit, never EOD flatten.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from overnight_gap import (
    BOOK,
    DEFAULT_BUY_PROB,
    DEFAULT_CLOSE,
    DEFAULT_ENTRY_MINUTES,
    DEFAULT_EXIT_MINUTES,
    DEFAULT_LATE_MINUTES,
    DEFAULT_OPEN,
    DEFAULT_SHORT_PROB,
    GapRead,
    in_entry_window,
    in_exit_window,
    parse_hhmm,
    score_ticks,
)
from strategy import Position, SignalResult
from symbols import s13_roll_intent

IST = ZoneInfo("Asia/Kolkata")
DEFAULT_STATE_PATH = Path(__file__).resolve().parent / "data" / "overnight_gap_state.json"


class OvernightGapStrategy:
    """One overnight hold. Independent of tick-window mood / regime."""

    name = BOOK
    holds_overnight = True

    def __init__(
        self,
        *,
        buy_prob: float = DEFAULT_BUY_PROB,
        short_prob: float = DEFAULT_SHORT_PROB,
        entry_minutes_before_close: int = DEFAULT_ENTRY_MINUTES,
        exit_minutes_after_open: int = DEFAULT_EXIT_MINUTES,
        late_minutes: int = DEFAULT_LATE_MINUTES,
        market_open: str = DEFAULT_OPEN,
        market_close: str = DEFAULT_CLOSE,
        allow_long: bool = True,
        allow_short: bool = True,
        state_path: Path = DEFAULT_STATE_PATH,
        db_path: Path | None = None,
        seed: bool = True,
    ) -> None:
        self.buy_prob = float(buy_prob)
        self.short_prob = float(short_prob)
        self.entry_minutes_before_close = int(entry_minutes_before_close)
        self.exit_minutes_after_open = int(exit_minutes_after_open)
        self.late_minutes = int(late_minutes)
        self.market_open = parse_hhmm(market_open)
        self.market_close = parse_hhmm(market_close)
        self.allow_long = bool(allow_long)
        self.allow_short = bool(allow_short)
        self.state_path = Path(state_path)
        self.db_path = Path(db_path) if db_path else (
            Path(__file__).resolve().parent / "data" / "ticks.db"
        )
        self.position: Position = "flat"
        self.entry_price: float | None = None
        self.entry_date: str | None = None
        self.last_skip: str | None = None
        self.last_read: GapRead | None = None
        self.last_signal: str | None = None
        self.samples: list[tuple[datetime, float, float, float]] = []
        self._day = ""
        self._entered_today = False
        self._exited_today = False
        self._hydrated_day = ""
        self._contract: dict[str, Any] | None = None
        self.contract_symbol: str | None = None
        if seed:
            self._load_state()

    @property
    def status_line(self) -> str:
        return (
            f"enabled (close→next-open) buy>={self.buy_prob} short<={self.short_prob} "
            f"entry={self.entry_minutes_before_close}m_before_close "
            f"exit={self.exit_minutes_after_open}m_after_open "
            f"mood_exempt pos={self.position}"
        )

    def set_contract(self, contract: dict[str, Any] | None) -> None:
        self._contract = dict(contract or {}) or None
        if self._contract:
            self.contract_symbol = str(self._contract.get("symbol") or "") or None

    def release_action_lock(self) -> None:
        self._entered_today = False

    def _load_state(self) -> None:
        if not self.state_path.is_file():
            return
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            return
        side = str(raw.get("side") or "flat").lower()
        if side in {"long", "short", "flat"}:
            self.position = side  # type: ignore[assignment]
        ep = raw.get("entry_price")
        self.entry_price = float(ep) if ep not in (None, "") else None
        self.entry_date = str(raw.get("entry_date") or "") or None
        self.contract_symbol = str(raw.get("contract_symbol") or "") or None

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            json.dumps(
                {
                    "side": self.position,
                    "entry_price": self.entry_price,
                    "entry_date": self.entry_date,
                    "contract_symbol": self.contract_symbol,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    def _reset_day(self, day: str) -> None:
        self._day = day
        self.samples = []
        self._entered_today = False
        self._exited_today = False
        self._hydrated_day = ""

    def _tbq_tsq(self, message: dict[str, Any] | None) -> tuple[float, float]:
        msg = message or {}
        tbq = msg.get("total_buy_quantity")
        tsq = msg.get("total_sell_quantity")
        if tbq is None:
            tbq = msg.get("bp")
        if tsq is None:
            tsq = msg.get("sp")
        try:
            b = float(tbq or 0)
        except (TypeError, ValueError):
            b = 0.0
        try:
            s = float(tsq or 0)
        except (TypeError, ValueError):
            s = 0.0
        return b, s

    def _hydrate_today(self, now: datetime) -> None:
        day = now.strftime("%Y-%m-%d")
        if self._hydrated_day == day:
            return
        db = self.db_path
        if not db.is_file():
            self._hydrated_day = day
            return
        prefix = f"{day}%"
        late_start = (now - timedelta(minutes=max(5, self.late_minutes))).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        try:
            con = sqlite3.connect(str(db))
            try:
                first = con.execute(
                    "SELECT received_at, ltp, bp, sp FROM ticks "
                    "WHERE ltp IS NOT NULL AND received_at LIKE ? "
                    "ORDER BY received_at ASC, id ASC LIMIT 1",
                    (prefix,),
                ).fetchone()
                hi = con.execute(
                    "SELECT received_at, ltp, bp, sp FROM ticks "
                    "WHERE ltp IS NOT NULL AND received_at LIKE ? "
                    "ORDER BY ltp DESC, received_at DESC LIMIT 1",
                    (prefix,),
                ).fetchone()
                lo = con.execute(
                    "SELECT received_at, ltp, bp, sp FROM ticks "
                    "WHERE ltp IS NOT NULL AND received_at LIKE ? "
                    "ORDER BY ltp ASC, received_at ASC LIMIT 1",
                    (prefix,),
                ).fetchone()
                grid = con.execute(
                    "SELECT received_at, ltp, bp, sp FROM ticks "
                    "WHERE ltp IS NOT NULL AND received_at LIKE ? "
                    "AND CAST(substr(received_at, 15, 2) AS INTEGER) % 15 = 0 "
                    "AND substr(received_at, 18, 2) = '00' "
                    "ORDER BY received_at ASC",
                    (prefix,),
                ).fetchall()
                late = con.execute(
                    "SELECT received_at, ltp, bp, sp FROM ticks "
                    "WHERE ltp IS NOT NULL AND received_at LIKE ? "
                    "AND received_at >= ? "
                    "ORDER BY received_at ASC",
                    (prefix, late_start),
                ).fetchall()
            finally:
                con.close()
        except sqlite3.Error:
            self._hydrated_day = day
            return
        out: list[tuple[datetime, float, float, float]] = []
        for row in (first, hi, lo):
            if row:
                out.append(self._row_sample(row))
        if len(grid) > 400:
            step = max(1, len(grid) // 200)
            grid = grid[::step]
        for row in grid:
            out.append(self._row_sample(row))
        if len(late) > 4_000:
            late = late[-4_000:]
        for row in late:
            out.append(self._row_sample(row))
        if out:
            seen = {(s[0].isoformat(), round(s[1], 4)) for s in self.samples}
            for sample in out:
                key = (sample[0].isoformat(), round(sample[1], 4))
                if key not in seen:
                    self.samples.append(sample)
                    seen.add(key)
            self.samples.sort(key=lambda r: r[0])
            if len(self.samples) > 8_000:
                self.samples = self.samples[-6_000:]
        self._hydrated_day = day

    @staticmethod
    def _row_sample(row: tuple[Any, ...]) -> tuple[datetime, float, float, float]:
        raw_ts, ltp, bp, sp = row[0], row[1], row[2], row[3]
        try:
            ts = datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00"))
        except ValueError:
            ts = datetime.now(IST)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=IST)
        else:
            ts = ts.astimezone(IST)
        return (ts, float(ltp or 0), float(bp or 0), float(sp or 0))

    def _roll_blocks_entry(self) -> str | None:
        c = self._contract
        if not c:
            return None
        intent = s13_roll_intent(
            held_symbol=self.contract_symbol,
            trade_symbol=str(c.get("symbol") or ""),
            rolled=bool(c.get("rolled")),
            days_to_front_expiry=int(c.get("days_to_front_expiry") or 0),
            rollover_days=int(c.get("rollover_days") or 5),
            in_position=self.position in {"long", "short"},
        )
        if intent in {"block_last_front", "flatten_last_front", "flatten_switch", "reset_switch"}:
            return f"rollover_{intent}"
        return None

    def on_tick(
        self,
        now: datetime,
        px: float,
        message: dict[str, Any] | None = None,
    ) -> SignalResult | None:
        if now.tzinfo is None:
            now = now.replace(tzinfo=IST)
        else:
            now = now.astimezone(IST)
        today = now.strftime("%Y-%m-%d")
        if self._day != today:
            self._reset_day(today)
        tbq, tsq = self._tbq_tsq(message)
        self.samples.append((now, float(px), tbq, tsq))
        if len(self.samples) > 8_000:
            self.samples = self.samples[-6_000:]

        # EXIT first: next session after the overnight hold (Fri → Mon is fine).
        if (
            self.position in {"long", "short"}
            and self.entry_date
            and self.entry_date < today
            and not self._exited_today
        ):
            if in_exit_window(
                now,
                market_open=self.market_open,
                minutes_after=self.exit_minutes_after_open,
            ):
                prev = self.position
                entry_date = self.entry_date
                self.position = "flat"
                self.entry_price = None
                self.entry_date = None
                self._exited_today = True
                self.last_signal = "CLOSE"
                self.last_skip = None
                self._save_state()
                return SignalResult(
                    action="CLOSE",
                    position_after="flat",
                    price_delta=None,
                    net=None,
                    net_delta=None,
                    prev_net_delta=None,
                    reason=(
                        f"OVERNIGHT_GAP CLOSE after open; was_{prev} "
                        f"entry_date={entry_date}"
                    ),
                )
            self.last_skip = "wait_open_exit"
            return None

        if self.position != "flat":
            self.last_skip = "in_overnight_hold"
            return None
        if self._entered_today:
            self.last_skip = "already_entered_today"
            return None
        if not in_entry_window(
            now,
            market_close=self.market_close,
            minutes_before=self.entry_minutes_before_close,
        ):
            self.last_skip = "wait_close_window"
            return None
        roll_why = self._roll_blocks_entry()
        if roll_why:
            self.last_skip = roll_why
            return None
        self._hydrate_today(now)
        read = score_ticks(
            self.samples,
            now=now,
            late_minutes=self.late_minutes,
            buy_prob=self.buy_prob,
            short_prob=self.short_prob,
        )
        self.last_read = read
        if read is None:
            self.last_skip = "day_unreadable"
            return None
        if read.bias == "BULLISH" and self.allow_long and read.prob_up >= self.buy_prob:
            action = "BUY"
            pos: Position = "long"
        elif read.bias == "BEARISH" and self.allow_short and read.prob_up <= self.short_prob:
            action = "SHORT"
            pos = "short"
        else:
            self.last_skip = f"neutral_{read.bias}"
            return None
        self.position = pos
        self.entry_price = float(px)
        self.entry_date = today
        self._entered_today = True
        self.last_signal = action
        self.last_skip = None
        if self._contract:
            self.contract_symbol = str(self._contract.get("symbol") or "") or self.contract_symbol
        self._save_state()
        return SignalResult(
            action=action,
            position_after=pos,
            price_delta=None,
            net=float(read.prob_up),
            net_delta=None,
            prev_net_delta=None,
            reason=f"OVERNIGHT_GAP {action} for next open | {read.reason}",
        )


def overnight_gap_from_env() -> OvernightGapStrategy:
    def _flag(key: str, default: str = "true") -> bool:
        return (os.getenv(key, default) or default).strip().lower() in {
            "1",
            "true",
            "yes",
            "y",
            "on",
        }

    return OvernightGapStrategy(
        buy_prob=float(os.getenv("OVERNIGHT_GAP_BUY_PROB", str(DEFAULT_BUY_PROB))),
        short_prob=float(os.getenv("OVERNIGHT_GAP_SHORT_PROB", str(DEFAULT_SHORT_PROB))),
        entry_minutes_before_close=int(
            os.getenv("OVERNIGHT_GAP_ENTRY_MINUTES_BEFORE_CLOSE", str(DEFAULT_ENTRY_MINUTES))
        ),
        exit_minutes_after_open=int(
            os.getenv("OVERNIGHT_GAP_EXIT_MINUTES_AFTER_OPEN", str(DEFAULT_EXIT_MINUTES))
        ),
        late_minutes=int(os.getenv("OVERNIGHT_GAP_LATE_MINUTES", str(DEFAULT_LATE_MINUTES))),
        market_open=os.getenv("MARKET_OPEN", DEFAULT_OPEN),
        market_close=os.getenv("MARKET_CLOSE", DEFAULT_CLOSE),
        allow_long=_flag("OVERNIGHT_GAP_ALLOW_LONG", "true"),
        allow_short=_flag("OVERNIGHT_GAP_ALLOW_SHORT", "true"),
    )
