"""S18_OHLC_VOL_HTF — 1h OHLC + volume + yesterday, pack overlay.

Base AND: green/red, C vs prevC, HH/LL, vol up, C vs completed day.
The learner may replace that pack from data/learn/s18/active.json.
FLIP at the finished hour close. Delivery — holds overnight. Only S16
flattens at MARKET_CLOSE. Live-eligible — tick Lots or ₹, then you Arm.
Paper 100 lots ≠ live.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from s18_ohlc_vol_htf import (
    PACK_PATH,
    S18_NAME,
    VolBar,
    _bar_volume,
    load_active_pack,
    s18_bar_decision,
)
from strategy import Position, SignalResult
from strategy_wick import _parse_ts
from wick_candles import wick_measure

IST = ZoneInfo("Asia/Kolkata")


class S18OhlcVolHtfStrategy:
    name = S18_NAME
    holds_overnight = True

    def __init__(
        self,
        *,
        bar_minutes: int = 60,
        market_open: str = "09:00",
        market_close: str = "23:30",
        pack_path: Path | None = None,
        seed: bool = True,
    ) -> None:
        self.bar_minutes = int(bar_minutes)
        self.market_open = market_open
        self.market_close = market_close
        self.pack_path = pack_path or PACK_PATH
        self.pack = load_active_pack(self.pack_path)
        self.position: Position = "flat"
        self.entry_price: float | None = None
        self.entry_date: str | None = None
        self.last_skip: str | None = None
        self._bar_key: datetime | None = None
        self._bar_o = self._bar_h = self._bar_l = self._bar_c = None
        self._bar_n = 0
        self._bar_last_vol: float | None = None
        self._bar_tbq = 0.0
        self._bar_tsq = 0.0
        self._prev: VolBar | None = None
        self._day: VolBar | None = None
        self._prev_close_vol: float | None = None
        self._catchup_prev: VolBar | None = None
        self._catchup_cur: VolBar | None = None
        self._last_signal_at: datetime | None = None
        if seed:
            self.seed_from_ticks()

    @property
    def bar_debug(self) -> str:
        prev = f"prevC={self._prev.close:.1f}" if self._prev else "prev=none"
        day = f"dayC={self._day.close:.1f}" if self._day else "day=none"
        if self._bar_o is None:
            return f"bar=none {prev} {day}"
        return (
            f"O={self._bar_o:.1f} H={self._bar_h} L={self._bar_l} "
            f"C={self._bar_c} {prev} {day} pack={self.pack.name}"
        )

    @property
    def status_line(self) -> str:
        return (
            f"TF={self.bar_minutes}m pack={self.pack.name} "
            f"1h AND+ticks FLIP delivery "
            f"{self.market_open}-{self.market_close} "
            f"{self.bar_debug} skip={self.last_skip or '-'} pos={self.position}"
        )

    def _floor_bar(self, ts: datetime) -> datetime:
        ts = ts.astimezone(IST)
        midnight = ts.replace(hour=0, minute=0, second=0, microsecond=0)
        mins = int((ts - midnight).total_seconds() // 60)
        block = (mins // max(1, self.bar_minutes)) * max(1, self.bar_minutes)
        return midnight + timedelta(minutes=block)

    def _hhmm(self, raw: str) -> tuple[int, int]:
        h, m = raw.strip().split(":")
        return int(h), int(m)

    def _in_session(self, now: datetime) -> bool:
        if now.weekday() >= 5:
            return False
        oh, om = self._hhmm(self.market_open)
        ch, cm = self._hhmm(self.market_close)
        t = now.hour * 60 + now.minute
        return (oh * 60 + om) <= t <= (ch * 60 + cm)

    def _bar_started_in_session(self, bar: VolBar) -> bool:
        try:
            dt = datetime.strptime(bar.time[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return False
        oh, om = self._hhmm(self.market_open)
        ch, cm = self._hhmm(self.market_close)
        t = dt.hour * 60 + dt.minute
        return (oh * 60 + om) <= t < (ch * 60 + cm)

    def _same_session_prev(self, prev: VolBar, cur: VolBar) -> bool:
        if not self._bar_started_in_session(prev) or not self._bar_started_in_session(
            cur
        ):
            return False
        return bool(prev.time[:10]) and prev.time[:10] == cur.time[:10]

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
        oh, om = self._hhmm(self.market_open)
        ch, cm = self._hhmm(self.market_close)
        t = now.hour * 60 + now.minute
        if t >= ch * 60 + cm:
            return f"session close {self.market_close}"
        if t < oh * 60 + om:
            return f"preopen leftover before {self.market_open}"
        today = now.strftime("%Y-%m-%d")
        if self.entry_date and self.entry_date < today:
            return f"overnight leftover {self.entry_date} flatten at {self.market_open}"
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

    def release_decision_lock(self) -> None:
        return None

    def _reset_bar(self, key: datetime, px: float) -> None:
        self._bar_key = key
        self._bar_o = self._bar_h = self._bar_l = self._bar_c = px
        self._bar_n = 1
        self._bar_last_vol = None
        self._bar_tbq = 0.0
        self._bar_tsq = 0.0

    def _bar_volume(self) -> float:
        last = self._bar_last_vol
        prev = self._prev_close_vol
        if last is None or prev is None:
            return 0.0
        if last + 1e-9 < prev:
            return max(0.0, last)
        return max(0.0, last - prev)

    def _closed_bar(self) -> VolBar | None:
        if self._bar_key is None or self._bar_o is None:
            return None
        return VolBar(
            self._bar_key.strftime("%Y-%m-%d %H:%M:%S"),
            float(self._bar_o),
            float(self._bar_h if self._bar_h is not None else self._bar_o),
            float(self._bar_l if self._bar_l is not None else self._bar_o),
            float(self._bar_c if self._bar_c is not None else self._bar_o),
            self._bar_volume(),
            float(self._bar_n),
            float(self._bar_tbq),
            float(self._bar_tsq),
        )

    def _seed_position_from_signals(self, db: Path) -> None:
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

    def _already_decided_close(self, closed_key: datetime) -> bool:
        if self._last_signal_at is None:
            return False
        close_at = closed_key + timedelta(minutes=max(1, self.bar_minutes))
        return self._last_signal_at >= close_at

    def _session_bar(
        self, built: tuple[VolBar, float | None] | None, now: datetime
    ) -> tuple[VolBar, float | None] | None:
        if built is None:
            return None
        bar, _last = built
        if bar.time[:10] != now.strftime("%Y-%m-%d"):
            return None
        if not self._bar_started_in_session(bar):
            return None
        return built

    def _apply_message(self, message: dict[str, Any] | None) -> None:
        if not message:
            return
        raw_v = message.get("volume_trade_for_the_day")
        if raw_v is not None:
            try:
                self._bar_last_vol = float(raw_v)
            except (TypeError, ValueError):
                pass
        raw_b = message.get("total_buy_quantity")
        raw_s = message.get("total_sell_quantity")
        try:
            if raw_b is not None:
                self._bar_tbq = float(raw_b)
            if raw_s is not None:
                self._bar_tsq = float(raw_s)
        except (TypeError, ValueError):
            pass

    def _bar_from_rows(
        self,
        rows: list[tuple],
        *,
        time_label: str,
        key: datetime | None = None,
    ) -> tuple[VolBar, float | None] | None:
        o = h = l = c = None
        n = 0
        last_vol = None
        tbq = tsq = 0.0
        for row in rows:
            ts = _parse_ts(row[0])
            if ts is None:
                continue
            if key is not None and self._floor_bar(ts) != key:
                continue
            px = float(row[1])
            if o is None:
                o = h = l = c = px
                n = 1
            else:
                h = max(h, px)
                l = min(l, px)
                c = px
                n += 1
            if len(row) >= 3 and row[2] is not None:
                try:
                    last_vol = float(row[2])
                except (TypeError, ValueError):
                    pass
            if len(row) >= 4 and row[3] is not None:
                try:
                    tbq = float(row[3])
                except (TypeError, ValueError):
                    pass
            if len(row) >= 5 and row[4] is not None:
                try:
                    tsq = float(row[4])
                except (TypeError, ValueError):
                    pass
        if o is None:
            return None
        return (
            VolBar(
                time_label,
                float(o),
                float(h),
                float(l),
                float(c),
                0.0,
                float(n),
                tbq,
                tsq,
            ),
            last_vol,
        )

    def seed_from_ticks(
        self, db_path: Path | None = None, *, now: datetime | None = None
    ) -> None:
        try:
            db = db_path or (Path(__file__).resolve().parent / "data" / "ticks.db")
            if not db.exists():
                return
            self.pack = load_active_pack(self.pack_path)
            self._seed_position_from_signals(db)
            now = (now or datetime.now(IST)).astimezone(IST)
            today = now.strftime("%Y-%m-%d")
            step = timedelta(minutes=max(1, self.bar_minutes))
            cur_key = self._floor_bar(now)
            closed_key = cur_key - step
            prev_key = closed_key - step
            prev_prev_key = prev_key - step
            start = (now - timedelta(days=4)).strftime("%Y-%m-%dT00:00:00")
            con = sqlite3.connect(str(db))
            try:
                rows = con.execute(
                    "SELECT received_at, ltp, volume, bp, sp FROM ticks "
                    "WHERE ltp IS NOT NULL AND received_at >= ? "
                    "ORDER BY received_at ASC, id ASC",
                    (start,),
                ).fetchall()
            finally:
                con.close()
            day_keys = sorted(
                {str(r[0])[:10] for r in rows if str(r[0])[:10] < today}
            )
            if day_keys:
                last_day = day_keys[-1]
                day_rows = [r for r in rows if str(r[0])[:10] == last_day]
                built = self._bar_from_rows(
                    day_rows, time_label=last_day + " 00:00:00"
                )
                if built:
                    self._day = built[0]
            prev_prev_built = self._bar_from_rows(
                rows,
                time_label=prev_prev_key.strftime("%Y-%m-%d %H:%M:%S"),
                key=prev_prev_key,
            )
            prev_built = self._bar_from_rows(
                rows,
                time_label=prev_key.strftime("%Y-%m-%d %H:%M:%S"),
                key=prev_key,
            )
            closed_built = self._bar_from_rows(
                rows,
                time_label=closed_key.strftime("%Y-%m-%d %H:%M:%S"),
                key=closed_key,
            )
            if prev_built is not None and prev_prev_built is not None:
                prev_built[0].volume = _bar_volume(
                    prev_built[1], prev_prev_built[1]
                )
            if closed_built is not None and prev_built is not None:
                closed_built[0].volume = _bar_volume(
                    closed_built[1], prev_built[1]
                )
            prev_sess = self._session_bar(prev_built, now)
            closed_sess = self._session_bar(closed_built, now)
            if closed_sess is not None:
                cb, clast = closed_sess
                self._prev = cb
                self._prev_close_vol = clast
                if prev_sess is not None and not self._already_decided_close(
                    closed_key
                ):
                    self._catchup_prev = prev_sess[0]
                    self._catchup_cur = cb
            cur_built = self._bar_from_rows(
                rows,
                time_label=cur_key.strftime("%Y-%m-%d %H:%M:%S"),
                key=cur_key,
            )
            if cur_built:
                cur, clast = cur_built
                self._reset_bar(cur_key, float(cur.open))
                self._bar_h = float(cur.high)
                self._bar_l = float(cur.low)
                self._bar_c = float(cur.close)
                self._bar_n = int(cur.n_ticks or 1)
                self._bar_tbq = float(cur.tbq)
                self._bar_tsq = float(cur.tsq)
                if clast is not None:
                    self._bar_last_vol = clast
        except Exception:
            return

    def _run_catchup(self, now: datetime) -> SignalResult | None:
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

    def _maybe_decide_closed(self, closed: VolBar | None, now: datetime) -> SignalResult | None:
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
            if self._bar_last_vol is not None:
                self._prev_close_vol = self._bar_last_vol
        return result

    def on_tick(
        self, now: datetime, ltp: float, message: dict[str, Any] | None = None
    ) -> SignalResult | None:
        now = now.astimezone(IST)
        self._drop_stale_prev(now)
        key = self._floor_bar(now)
        px = float(ltp)
        flatten_why = self._session_flatten_why(now)
        had_catchup = self._catchup_cur is not None
        catchup = None if flatten_why else self._run_catchup(now)

        if self._bar_key is None:
            self._reset_bar(key, px)
            self._apply_message(message)
            if flatten_why:
                return self._flatten(px, flatten_why)
            if catchup is not None:
                return catchup
            if had_catchup:
                return None
            self.last_skip = "waiting_1h_close"
            return None

        if key != self._bar_key:
            closed = self._closed_bar()
            result = None if flatten_why else self._maybe_decide_closed(closed, now)
            if (
                flatten_why
                and closed is not None
                and self._bar_started_in_session(closed)
                and closed.time[:10] == now.strftime("%Y-%m-%d")
            ):
                self._prev = closed
                if self._bar_last_vol is not None:
                    self._prev_close_vol = self._bar_last_vol
            self._reset_bar(key, px)
            self._apply_message(message)
            if flatten_why:
                return self._flatten(px, flatten_why)
            return result if result is not None else catchup

        assert self._bar_h is not None and self._bar_l is not None
        self._bar_h = max(self._bar_h, px)
        self._bar_l = min(self._bar_l, px)
        self._bar_c = px
        self._bar_n += 1
        self._apply_message(message)
        if flatten_why:
            return self._flatten(px, flatten_why)
        if catchup is not None:
            return catchup
        if had_catchup:
            return None
        self.last_skip = "waiting_1h_close"
        return None

    def _decide_closed(self, cur: VolBar | None) -> SignalResult | None:
        if cur is None:
            self.last_skip = "no_closed_bar"
            return None
        if self._prev is None or not self._same_session_prev(self._prev, cur):
            self.last_skip = "need_prev_1h"
            return None
        side, why = s18_bar_decision(self._prev, cur, self._day, self.pack)
        if side is None:
            self.last_skip = why
            return None
        return self._flip_to(side, cur, why)

    def _flip_to(self, side: str, cur: VolBar, why: str) -> SignalResult | None:
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
        else:
            self.position = "short"
            action = "SHORT"
        self.entry_price = float(cur.close)
        self.entry_date = cur.time[:10] if cur.time else None
        kind = "FLIP" if prev != "flat" else "enter"
        return SignalResult(
            action=action,
            position_after=self.position,
            price_delta=None,
            net=cur.net,
            net_delta=None,
            prev_net_delta=None,
            reason=(
                f"{self.name} {kind} {prev}→{self.position} {why} "
                f"vol={cur.volume:.0f} n={cur.n_ticks:.0f} "
                f"O={cur.open:.1f} H={cur.high:.1f} L={cur.low:.1f} "
                f"C={cur.close:.1f} U={m.upper:.1f} Lwick={m.lower:.1f}"
            ),
        )


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
    except Exception:
        pass


def s18_from_env() -> S18OhlcVolHtfStrategy:
    _load_dotenv()
    return S18OhlcVolHtfStrategy(
        bar_minutes=int(os.getenv("S18_BAR_MINUTES", "60")),
        market_open=os.getenv("MARKET_OPEN", "09:00"),
        market_close=os.getenv("MARKET_CLOSE", "23:30"),
    )
