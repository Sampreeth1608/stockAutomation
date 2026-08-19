"""AMISE slot books S21, S22, … — compiled factory genomes on 1h bars.

FLIP at the finished hour close. Flatten at MARKET_CLOSE. Paper ENABLE
is written only after Lab Approve. Not a live unlock. Keep DRY_RUN=true.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from amise_slots import amise_books_now, load_slot_genome
from flow_lab import FlowBar, FlowParams, build_flow_features
from research_features import compile_genome
from s18_ohlc_vol_htf import VolBar, _bar_volume
from strategy import SignalResult
from strategy_genome import StrategyGenome, params_from_genome
from strategy_s18 import S18OhlcVolHtfStrategy, _load_dotenv
from strategy_wick import _parse_ts

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
        bar_minutes: int = 60,
        market_open: str = "09:00",
        market_close: str = "23:30",
        seed: bool = True,
        slots_dir: Path | None = None,
    ) -> None:
        self.slot_name = str(slot_name)
        self.name = self.slot_name
        self.genome = genome.normalized()
        self.flow_params: FlowParams = params_from_genome(self.genome)
        self._decide_fn = compile_genome(self.genome)
        self._flow_bars: list[FlowBar] = []
        self._flow_state: dict[str, Any] = {}
        self._slots_dir = slots_dir
        super().__init__(
            bar_minutes=bar_minutes,
            market_open=market_open,
            market_close=market_close,
            seed=seed,
        )

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
        return (
            f"TF={self.bar_minutes}m AMISE {self.genome.name} "
            f"{self.genome.direction} FLIP flatten-at-close "
            f"{self.market_open}-{self.market_close} "
            f"{self.bar_debug} skip={self.last_skip or '-'} pos={self.position}"
        )

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
            start = (now - timedelta(days=5)).strftime("%Y-%m-%dT00:00:00")
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
            keys: list[datetime] = []
            seen: set[datetime] = set()
            for row in rows:
                ts = _parse_ts(row[0])
                if ts is None:
                    continue
                key = self._floor_bar(ts)
                if key in seen:
                    continue
                seen.add(key)
                keys.append(key)
            cur_key = self._floor_bar(now)
            bars: list[FlowBar] = []
            prev_vol: float | None = None
            for key in keys:
                if key >= cur_key:
                    break
                built = self._bar_from_rows(
                    rows,
                    time_label=key.strftime("%Y-%m-%d %H:%M:%S"),
                    key=key,
                )
                if built is None:
                    continue
                bar, last_vol = built
                bar.volume = _bar_volume(last_vol, prev_vol)
                prev_vol = last_vol
                if self._bar_started_in_session(bar):
                    bars.append(vol_to_flow(bar))
            self._flow_bars = bars[-80:]
        except Exception:
            return

    def _decide_closed(self, cur: VolBar | None) -> SignalResult | None:
        if cur is None:
            self.last_skip = "no_closed_bar"
            return None
        if not self._bar_started_in_session(cur):
            self.last_skip = "outside_session"
            return None
        fb = vol_to_flow(cur)
        self._flow_bars.append(fb)
        if len(self._flow_bars) > 80:
            self._flow_bars = self._flow_bars[-80:]
        feats = build_flow_features(self._flow_bars, self.flow_params)
        i = len(self._flow_bars) - 1
        want, why = self._decide_fn(
            self._flow_bars, feats, i, self.flow_params, self._flow_state
        )
        if want not in {"long", "short"}:
            self.last_skip = why
            return None
        return self._flip_to(want, cur, why)


def amise_slot_from_env(slot_name: str, *, slots_dir: Path | None = None) -> AmiseSlotStrategy:
    _load_dotenv()
    genome = load_slot_genome(slot_name, slots_dir)
    if genome is None:
        raise FileNotFoundError(f"no AMISE genome for {slot_name}")
    return AmiseSlotStrategy(
        slot_name,
        genome,
        bar_minutes=int(os.getenv("S21_BAR_MINUTES", os.getenv("S18_BAR_MINUTES", "60"))),
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
