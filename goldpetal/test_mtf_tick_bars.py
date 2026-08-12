#!/usr/bin/env python3
"""Tests for count-based MTF bars (5t…60t)."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from mtf_bars import TICK_INTERVALS, build_rich_bars_by_ticks
from storage import init_db

IST = ZoneInfo("Asia/Kolkata")


def _make_db(path: Path, n: int = 125) -> None:
    if path.exists():
        path.unlink()
    init_db(path)
    start = datetime(2026, 8, 10, 10, 0, 0, tzinfo=IST)
    rows = []
    px, tbq, tsq = 10000.0, 11000.0, 9000.0
    for i in range(n):
        t = start + timedelta(seconds=i)
        px += 1.0
        tbq += 10
        raw = {
            "last_traded_price": px,
            "total_buy_quantity": tbq,
            "total_sell_quantity": tsq,
            "last_traded_quantity": 1.0,
            "volume_trade_for_the_day": float(i + 1),
        }
        rows.append(
            (
                t.isoformat(),
                None,
                "GOLDPETAL",
                "T",
                px,
                None,
                None,
                None,
                None,
                i + 1,
                tbq,
                tsq,
                json.dumps(raw),
            )
        )
    con = sqlite3.connect(path)
    con.executemany(
        """
        INSERT INTO ticks (
            received_at, exchange_timestamp, symbol, token,
            ltp, open, high, low, close, volume, bp, sp, raw_json
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        rows,
    )
    con.commit()
    con.close()


def test_tick_intervals_cover_5_to_60():
    names = [n for n, _ in TICK_INTERVALS]
    counts = [c for _, c in TICK_INTERVALS]
    assert names[0] == "5t" and counts[0] == 5
    assert names[-1] == "60t" and counts[-1] == 60
    assert counts == sorted(counts)
    assert min(counts) == 5 and max(counts) == 60


def test_build_rich_bars_by_ticks_sizes():
    from mtf_bars import load_tick_rows

    db = Path("data") / "_test_tick_bars.db"
    _make_db(db, n=125)
    rows = load_tick_rows(db)
    bars5 = build_rich_bars_by_ticks(rows, "5t", 5)
    bars60 = build_rich_bars_by_ticks(rows, "60t", 60)
    assert len(bars5) == 125 // 5
    assert len(bars60) == 125 // 60
    assert all(b.n_ticks == 5 for b in bars5)
    assert all(b.n_ticks == 60 for b in bars60)
    # OHLC sanity on first 5t bar
    assert bars5[0].open == 10001.0
    assert bars5[0].close == 10005.0
    assert bars5[0].high >= bars5[0].close
    if db.exists():
        db.unlink()


def main() -> None:
    test_tick_intervals_cover_5_to_60()
    test_build_rich_bars_by_ticks_sizes()
    print("test_mtf_tick_bars: OK")


if __name__ == "__main__":
    main()
