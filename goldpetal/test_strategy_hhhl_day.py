"""Tests for S13_HHHL_DAY daily HH/LL same-candle strategy."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from strategy_hhhl_day import DayOhlc, HhhlDayConfig, HhhlDayOvernightStrategy

IST = ZoneInfo("Asia/Kolkata")


def _ts(day: str, hhmm: str) -> datetime:
    return datetime.fromisoformat(f"{day}T{hhmm}:00+05:30").astimezone(IST)


def _fresh(path: str) -> HhhlDayOvernightStrategy:
    state = Path(path)
    if state.exists():
        state.unlink()
    return HhhlDayOvernightStrategy(
        HhhlDayConfig(min_range=5, entry_minutes_before_close=15, no_flip=True),
        state_path=state,
    )


def test_long_near_close_holds_next_open_exits_same_candle() -> None:
    state = Path("/tmp/s13_test_state.json")
    s = _fresh(str(state))
    s.prev_day = DayOhlc("2026-08-10", 15000, 15100, 14900, 15050)

    for t, px in [
        ("10:00", 15080),
        ("12:00", 15200),
        ("18:00", 15150),
        ("23:00", 15250),
    ]:
        r = s.on_tick(_ts("2026-08-11", t), px)
        assert r is None or r.action in {"BUY", "SHORT", "CLOSE"}

    buy = s.on_tick(_ts("2026-08-11", "23:20"), 15260)
    assert buy is not None and buy.action == "BUY"
    assert s.position == "long"
    assert "same-day" in (buy.reason or "")

    # Next morning must NOT flatten — that was next-candle exit.
    morning = s.on_tick(_ts("2026-08-12", "09:02"), 15200)
    assert morning is None
    assert s.position == "long"

    # Later day: LH + red in last 15m → CLOSE on that day candle.
    s.on_tick(_ts("2026-08-12", "12:00"), 15180)
    close = s.on_tick(_ts("2026-08-12", "23:20"), 15120)
    assert close is not None and close.action == "CLOSE"
    assert s.position == "flat"
    assert "same-day" in (close.reason or "")
    if state.exists():
        state.unlink()


def test_long_exit_same_day_reenter_short() -> None:
    """Exit-long day that is also LL+red → close long and open short."""
    state = Path("/tmp/s13_test_reenter.json")
    s = _fresh(str(state))
    s.prev_day = DayOhlc("2026-08-10", 15000, 15100, 14900, 15050)
    s.on_tick(_ts("2026-08-11", "10:00"), 15080)
    s.on_tick(_ts("2026-08-11", "12:00"), 15200)
    buy = s.on_tick(_ts("2026-08-11", "23:20"), 15260)
    assert buy is not None and buy.action == "BUY"
    # Next day: LH vs 15260 AND LL vs 15080, red close
    s.on_tick(_ts("2026-08-12", "10:00"), 15200)
    s.on_tick(_ts("2026-08-12", "14:00"), 14850)
    short = s.on_tick(_ts("2026-08-12", "23:20"), 14880)
    assert short is not None and short.action == "SHORT"
    assert s.position == "short"
    assert "re-enter" in (short.reason or "")
    if state.exists():
        state.unlink()


def test_s13_close_then_later_tick_reenter() -> None:
    """CLOSE at 23:16 (LH+red); 23:22 breaks yesterday low → SHORT same day."""
    state = Path("/tmp/s13_test_later_tick.json")
    s = _fresh(str(state))
    s.prev_day = DayOhlc("2026-08-11", 15080, 15260, 15080, 15260)
    s.position = "long"
    s.entry_price = 15260.0
    s.entry_date = "2026-08-11"
    s.on_tick(_ts("2026-08-12", "10:00"), 15200)
    close = s.on_tick(_ts("2026-08-12", "23:16"), 15140)
    assert close is not None and close.action == "CLOSE"
    assert s.position == "flat"
    short = s.on_tick(_ts("2026-08-12", "23:22"), 15000)
    assert short is not None and short.action == "SHORT"
    assert s.position == "short"
    if state.exists():
        state.unlink()


def test_s13_fakeout_close_not_beyond() -> None:
    state = Path("/tmp/s13_test_fakeout.json")
    s = _fresh(str(state))
    s.cfg.min_close_beyond = 3.0
    s.prev_day = DayOhlc("2026-08-10", 15000, 15100, 14900, 15050)
    s.on_tick(_ts("2026-08-11", "10:00"), 15080)
    s.on_tick(_ts("2026-08-11", "12:00"), 15200)
    r = s.on_tick(_ts("2026-08-11", "23:20"), 15102)
    assert r is None
    assert s.position == "flat"
    assert "fakeout" in (s.last_skip or "")
    if state.exists():
        state.unlink()


def test_no_entry_outside_window() -> None:
    state = Path("/tmp/s13_test_state2.json")
    s = _fresh(str(state))
    s.prev_day = DayOhlc("2026-08-10", 15000, 15100, 14900, 15050)
    s.on_tick(_ts("2026-08-11", "10:00"), 15200)
    s.on_tick(_ts("2026-08-11", "12:00"), 15250)
    mid = s.on_tick(_ts("2026-08-11", "15:00"), 15280)
    assert mid is None
    assert s.position == "flat"
    assert s.last_skip in {
        "watching_hh",
        "outside_confirm_window",
        "outside_entry_window",
    }
    if state.exists():
        state.unlink()


def test_short_same_day_last_15m() -> None:
    state = Path("/tmp/s13_test_state3.json")
    s = _fresh(str(state))
    s.prev_day = DayOhlc("2026-08-10", 15000, 15100, 14900, 15050)
    s.on_tick(_ts("2026-08-11", "10:00"), 15040)
    s.on_tick(_ts("2026-08-11", "14:00"), 14880)
    short = s.on_tick(_ts("2026-08-11", "23:20"), 14890)
    assert short is not None and short.action == "SHORT"
    assert s.position == "short"
    if state.exists():
        state.unlink()


def test_merge_forming_day_keeps_open_widens_range() -> None:
    state = Path("/tmp/s13_test_merge.json")
    s = _fresh(str(state))
    s._day = DayOhlc("2026-08-17", 15424.0, 15424.0, 15424.0, 15424.0)
    s._merge_forming_day(DayOhlc("2026-08-17", 15430.0, 15480.0, 15410.0, 15456.0))
    assert s._day.open == 15424.0
    assert s._day.high == 15480.0
    assert s._day.low == 15410.0
    assert s._day.close == 15456.0
    if state.exists():
        state.unlink()


def test_hl_extend_persists_today_bar() -> None:
    import json

    state = Path("/tmp/s13_test_persist.json")
    s = _fresh(str(state))
    s.prev_day = DayOhlc("2026-08-14", 15269.0, 15424.0, 15157.0, 15395.0)
    s.on_tick(_ts("2026-08-17", "09:00"), 15424.0)
    s.on_tick(_ts("2026-08-17", "09:30"), 15480.0)
    s.on_tick(_ts("2026-08-17", "10:10"), 15410.0)
    raw = json.loads(state.read_text(encoding="utf-8"))
    today = raw["today"]
    assert today["open"] == 15424.0
    assert today["high"] == 15480.0
    assert today["low"] == 15410.0
    assert today["close"] == 15410.0
    if state.exists():
        state.unlink()


def test_sql_seed_hydrates_today_from_ticks_db() -> None:
    import sqlite3

    state = Path("/tmp/s13_test_sql_seed.json")
    db = Path("/tmp/s13_test_ticks.db")
    if db.exists():
        db.unlink()
    con = sqlite3.connect(str(db))
    con.execute(
        "CREATE TABLE ticks (id INTEGER PRIMARY KEY, received_at TEXT, ltp REAL)"
    )
    rows = [
        ("2026-08-14T09:00:00+05:30", 15269.0),
        ("2026-08-14T12:00:00+05:30", 15424.0),
        ("2026-08-14T15:00:00+05:30", 15157.0),
        ("2026-08-14T23:20:00+05:30", 15395.0),
        ("2026-08-17T09:00:00+05:30", 15424.0),
        ("2026-08-17T09:30:00+05:30", 15480.0),
        ("2026-08-17T10:10:00+05:30", 15410.0),
        ("2026-08-17T10:45:00+05:30", 15456.0),
    ]
    con.executemany("INSERT INTO ticks (received_at, ltp) VALUES (?, ?)", rows)
    con.commit()
    con.close()

    s = _fresh(str(state))
    s._day = DayOhlc("2026-08-17", 15424.0, 15424.0, 15424.0, 15424.0)
    s._seed_from_ticks(db, today="2026-08-17")
    assert s.prev_day is not None
    assert s.prev_day.date == "2026-08-14"
    assert s.prev_day.high == 15424.0
    assert s.prev_day.low == 15157.0
    assert s._day.open == 15424.0
    assert s._day.high == 15480.0
    assert s._day.low == 15410.0
    assert s._day.close == 15456.0
    if state.exists():
        state.unlink()
    if db.exists():
        db.unlink()


if __name__ == "__main__":
    test_long_near_close_holds_next_open_exits_same_candle()
    print("ok long_same_candle_exit")
    test_long_exit_same_day_reenter_short()
    print("ok reenter_short")
    test_s13_close_then_later_tick_reenter()
    print("ok later_tick_reenter")
    test_s13_fakeout_close_not_beyond()
    print("ok fakeout")
    test_no_entry_outside_window()
    print("ok outside_window")
    test_short_same_day_last_15m()
    print("ok short")
    test_merge_forming_day_keeps_open_widens_range()
    print("ok merge")
    test_hl_extend_persists_today_bar()
    print("ok persist")
    test_sql_seed_hydrates_today_from_ticks_db()
    print("ok sql seed")
    print("ALL test_strategy_hhhl_day OK")
