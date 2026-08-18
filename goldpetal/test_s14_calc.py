"""S14 desk calculation: finished 30m candles pick open/close with the formula visible."""

from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from s14_calc import calc_payload, explain_bar, walk_candles
from storage import init_db, save_tick

IST = ZoneInfo("Asia/Kolkata")


def _tick(db: Path, when: str, ltp: float) -> None:
    save_tick(
        {
            "last_traded_price": int(round(ltp * 100)),
            "total_buy_quantity": 1,
            "total_sell_quantity": 1,
            "volume_trade_for_the_day": 1,
        },
        symbol="GOLDPETAL",
        token="1",
        received_at=when,
        db_path=db,
    )


def test_explain_open_high_shorts() -> None:
    row = explain_bar(time="t", o=100, h=100, l=90, c=95, pos="flat")
    assert row["open_eq_high"] is True
    assert row["open_eq_low"] is False
    assert row["side"] == "short"
    assert row["action"] == "enter"
    assert "open=high" in row["rule"]


def test_walk_flip_closes_then_opens() -> None:
    bars = [
        {"time": "10:00", "open": 100, "high": 100, "low": 90, "close": 95},
        {"time": "10:30", "open": 94, "high": 110, "low": 94, "close": 105},
    ]
    rows = walk_candles(bars)
    assert rows[0]["action"] == "enter" and rows[0]["side"] == "short"
    assert rows[1]["action"] == "FLIP" and rows[1]["side"] == "long"
    from s14_calc import book_from_walk

    book = book_from_walk(rows)
    assert book["open"]["side"] == "BUY"
    assert book["open"]["status"] == "OPEN"
    assert len(book["closed"]) == 1
    assert book["closed"][0]["side"] == "SHORT"
    assert book["closed"][0]["status"] == "CLOSED"


def test_calc_payload_from_ticks_shows_closed_and_open() -> None:
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "ticks.db"
        init_db(db)
        _tick(db, "2026-08-17T10:00:00+05:30", 100.0)
        _tick(db, "2026-08-17T10:10:00+05:30", 90.0)
        _tick(db, "2026-08-17T10:29:00+05:30", 95.0)
        _tick(db, "2026-08-17T10:30:00+05:30", 94.0)
        _tick(db, "2026-08-17T10:35:00+05:30", 110.0)
        _tick(db, "2026-08-17T10:50:00+05:30", 105.0)
        now = datetime.fromisoformat("2026-08-17T11:00:00+05:30").astimezone(IST)
        payload = calc_payload(db, minutes=30, max_bars=48, now=now)
        assert payload["error"] == ""
        assert payload["total_closed"] >= 1
        assert payload["total_open"] == 1
        assert payload["open"]["side"] == "BUY"
        assert any(r["action"] in {"enter", "FLIP"} for r in payload["bars"])
        assert "open=high" in payload["formula"] or "SHORT" in payload["formula"]


if __name__ == "__main__":
    test_explain_open_high_shorts()
    test_walk_flip_closes_then_opens()
    test_calc_payload_from_ticks_shows_closed_and_open()
    print("ALL test_s14_calc OK")
