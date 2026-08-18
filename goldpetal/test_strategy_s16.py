"""Live S16 1h: wait for the hour to finish, then close-vs-prev FLIP."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from strategy_s16 import S16Config, S16HhhlWickStrategy
from strategy_wick import wick_record_actions

IST = ZoneInfo("Asia/Kolkata")


def _s16() -> S16HhhlWickStrategy:
    return S16HhhlWickStrategy(S16Config(bar_minutes=60, min_wick_gap=0.0), seed=False)


def _t(h: int, m: int, s: int = 0) -> datetime:
    return datetime(2026, 8, 17, h, m, s, tzinfo=IST)


def test_forming_hour_does_not_trade() -> None:
    s = _s16()
    assert s.on_tick(_t(10, 5), 100.0) is None
    assert s.on_tick(_t(10, 59), 110.0) is None
    assert s.position == "flat"
    assert s.last_skip == "waiting_1h_close"


def test_first_closed_hour_needs_prev() -> None:
    s = _s16()
    s.on_tick(_t(9, 0), 100.0)
    s.on_tick(_t(9, 10), 105.0)
    s.on_tick(_t(9, 20), 99.0)
    s.on_tick(_t(9, 59), 104.0)
    assert s.on_tick(_t(10, 0), 100.0) is None
    assert s.last_skip == "need_prev_1h"
    assert s._prev is not None
    assert s._prev.close == 104.0


def test_up_close_hh_green_enters_long_at_close() -> None:
    s = _s16()
    s.on_tick(_t(9, 0), 100.0)
    s.on_tick(_t(9, 10), 105.0)
    s.on_tick(_t(9, 20), 99.0)
    s.on_tick(_t(9, 59), 104.0)
    s.on_tick(_t(10, 0), 100.0)  # store 09:00 as prev
    s.on_tick(_t(10, 10), 120.0)
    s.on_tick(_t(10, 59), 110.0)
    result = s.on_tick(_t(11, 0), 110.0)
    assert result is not None
    assert result.action == "BUY"
    assert s.position == "long"
    assert s.entry_price == 110.0
    assert "C>prev → HH+green" in result.reason


def test_up_close_without_hhhl_skips_even_with_wick() -> None:
    s = _s16()
    s.on_tick(_t(9, 0), 100.0)
    s.on_tick(_t(9, 10), 120.0)
    s.on_tick(_t(9, 59), 104.0)
    s.on_tick(_t(10, 0), 104.0)
    s.on_tick(_t(10, 10), 104.5)  # no HH vs prevH=120
    s.on_tick(_t(10, 20), 90.0)  # long lower wick, ignored on up close
    s.on_tick(_t(10, 59), 104.2)
    result = s.on_tick(_t(11, 0), 104.2)
    assert result is None
    assert s.position == "flat"
    assert "no HH/LL" in str(s.last_skip)


def test_down_close_wick_flips() -> None:
    s = _s16()
    s.on_tick(_t(9, 0), 100.0)
    s.on_tick(_t(9, 10), 105.0)
    s.on_tick(_t(9, 59), 104.0)
    s.on_tick(_t(10, 0), 100.0)
    s.on_tick(_t(10, 10), 120.0)
    s.on_tick(_t(10, 59), 110.0)
    first = s.on_tick(_t(11, 0), 110.0)
    assert first is not None and first.action == "BUY"
    s.on_tick(_t(11, 10), 140.0)
    s.on_tick(_t(11, 30), 70.0)
    s.on_tick(_t(11, 59), 80.0)
    second = s.on_tick(_t(12, 0), 80.0)
    assert second is not None
    assert second.action == "SHORT"
    assert s.position == "short"
    assert s.entry_price == 80.0
    assert "FLIP" in second.reason
    assert wick_record_actions("long", second) == [
        ("CLOSE", "flat"),
        ("SHORT", "short"),
    ]


def test_equal_close_skips() -> None:
    s = _s16()
    s.on_tick(_t(9, 0), 100.0)
    s.on_tick(_t(9, 59), 104.0)
    s.on_tick(_t(10, 0), 104.0)
    s.on_tick(_t(10, 59), 104.0)
    result = s.on_tick(_t(11, 0), 104.0)
    assert result is None
    assert "C=prev" in str(s.last_skip)


def test_on_bar_row_uses_prev() -> None:
    s = _s16()
    assert (
        s.on_bar_row(
            {"time": "2026-08-17 09:00:00", "open": 100, "high": 105, "low": 99, "close": 104}
        )
        is None
    )
    result = s.on_bar_row(
        {"time": "2026-08-17 10:00:00", "open": 100, "high": 120, "low": 100, "close": 110}
    )
    assert result is not None
    assert result.action == "BUY"
    assert s.entry_price == 110.0


if __name__ == "__main__":
    test_forming_hour_does_not_trade()
    test_first_closed_hour_needs_prev()
    test_up_close_hh_green_enters_long_at_close()
    test_up_close_without_hhhl_skips_even_with_wick()
    test_down_close_wick_flips()
    test_equal_close_skips()
    test_on_bar_row_uses_prev()
    print("ALL test_strategy_s16 OK")
