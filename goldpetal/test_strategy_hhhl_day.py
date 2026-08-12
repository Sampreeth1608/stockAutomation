"""Tests for S13_HHHL_DAY daily HH/LL overnight strategy."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from strategy_hhhl_day import DayOhlc, HhhlDayConfig, HhhlDayOvernightStrategy

IST = ZoneInfo("Asia/Kolkata")


def _ts(day: str, hhmm: str) -> datetime:
    return datetime.fromisoformat(f"{day}T{hhmm}:00+05:30").astimezone(IST)


def test_long_near_close_and_exit_next_open(tmp_path: Path | None = None) -> None:
    state = Path("/tmp/s13_test_state.json")
    if state.exists():
        state.unlink()
    s = HhhlDayOvernightStrategy(
        HhhlDayConfig(min_range=5, entry_minutes_before_close=15, exit_minutes_after_open=5),
        state_path=state,
    )
    # Seed previous day manually
    s.prev_day = DayOhlc("2026-08-10", 15000, 15100, 14900, 15050)

    # Build today bar during session (HH + green)
    for t, px in [
        ("10:00", 15080),
        ("12:00", 15200),
        ("18:00", 15150),
        ("23:00", 15250),  # high/close green vs prevH 15100
    ]:
        r = s.on_tick(_ts("2026-08-11", t), px)
        assert r is None or r.action in {"BUY", "SHORT", "CLOSE"}

    # Entry window 23:15–23:30
    buy = s.on_tick(_ts("2026-08-11", "23:20"), 15260)
    assert buy is not None and buy.action == "BUY"
    assert s.position == "long"

    # Next day open exit window
    close = s.on_tick(_ts("2026-08-12", "09:02"), 15300)
    assert close is not None and close.action == "CLOSE"
    assert s.position == "flat"
    if state.exists():
        state.unlink()


def test_no_entry_outside_window() -> None:
    state = Path("/tmp/s13_test_state2.json")
    if state.exists():
        state.unlink()
    s = HhhlDayOvernightStrategy(HhhlDayConfig(min_range=5), state_path=state)
    s.prev_day = DayOhlc("2026-08-10", 15000, 15100, 14900, 15050)
    s.on_tick(_ts("2026-08-11", "10:00"), 15200)
    s.on_tick(_ts("2026-08-11", "12:00"), 15250)
    mid = s.on_tick(_ts("2026-08-11", "15:00"), 15280)
    assert mid is None
    assert s.position == "flat"
    if state.exists():
        state.unlink()


if __name__ == "__main__":
    test_long_near_close_and_exit_next_open()
    print("ok long_exit")
    test_no_entry_outside_window()
    print("ok outside_window")
    print("ALL test_strategy_hhhl_day OK")
