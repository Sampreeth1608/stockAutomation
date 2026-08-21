"""Tests for one-click date-range panel exports."""

from __future__ import annotations

import tempfile
from pathlib import Path

from panel_export import (
    default_date_range,
    export_pack_zip,
    export_signals_csv,
    export_summary,
    export_ticks_csv,
    export_trades_csv,
    rows_to_tsv,
    signals_in_range,
    ticks_in_range,
    trades_in_range,
)
from storage import init_db, save_signal, save_tick


def test_default_range() -> None:
    a, b = default_date_range()
    assert len(a) == 10 and len(b) == 10
    assert a <= b


def test_ticks_and_trades_range_export() -> None:
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "ticks.db"
        init_db(db)
        for i, day in enumerate(["2026-08-09", "2026-08-10", "2026-08-11"]):
            save_tick(
                {
                    "last_traded_price": 1500000 + i,
                    "total_buy_quantity": 100,
                    "total_sell_quantity": 80,
                    "volume_trade_for_the_day": 10 + i,
                },
                symbol="GOLDPETAL",
                token="1",
                received_at=f"{day}T10:00:00+05:30",
                db_path=db,
            )
        save_signal(
            time_label="2026-08-10T11:00:00+05:30",
            symbol="GOLDPETAL",
            action="BUY",
            position_after="long",
            reason="test",
            price_delta=1.0,
            net=20.0,
            net_delta=2.0,
            dry_run=True,
            strategy="S5_MINEDGE",
            cmp=15010.0,
            db_path=db,
        )
        save_signal(
            time_label="2026-08-10T12:00:00+05:30",
            symbol="GOLDPETAL",
            action="CLOSE",
            position_after="flat",
            reason="test_exit",
            price_delta=1.0,
            net=10.0,
            net_delta=-2.0,
            dry_run=True,
            strategy="S5_MINEDGE",
            cmp=15025.0,
            db_path=db,
        )

        ticks = ticks_in_range("2026-08-10", "2026-08-11", db_path=db)
        assert len(ticks) == 2
        assert all(t["received_at"][:10] in {"2026-08-10", "2026-08-11"} for t in ticks)
        assert "total_buy_quantity" in ticks[0]
        assert ticks[0]["total_buy_quantity"] == 100

        trades = trades_in_range("2026-08-10", "2026-08-10", db_path=db)
        assert len(trades) >= 1
        assert trades[0]["strategy"] == "S5_MINEDGE"

        csv_t = export_ticks_csv("2026-08-09", "2026-08-11", db_path=db)
        header = csv_t.splitlines()[0]
        assert "received_at" in header
        assert "last_traded_quantity" in header
        assert "total_buy_quantity" in header
        assert "total_sell_quantity" in header
        assert "buy1_price" in header
        assert "buy5_qty" in header
        assert "sell1_price" in header
        assert "sell5_qty" in header
        assert "open_interest" in header
        assert csv_t.count("\n") >= 4

        csv_tr = export_trades_csv("2026-08-10", "2026-08-11", db_path=db)
        assert "strategy" in csv_tr.splitlines()[0]
        csv_s = export_signals_csv("2026-08-09", "2026-08-11", db_path=db)
        assert "time_label" in csv_s.splitlines()[0]
        assert "S5_MINEDGE" in csv_s
        sigs = signals_in_range("2026-08-10", "2026-08-10", db_path=db)
        assert len(sigs) >= 2

        tsv = rows_to_tsv(ticks, ["received_at", "ltp", "total_buy_quantity", "total_sell_quantity"])
        assert "\t" in tsv.splitlines()[0]

        z = export_pack_zip("2026-08-09", "2026-08-11", db_path=db)
        assert z[:2] == b"PK"
        assert len(z) > 50
        import zipfile
        import io
        with zipfile.ZipFile(io.BytesIO(z)) as zf:
            names = zf.namelist()
        assert "signals.csv" in names
        assert "ticks.csv" in names

        s = export_summary("2026-08-09", "2026-08-11", db_path=db)
        assert s["tick_count"] == 3
        assert s["signal_count"] >= 2
        assert s["trade_count"] >= 1


def test_ticks_csv_includes_depth_ltq_oi() -> None:
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "ticks.db"
        init_db(db)
        save_tick(
            {
                "last_traded_price": 1_512_300,
                "open_price_of_the_day": 1_500_000,
                "high_price_of_the_day": 1_520_000,
                "low_price_of_the_day": 1_490_000,
                "closed_price": 1_511_000,
                "volume_trade_for_the_day": 88421,
                "last_traded_quantity": 3,
                "average_traded_price": 1_508_000,
                "total_buy_quantity": 4120,
                "total_sell_quantity": 3890,
                "open_interest": 170541,
                "last_traded_time": "09:15:02",
                "best_5_buy_data": [
                    {"price": 1_512_200, "quantity": 11},
                    {"price": 1_512_100, "quantity": 8},
                    {"price": 1_512_000, "quantity": 5},
                    {"price": 1_511_900, "quantity": 4},
                    {"price": 1_511_800, "quantity": 2},
                ],
                "best_5_sell_data": [
                    {"price": 1_512_400, "quantity": 9},
                    {"price": 1_512_500, "quantity": 7},
                    {"price": 1_512_600, "quantity": 6},
                    {"price": 1_512_700, "quantity": 3},
                    {"price": 1_512_800, "quantity": 1},
                ],
            },
            symbol="GOLDPETAL",
            token="99",
            received_at="2026-08-21T09:15:02+05:30",
            db_path=db,
        )
        rows = ticks_in_range("2026-08-21", "2026-08-21", db_path=db)
        assert len(rows) == 1
        t = rows[0]
        assert t["ltp"] == 15123.0
        assert t["volume"] == 88421
        assert t["last_traded_quantity"] == 3
        assert t["total_buy_quantity"] == 4120
        assert t["total_sell_quantity"] == 3890
        assert t["open_interest"] == 170541
        assert t["buy1_price"] == 15122.0
        assert t["buy1_qty"] == 11
        assert t["buy5_qty"] == 2
        assert t["sell1_price"] == 15124.0
        assert t["sell5_qty"] == 1
        csv_t = export_ticks_csv("2026-08-21", "2026-08-21", db_path=db)
        header = csv_t.splitlines()[0].split(",")
        assert header[0] == "received_at"
        assert "open_interest" in header
        assert "buy1_price" in header
        assert "sell5_qty" in header
        body = csv_t.splitlines()[1]
        assert "15123" in body
        assert "170541" in body
        from storage import export_csv

        out = Path(td) / "all.csv"
        n = export_csv(out, db_path=db)
        assert n == 1
        text = out.read_text(encoding="utf-8")
        assert "last_traded_quantity" in text.splitlines()[0]
        assert "buy1_price" in text.splitlines()[0]


if __name__ == "__main__":
    test_default_range()
    test_ticks_and_trades_range_export()
    test_ticks_csv_includes_depth_ltq_oi()
    print("test_panel_export: OK")
