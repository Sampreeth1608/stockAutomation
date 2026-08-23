"""Archived leftovers square at MARKET_OPEN; S16 stays the only live book."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from archive_open_flatten import (
    REASON,
    archived_leftover_names,
    in_session_after_open,
    is_archived_book,
    square_archived_leftovers_at_open,
)
from live_readiness import LIVE_ELIGIBLE_BOOKS

IST = ZoneInfo("Asia/Kolkata")


def test_s16_is_not_archived() -> None:
    assert "S16_HHHL_WICK_1H" in LIVE_ELIGIBLE_BOOKS
    assert is_archived_book("S16_HHHL_WICK_1H") is False
    assert is_archived_book("S5_MINEDGE") is True
    assert is_archived_book("S18_OHLC_VOL_HTF") is True
    assert is_archived_book("OVERNIGHT_GAP") is True
    assert is_archived_book("YOU_MANUAL") is False


def test_window_weekday_open_only() -> None:
    mon_open = datetime(2026, 8, 24, 9, 0, tzinfo=IST)
    mon_pre = datetime(2026, 8, 24, 8, 59, tzinfo=IST)
    mon_mid = datetime(2026, 8, 24, 14, 0, tzinfo=IST)
    mon_close = datetime(2026, 8, 24, 23, 30, tzinfo=IST)
    sat = datetime(2026, 8, 22, 10, 0, tzinfo=IST)
    assert in_session_after_open(mon_open) is True
    assert in_session_after_open(mon_pre) is False
    assert in_session_after_open(mon_mid) is True
    assert in_session_after_open(mon_close) is False
    assert in_session_after_open(sat) is False


def test_names_skip_s16() -> None:
    names = archived_leftover_names(
        {
            "S16_HHHL_WICK_1H": {"lots": 1},
            "S5_MINEDGE": {"lots": 3},
            "S19_BODY_CLOSE_1H": {"lots": 1},
        }
    )
    assert names == ["S5_MINEDGE", "S19_BODY_CLOSE_1H"]


def test_squares_archived_once_and_skips_s16(tmp_path: Path) -> None:
    path = tmp_path / "archive_open_once.json"
    now = datetime(2026, 8, 24, 9, 1, tzinfo=IST)
    called: list[str] = []

    def square(name: str, leftover: dict) -> dict:
        called.append(name)
        return {"ok": True, "skipped": False, "reason": "placed", "transaction": "BUY"}

    leftover = {
        "S16_HHHL_WICK_1H": {"lots": 1, "side": "BUY"},
        "S5_MINEDGE": {"lots": 3, "side": "SHORT"},
    }
    rows = square_archived_leftovers_at_open(
        now=now,
        leftover=leftover,
        square_leftover=square,
        path=path,
        clock=lambda: 1_000.0,
    )
    assert [r["strategy"] for r in rows] == ["S5_MINEDGE"]
    assert rows[0]["ok"] is True
    assert rows[0]["leftover_squared"] is True
    assert rows[0]["reason"] == REASON
    assert called == ["S5_MINEDGE"]

    again = square_archived_leftovers_at_open(
        now=now,
        leftover=leftover,
        square_leftover=square,
        path=path,
        clock=lambda: 1_040.0,
    )
    assert again == []
    assert called == ["S5_MINEDGE"]


def test_cooldown_then_retry_on_fail(tmp_path: Path) -> None:
    path = tmp_path / "archive_open_retry.json"
    now = datetime(2026, 8, 24, 10, 0, tzinfo=IST)
    n = {"i": 0}

    def square(name: str, leftover: dict) -> dict:
        n["i"] += 1
        if n["i"] == 1:
            return {"ok": False, "skipped": True, "reason": "emergency_off"}
        return {"ok": True, "skipped": False, "reason": "placed"}

    leftover = {"S8_NET_ZIGZAG": {"lots": 1, "side": "BUY"}}
    first = square_archived_leftovers_at_open(
        now=now,
        leftover=leftover,
        square_leftover=square,
        path=path,
        retry_sec=30,
        clock=lambda: 100.0,
    )
    assert first[0]["ok"] is False
    cooled = square_archived_leftovers_at_open(
        now=now,
        leftover=leftover,
        square_leftover=square,
        path=path,
        retry_sec=30,
        clock=lambda: 120.0,
    )
    assert cooled == []
    retry = square_archived_leftovers_at_open(
        now=now,
        leftover=leftover,
        square_leftover=square,
        path=path,
        retry_sec=30,
        clock=lambda: 140.0,
    )
    assert retry[0]["ok"] is True
    assert n["i"] == 2


def test_angel_already_flat_counts_done(tmp_path: Path) -> None:
    path = tmp_path / "archive_open_flat.json"
    now = datetime(2026, 8, 24, 9, 5, tzinfo=IST)

    def square(name: str, leftover: dict) -> dict:
        return {"ok": True, "skipped": False, "reason": "angel_already_flat"}

    rows = square_archived_leftovers_at_open(
        now=now,
        leftover={"S13_HHHL_DAY": {"lots": 2, "side": "BUY"}},
        square_leftover=square,
        path=path,
        clock=lambda: 50.0,
    )
    assert rows[0]["ok"] is True
    assert rows[0]["already_flat"] is True
    assert rows[0]["leftover_squared"] is False


def test_before_open_does_nothing(tmp_path: Path) -> None:
    called = []
    rows = square_archived_leftovers_at_open(
        now=datetime(2026, 8, 24, 8, 50, tzinfo=IST),
        leftover={"S5_MINEDGE": {"lots": 1, "side": "BUY"}},
        square_leftover=lambda n, r: called.append(n) or {"ok": True},
        path=tmp_path / "x.json",
    )
    assert rows == []
    assert called == []


if __name__ == "__main__":
    import tempfile

    test_s16_is_not_archived()
    test_window_weekday_open_only()
    test_names_skip_s16()
    td = Path(tempfile.mkdtemp())
    test_squares_archived_once_and_skips_s16(td)
    test_cooldown_then_retry_on_fail(td)
    test_angel_already_flat_counts_done(td)
    test_before_open_does_nothing(td)
    print("ALL test_archive_open_flatten OK")
