"""Tests for Google Sheets pack exporter."""

from __future__ import annotations

import io
import tempfile
import zipfile
from pathlib import Path

from sheets_pack import sheets_pack_zip_bytes, write_sheets_pack
from storage import init_db, save_signal, save_tick


def _seed(db: Path) -> None:
    init_db(db)
    save_tick(
        {
            "last_traded_price": 1500000,
            "total_buy_quantity": 100,
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


def test_write_sheets_pack() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        db = root / "ticks.db"
        _seed(db)
        out = root / "packs"
        meta = write_sheets_pack(out, db_path=db, signal_limit=50)
        zip_path = Path(meta["zip"])
        assert zip_path.exists()
        with zipfile.ZipFile(zip_path) as zf:
            names = set(zf.namelist())
        assert "scoreboard.csv" in names
        assert "trades_all.csv" in names
        assert "open_positions.csv" in names
        assert "signals_recent.csv" in names
        assert "control_status.csv" in names
        assert "README.txt" in names
        assert meta["counts"]["trades"] >= 1


def test_zip_bytes() -> None:
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "ticks.db"
        _seed(db)
        blob = sheets_pack_zip_bytes(db_path=db)
        assert blob[:2] == b"PK"
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            assert "scoreboard.csv" in zf.namelist()


if __name__ == "__main__":
    test_write_sheets_pack()
    print("ok write_sheets_pack")
    test_zip_bytes()
    print("ok zip_bytes")
    print("ALL test_sheets_pack OK")
