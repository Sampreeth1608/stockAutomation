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


if __name__ == "__main__":
    test_long_near_close_holds_next_open_exits_same_candle()
    print("ok long_same_candle_exit")
    test_no_entry_outside_window()
    print("ok outside_window")
    test_short_same_day_last_15m()
    print("ok short")
    print("ALL test_strategy_hhhl_day OK")
