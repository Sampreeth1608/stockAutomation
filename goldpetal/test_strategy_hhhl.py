"""Tests for S12 HH/LL candle strategy."""

from __future__ import annotations

from strategy_hhhl import HhhlCandleStrategy, HhhlConfig


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


if __name__ == "__main__":
    test_long_entry_exit_no_flip()
    test_short_entry()
    test_min_range_blocks()
    print("ALL test_strategy_hhhl OK")
