"""Tests for the 7-tab Google Sheets trading dashboard."""

from __future__ import annotations

import tempfile
from pathlib import Path

from sheet_dashboard import (
    build_live_rows,
    build_market_rows,
    build_risk_rows,
    build_strategy_rows,
    command_template_rows,
    last_quote,
)
from storage import init_db, save_signal, save_tick


def _seed(db: Path) -> None:
    init_db(db)
    save_tick(
        {
            "last_traded_price": 1432000,
            "total_buy_quantity": 720,
            "total_sell_quantity": 280,
            "volume_trade_for_the_day": 293012,
            "open_interest": 170541,
        },
        symbol="GOLDPETAL",
        token="1",
        received_at="2026-08-11T10:00:00+05:30",
        db_path=db,
    )
    save_tick(
        {
            "last_traded_price": 1435500,
            "total_buy_quantity": 800,
            "total_sell_quantity": 200,
            "volume_trade_for_the_day": 293100,
            "open_interest": 170600,
        },
        symbol="GOLDPETAL",
        token="1",
        received_at="2026-08-11T10:00:05+05:30",
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
        cmp=14320.0,
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
        cmp=14355.0,
        db_path=db,
    )


def test_live_dashboard_is_honest() -> None:
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "ticks.db"
        _seed(db)
        q = last_quote(db)
        assert q["oi"] == 170600
        assert float(q["tbq"]) == 800
        live_rows = build_live_rows(db_path=db)
        live = {r["field"]: r["value"] for r in live_rows}
        assert "GOLDPETAL" in live["title"]
        assert "PAPER DASHBOARD" not in live["title"]
        assert live["ltp"]
        assert "none" in live["ai_decision"].lower()
        assert "ENABLE_FLOW_BRAIN=false" in live["small_medium_large_move"]
        buy = next(r for r in live_rows if r["field"] == "buy_pct")
        assert "█" in buy["bar"]
        market = {r["field"]: r["value"] for r in build_market_rows(db_path=db)}
        assert "regime" in market
        assert "breakout_probability" in market
        from storage import build_trades
        from charges import paper_lots

        trades = build_trades(strategy="S5_MINEDGE", db_path=db, lot_size=paper_lots())
        books = build_strategy_rows(trades, db_path=db)
        s5 = next(r for r in books if r["strategy"] == "S5_MINEDGE")
        assert "pf_after_charges" in s5
        flow = next(r for r in books if r["strategy"] == "FLOW_BRAIN")
        assert flow["in_bot"] == "NO"
        risk = {r["key"]: r["value"] for r in build_risk_rows()}
        assert "refused" in risk["micro_live"].lower()
        cmds = command_template_rows()
        names = [r["command"] for r in cmds]
        assert "pause_all" in names
        assert "micro_live" in names
        micro = next(r for r in cmds if r["command"] == "micro_live")
        assert micro["allowed"] == "NO"
        from sheet_dashboard import build_angel_rows, desk_mode_label

        assert desk_mode_label({"would_place_real_orders": True}) == "LIVE ARMED"
        assert desk_mode_label({"dry_run": True}) == "PAPER"
        angel = {r["field"]: r["value"] for r in build_angel_rows(db_path=db)}
        assert "live_open" in angel
        assert angel["hard_cap"] == "1000"


if __name__ == "__main__":
    test_live_dashboard_is_honest()
    print("ALL test_sheet_dashboard OK")
