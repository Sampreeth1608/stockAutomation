"""Tests for wick-length long/short math and backtest fills."""

from __future__ import annotations

from backtest_hhhl_candles import Candle
from backtest_wick_candles import simulate_wick
from wick_candles import wick_measure, wick_side


def test_wick_measure_hammer() -> None:
    m = wick_measure(100.0, 102.0, 90.0, 101.0)
    assert m.lower == 10.0
    assert m.upper == 1.0
    assert m.body == 1.0
    assert m.dominant == "long"
    assert wick_side(100.0, 102.0, 90.0, 101.0) == "long"


def test_wick_measure_shooting_star() -> None:
    m = wick_measure(100.0, 120.0, 99.0, 101.0)
    assert m.upper == 19.0
    assert m.lower == 1.0
    assert m.dominant == "short"
    assert wick_side(100.0, 120.0, 99.0, 101.0) == "short"


def test_equal_wicks_no_signal() -> None:
    assert wick_side(100.0, 110.0, 90.0, 100.0) is None


def test_min_diff_and_frac_filters() -> None:
    # lower 6, upper 1 → raw long, blocked by diff10, allowed by frac50 on range 11
    assert wick_side(100.0, 105.0, 94.0, 104.0, min_diff=5) == "long"
    assert wick_side(100.0, 105.0, 94.0, 104.0, min_diff=10) is None
    assert wick_side(100.0, 105.0, 90.0, 104.0, min_frac=0.5) == "long"
    assert wick_side(100.0, 105.0, 98.0, 104.0, min_frac=0.5) is None


def test_pin_requires_wick_vs_body() -> None:
    # small lower wick vs large body → not a pin
    assert wick_side(100.0, 110.0, 98.0, 108.0, min_body_ratio=2.0) is None
    # long lower wick 2x body
    assert wick_side(100.0, 102.0, 90.0, 101.0, min_body_ratio=2.0) == "long"


def test_simulate_long_then_flip_short() -> None:
    candles = [
        Candle("2026-08-17 10:00:00", 100.0, 102.0, 90.0, 101.0),  # lower wick → long
        Candle("2026-08-17 10:30:00", 101.0, 103.0, 100.0, 102.0),  # tiny, hold
        Candle("2026-08-17 11:00:00", 102.0, 120.0, 101.0, 103.0),  # upper wick → short
    ]
    r = simulate_wick(candles, tf="30m:raw", min_range=5, no_flip=True)
    assert r.n_trades == 2
    assert r.trades[0].side == "LONG"
    assert r.trades[0].entry_px == 101.0
    assert r.trades[0].exit_px == 103.0
    assert r.trades[1].side == "SHORT"
    assert r.trades[1].entry_px == 103.0


def test_equal_wick_holds() -> None:
    candles = [
        Candle("2026-08-17 10:00:00", 100.0, 102.0, 90.0, 101.0),  # long
        Candle("2026-08-17 10:30:00", 101.0, 111.0, 91.0, 101.0),  # equal 10/10, hold
        Candle("2026-08-17 11:00:00", 101.0, 102.0, 90.0, 101.5),  # still lower wick
    ]
    r = simulate_wick(candles, tf="30m:raw", min_range=5, no_flip=True)
    assert r.n_trades == 1
    assert r.trades[0].side == "LONG"
    assert r.trades[0].exit_time == candles[-1].time


if __name__ == "__main__":
    test_wick_measure_hammer()
    print("ok hammer")
    test_wick_measure_shooting_star()
    print("ok star")
    test_equal_wicks_no_signal()
    print("ok equal")
    test_min_diff_and_frac_filters()
    print("ok filters")
    test_pin_requires_wick_vs_body()
    print("ok pin")
    test_simulate_long_then_flip_short()
    print("ok flip")
    test_equal_wick_holds()
    print("ok hold")
    print("ALL test_wick_candles OK")
