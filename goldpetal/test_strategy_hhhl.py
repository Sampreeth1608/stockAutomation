"""Tests for S12 HH/LL candle strategy."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from strategy_hhhl import HhhlCandleStrategy, HhhlConfig

IST = ZoneInfo("Asia/Kolkata")


def test_long_entry_exit_no_flip() -> None:
    s = HhhlCandleStrategy(HhhlConfig(min_range=5, no_flip=True))
    # seed prev
    assert s.on_bar_row({"open": 100, "high": 105, "low": 99, "close": 104}) is None
    # HH green → long
    r = s.on_bar_row({"open": 104, "high": 112, "low": 103, "close": 110})
    assert r is not None and r.action == "BUY"
    assert s.position == "long"
    # LH red → close, no flip even if LL red
    r2 = s.on_bar_row({"open": 110, "high": 109, "low": 95, "close": 96})
    assert r2 is not None and r2.action == "CLOSE"
    assert s.position == "flat"


def test_short_entry() -> None:
    s = HhhlCandleStrategy(HhhlConfig(min_range=5, no_flip=True))
    assert s.on_bar_row({"open": 110, "high": 111, "low": 105, "close": 106}) is None
    r = s.on_bar_row({"open": 106, "high": 107, "low": 98, "close": 99})
    assert r is not None and r.action == "SHORT"
    assert s.position == "short"


def test_min_range_blocks() -> None:
    s = HhhlCandleStrategy(HhhlConfig(min_range=20, no_flip=True))
    assert s.on_bar_row({"open": 100, "high": 105, "low": 99, "close": 104}) is None
    r = s.on_bar_row({"open": 104, "high": 110, "low": 103, "close": 109})  # range 7
    assert r is None
    assert s.position == "flat"


def test_same_candle_last_minute_long() -> None:
    """HH during 10:00 bar + last-minute green close → BUY inside that 30m (not +30m)."""
    s = HhhlCandleStrategy(HhhlConfig(min_range=5, no_flip=True, confirm_minutes=1))
    # Pretend previous 09:30 bar already known (seed)
    s.prev_o, s.prev_h, s.prev_l, s.prev_c = 100.0, 105.0, 99.0, 104.0

    def ts(hhmm: str) -> datetime:
        return datetime.fromisoformat(f"2026-08-12T{hhmm}:00+05:30").astimezone(IST)

    # 10:00 open
    assert s.on_tick(ts("10:00"), 104.0) is None
    # HH prints mid-bar
    assert s.on_tick(ts("10:10"), 112.0) is None
    assert s._watching in {"hh", "both"}
    # Still before last minute — no entry yet
    assert s.on_tick(ts("10:28"), 111.0) is None
    assert s.position == "flat"
    # Last minute of 10:00–10:30 candle, still green vs open 104
    buy = s.on_tick(ts("10:29"), 110.0)
    assert buy is not None and buy.action == "BUY"
    assert s.position == "long"
    assert "same-candle" in (buy.reason or "")


def test_same_candle_last_minute_short() -> None:
    s = HhhlCandleStrategy(HhhlConfig(min_range=5, no_flip=True, confirm_minutes=1))
    s.prev_o, s.prev_h, s.prev_l, s.prev_c = 110.0, 111.0, 105.0, 106.0

    def ts(hhmm: str) -> datetime:
        return datetime.fromisoformat(f"2026-08-12T{hhmm}:00+05:30").astimezone(IST)

    assert s.on_tick(ts("10:00"), 106.0) is None
    assert s.on_tick(ts("10:12"), 98.0) is None  # LL
    short = s.on_tick(ts("10:29"), 99.0)  # red close
    assert short is not None and short.action == "SHORT"
    assert s.position == "short"


if __name__ == "__main__":
    test_long_entry_exit_no_flip()
    test_short_entry()
    test_min_range_blocks()
    test_same_candle_last_minute_long()
    test_same_candle_last_minute_short()
    print("ALL test_strategy_hhhl OK")
