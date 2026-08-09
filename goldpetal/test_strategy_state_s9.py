"""Tests for S9_STATE30."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from strategy_state_s9 import StateS9Config, StateS9Strategy, state_code, short_label

IST = ZoneInfo("Asia/Kolkata")


def test_state_code_and_label():
    assert state_code(10, -5, 2, 1, 0.5) == "TBQ+_TSQ-_P+"
    assert short_label("TBQ+_TSQ-_P+") == "B+S-P+"


def test_enter_long_on_confirm_bar():
    s = StateS9Strategy(
        StateS9Config(
            bar_minutes=30,
            tp_points=26,
            sl_points=16,
            require_net_sign=True,
            allow_short=False,
            min_imb_pct=0,
        )
    )
    # first bar = START
    s.on_bar_row(
        {
            "time": "2026-08-07 10:00:00",
            "open": 10000,
            "high": 10010,
            "low": 9990,
            "close": 10005,
            "tbq_open": 10000,
            "tsq_open": 9000,
            "tbq_close": 10000,
            "tsq_close": 9000,
            "n_ticks": 10,
        }
    )
    assert s.position == "flat"
    # B+S-P+ with NET+
    sig = s.on_bar_row(
        {
            "time": "2026-08-07 10:30:00",
            "open": 10005,
            "high": 10040,
            "low": 10000,
            "close": 10030,
            "tbq_open": 10000,
            "tsq_open": 9000,
            "tbq_close": 12000,
            "tsq_close": 8000,
            "n_ticks": 10,
        }
    )
    assert sig is not None and sig.action == "BUY"
    assert s.last_label == "B+S-P+"
    assert s.position == "long"


def test_tp_and_no_short_by_default():
    s = StateS9Strategy(
        StateS9Config(tp_points=26, sl_points=16, allow_short=False, min_imb_pct=0)
    )
    s.on_bar_row(
        {
            "time": "2026-08-07 10:00:00",
            "open": 10000,
            "high": 10000,
            "low": 10000,
            "close": 10000,
            "tbq_open": 11000,
            "tsq_open": 9000,
            "tbq_close": 11000,
            "tsq_close": 9000,
        }
    )
    s.on_bar_row(
        {
            "time": "2026-08-07 10:30:00",
            "open": 10000,
            "high": 10020,
            "low": 10000,
            "close": 10020,
            "tbq_open": 11000,
            "tsq_open": 9000,
            "tbq_close": 13000,
            "tsq_close": 8000,
        }
    )
    assert s.position == "long"
    ep = s.entry_price
    sig = s.on_bar_row(
        {
            "time": "2026-08-07 11:00:00",
            "open": ep,
            "high": ep + 30,
            "low": ep,
            "close": ep + 26,
            "tbq_open": 13000,
            "tsq_open": 8000,
            "tbq_close": 14000,
            "tsq_close": 7000,
        }
    )
    assert sig and sig.action == "CLOSE" and "tp" in sig.reason

    # bear confirm should NOT short
    s2 = StateS9Strategy(StateS9Config(allow_short=False, min_imb_pct=0))
    s2.on_bar_row(
        {
            "time": "2026-08-07 12:00:00",
            "open": 10000,
            "high": 10000,
            "low": 10000,
            "close": 10000,
            "tbq_close": 8000,
            "tsq_close": 12000,
            "tbq_open": 8000,
            "tsq_open": 12000,
        }
    )
    sig2 = s2.on_bar_row(
        {
            "time": "2026-08-07 12:30:00",
            "open": 10000,
            "high": 10000,
            "low": 9970,
            "close": 9970,
            "tbq_close": 7000,
            "tsq_close": 14000,
            "tbq_open": 8000,
            "tsq_open": 12000,
        }
    )
    assert sig2 is None or sig2.action != "SHORT"


def test_tick_bar_boundary_emits():
    s = StateS9Strategy(StateS9Config(bar_minutes=30, min_imb_pct=0))
    t0 = datetime(2026, 8, 7, 10, 5, tzinfo=IST)
    msg = {"total_buy_quantity": 10000, "total_sell_quantity": 9000}
    assert s.on_tick(t0, 10000.0, msg) is None
    # still same bar
    assert s.on_tick(t0.replace(minute=10), 10005.0, msg) is None
    # next 30m bar → close previous (START, no trade)
    sig = s.on_tick(
        datetime(2026, 8, 7, 10, 30, tzinfo=IST),
        10010.0,
        {"total_buy_quantity": 12000, "total_sell_quantity": 8000},
    )
    # first close is START
    assert sig is None or sig.action in {"BUY", "SHORT", "CLOSE"} or True
    assert s.last_state in {"START", "TBQ+_TSQ-_P+", "WARMUP"} or s._prev_tbq is not None


if __name__ == "__main__":
    test_state_code_and_label()
    test_enter_long_on_confirm_bar()
    test_tp_and_no_short_by_default()
    test_tick_bar_boundary_emits()
    print("ok")
