"""Tests for the read-only Google Sheets phone monitor."""

from __future__ import annotations

import io
import tempfile
import zipfile
from pathlib import Path

from google_sheet_io import resolve_creds_path, resolve_sheet_id, spreadsheet_url
from monitor_sheet import (
    BOOK_FIELDS,
    STATUS_FIELDS,
    TAB_ORDER,
    WATCH_NOTE,
    build_book_rows,
    build_status_rows,
    how_to_rows,
    monitor_sheet_zip_bytes,
    write_monitor_pack,
)
from storage import init_db, save_signal, save_tick


def _seed(db: Path) -> None:
    init_db(db)
    save_tick(
        {
            "last_traded_price": 1500000,
            "total_buy_quantity": 120,
            "total_sell_quantity": 80,
            "volume_trade_for_the_day": 10,
        },
        symbol="GOLDPETAL",
        token="1",
        received_at="2026-08-11T10:00:00+05:30",
        db_path=db,
    )
    save_signal(
        time_label="2026-08-11T10:00:00+05:30",
        symbol="GOLDPETAL",
        action="BUY",
        position_after="long",
        reason="test",
        price_delta=1.0,
        net=10.0,
        net_delta=1.0,
        dry_run=True,
        strategy="S5_MINEDGE",
        cmp=10000.0,
        db_path=db,
    )
    save_signal(
        time_label="2026-08-11T11:00:00+05:30",
        symbol="GOLDPETAL",
        action="CLOSE",
        position_after="flat",
        reason="test_exit",
        price_delta=5.0,
        net=12.0,
        net_delta=2.0,
        dry_run=True,
        strategy="S5_MINEDGE",
        cmp=10005.0,
        db_path=db,
    )


def test_write_monitor_pack() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        db = root / "ticks.db"
        _seed(db)
        out = root / "packs"
        meta = write_monitor_pack(out, db_path=db, closed_limit=20, signal_limit=20)
        zip_path = Path(meta["zip"])
        assert zip_path.exists()
        with zipfile.ZipFile(zip_path) as zf:
            names = set(zf.namelist())
        for name in (
            "LIVE.csv",
            "ANGEL.csv",
            "MARKET.csv",
            "STRATEGIES.csv",
            "SIGNALS.csv",
            "TRADES.csv",
            "RISK.csv",
            "COMMANDS.csv",
            "STATUS.csv",
            "BOOKS.csv",
            "HOW_TO.csv",
            "README.txt",
        ):
            assert name in names
        status = {r["key"]: r["value"] for r in build_status_rows(db_path=db)}
        assert status["note"] == WATCH_NOTE
        assert "rank" in status
        assert "after_charges" in status["rank"]
        assert status["ltp"]
        books = build_book_rows(db_path=db)
        assert books[-1]["strategy"] == "ALL"
        assert "after_charges_₹" in BOOK_FIELDS
        s5 = next(r for r in books if r["strategy"] == "S5_MINEDGE")
        assert int(s5["closed"]) >= 1
        flow = next(r for r in books if r["strategy"] == "FLOW_BRAIN")
        assert flow["in_bot"] == "NO"
        how = "\n".join(r["step"] for r in how_to_rows())
        assert "LIVE" in how
        assert "ANGEL" in how
        assert "Unlock live" in how
        assert "GOOGLE_MONITOR_SHEET_ID" in how
        assert "micro_live" in how


def test_zip_bytes() -> None:
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "ticks.db"
        _seed(db)
        blob = monitor_sheet_zip_bytes(db_path=db)
        assert blob[:2] == b"PK"
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            names = zf.namelist()
            assert "LIVE.csv" in names
            assert "ANGEL.csv" in names
            assert "STRATEGIES.csv" in names
            assert "COMMANDS.csv" in names


def test_sheet_env_helpers() -> None:
    env = {
        "GOOGLE_SERVICE_ACCOUNT_JSON": "/tmp/sa.json",
        "GOOGLE_MONITOR_SHEET_ID": "abc123",
        "GOOGLE_SHEET_ID": "ignored",
    }
    assert resolve_creds_path(env=env) == "/tmp/sa.json"
    assert resolve_sheet_id(env=env) == "abc123"
    env2 = {"GOOGLE_SHEET_ID": "fallback"}
    assert resolve_sheet_id(env=env2) == "fallback"
    assert "abc123" in spreadsheet_url("abc123")
    assert TAB_ORDER[0] == "LIVE"
    assert STATUS_FIELDS == ["key", "value"]


if __name__ == "__main__":
    test_write_monitor_pack()
    print("ok write_monitor_pack")
    test_zip_bytes()
    print("ok zip_bytes")
    test_sheet_env_helpers()
    print("ok env helpers")
    print("ALL test_monitor_sheet OK")
