"""Tests for S14 30m:strict and S15 30m:nowick live strategies."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from strategy_wick import WickCandleStrategy, WickConfig

IST = ZoneInfo("Asia/Kolkata")


def _strict(seed: bool = False) -> WickCandleStrategy:
    return WickCandleStrategy(
        "S14_WICK30_STRICT",
        WickConfig(
            bar_minutes=30,
            min_range=0,
            confirm_minutes=1,
            nowick_eps=1.0,
            nowick_body=True,
            nowick_only=False,
            entry_strict=True,
            exit_strict=True,
        ),
        seed=seed,
    )


def _nowick(seed: bool = False) -> WickCandleStrategy:
    return WickCandleStrategy(
        "S15_WICK30_NOWICK",
        WickConfig(
            bar_minutes=30,
            min_range=0,
            confirm_minutes=1,
            nowick_eps=1.0,
            nowick_body=True,
            nowick_only=True,
            exit_strict=False,
        ),
        seed=seed,
    )


def _flip() -> WickCandleStrategy:
    return WickCandleStrategy(
        "S16_WICK30_STRICT_FLIP",
        WickConfig(
            bar_minutes=30,
            min_range=0,
            confirm_minutes=1,
            nowick_eps=1.0,
            nowick_body=True,
            nowick_only=False,
            entry_strict=True,
            exit_strict=True,
            reenter=True,
        ),
        seed=False,
    )


def ts(hhmm: str) -> datetime:
    return datetime.fromisoformat(f"2026-08-17T{hhmm}:00+05:30").astimezone(IST)


def ts_ss(hhmmss: str) -> datetime:
    return datetime.fromisoformat(f"2026-08-17T{hhmmss}+05:30").astimezone(IST)


def test_last_minute_long_hammer() -> None:
    """Lower wick hammer in last minute of 10:00 bar → BUY."""
    s = _strict()
    assert s.on_tick(ts("10:00"), 100.0) is None
    assert s.on_tick(ts("10:10"), 90.0) is None  # print the low
    assert s.on_tick(ts("10:20"), 102.0) is None
    assert s.position == "flat"
    assert s.on_tick(ts("10:28"), 101.0) is None
    buy = s.on_tick(ts("10:29"), 101.0)
    assert buy is not None and buy.action == "BUY"
    assert s.position == "long"
    assert "last-minute" in (buy.reason or "")


def test_no_next_bar_fallback_entry() -> None:
    """Hammer on 10:00 bar must NOT buy on the first tick of 10:30."""
    s = _strict()
    s.on_tick(ts("10:00"), 100.0)
    s.on_tick(ts("10:10"), 90.0)
    s.on_tick(ts("10:20"), 102.0)
    rolled = s.on_tick(ts("10:30"), 101.0)
    assert rolled is None
    assert s.position == "flat"


def test_hold_exit_no_reverse_same_bar() -> None:
    """Opposite last-minute → CLOSE; later tick on same candle stays flat."""
    s = _strict()
    s.on_tick(ts("10:00"), 100.0)
    s.on_tick(ts("10:10"), 90.0)
    buy = s.on_tick(ts("10:29"), 101.0)
    assert buy is not None and buy.action == "BUY"
    # First tick of next candle must not exit
    assert s.on_tick(ts("10:30"), 101.0) is None
    assert s.position == "long"
    # 10:30 bar: shooting star (decisive upper wick)
    s.on_tick(ts("10:40"), 120.0)
    close = s.on_tick(ts("10:59"), 103.0)
    assert close is not None and close.action == "CLOSE"
    assert s.position == "flat"
    assert s._decided_this_bar is True
    # Same last minute, still a short candle — HOLD must not reverse
    later = s.on_tick(ts_ss("10:59:20"), 102.0)
    assert later is None
    assert s.position == "flat"


def test_flip_reverses_same_last_minute() -> None:
    """S16: decisive opposite in last minute → SHORT, not CLOSE-to-flat."""
    s = _flip()
    s.on_tick(ts("10:00"), 100.0)
    s.on_tick(ts("10:10"), 90.0)
    buy = s.on_tick(ts("10:29"), 101.0)
    assert buy is not None and buy.action == "BUY"
    assert s.on_tick(ts("10:30"), 101.0) is None
    s.on_tick(ts("10:40"), 120.0)
    rev = s.on_tick(ts("10:59"), 103.0)
    assert rev is not None and rev.action == "SHORT"
    assert s.position == "short"
    assert "FLIP" in (rev.reason or "")
    later = s.on_tick(ts_ss("10:59:20"), 102.0)
    assert later is None
    assert s.position == "short"


def test_later_bar_can_enter_after_flat() -> None:
    s = _strict()
    s.on_tick(ts("10:00"), 100.0)
    s.on_tick(ts("10:10"), 90.0)
    assert s.on_tick(ts("10:29"), 101.0).action == "BUY"
    s.on_tick(ts("10:30"), 101.0)
    s.on_tick(ts("10:40"), 120.0)
    assert s.on_tick(ts("10:59"), 103.0).action == "CLOSE"
    # Next bar still upper-wick short → SHORT in its last minute
    s.on_tick(ts("11:00"), 103.0)
    s.on_tick(ts("11:10"), 121.0)
    short = s.on_tick(ts("11:29"), 104.0)
    assert short is not None and short.action == "SHORT"
    assert s.position == "short"


def test_strict_ignores_weak_opposite() -> None:
    """Weak opposite (not bald/frac50/pin2) — stay long."""
    s = _strict()
    # Enter on hammer
    assert s.on_bar_row({"open": 100, "high": 102, "low": 90, "close": 101}).action == "BUY"
    # Weak short: U=3 L=1 body=4 range=8 → frac 0.375, not 2x body
    hold = s.on_bar_row({"open": 101, "high": 108, "low": 100, "close": 105})
    assert hold is None
    assert s.position == "long"
    assert s.last_skip == "hold_long"


def test_strict_exits_on_decisive_opposite() -> None:
    s = _strict()
    assert s.on_bar_row({"open": 100, "high": 102, "low": 90, "close": 101}).action == "BUY"
    # Upper wick 19, range 21 → frac > 0.5
    close = s.on_bar_row({"open": 102, "high": 120, "low": 101, "close": 103})
    assert close is not None and close.action == "CLOSE"
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
    s.on_tick(ts("10:10"), 110.0)  # high, no lower wick if close stays up
    buy = s.on_tick(ts("10:29"), 110.0)
    assert buy is not None and buy.action == "BUY"
    assert s.position == "long"


def test_nowick_hammer_does_not_exit() -> None:
    s = _nowick()
    assert s.on_bar_row({"open": 100, "high": 110, "low": 100, "close": 110}).action == "BUY"
    # Shooting star would be a raw short; nowick ignores it
    hold = s.on_bar_row({"open": 110, "high": 130, "low": 109, "close": 111})
    assert hold is None
    assert s.position == "long"
    bald_red = s.on_bar_row({"open": 111, "high": 111, "low": 100, "close": 100})
    assert bald_red is not None and bald_red.action == "CLOSE"
    assert s.position == "flat"


def test_min_range_is_ignored() -> None:
    """No high−low skip: a 12-pt bar still trades even if min_range is set."""
    s = WickCandleStrategy(
        "S14_WICK30_STRICT",
        WickConfig(min_range=20, entry_strict=True, exit_strict=True),
        seed=False,
    )
    sig = s.on_bar_row({"open": 100, "high": 102, "low": 90, "close": 101})
    assert sig is not None and sig.action == "BUY"
    assert s.position == "long"


def test_small_range_candle_is_taken() -> None:
    """Range 3, upper 2.5 ≥ 0.5×range → SHORT. No H−L skip."""
    s = _strict()
    sig = s.on_bar_row({"open": 100, "high": 103, "low": 100, "close": 100.5})
    assert sig is not None and sig.action == "SHORT"
    assert s.position == "short"


def test_weak_wick_does_not_enter() -> None:
    """upper>lower but not bald/frac50/pin2 → no trade."""
    s = _strict()
    # U=2 L=1 range=6 body=3 → frac 0.33, pin 2<6
    sig = s.on_bar_row({"open": 100, "high": 105, "low": 99, "close": 103})
    assert sig is None
    assert s.position == "flat"
    assert s.last_skip == "no_signal"


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
        ("2026-08-17T09:50:00+05:30", 88.0),  # previous bar, ignored by >= cur_key
    ]
    con.executemany("INSERT INTO ticks (received_at, ltp) VALUES (?, ?)", rows)
    con.commit()
    con.close()
    s = _strict(seed=False)
    s.seed_from_ticks(db, now=ts("10:22"))
    assert s._bar_o == 100.0
    assert s._bar_h == 102.0
    assert s._bar_l == 90.0
    assert s._bar_c == 102.0
    db.unlink(missing_ok=True)


def test_gate_reject_can_retry() -> None:
    s = _strict()
    s.on_tick(ts("10:00"), 100.0)
    s.on_tick(ts("10:10"), 90.0)
    buy = s.on_tick(ts("10:29"), 101.0)
    assert buy is not None and buy.action == "BUY"
    s.position = "flat"
    s.entry_price = None
    s.release_decision_lock()
    buy2 = s.on_tick(ts_ss("10:29:20"), 101.0)
    assert buy2 is not None and buy2.action == "BUY"


if __name__ == "__main__":
    test_last_minute_long_hammer()
    test_no_next_bar_fallback_entry()
    test_hold_exit_no_reverse_same_bar()
    test_flip_reverses_same_last_minute()
    test_later_bar_can_enter_after_flat()
    test_strict_ignores_weak_opposite()
    test_strict_exits_on_decisive_opposite()
    test_nowick_ignores_hammer()
    test_nowick_bald_green_buys()
    test_nowick_hammer_does_not_exit()
    test_min_range_is_ignored()
    test_small_range_candle_is_taken()
    test_weak_wick_does_not_enter()
    test_seed_current_bar_from_sql()
    test_gate_reject_can_retry()
    print("ALL test_strategy_wick OK")
