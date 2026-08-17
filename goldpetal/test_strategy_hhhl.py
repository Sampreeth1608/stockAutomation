"""Tests for S12 HH/LL candle strategy."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from strategy_hhhl import HhhlCandleStrategy, HhhlConfig

IST = ZoneInfo("Asia/Kolkata")


def test_long_entry_exit_no_reentry() -> None:
    """LH+red without LL → CLOSE only (stay flat)."""
    s = HhhlCandleStrategy(HhhlConfig(min_range=5, no_flip=True), seed=False)
    assert s.on_bar_row({"open": 100, "high": 105, "low": 90, "close": 104}) is None
    r = s.on_bar_row({"open": 104, "high": 112, "low": 91, "close": 110})
    assert r is not None and r.action == "BUY"
    assert s.position == "long"
    # H < prevH 112, L > prevL 91, red → exit long, no short re-entry
    r2 = s.on_bar_row({"open": 110, "high": 109, "low": 100, "close": 101})
    assert r2 is not None and r2.action == "CLOSE"
    assert s.position == "flat"


def test_long_exit_same_candle_reenter_short() -> None:
    """Exit-long candle that is also LL+red → close long and open short."""
    s = HhhlCandleStrategy(HhhlConfig(min_range=5, no_flip=True), seed=False)
    assert s.on_bar_row({"open": 100, "high": 105, "low": 99, "close": 104}) is None
    r = s.on_bar_row({"open": 104, "high": 112, "low": 103, "close": 110})
    assert r is not None and r.action == "BUY"
    r2 = s.on_bar_row({"open": 110, "high": 109, "low": 95, "close": 96})
    assert r2 is not None and r2.action == "SHORT"
    assert s.position == "short"
    assert "re-enter" in (r2.reason or "")


def test_short_entry() -> None:
    s = HhhlCandleStrategy(HhhlConfig(min_range=5, no_flip=True), seed=False)
    assert s.on_bar_row({"open": 110, "high": 111, "low": 105, "close": 106}) is None
    r = s.on_bar_row({"open": 106, "high": 107, "low": 98, "close": 99})
    assert r is not None and r.action == "SHORT"
    assert s.position == "short"


def test_min_range_blocks() -> None:
    s = HhhlCandleStrategy(HhhlConfig(min_range=20, no_flip=True), seed=False)
    assert s.on_bar_row({"open": 100, "high": 105, "low": 99, "close": 104}) is None
    r = s.on_bar_row({"open": 104, "high": 110, "low": 103, "close": 109})
    assert r is None
    assert s.position == "flat"


def test_same_candle_last_minute_long() -> None:
    """HH during 10:00 bar + last-minute green close → BUY inside that 30m."""
    s = HhhlCandleStrategy(
        HhhlConfig(min_range=5, no_flip=True, confirm_minutes=1), seed=False
    )
    s.prev_o, s.prev_h, s.prev_l, s.prev_c = 100.0, 105.0, 99.0, 104.0

    def ts(hhmm: str) -> datetime:
        return datetime.fromisoformat(f"2026-08-12T{hhmm}:00+05:30").astimezone(IST)

    assert s.on_tick(ts("10:00"), 104.0) is None
    assert s.on_tick(ts("10:10"), 112.0) is None
    assert s._watching in {"hh", "both"}
    assert s.on_tick(ts("10:28"), 111.0) is None
    assert s.position == "flat"
    # Briefly red in last minute — must NOT lock out
    assert s.on_tick(ts("10:29"), 103.0) is None
    assert s._decided_this_bar is False
    buy = s.on_tick(
        datetime.fromisoformat("2026-08-12T10:29:30+05:30").astimezone(IST), 110.0
    )
    assert buy is not None and buy.action == "BUY"
    assert s.position == "long"
    assert "same-candle" in (buy.reason or "")


def test_same_candle_last_minute_short() -> None:
    s = HhhlCandleStrategy(
        HhhlConfig(min_range=5, no_flip=True, confirm_minutes=1), seed=False
    )
    s.prev_o, s.prev_h, s.prev_l, s.prev_c = 110.0, 111.0, 105.0, 106.0

    def ts(hhmm: str) -> datetime:
        return datetime.fromisoformat(f"2026-08-12T{hhmm}:00+05:30").astimezone(IST)

    assert s.on_tick(ts("10:00"), 106.0) is None
    assert s.on_tick(ts("10:12"), 98.0) is None
    short = s.on_tick(ts("10:29"), 99.0)
    assert short is not None and short.action == "SHORT"
    assert s.position == "short"


def test_no_next_bar_fallback_entry() -> None:
    """HH+green on 10:00 bar must NOT buy on the first tick of 10:30."""
    s = HhhlCandleStrategy(
        HhhlConfig(min_range=5, no_flip=True, confirm_minutes=1), seed=False
    )
    s.prev_o, s.prev_h, s.prev_l, s.prev_c = 100.0, 105.0, 99.0, 104.0

    def ts(hhmm: str) -> datetime:
        return datetime.fromisoformat(f"2026-08-12T{hhmm}:00+05:30").astimezone(IST)

    assert s.on_tick(ts("10:00"), 104.0) is None
    assert s.on_tick(ts("10:10"), 112.0) is None
    rolled = s.on_tick(ts("10:30"), 110.0)
    assert rolled is None
    assert s.position == "flat"


def test_same_candle_last_minute_exit_after_entry() -> None:
    """Enter last minute of HH+green bar; exit last minute of later LH+red bar."""
    s = HhhlCandleStrategy(
        HhhlConfig(min_range=5, no_flip=True, confirm_minutes=1), seed=False
    )
    s.prev_o, s.prev_h, s.prev_l, s.prev_c = 100.0, 105.0, 99.0, 104.0

    def ts(hhmm: str) -> datetime:
        return datetime.fromisoformat(f"2026-08-12T{hhmm}:00+05:30").astimezone(IST)

    s.on_tick(ts("10:00"), 104.0)
    s.on_tick(ts("10:10"), 112.0)
    buy = s.on_tick(ts("10:29"), 110.0)
    assert buy is not None and buy.action == "BUY"
    # First tick of next candle must not exit/enter
    assert s.on_tick(ts("10:30"), 110.0) is None
    assert s.position == "long"
    s.on_tick(ts("10:40"), 96.0)
    short = s.on_tick(ts("10:59"), 96.0)
    assert short is not None and short.action == "SHORT"
    assert s.position == "short"
    assert "re-enter" in (short.reason or "")


def test_close_then_later_tick_reenter() -> None:
    """CLOSE on first last-minute tick; later tick on same candle prints LL → SHORT."""
    s = HhhlCandleStrategy(
        HhhlConfig(min_range=5, no_flip=True, confirm_minutes=1), seed=False
    )
    s.prev_o, s.prev_h, s.prev_l, s.prev_c = 100.0, 112.0, 104.0, 110.0
    s.position = "long"
    s.entry_price = 110.0

    def ts(hhmmss: str) -> datetime:
        return datetime.fromisoformat(f"2026-08-12T{hhmmss}+05:30").astimezone(IST)

    s.on_tick(ts("10:30:00"), 110.0)
    # LH+red but low still above prevL 104 → CLOSE only
    close = s.on_tick(ts("10:59:00"), 105.0)
    assert close is not None and close.action == "CLOSE"
    assert s.position == "flat"
    assert s._decided_this_bar is False
    # Same candle, later tick breaks prev low → re-enter short
    short = s.on_tick(ts("10:59:20"), 96.0)
    assert short is not None and short.action == "SHORT"
    assert s.position == "short"


def test_gate_reject_can_retry() -> None:
    s = HhhlCandleStrategy(
        HhhlConfig(min_range=5, no_flip=True, confirm_minutes=1), seed=False
    )
    s.prev_o, s.prev_h, s.prev_l, s.prev_c = 100.0, 105.0, 99.0, 104.0
    s.on_tick(datetime.fromisoformat("2026-08-12T10:00:00+05:30").astimezone(IST), 104.0)
    s.on_tick(datetime.fromisoformat("2026-08-12T10:10:00+05:30").astimezone(IST), 112.0)
    buy = s.on_tick(
        datetime.fromisoformat("2026-08-12T10:29:00+05:30").astimezone(IST), 110.0
    )
    assert buy is not None and buy.action == "BUY"
    s.position = "flat"
    s.entry_price = None
    s.release_decision_lock()
    buy2 = s.on_tick(
        datetime.fromisoformat("2026-08-12T10:29:20+05:30").astimezone(IST), 111.0
    )
    assert buy2 is not None and buy2.action == "BUY"


if __name__ == "__main__":
    test_long_entry_exit_no_reentry()
    test_long_exit_same_candle_reenter_short()
    test_short_entry()
    test_min_range_blocks()
    test_same_candle_last_minute_long()
    test_same_candle_last_minute_short()
    test_no_next_bar_fallback_entry()
    test_same_candle_last_minute_exit_after_entry()
    test_close_then_later_tick_reenter()
    test_gate_reject_can_retry()
    print("ALL test_strategy_hhhl OK")
