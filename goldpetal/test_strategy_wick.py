"""Tests for S14 same-candle open=high/low + wick on close, and S15 nowick HOLD."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from strategy_wick import WickCandleStrategy, WickConfig, _open_hold_side

IST = ZoneInfo("Asia/Kolkata")


def _s14(seed: bool = False, *, open_hold_on_close: bool = True) -> WickCandleStrategy:
    return WickCandleStrategy(
        "S14_WICK30_STRICT",
        WickConfig(
            bar_minutes=30,
            confirm_minutes=1,
            nowick_body=False,
            nowick_only=False,
            entry_strict=False,
            exit_strict=False,
            reenter=True,
            wick_anytime=False,
            wick_on_close=True,
            open_hold_minutes=0.0,
            open_hold_on_close=open_hold_on_close,
        ),
        seed=seed,
    )


def _nowick(seed: bool = False) -> WickCandleStrategy:
    return WickCandleStrategy(
        "S15_WICK30_NOWICK",
        WickConfig(
            bar_minutes=30,
            confirm_minutes=1,
            nowick_eps=1.0,
            nowick_body=True,
            nowick_only=True,
            exit_strict=False,
            reenter=False,
            wick_anytime=False,
            wick_on_close=False,
            open_hold_minutes=0,
            open_hold_on_close=False,
        ),
        seed=seed,
    )


def ts(hhmm: str) -> datetime:
    return datetime.fromisoformat(f"2026-08-17T{hhmm}:00+05:30").astimezone(IST)


def test_open_high_on_same_closed_candle_shorts() -> None:
    """Finished candle: high never left open → SHORT (not the lower-wick LONG)."""
    s = _s14()
    assert s.on_tick(ts("10:00"), 100.0) is None
    assert s.on_tick(ts("10:10"), 90.0) is None
    assert s.on_tick(ts("10:11"), 95.0) is None
    assert s.position == "flat"
    sig = s.on_tick(ts("10:30"), 94.0)
    assert sig is not None and sig.action == "SHORT"
    assert s.position == "short"
    assert "open=high" in (sig.reason or "")


def test_open_low_on_same_closed_candle_longs() -> None:
    """Finished candle: low never left open → LONG."""
    s = _s14()
    s.on_tick(ts("10:00"), 100.0)
    s.on_tick(ts("10:10"), 110.0)
    s.on_tick(ts("10:29"), 105.0)
    assert s.position == "flat"
    sig = s.on_tick(ts("10:30"), 105.0)
    assert sig is not None and sig.action == "BUY"
    assert s.position == "long"
    assert "open=low" in (sig.reason or "")


def test_weak_wick_skipped_open_high_still_shorts() -> None:
    """Tiny wick gap is skipped; open=high still shorts (formula unchanged)."""
    s = _s14()
    s.on_tick(ts("10:00"), 100.0)
    s.on_tick(ts("10:05"), 99.0)
    s.on_tick(ts("10:10"), 103.0)
    s.on_tick(ts("10:29"), 101.0)
    none = s.on_tick(ts("10:30"), 101.0)
    assert none is None
    assert s.position == "flat"
    assert "weak_wick" in (s.last_skip or "")

    s2 = _s14()
    s2.on_tick(ts("10:00"), 100.0)
    s2.on_tick(ts("10:10"), 90.0)
    s2.on_tick(ts("10:11"), 95.0)
    sig = s2.on_tick(ts("10:30"), 94.0)
    assert sig is not None and sig.action == "SHORT"
    assert "open=high" in (sig.reason or "")


def test_open_high_and_low_flat_uses_wick() -> None:
    """O=H=L: skip open=high/low, equal wick → stay flat."""
    s = _s14()
    s.on_tick(ts("10:00"), 100.0)
    s.on_tick(ts("10:10"), 100.0)
    s.on_tick(ts("10:29"), 100.0)
    none = s.on_tick(ts("10:30"), 100.0)
    assert none is None
    assert s.position == "flat"
    assert s.last_skip == "equal_wick"


def test_neither_open_high_nor_low_uses_wick() -> None:
    """Price left open both ways: wick on the same closed candle."""
    s = _s14()
    s.on_tick(ts("10:00"), 100.0)
    s.on_tick(ts("10:05"), 90.0)
    s.on_tick(ts("10:10"), 105.0)
    s.on_tick(ts("10:29"), 102.0)
    buy = s.on_tick(ts("10:30"), 102.0)
    # O=100 H=105 L=90 C=102 → U=3 L=10 → LONG
    assert buy is not None and buy.action == "BUY"
    assert "bar closed" in (buy.reason or "")


def test_forming_bar_does_not_trade() -> None:
    s = _s14()
    s.on_tick(ts("10:00"), 100.0)
    s.on_tick(ts("10:10"), 90.0)
    s.on_tick(ts("10:11"), 95.0)
    assert s.on_tick(ts("10:20"), 120.0) is None
    assert s.on_tick(ts("10:21"), 100.0) is None
    assert s.position == "flat"
    # Closed: O=100 H=120 L=90 C=100 → neither OH/OL, U=20 L=10 → SHORT
    rev = s.on_tick(ts("10:30"), 100.0)
    assert rev is not None and rev.action == "SHORT"
    assert s.position == "short"


def test_next_closed_bar_flips() -> None:
    s = _s14()
    s.on_tick(ts("10:00"), 100.0)
    s.on_tick(ts("10:05"), 90.0)
    s.on_tick(ts("10:10"), 105.0)
    s.on_tick(ts("10:29"), 102.0)
    assert s.on_tick(ts("10:30"), 100.0).action == "BUY"
    s.on_tick(ts("10:35"), 90.0)
    s.on_tick(ts("10:40"), 120.0)
    s.on_tick(ts("10:50"), 100.0)
    rev = s.on_tick(ts("11:00"), 100.0)
    assert rev is not None and rev.action == "SHORT"
    assert s.position == "short"
    assert "FLIP" in (rev.reason or "")


def test_equal_wick_skips() -> None:
    s = _s14()
    s.on_tick(ts("10:00"), 100.0)
    s.on_tick(ts("10:05"), 90.0)
    s.on_tick(ts("10:10"), 105.0)
    s.on_tick(ts("10:29"), 102.0)
    assert s.on_tick(ts("10:30"), 100.0).action == "BUY"
    s.on_tick(ts("10:40"), 110.0)
    s.on_tick(ts("10:45"), 90.0)
    s.on_tick(ts("10:50"), 100.0)
    none = s.on_tick(ts("11:00"), 100.0)
    assert none is None
    assert s.position == "long"
    assert s.last_skip == "equal_wick"


def test_open_hold_side_math() -> None:
    assert _open_hold_side(100.0, 100.0, 90.0) == "short"
    assert _open_hold_side(100.0, 110.0, 100.0) == "long"
    assert _open_hold_side(100.0, 100.0, 100.0) is None
    assert _open_hold_side(100.0, 110.0, 90.0) is None


def test_two_minutes_does_not_trade() -> None:
    """open=high/low is the finished candle, not +2 minutes."""
    s = _s14()
    assert s.on_tick(ts("10:30"), 100.0) is None
    assert s.on_tick(ts("10:31"), 90.0) is None
    assert s.on_tick(ts("10:32"), 90.0) is None
    assert s.position == "flat"


def test_nowick_ignores_hammer() -> None:
    s = _nowick()
    s.on_tick(ts("10:00"), 100.0)
    s.on_tick(ts("10:10"), 90.0)
    none = s.on_tick(ts("10:29"), 101.0)
    assert none is None
    assert s.position == "flat"
    assert s.last_skip == "no_signal"


def test_nowick_bald_green_buys() -> None:
    s = _nowick()
    s.on_tick(ts("10:00"), 100.0)
    s.on_tick(ts("10:10"), 110.0)
    buy = s.on_tick(ts("10:29"), 110.0)
    assert buy is not None and buy.action == "BUY"
    assert s.position == "long"


def test_nowick_hammer_does_not_exit() -> None:
    s = _nowick()
    assert s.on_bar_row({"open": 100, "high": 110, "low": 100, "close": 110}).action == "BUY"
    hold = s.on_bar_row({"open": 110, "high": 130, "low": 109, "close": 111})
    assert hold is None
    assert s.position == "long"
    bald_red = s.on_bar_row({"open": 111, "high": 111, "low": 100, "close": 100})
    assert bald_red is not None and bald_red.action == "CLOSE"
    assert s.position == "flat"


def test_wick_record_actions_flip_emits_close_then_entry() -> None:
    from types import SimpleNamespace
    from strategy_wick import wick_record_actions

    buy = SimpleNamespace(action="BUY", position_after="long")
    short = SimpleNamespace(action="SHORT", position_after="short")
    assert wick_record_actions("flat", buy) == [("BUY", "long")]
    assert wick_record_actions("long", short) == [("CLOSE", "flat"), ("SHORT", "short")]
    assert wick_record_actions("short", buy) == [("CLOSE", "flat"), ("BUY", "long")]
    assert wick_record_actions("long", None) == []


def test_seed_current_bar_from_sql() -> None:
    import sqlite3
    from pathlib import Path

    db = Path("/tmp/test_s14_seed_ticks.db")
    if db.exists():
        db.unlink()
    con = sqlite3.connect(str(db))
    con.execute(
        "CREATE TABLE ticks (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "received_at TEXT NOT NULL, ltp REAL)"
    )
    rows = [
        ("2026-08-17T10:00:00+05:30", 100.0),
        ("2026-08-17T10:10:00+05:30", 90.0),
        ("2026-08-17T10:20:00+05:30", 102.0),
        ("2026-08-17T09:50:00+05:30", 88.0),
    ]
    con.executemany("INSERT INTO ticks (received_at, ltp) VALUES (?, ?)", rows)
    con.commit()
    con.close()
    s = _s14(seed=False)
    s.seed_from_ticks(db, now=ts("10:22"))
    assert s._bar_o == 100.0
    assert s._bar_h == 102.0
    assert s._bar_l == 90.0
    assert s._bar_c == 102.0
    db.unlink(missing_ok=True)


def test_seed_restores_open_s14_position() -> None:
    import tempfile
    from pathlib import Path

    from storage import init_db, save_signal

    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "ticks.db"
        init_db(db)
        save_signal(
            time_label="2026-08-17T10:30:00+05:30",
            symbol="GOLDPETAL",
            action="BUY",
            position_after="long",
            reason="enter",
            price_delta=None,
            net=0.0,
            net_delta=None,
            dry_run=True,
            strategy="S14_WICK30_STRICT",
            cmp=102.0,
            db_path=db,
        )
        s = _s14(seed=False)
        s.seed_from_ticks(db, now=ts("10:22"))
        assert s.position == "long"
        assert s.entry_price == 102.0


if __name__ == "__main__":
    test_open_high_on_same_closed_candle_shorts()
    test_open_low_on_same_closed_candle_longs()
    test_weak_wick_skipped_open_high_still_shorts()
    test_open_high_and_low_flat_uses_wick()
    test_neither_open_high_nor_low_uses_wick()
    test_forming_bar_does_not_trade()
    test_next_closed_bar_flips()
    test_equal_wick_skips()
    test_open_hold_side_math()
    test_two_minutes_does_not_trade()
    test_nowick_ignores_hammer()
    test_nowick_bald_green_buys()
    test_nowick_hammer_does_not_exit()
    test_seed_current_bar_from_sql()
    test_wick_record_actions_flip_emits_close_then_entry()
    test_seed_restores_open_s14_position()
    print("ALL test_strategy_wick OK")
