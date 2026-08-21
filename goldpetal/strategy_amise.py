"""AMISE slot books S21, S22, … — compiled factory genomes on the genome TF.

FLIP at the finished bar close. Delivery — holds overnight (only S16
flattens at MARKET_CLOSE). Daily swing also flattens on the Gold Petal
roll (same rule as S13). You-hours mimic still exits when that window ends.
Paper ENABLE is written only after Lab Approve. Not a live unlock.
Keep DRY_RUN=true.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from amise_slots import amise_books_now, load_slot_genome
from amise_timeframes import daily_bar_close, floor_session_bar, parse_tf
from flow_lab import FlowBar, FlowParams, build_flow_features, lab_bars_for_tf
from mtf_bars import parse_ts
from research_features import compile_genome
from s18_ohlc_vol_htf import VolBar
from strategy import SignalResult
from strategy_genome import StrategyGenome, params_from_genome
from strategy_s18 import S18OhlcVolHtfStrategy, _load_dotenv
from symbols import s13_roll_intent

IST = ZoneInfo("Asia/Kolkata")


def vol_to_flow(bar: VolBar) -> FlowBar:
    return FlowBar(
        bar.time,
        float(bar.open),
        float(bar.high),
        float(bar.low),
        float(bar.close),
        float(bar.volume),
        float(bar.n_ticks),
        float(bar.tbq),
        float(bar.tsq),
    )


class AmiseSlotStrategy(S18OhlcVolHtfStrategy):
    """One named AMISE slot. Genome comes from data/amise/slots/<slot>.json."""

    def __init__(
        self,
        slot_name: str,
        genome: StrategyGenome,
        *,
        bar_minutes: int | None = None,
        market_open: str = "09:00",
        market_close: str = "23:30",
        seed: bool = True,
        slots_dir: Path | None = None,
    ) -> None:
        self.slot_name = str(slot_name)
        self.name = self.slot_name
        self.genome = genome.normalized()
        self._tf = parse_tf(self.genome.timeframe)
        minutes = int(bar_minutes) if bar_minutes is not None else self._tf.minutes
        self.flow_params: FlowParams = params_from_genome(self.genome)
        self._decide_fn = compile_genome(self.genome)
        self._flow_bars: list[FlowBar] = []
        self._flow_state: dict[str, Any] = {}
        self._slots_dir = slots_dir
        self._contract: dict[str, Any] | None = None
        self.contract_symbol: str | None = None
        self._last_token = ""
        self._keep_bars = max(80, int(getattr(self.flow_params, "lookback", 20) or 20) * 4)
        super().__init__(
            bar_minutes=minutes,
            market_open=market_open,
            market_close=market_close,
            seed=seed,
        )

    @property
    def holds_overnight(self) -> bool:
        return bool(self._tf.daily)

    @property
    def bar_debug(self) -> str:
        prev = f"bars={len(self._flow_bars)}"
        if self._bar_o is None:
            return f"bar=none {prev} genome={self.genome.genome_id}"
        return (
            f"O={self._bar_o:.1f} H={self._bar_h} L={self._bar_l} "
            f"C={self._bar_c} {prev} genome={self.genome.genome_id}"
        )

    @property
    def status_line(self) -> str:
        hold = "overnight" if self.holds_overnight else "delivery"
        return (
            f"TF={self._tf.label} AMISE {self.genome.name} "
            f"{self.genome.direction} FLIP {hold} "
            f"{self.market_open}-{self.market_close} "
            f"{self.bar_debug} skip={self.last_skip or '-'} pos={self.position}"
        )

    def _floor_bar(self, ts: datetime) -> datetime:
        return floor_session_bar(
            ts,
            self.bar_minutes,
            market_open=self.market_open,
            market_close=self.market_close,
        )

    def _you_hours(self) -> tuple[int | None, int | None, int]:
        """Sitting window learned from You-tab clicks. None = whole Gold Petal session."""
        p = self.genome.params or {}
        if "you_open_min" not in p or "you_close_min" not in p:
            return None, None, int(p.get("you_hours_mask") or 0)
        return int(p["you_open_min"]), int(p["you_close_min"]), int(p.get("you_hours_mask") or 0)

    def _in_you_hours(self, now: datetime) -> bool:
        lo, hi, _mask = self._you_hours()
        if lo is None or hi is None:
            return True
        t = now.hour * 60 + now.minute
        return lo <= t < hi

    def _bar_clock(self, cur: VolBar) -> datetime:
        try:
            return datetime.strptime(cur.time[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
        except ValueError:
            return datetime.now(IST)

    def _session_flatten_why(self, now: datetime) -> str | None:
        if self.holds_overnight:
            return None
        lo, hi, _mask = self._you_hours()
        if hi is not None and self.position != "flat":
            t = now.hour * 60 + now.minute
            if t >= hi:
                return f"you hours end {hi // 60:02d}:{hi % 60:02d}"
            if lo is not None and t < lo:
                return f"preopen leftover before you hours {lo // 60:02d}:{lo % 60:02d}"
        # Delivery: only S16 flattens at MARKET_CLOSE. Do not inherit S18's old EOD close.
        return None

    def _already_decided_close(self, closed_key: datetime) -> bool:
        if self._last_signal_at is None:
            return False
        if self.holds_overnight:
            close_at = daily_bar_close(closed_key, market_close=self.market_close)
        else:
            close_at = closed_key + timedelta(minutes=max(1, self.bar_minutes))
        return self._last_signal_at >= close_at

    def _at_or_after_close(self, now: datetime) -> bool:
        ch, cm = self._hhmm(self.market_close)
        t = now.hour * 60 + now.minute
        return t >= ch * 60 + cm

    def set_contract(self, contract: dict[str, Any] | None) -> None:
        """Bind the live Gold Petal contract (front vs next per ROLLOVER_DAYS)."""
        self._contract = dict(contract) if contract else None
        if self._contract:
            self.contract_symbol = str(self._contract.get("symbol") or "") or self.contract_symbol

    def _reset_book_for_new_contract(self) -> None:
        self._flow_bars = []
        self._flow_state = {}
        self._prev = None
        self._prev_close_vol = None
        self._last_token = ""

    def _roll_on_tick(self, now: datetime, px: float) -> SignalResult | None:
        c = self._contract
        if not c:
            return None
        symbol = str(c.get("symbol") or "")
        intent = s13_roll_intent(
            held_symbol=self.contract_symbol,
            trade_symbol=symbol,
            rolled=bool(c.get("rolled")),
            days_to_front_expiry=int(c.get("days_to_front_expiry") or 0),
            rollover_days=int(c.get("rollover_days") or 5),
            in_position=self.position in {"long", "short"},
        )
        if intent in {"flatten_switch", "reset_switch"}:
            sig = None
            if self.position != "flat":
                sig = self._flatten(
                    px,
                    f"contract switch {self.contract_symbol}→{symbol} "
                    f"ROLLOVER_DAYS={c.get('rollover_days')}",
                )
            self._reset_book_for_new_contract()
            self.contract_symbol = symbol or None
            self.last_skip = "rolled_new_contract"
            return sig
        if intent == "flatten_last_front":
            sig = self._flatten(
                px,
                f"last front-month session "
                f"days_to_front={c.get('days_to_front_expiry')} "
                f"ROLLOVER_DAYS={c.get('rollover_days')} — avoid near expiry",
            )
            self.last_skip = "avoid_front_roll"
            return sig
        if intent == "block_last_front":
            self.last_skip = "avoid_front_roll"
            if symbol and self.contract_symbol != symbol:
                self.contract_symbol = symbol
            return None
        if symbol:
            self.contract_symbol = symbol
        return None

    def seed_from_ticks(
        self, db_path: Path | None = None, *, now: datetime | None = None
    ) -> None:
        super().seed_from_ticks(db_path, now=now)
        self._rebuild_flow_history(db_path=db_path, now=now)

    def _rebuild_flow_history(
        self, *, db_path: Path | None = None, now: datetime | None = None
    ) -> None:
        try:
            import sqlite3

            db = db_path or (Path(__file__).resolve().parent / "data" / "ticks.db")
            if not db.exists():
                return
            now = (now or datetime.now(IST)).astimezone(IST)
            days = 90 if self.holds_overnight else (14 if self.bar_minutes >= 120 else 5)
            start = (now - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00")
            con = sqlite3.connect(str(db))
            con.row_factory = sqlite3.Row
            try:
                rows = con.execute(
                    """
                    SELECT id, received_at, exchange_timestamp, ltp, bp, sp, volume,
                           raw_json, token, symbol
                    FROM ticks
                    WHERE ltp IS NOT NULL AND received_at >= ?
                    ORDER BY received_at ASC, id ASC
                    """,
                    (start,),
                ).fetchall()
            finally:
                con.close()
            bars = lab_bars_for_tf(rows, self._tf)
            cur_key = self._floor_bar(now)
            closed: list[FlowBar] = []
            for bar in bars:
                ts = parse_ts(bar.time)
                if ts >= cur_key:
                    break
                closed.append(bar)
            self._flow_bars = closed[-self._keep_bars :]
        except Exception:
            return

    def _maybe_decide_closed(self, closed: VolBar | None, now: datetime) -> SignalResult | None:
        if not self.holds_overnight:
            return super()._maybe_decide_closed(closed, now)
        if closed is None:
            return None
        result = None
        try:
            key = datetime.strptime(closed.time[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
        except ValueError:
            key = self._floor_bar(now)
        if not self._already_decided_close(key):
            if self._in_session(now) or self._at_or_after_close(now):
                result = self._decide_closed(closed)
        if self._bar_started_in_session(closed):
            self._prev = closed
            if self._bar_last_vol is not None:
                self._prev_close_vol = self._bar_last_vol
        return result

    def on_tick(
        self, now: datetime, ltp: float, message: dict[str, Any] | None = None
    ) -> SignalResult | None:
        now = now.astimezone(IST)
        px = float(ltp)
        rolled = self._roll_on_tick(now, px)
        if rolled is not None:
            return rolled
        tok = ""
        if message:
            tok = str(message.get("token") or "")
        if not tok and self._contract:
            tok = str(self._contract.get("token") or "")
        if self._last_token and tok and tok != self._last_token:
            self._reset_book_for_new_contract()
            self._reset_bar(self._floor_bar(now), px)
        if tok:
            self._last_token = tok
        if self.holds_overnight and self._at_or_after_close(now) and now.weekday() < 5:
            if self._bar_key is not None:
                self._apply_message(message)
                if self._bar_h is not None and self._bar_l is not None:
                    self._bar_h = max(self._bar_h, px)
                    self._bar_l = min(self._bar_l, px)
                    self._bar_c = px
                    self._bar_n += 1
                closed = self._closed_bar()
                if closed is not None and not self._already_decided_close(self._bar_key):
                    return self._decide_closed(closed)
                return None
        return super().on_tick(now, ltp, message)

    def _decide_closed(self, cur: VolBar | None) -> SignalResult | None:
        if cur is None:
            self.last_skip = "no_closed_bar"
            return None
        if not self._bar_started_in_session(cur):
            self.last_skip = "outside_session"
            return None
        fb = vol_to_flow(cur)
        self._flow_bars.append(fb)
        if len(self._flow_bars) > self._keep_bars:
            self._flow_bars = self._flow_bars[-self._keep_bars :]
        feats = build_flow_features(self._flow_bars, self.flow_params)
        i = len(self._flow_bars) - 1
        want, why = self._decide_fn(
            self._flow_bars, feats, i, self.flow_params, self._flow_state
        )
        if want not in {"long", "short"}:
            self.last_skip = why
            return None
        if not self._in_you_hours(self._bar_clock(cur)):
            self.last_skip = "outside_you_hours"
            return None
        return self._flip_to(want, cur, why)


def amise_slot_from_env(slot_name: str, *, slots_dir: Path | None = None) -> AmiseSlotStrategy:
    _load_dotenv()
    genome = load_slot_genome(slot_name, slots_dir)
    if genome is None:
        raise FileNotFoundError(f"no AMISE genome for {slot_name}")
    spec = parse_tf(genome.timeframe)
    return AmiseSlotStrategy(
        slot_name,
        genome,
        bar_minutes=spec.minutes,
        market_open=os.getenv("MARKET_OPEN", "09:00"),
        market_close=os.getenv("MARKET_CLOSE", "23:30"),
        slots_dir=slots_dir,
    )


def load_amise_slot_books(portfolio, *, slots_dir: Path | None = None) -> list[Any]:
    from strategy_disabled import DisabledStrategy

    out: list[Any] = []
    for name in amise_books_now(slots_dir):
        genome = load_slot_genome(name, slots_dir)
        if portfolio.is_enabled(name) and genome is not None:
            try:
                out.append(amise_slot_from_env(name, slots_dir=slots_dir))
                continue
            except Exception:
                pass
        out.append(DisabledStrategy(name))
    return out
