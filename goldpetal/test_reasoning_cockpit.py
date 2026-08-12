"""Tests for entry/hold/exit reasoning cockpit + multi-TF panel bars."""

from __future__ import annotations

import tempfile
from pathlib import Path

from mtf_bars import INTERVALS
from reasoning_cockpit import (
    bars_for_panel,
    panel_timeframes,
    run_lifecycle_reasoning,
    save_reasoning,
)
from s8_reasoner import reason_entry, reason_exit, reason_hold
from storage import init_db, save_tick


def test_interval_includes_day() -> None:
    names = {n for n, _ in INTERVALS}
    assert "1m" in names and "1h" in names and "1d" in names and "4h" in names
    tfs = {t["tf"] for t in panel_timeframes()}
    assert "1d" in tfs


def test_reason_hold_and_exit_heads() -> None:
    hold = reason_hold(
        side="long",
        move=18.0,
        tp=45.0,
        sl=35.0,
        tbq_falling=False,
        tsq_falling=False,
        hold_proba=0.62,
        exit_soon_proba=0.22,
        regime="TREND_UP",
        imb=20.0,
        net=400.0,
    )
    assert hold.action == "HOLD"

    ex = reason_exit(
        side="long",
        move=-36.0,
        tp=45.0,
        sl=35.0,
        tbq_falling=True,
        tsq_falling=False,
        regime="TREND_DOWN",
        imb=8.0,
        net=-100.0,
    )
    assert ex.action == "EXIT"
    assert "hit_sl" in ex.summary or "book_drop" in ex.summary or "regime" in ex.summary


def test_entry_skips_against_regime() -> None:
    tr = reason_entry(
        px=15200.0,
        net=-500.0,  # short candidate
        imb=25.0,
        prev_imb=18.0,
        tp=45.0,
        sl=35.0,
        min_imb=10.0,
        imb_rising=True,
        require_rising_imb=True,
        tbq_rising=False,
        tsq_rising=True,
        loss_locked=False,
        in_cooldown=False,
        entry_proba=0.70,
        regime="TREND_UP",  # fighting uptrend with short
    )
    assert tr.action == "SKIP"


def test_lifecycle_and_bars_from_synth_ticks() -> None:
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "ticks.db"
        init_db(db)
        # seed a few rising ticks
        base = 15000.0
        for i in range(40):
            msg = {
                "last_traded_price": int((base + i * 0.5) * 100),
                "total_buy_quantity": 1000 + i * 20,
                "total_sell_quantity": 800,
                "volume_trade_for_the_day": 1000 + i,
                "last_traded_quantity": 2,
                "exchange_timestamp": i,
            }
            save_tick(
                msg,
                symbol="GOLDPETAL",
                token="1",
                received_at=f"2026-08-11T10:{i:02d}:00+05:30",
                db_path=db,
            )
        life = run_lifecycle_reasoning(db=db, lots=10.0, min_imb=5.0, require_rising_imb=False)
        assert life.recommended in {
            "ENTER_LONG",
            "ENTER_SHORT",
            "HOLD",
            "EXIT",
            "WAIT",
            "SKIP",
        }
        assert "entry" in life.to_dict() and "hold" in life.to_dict() and "exit" in life.to_dict()
        path = Path(td) / "reasoning.json"
        save_reasoning(life, path=path)
        assert path.exists()

        pack = bars_for_panel("1m", limit=20, db=db, max_ticks=1000)
        assert pack["tf"] == "1m"
        assert pack["n_bars"] >= 1
        day = bars_for_panel("1d", limit=5, db=db, max_ticks=1000)
        assert day["tf"] == "1d"


if __name__ == "__main__":
    test_interval_includes_day()
    test_reason_hold_and_exit_heads()
    test_entry_skips_against_regime()
    test_lifecycle_and_bars_from_synth_ticks()
    print("test_reasoning_cockpit: OK")
