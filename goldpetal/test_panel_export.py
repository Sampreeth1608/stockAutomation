"""Tests for one-click date-range panel exports."""

from __future__ import annotations

import tempfile
from pathlib import Path

from panel_export import (
    default_date_range,
    export_pack_zip,
    export_summary,
    export_ticks_csv,
    export_trades_csv,
    rows_to_tsv,
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

        trades = trades_in_range("2026-08-10", "2026-08-10", db_path=db)
        assert len(trades) >= 1
        assert trades[0]["strategy"] == "S5_MINEDGE"

        csv_t = export_ticks_csv("2026-08-09", "2026-08-11", db_path=db)
        assert "received_at" in csv_t.splitlines()[0]
        assert csv_t.count("\n") >= 4

        csv_tr = export_trades_csv("2026-08-10", "2026-08-11", db_path=db)
        assert "strategy" in csv_tr.splitlines()[0]

        tsv = rows_to_tsv(ticks, ["received_at", "ltp", "bp", "sp"])
        assert "\t" in tsv.splitlines()[0]

        z = export_pack_zip("2026-08-09", "2026-08-11", db_path=db)
        assert z[:2] == b"PK"
        assert len(z) > 50

        s = export_summary("2026-08-09", "2026-08-11", db_path=db)
        assert s["tick_count"] == 3
        assert s["trade_count"] >= 1


if __name__ == "__main__":
    test_default_range()
    test_ticks_and_trades_range_export()
    print("test_panel_export: OK")
