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
Intraday only: the first finished session 1h is stored as prev (no trade).
The next finished session 1h can BUY/SHORT only if this formula picks a
side vs that same-session prev. Never use yesterday's last hour or a
preopen hour as prev. A restart after that hour already finished still
applies the last closed session 1h vs the hour before it (fill at that
bar's close), unless a signal was already recorded at/after that close.
Flatten at MARKET_CLOSE (and leftover at next MARKET_OPEN). Never overnight.
Not live-unlocked. Paper 100 lots ≠ live.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
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

    def __init__(
        self,
        cfg: S16Config | None = None,
        *,
        seed: bool = True,
        persist_day: bool | None = None,
    ) -> None:
        self.cfg = cfg or S16Config()
        self.position: Position = "flat"
        self.entry_price: float | None = None
        self.entry_date: str | None = None
        self.last_skip: str | None = None
        self._bar_key: datetime | None = None
        self._bar_o = self._bar_h = self._bar_l = self._bar_c = None
        self._bar_n = 0
        self._prev: Candle | None = None
        self._catchup_prev: Candle | None = None
        self._catchup_cur: Candle | None = None
        self._last_signal_at: datetime | None = None
        self._closed_today: list[Candle] = []
        self._last_persist_m = 0.0
        if persist_day is None:
            from storage import store_ticks_enabled

            persist_day = bool(seed) and not store_ticks_enabled()
        self._persist_day = bool(persist_day)
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

    def _same_session_prev(self, prev: Candle, cur: Candle) -> bool:
        """Prev must be a session 1h from the same calendar day as cur."""
        if not self._bar_started_in_session(prev):
            return False
        if not self._bar_started_in_session(cur):
            return False
        prev_day = prev.time[:10]
        return bool(prev_day) and prev_day == cur.time[:10]

    def _drop_stale_prev(self, now: datetime) -> None:
        if self._prev is None:
            return
        today = now.strftime("%Y-%m-%d")
        if self._prev.time[:10] != today or not self._bar_started_in_session(self._prev):
            self._prev = None

    def _session_flatten_why(self, now: datetime) -> str | None:
        if self.position == "flat":
            return None
        from position_safety import is_session_intraday

        if not is_session_intraday(self.name):
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
        if (
            self._bar_started_in_session(closed)
            and closed.time[:10] == now.strftime("%Y-%m-%d")
        ):
            self._prev = closed
            self._remember_closed(closed)
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
            tl = str(row["time_label"] or "")
            self._last_signal_at = _parse_ts(tl)
            if action in {"BUY", "SHORT"} and pos in {"long", "short"}:
                self.position = pos  # type: ignore[assignment]
                cmp = row["cmp"]
                self.entry_price = float(cmp) if cmp is not None else None
                self.entry_date = tl[:10] if tl else None
            elif action == "CLOSE" or pos == "flat":
                self.position = "flat"
                self.entry_price = None
                self.entry_date = None
        except Exception:
            return

    def _session_candle(
        self,
        key: datetime,
        ohlc: tuple[float, float, float, float, int] | None,
        now: datetime,
    ) -> Candle | None:
        if ohlc is None:
            return None
        o, h, l, c, _n = ohlc
        cand = Candle(
            time=key.strftime("%Y-%m-%d %H:%M:%S"),
            open=o,
            high=h,
            low=l,
            close=c,
        )
        if cand.time[:10] != now.strftime("%Y-%m-%d"):
            return None
        if not self._bar_started_in_session(cand):
            return None
        return cand

    def _already_decided_close(self, closed_key: datetime) -> bool:
        if self._last_signal_at is None:
            return False
        close_at = closed_key + timedelta(minutes=max(1, self.cfg.bar_minutes))
        return self._last_signal_at >= close_at

    def _run_catchup(self, now: datetime) -> SignalResult | None:
        """Apply the last finished session 1h if restart missed that close tick."""
        cur = self._catchup_cur
        prev = self._catchup_prev
        self._catchup_cur = None
        self._catchup_prev = None
        if cur is None or prev is None or not self._in_session(now):
            return None
        saved = self._prev
        self._prev = prev
        try:
            result = self._decide_closed(cur)
        finally:
            self._prev = saved if saved is not None else cur
        if result is not None:
            result.reason = f"{result.reason} restart catch-up"
        return result

    def _remember_closed(self, closed: Candle) -> None:
        key = closed.time[:19]
        self._closed_today = [c for c in self._closed_today if c.time[:19] != key]
        self._closed_today.append(closed)
        self._closed_today.sort(key=lambda c: c.time)

    def day_state_path(self) -> Path:
        return Path(__file__).resolve().parent / "data" / "control" / "s16_day.json"

    def dump_day_state(self) -> dict[str, Any]:
        forming = None
        if self._bar_key is not None and self._bar_o is not None:
            forming = {
                "key": self._bar_key.isoformat(timespec="seconds"),
                "open": float(self._bar_o),
                "high": float(self._bar_h if self._bar_h is not None else self._bar_o),
                "low": float(self._bar_l if self._bar_l is not None else self._bar_o),
                "close": float(self._bar_c if self._bar_c is not None else self._bar_o),
                "n": int(self._bar_n),
            }
        date = ""
        if self._bar_key is not None:
            date = self._bar_key.strftime("%Y-%m-%d")
        elif self._closed_today:
            date = self._closed_today[-1].time[:10]
        return {
            "date": date,
            "closed": [
                {
                    "time": c.time,
                    "open": float(c.open),
                    "high": float(c.high),
                    "low": float(c.low),
                    "close": float(c.close),
                }
                for c in self._closed_today
            ],
            "forming": forming,
        }

    def persist_day_state(self, path: Path | None = None) -> None:
        """Tiny today's 1h bars — not a tick archive. Formula unchanged."""
        dest = path or self.day_state_path()
        dest.parent.mkdir(parents=True, exist_ok=True)
        payload = self.dump_day_state()
        if not payload.get("date"):
            payload["date"] = datetime.now(IST).strftime("%Y-%m-%d")
        dest.write_text(json.dumps(payload, default=str), encoding="utf-8")

    def _maybe_persist_day(self) -> None:
        if not self._persist_day:
            return
        now_m = time.monotonic()
        if self._last_persist_m and now_m - self._last_persist_m < 2.0:
            return
        self._last_persist_m = now_m
        try:
            self.persist_day_state()
        except Exception:
            return

    def seed_from_day_state(
        self, path: Path | None = None, *, now: datetime | None = None
    ) -> None:
        """Restart from today's closed 1h bars + forming hour (no ticks.db)."""
        dest = path or self.day_state_path()
        if not dest.exists():
            return
        try:
            data = json.loads(dest.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        if not isinstance(data, dict):
            return
        now = (now or datetime.now(IST)).astimezone(IST)
        today = now.strftime("%Y-%m-%d")
        if str(data.get("date") or "") != today:
            return
        closed: list[Candle] = []
        for row in data.get("closed") or []:
            if not isinstance(row, dict):
                continue
            try:
                cand = Candle(
                    time=str(row["time"]),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                )
            except (KeyError, TypeError, ValueError):
                continue
            if cand.time[:10] != today or not self._bar_started_in_session(cand):
                continue
            closed.append(cand)
        closed.sort(key=lambda c: c.time)
        self._closed_today = closed
        step = timedelta(minutes=max(1, self.cfg.bar_minutes))
        cur_key = self._floor_bar(now)
        closed_key = cur_key - step
        forming = data.get("forming") or {}
        forming_key = None
        if isinstance(forming, dict) and forming:
            try:
                forming_key = datetime.fromisoformat(str(forming["key"]))
                if forming_key.tzinfo is None:
                    forming_key = forming_key.replace(tzinfo=IST)
                else:
                    forming_key = forming_key.astimezone(IST)
                forming_candle = Candle(
                    time=forming_key.strftime("%Y-%m-%d %H:%M:%S"),
                    open=float(forming["open"]),
                    high=float(forming["high"]),
                    low=float(forming["low"]),
                    close=float(
                        forming["close"]
                        if forming.get("close") is not None
                        else forming["open"]
                    ),
                )
            except (KeyError, TypeError, ValueError):
                forming_key = None
                forming_candle = None
            else:
                if (
                    forming_key == closed_key
                    and forming_candle.time[:10] == today
                    and self._bar_started_in_session(forming_candle)
                ):
                    self._remember_closed(forming_candle)
                    closed = list(self._closed_today)
        if closed:
            last = closed[-1]
            try:
                last_key = datetime.strptime(last.time[:19], "%Y-%m-%d %H:%M:%S").replace(
                    tzinfo=IST
                )
            except ValueError:
                last_key = None
            self._prev = last
            if (
                last_key == closed_key
                and len(closed) >= 2
                and not self._already_decided_close(closed_key)
            ):
                self._catchup_prev = closed[-2]
                self._catchup_cur = last
        if forming_key != cur_key or not isinstance(forming, dict) or not forming:
            return
        try:
            self._reset_bar(forming_key, float(forming["open"]))
            self._bar_h = float(forming["high"])
            self._bar_l = float(forming["low"])
            self._bar_c = float(
                forming["close"] if forming.get("close") is not None else forming["open"]
            )
            self._bar_n = max(1, int(forming.get("n") or 1))
        except (KeyError, TypeError, ValueError):
            return

    def seed_from_ticks(
        self, db_path: Path | None = None, *, now: datetime | None = None
    ) -> None:
        """Hydrate last closed 1h, catch up that close if restart missed it."""
        try:
            from storage import store_ticks_enabled

            db = db_path or (Path(__file__).resolve().parent / "data" / "ticks.db")
            if db.exists():
                self._seed_position_from_signals(db)
            now = (now or datetime.now(IST)).astimezone(IST)
            want_ticks = db_path is not None or store_ticks_enabled()
            if want_ticks and db.exists():
                self._hydrate_from_tick_rows(db, now)
                return
            self.seed_from_day_state(now=now)
            if self._prev is None and self._bar_key is None and db.exists():
                self._hydrate_from_tick_rows(db, now)
        except Exception:
            return

    def _hydrate_from_tick_rows(self, db: Path, now: datetime) -> None:
        step = timedelta(minutes=max(1, self.cfg.bar_minutes))
        cur_key = self._floor_bar(now)
        closed_key = cur_key - step
        prev_key = closed_key - step
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
        prev_c = self._session_candle(
            prev_key, self._ohlc_from_rows(rows, prev_key), now
        )
        closed_c = self._session_candle(
            closed_key, self._ohlc_from_rows(rows, closed_key), now
        )
        cur_ohlc = self._ohlc_from_rows(rows, cur_key)
        if closed_c is not None:
            self._prev = closed_c
            self._remember_closed(closed_c)
            if prev_c is not None:
                self._remember_closed(prev_c)
            if prev_c is not None and not self._already_decided_close(closed_key):
                self._catchup_prev = prev_c
                self._catchup_cur = closed_c
        if cur_ohlc is None:
            return
        o, h, l, c, n = cur_ohlc
        self._reset_bar(cur_key, float(o))
        self._bar_h = float(h)
        self._bar_l = float(l)
        self._bar_c = float(c)
        self._bar_n = max(1, n)

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
        try:
            return self._on_tick_body(now, ltp)
        finally:
            self._maybe_persist_day()

    def _on_tick_body(self, now: datetime, ltp: float) -> SignalResult | None:
        self._drop_stale_prev(now)
        key = self._floor_bar(now)
        px = float(ltp)
        flatten_why = self._session_flatten_why(now)
        had_catchup = self._catchup_cur is not None
        catchup = None if flatten_why else self._run_catchup(now)

        if self._bar_key is None:
            self._reset_bar(key, px)
            if flatten_why:
                return self._flatten(px, flatten_why)
            if catchup is not None:
                return catchup
            if had_catchup:
                return None
            self.last_skip = "waiting_1h_close"
            return None

        if key != self._bar_key:
            closed = self._closed_candle()
            result = None if flatten_why else self._maybe_decide_closed(closed, now)
            if (
                flatten_why
                and closed is not None
                and self._bar_started_in_session(closed)
                and closed.time[:10] == now.strftime("%Y-%m-%d")
            ):
                self._prev = closed
                self._remember_closed(closed)
            self._reset_bar(key, px)
            self._last_persist_m = 0.0
            if flatten_why:
                return self._flatten(px, flatten_why)
            return result if result is not None else catchup

        assert self._bar_h is not None and self._bar_l is not None
        self._bar_h = max(self._bar_h, px)
        self._bar_l = min(self._bar_l, px)
        self._bar_c = px
        self._bar_n += 1
        if flatten_why:
            return self._flatten(px, flatten_why)
        if catchup is not None:
            return catchup
        if had_catchup:
            return None
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
        if self._prev is None or not self._same_session_prev(self._prev, cur):
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
