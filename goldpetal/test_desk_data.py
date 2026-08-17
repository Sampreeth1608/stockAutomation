"""Watch tape: ticks load without rebuilding every trade."""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

from desk_data import (
    history_payload,
    json_safe,
    reset_trade_cache,
    resolve_desk_db,
    tape_payload,
)
from storage import init_db, list_signals, save_signal, save_tick


def _tick(db: Path, day: str = "2026-08-17", ltp: int = 1500000) -> None:
    save_tick(
        {
            "last_traded_price": ltp,
            "total_buy_quantity": 100,
            "total_sell_quantity": 80,
            "volume_trade_for_the_day": 10,
        },
        symbol="GOLDPETAL",
        token="1",
        received_at=f"{day}T10:00:00+05:30",
        db_path=db,
    )


def _round_trip(db: Path) -> None:
    save_signal(
        time_label="2026-08-17T11:00:00+05:30",
        symbol="GOLDPETAL",
        action="BUY",
        position_after="long",
        reason="test",
        price_delta=1.0,
        net=20.0,
        net_delta=2.0,
        dry_run=True,
        strategy="S14_WICK30_STRICT",
        cmp=15010.0,
        db_path=db,
    )
    save_signal(
        time_label="2026-08-17T12:00:00+05:30",
        symbol="GOLDPETAL",
        action="CLOSE",
        position_after="flat",
        reason="test_exit",
        price_delta=1.0,
        net=10.0,
        net_delta=-2.0,
        dry_run=True,
        strategy="S14_WICK30_STRICT",
        cmp=15025.0,
        db_path=db,
    )


def test_json_safe_strips_nan() -> None:
    payload = json_safe({"x": float("nan"), "y": float("inf"), "z": 1.5})
    assert payload == {"x": None, "y": None, "z": 1.5}
    json.dumps(payload, allow_nan=False)


def test_resolve_desk_db_prefers_newer_nonempty() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        old = root / "old.db"
        new = root / "new.db"
        old.write_bytes(b"x" * 8000)
        new.write_bytes(b"y" * 8000)
        time.sleep(0.05)
        new.write_bytes(b"z" * 8000)
        picked = resolve_desk_db(candidates=[old, new])
        assert picked == new


def test_tape_payload_shows_ticks_without_trades() -> None:
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "ticks.db"
        init_db(db)
        _tick(db)
        tape = tape_payload(db_path=db)
        assert tape["error"] == ""
        assert tape["ticks"]
        assert tape["tick_count"] >= 1
        assert tape["ltp"] is not None
        assert str(db) in tape["db_path"]


def test_history_payload_has_closed_trade_and_ticks() -> None:
    reset_trade_cache()
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "ticks.db"
        init_db(db)
        _tick(db)
        _round_trip(db)
        hist = history_payload(db_path=db, limit=80)
        assert hist["ticks"]
        assert hist["total_closed"] >= 1
        assert hist["trades"]
        assert hist["trades"][0]["strategy"] == "S14_WICK30_STRICT"
        assert hist["scoreboard"]


def test_list_signals_limit_keeps_latest() -> None:
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "ticks.db"
        init_db(db)
        for i in range(5):
            save_signal(
                time_label=f"2026-08-17T10:0{i}:00+05:30",
                symbol="GOLDPETAL",
                action="HOLD",
                position_after="flat",
                reason=str(i),
                price_delta=0.0,
                net=0.0,
                net_delta=0.0,
                dry_run=True,
                strategy="S14_WICK30_STRICT",
                cmp=15000.0 + i,
                db_path=db,
            )
        rows = list_signals(strategy="S14_WICK30_STRICT", db_path=db, limit=2)
        assert len(rows) == 2
        assert rows[0]["reason"] == "3"
        assert rows[1]["reason"] == "4"


if __name__ == "__main__":
    test_json_safe_strips_nan()
    test_resolve_desk_db_prefers_newer_nonempty()
    test_tape_payload_shows_ticks_without_trades()
    test_history_payload_has_closed_trade_and_ticks()
    test_list_signals_limit_keeps_latest()
    print("ALL test_desk_data OK")
