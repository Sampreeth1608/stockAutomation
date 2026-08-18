"""S4_OVERNIGHT is old S13 HH/LL daily swing — last 15m, hold until opposite."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from strategy_hhhl_day import (
    DayOhlc,
    HhhlDayConfig,
    HhhlDayOvernightStrategy,
    S4_NAME,
)
from strategy_wick import wick_record_actions

IST = ZoneInfo("Asia/Kolkata")


def _ts(day: str, hhmm: str) -> datetime:
    return datetime.fromisoformat(f"{day}T{hhmm}:00+05:30").astimezone(IST)


def _s4(path: str) -> HhhlDayOvernightStrategy:
    state = Path(path)
    if state.exists():
        state.unlink()
    return HhhlDayOvernightStrategy(
        HhhlDayConfig(formula="hhhl", entry_minutes_before_close=15),
        state_path=state,
        name=S4_NAME,
    )


def test_s4_name_and_status() -> None:
    s = _s4("/tmp/s4_test_name.json")
    assert s.name == "S4_OVERNIGHT"
    assert "HH/LL swing" in s.status_line
    assert "hold-until-opposite" in s.status_line
    Path("/tmp/s4_test_name.json").unlink(missing_ok=True)


def test_s4_hh_green_buys_holds_next_open() -> None:
    s = _s4("/tmp/s4_test_hold.json")
    s.prev_day = DayOhlc("2026-08-10", 15000, 15100, 14900, 15050)
    assert s.on_tick(_ts("2026-08-11", "10:00"), 15080) is None
    assert s.on_tick(_ts("2026-08-11", "12:00"), 15200) is None
    buy = s.on_tick(_ts("2026-08-11", "23:20"), 15260)
    assert buy is not None and buy.action == "BUY"
    assert s.position == "long"
    assert "HH+green" in (buy.reason or "")
    assert "hold-until-opposite" in (buy.reason or "")
    morning = s.on_tick(_ts("2026-08-12", "09:02"), 15200)
    assert morning is None
    assert s.position == "long"
    Path("/tmp/s4_test_hold.json").unlink(missing_ok=True)


def test_s4_inside_day_holds_the_trend() -> None:
    """No HH/LL on a later day → stay in. That is the multi-week ride."""
    s = _s4("/tmp/s4_test_inside.json")
    s.prev_day = DayOhlc("2026-08-10", 15000, 15100, 14900, 15050)
    s.on_tick(_ts("2026-08-11", "10:00"), 15080)
    s.on_tick(_ts("2026-08-11", "12:00"), 15200)
    buy = s.on_tick(_ts("2026-08-11", "23:20"), 15260)
    assert buy is not None and buy.action == "BUY"
    s.on_tick(_ts("2026-08-12", "10:00"), 15220)
    s.on_tick(_ts("2026-08-12", "14:00"), 15140)
    hold = s.on_tick(_ts("2026-08-12", "23:20"), 15180)
    assert hold is None
    assert s.position == "long"
    assert "no HH/LL" in (s.last_skip or "")
    Path("/tmp/s4_test_inside.json").unlink(missing_ok=True)


def test_s4_ll_red_flips_short() -> None:
    s = _s4("/tmp/s4_test_flip.json")
    s.prev_day = DayOhlc("2026-08-10", 15000, 15100, 14900, 15050)
    s.on_tick(_ts("2026-08-11", "10:00"), 15080)
    s.on_tick(_ts("2026-08-11", "12:00"), 15200)
    buy = s.on_tick(_ts("2026-08-11", "23:20"), 15260)
    assert buy is not None and buy.action == "BUY"
    s.on_tick(_ts("2026-08-12", "10:00"), 15200)
    s.on_tick(_ts("2026-08-12", "14:00"), 14850)
    short = s.on_tick(_ts("2026-08-12", "23:20"), 14880)
    assert short is not None and short.action == "SHORT"
    assert s.position == "short"
    assert "FLIP" in (short.reason or "")
    assert "LL+red" in (short.reason or "")
    assert wick_record_actions("long", short) == [
        ("CLOSE", "flat"),
        ("SHORT", "short"),
    ]
    Path("/tmp/s4_test_flip.json").unlink(missing_ok=True)


def test_s4_down_close_wick_without_ll_does_not_enter() -> None:
    """S13 S16 would BUY a lower wick on a down close; old HH/LL S4 waits for LL+red."""
    s = _s4("/tmp/s4_test_wick.json")
    s.prev_day = DayOhlc("2026-08-10", 15000, 15100, 14900, 15050)
    s.on_tick(_ts("2026-08-11", "10:00"), 15040)
    s.on_tick(_ts("2026-08-11", "14:00"), 14950)  # not below prev low 14900
    r = s.on_tick(_ts("2026-08-11", "23:20"), 14960)
    assert r is None
    assert s.position == "flat"
    assert "no HH/LL" in (s.last_skip or "")
    Path("/tmp/s4_test_wick.json").unlink(missing_ok=True)


if __name__ == "__main__":
    test_s4_name_and_status()
    print("ok name")
    test_s4_hh_green_buys_holds_next_open()
    print("ok hold next open")
    test_s4_inside_day_holds_the_trend()
    print("ok inside hold")
    test_s4_ll_red_flips_short()
    print("ok flip")
    test_s4_down_close_wick_without_ll_does_not_enter()
    print("ok no wick entry")
    print("ALL test_strategy_s4_swing OK")
