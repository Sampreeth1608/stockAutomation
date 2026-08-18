"""Tests for S13_HHHL_DAY — daily S16 close-vs-prev, last-15m fill."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from strategy_hhhl_day import DayOhlc, HhhlDayConfig, HhhlDayOvernightStrategy
from strategy_wick import wick_record_actions

IST = ZoneInfo("Asia/Kolkata")


def _ts(day: str, hhmm: str) -> datetime:
    return datetime.fromisoformat(f"{day}T{hhmm}:00+05:30").astimezone(IST)


def _fresh(path: str) -> HhhlDayOvernightStrategy:
    state = Path(path)
    if state.exists():
        state.unlink()
    return HhhlDayOvernightStrategy(
        HhhlDayConfig(entry_minutes_before_close=15, min_wick_gap=0.0),
        state_path=state,
    )


def test_up_close_hh_green_buys_last_15m_holds_next_open() -> None:
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
        assert r is None

    buy = s.on_tick(_ts("2026-08-11", "23:20"), 15260)
    assert buy is not None and buy.action == "BUY"
    assert s.position == "long"
    assert "C>prev → HH+green" in (buy.reason or "")
    assert "last-15m" in (buy.reason or "")

    # Next morning must NOT flatten — that was next-candle / next-open exit.
    morning = s.on_tick(_ts("2026-08-12", "09:02"), 15200)
    assert morning is None
    assert s.position == "long"
    if state.exists():
        state.unlink()


def test_down_close_equal_wick_holds_overnight() -> None:
    """Bald down day is C<prev with equal wick → skip, stay in the long."""
    state = Path("/tmp/s13_test_hold_equal_wick.json")
    s = _fresh(str(state))
    s.prev_day = DayOhlc("2026-08-10", 15000, 15100, 14900, 15050)
    s.on_tick(_ts("2026-08-11", "10:00"), 15080)
    s.on_tick(_ts("2026-08-11", "12:00"), 15200)
    buy = s.on_tick(_ts("2026-08-11", "23:20"), 15260)
    assert buy is not None and buy.action == "BUY"

    s.on_tick(_ts("2026-08-12", "09:02"), 15200)
    s.on_tick(_ts("2026-08-12", "12:00"), 15180)
    hold = s.on_tick(_ts("2026-08-12", "23:20"), 15120)
    assert hold is None
    assert s.position == "long"
    assert "equal wick" in (s.last_skip or "") or "C<prev" in (s.last_skip or "")
    if state.exists():
        state.unlink()


def test_down_close_upper_wick_flips_short() -> None:
    """C<prev + upper wick → SHORT; long overnight FLIPs (CLOSE+SHORT on tape)."""
    state = Path("/tmp/s13_test_reenter.json")
    s = _fresh(str(state))
    s.prev_day = DayOhlc("2026-08-10", 15000, 15100, 14900, 15050)
    s.on_tick(_ts("2026-08-11", "10:00"), 15080)
    s.on_tick(_ts("2026-08-11", "12:00"), 15200)
    buy = s.on_tick(_ts("2026-08-11", "23:20"), 15260)
    assert buy is not None and buy.action == "BUY"
    # Next day: spike above open then close down → upper wick.
    s.on_tick(_ts("2026-08-12", "10:00"), 15200)
    s.on_tick(_ts("2026-08-12", "12:00"), 15300)
    s.on_tick(_ts("2026-08-12", "14:00"), 14850)
    short = s.on_tick(_ts("2026-08-12", "23:20"), 14880)
    assert short is not None and short.action == "SHORT"
    assert s.position == "short"
    assert "FLIP" in (short.reason or "")
    assert "C<prev → upper wick" in (short.reason or "")
    assert wick_record_actions("long", short) == [
        ("CLOSE", "flat"),
        ("SHORT", "short"),
    ]
    if state.exists():
        state.unlink()


def test_skip_then_later_tick_flips() -> None:
    """Equal close at 23:16 skips; 23:22 down-close upper wick FLIPs."""
    state = Path("/tmp/s13_test_later_tick.json")
    s = _fresh(str(state))
    s.prev_day = DayOhlc("2026-08-11", 15080, 15260, 15080, 15260)
    s.position = "long"
    s.entry_price = 15260.0
    s.entry_date = "2026-08-11"
    s.on_tick(_ts("2026-08-12", "10:00"), 15200)
    skip = s.on_tick(_ts("2026-08-12", "23:16"), 15260)
    assert skip is None
    assert s.position == "long"
    assert "C=prev" in (s.last_skip or "")
    short = s.on_tick(_ts("2026-08-12", "23:22"), 15000)
    assert short is not None and short.action == "SHORT"
    assert s.position == "short"
    assert "FLIP" in (short.reason or "")
    if state.exists():
        state.unlink()


def test_up_close_hh_green_ignores_old_fakeout_filter() -> None:
    """Close barely beyond prev high used to be a fakeout skip; S16 still BUYs."""
    state = Path("/tmp/s13_test_fakeout.json")
    s = _fresh(str(state))
    s.prev_day = DayOhlc("2026-08-10", 15000, 15100, 14900, 15050)
    s.on_tick(_ts("2026-08-11", "10:00"), 15080)
    s.on_tick(_ts("2026-08-11", "12:00"), 15200)
    r = s.on_tick(_ts("2026-08-11", "23:20"), 15102)
    assert r is not None and r.action == "BUY"
    assert s.position == "long"
    if state.exists():
        state.unlink()


def test_up_close_no_hhhl_skips() -> None:
    state = Path("/tmp/s13_test_no_hhhl.json")
    s = _fresh(str(state))
    s.prev_day = DayOhlc("2026-08-10", 15000, 15100, 14900, 15050)
    s.on_tick(_ts("2026-08-11", "10:00"), 15080)
    s.on_tick(_ts("2026-08-11", "12:00"), 15090)
    r = s.on_tick(_ts("2026-08-11", "23:20"), 15080)
    assert r is None
    assert s.position == "flat"
    assert "no HH/LL" in (s.last_skip or "")
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
        "watching_up_close_hhll",
        "watching_down_close_wick",
        "watching_equal_close",
        "outside_confirm_window",
        "outside_entry_window",
    }
    if state.exists():
        state.unlink()


def test_down_close_lower_wick_long() -> None:
    """Old HH/LL-only short (LL+red) is S16 lower-wick LONG on a down close."""
    state = Path("/tmp/s13_test_state3.json")
    s = _fresh(str(state))
    s.prev_day = DayOhlc("2026-08-10", 15000, 15100, 14900, 15050)
    s.on_tick(_ts("2026-08-11", "10:00"), 15040)
    s.on_tick(_ts("2026-08-11", "14:00"), 14880)
    long = s.on_tick(_ts("2026-08-11", "23:20"), 14890)
    assert long is not None and long.action == "BUY"
    assert s.position == "long"
    assert "C<prev → lower wick" in (long.reason or "")
    if state.exists():
        state.unlink()


def test_down_close_upper_wick_short_from_flat() -> None:
    state = Path("/tmp/s13_test_short_wick.json")
    s = _fresh(str(state))
    s.prev_day = DayOhlc("2026-08-10", 15000, 15100, 14900, 15050)
    s.on_tick(_ts("2026-08-11", "10:00"), 15100)
    s.on_tick(_ts("2026-08-11", "12:00"), 15200)
    s.on_tick(_ts("2026-08-11", "14:00"), 14990)
    short = s.on_tick(_ts("2026-08-11", "23:20"), 15000)
    assert short is not None and short.action == "SHORT"
    assert s.position == "short"
    assert "C<prev → upper wick" in (short.reason or "")
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


def _front_contract(*, days_left: int, rolled: bool = False, symbol: str = "GOLDPETAL31AUG26FUT") -> dict:
    return {
        "symbol": symbol,
        "token": "1",
        "rolled": rolled,
        "rollover_days": 5,
        "days_to_front_expiry": days_left,
        "front_month_expiry": "31AUG2026",
        "next_month_expiry": "30SEP2026",
    }


def test_last_front_session_closes_and_blocks() -> None:
    s = _fresh("/tmp/s13_roll_last_front.json")
    s.prev_day = DayOhlc("2026-08-24", 15000, 15100, 14900, 15050)
    s._day = DayOhlc("2026-08-25", 15080, 15200, 15040, 15180)
    s.position = "long"
    s.entry_price = 15180.0
    s.entry_date = "2026-08-11"
    s.contract_symbol = "GOLDPETAL31AUG26FUT"
    s.set_contract(_front_contract(days_left=6, rolled=False))
    close = s.on_tick(_ts("2026-08-25", "10:00"), 15190)
    assert close is not None and close.action == "CLOSE"
    assert s.position == "flat"
    assert "last front-month" in (close.reason or "")
    blocked = s.on_tick(_ts("2026-08-25", "23:20"), 15200)
    assert blocked is None
    assert s.last_skip == "avoid_front_roll"
    Path("/tmp/s13_roll_last_front.json").unlink(missing_ok=True)


def test_contract_switch_closes_and_resets_book() -> None:
    s = _fresh("/tmp/s13_roll_switch.json")
    s.prev_day = DayOhlc("2026-08-25", 15000, 15100, 14900, 15050)
    s._day = DayOhlc("2026-08-26", 15080, 15200, 15040, 15180)
    s.position = "long"
    s.entry_price = 15180.0
    s.contract_symbol = "GOLDPETAL31AUG26FUT"
    s.set_contract(
        _front_contract(
            days_left=5,
            rolled=True,
            symbol="GOLDPETAL30SEP26FUT",
        )
    )
    close = s.on_tick(_ts("2026-08-26", "09:05"), 16010)
    assert close is not None and close.action == "CLOSE"
    assert s.position == "flat"
    assert s.prev_day is None
    assert s.contract_symbol == "GOLDPETAL30SEP26FUT"
    Path("/tmp/s13_roll_switch.json").unlink(missing_ok=True)


if __name__ == "__main__":
    test_up_close_hh_green_buys_last_15m_holds_next_open()
    print("ok long_last_15m_hold_open")
    test_down_close_equal_wick_holds_overnight()
    print("ok equal_wick_hold")
    test_down_close_upper_wick_flips_short()
    print("ok flip_short")
    test_skip_then_later_tick_flips()
    print("ok later_tick_flip")
    test_up_close_hh_green_ignores_old_fakeout_filter()
    print("ok no_fakeout")
    test_up_close_no_hhhl_skips()
    print("ok no_hhhl")
    test_no_entry_outside_window()
    print("ok outside_window")
    test_down_close_lower_wick_long()
    print("ok lower_wick_long")
    test_down_close_upper_wick_short_from_flat()
    print("ok upper_wick_short")
    test_merge_forming_day_keeps_open_widens_range()
    print("ok merge")
    test_hl_extend_persists_today_bar()
    print("ok persist")
    test_sql_seed_hydrates_today_from_ticks_db()
    print("ok sql seed")
    test_last_front_session_closes_and_blocks()
    print("ok last front flatten")
    test_contract_switch_closes_and_resets_book()
    print("ok contract switch")
    print("ALL test_strategy_hhhl_day OK")
