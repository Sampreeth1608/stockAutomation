"""Unit tests for trade journal pairing and PnL."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

# Zero costs so pairing tests stay about price PnL
os.environ["TRADE_CHARGE_PER_SIDE"] = "0"
os.environ["TAX_RATE"] = "0"
os.environ["LOT_SIZE"] = "1"
os.environ.pop("TRADE_ROUND_TRIP_CHARGE", None)

from storage import build_trades, export_trades_csv, init_db, save_signal


def _db() -> Path:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    path = Path(tmp.name)
    tmp.close()
    init_db(path)
    return path


def test_buy_close_pnl() -> None:
    db = _db()
    save_signal(
        "2026-08-04T10:00:00", "GOLDPETAL", "BUY", "long", "enter",
        None, 100, 50, True, strategy="S1_NETDELTA", cmp=10000.0, db_path=db,
    )
    save_signal(
        "2026-08-04T10:30:00", "GOLDPETAL", "CLOSE", "flat", "exit",
        None, 80, -20, True, strategy="S1_NETDELTA", cmp=10050.0, db_path=db,
    )
    trades = build_trades(strategy="S1_NETDELTA", db_path=db)
    assert len(trades) == 1
    t = trades[0]
    assert t["side"] == "BUY"
    assert t["entry_price"] == 10000.0
    assert t["exit_price"] == 10050.0
    assert t["net_pnl"] == 50.0
    assert t["status"] == "CLOSED"


def test_short_close_pnl() -> None:
    db = _db()
    save_signal(
        "2026-08-04T11:00:00", "GOLDPETAL", "SHORT", "short", "enter",
        None, -100, -50, True, strategy="S2_BALANCE", cmp=20000.0, db_path=db,
    )
    save_signal(
        "2026-08-04T11:01:00", "GOLDPETAL", "CLOSE", "flat", "exit",
        None, 0, None, True, strategy="S2_BALANCE", cmp=19900.0, db_path=db,
    )
    trades = build_trades(strategy="S2_BALANCE", db_path=db)
    assert len(trades) == 1
    assert trades[0]["net_pnl"] == 100.0  # short: entry - exit


def test_flip_forces_close() -> None:
    db = _db()
    save_signal(
        "t1", "GOLDPETAL", "BUY", "long", "buy",
        None, 10, 10, True, strategy="S2_BALANCE", cmp=100.0, db_path=db,
    )
    save_signal(
        "t2", "GOLDPETAL", "SHORT", "short", "flip",
        None, -5, None, True, strategy="S2_BALANCE", cmp=110.0, db_path=db,
    )
    save_signal(
        "t3", "GOLDPETAL", "CLOSE", "flat", "done",
        None, 0, None, True, strategy="S2_BALANCE", cmp=105.0, db_path=db,
    )
    trades = build_trades(strategy="S2_BALANCE", db_path=db)
    assert len(trades) == 2
    assert trades[0]["status"] == "CLOSED_FORCED"
    assert trades[0]["net_pnl"] == 10.0  # 110 - 100
    assert trades[1]["status"] == "CLOSED"
    assert trades[1]["net_pnl"] == 5.0  # short 110 - 105


def test_ignores_hold_wait() -> None:
    db = _db()
    save_signal(
        "t0", "GOLDPETAL", "WAIT", "flat", "seed",
        None, 0, None, True, strategy="S1_NETDELTA", cmp=100.0, db_path=db,
    )
    save_signal(
        "t1", "GOLDPETAL", "BUY", "long", "buy",
        None, 10, 10, True, strategy="S1_NETDELTA", cmp=100.0, db_path=db,
    )
    save_signal(
        "t2", "GOLDPETAL", "HOLD", "long", "hold",
        None, 12, 2, True, strategy="S1_NETDELTA", cmp=101.0, db_path=db,
    )
    trades = build_trades(strategy="S1_NETDELTA", db_path=db)
    assert len(trades) == 1
    assert trades[0]["status"] == "OPEN"


def test_export_csv() -> None:
    db = _db()
    save_signal(
        "t1", "GOLDPETAL", "BUY", "long", "buy",
        None, 10, 10, True, strategy="S1_NETDELTA", cmp=100.0, db_path=db,
    )
    save_signal(
        "t2", "GOLDPETAL", "CLOSE", "flat", "close",
        None, 5, -5, True, strategy="S1_NETDELTA", cmp=90.0, db_path=db,
    )
    out = Path(tempfile.mkdtemp()) / "trades.csv"
    n, closed, pnl = export_trades_csv(out, strategy="S1_NETDELTA", db_path=db)
    assert n == 1 and closed == 1 and pnl == -10.0
    text = out.read_text(encoding="utf-8")
    assert "net_pnl" in text
    assert "BUY" in text


def test_all_strategies_do_not_cross_pair() -> None:
    db = _db()
    save_signal(
        "t1", "GOLDPETAL", "BUY", "long", "s1",
        None, 1, 1, True, strategy="S1_NETDELTA", cmp=100.0, db_path=db,
    )
    save_signal(
        "t2", "GOLDPETAL", "CLOSE", "flat", "s2close",
        None, 0, None, True, strategy="S2_BALANCE", cmp=90.0, db_path=db,
    )
    trades = build_trades(strategy=None, db_path=db)
    assert len(trades) == 1
    assert trades[0]["strategy"] == "S1_NETDELTA"
    assert trades[0]["status"] == "OPEN"


if __name__ == "__main__":
    test_buy_close_pnl()
    test_short_close_pnl()
    test_flip_forces_close()
    test_ignores_hold_wait()
    test_export_csv()
    test_all_strategies_do_not_cross_pair()
    print("ok")
