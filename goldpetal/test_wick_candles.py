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


def test_simulate_long_exits_flat_no_reverse() -> None:
    candles = [
        Candle("2026-08-17 10:00:00", 100.0, 102.0, 90.0, 101.0),  # lower wick → long
        Candle("2026-08-17 10:30:00", 101.0, 103.0, 100.0, 102.0),  # tiny, hold
        Candle("2026-08-17 11:00:00", 102.0, 120.0, 101.0, 103.0),  # upper wick → EXIT flat
    ]
    r = simulate_wick(candles, tf="30m:raw", min_range=5, reenter=False)
    assert r.n_trades == 1
    assert r.trades[0].side == "LONG"
    assert r.trades[0].entry_px == 101.0
    assert r.trades[0].exit_px == 103.0


def test_flip_reverses_on_exit_candle() -> None:
    candles = [
        Candle("2026-08-17 10:00:00", 100.0, 102.0, 90.0, 101.0),  # lower wick → long
        Candle("2026-08-17 11:00:00", 102.0, 120.0, 101.0, 103.0),  # upper wick → reverse short
    ]
    hold = simulate_wick(candles, tf="30m:raw", min_range=5, reenter=False)
    flip = simulate_wick(candles, tf="30m:raw_flip", min_range=5, reenter=True)
    assert hold.n_trades == 1
    assert hold.trades[0].side == "LONG"
    assert hold.trades[0].exit_px == 103.0
    assert flip.n_trades == 2
    assert flip.trades[0].side == "LONG"
    assert flip.trades[1].side == "SHORT"
    assert flip.trades[1].entry_px == 103.0


def test_later_bar_can_enter_after_flat() -> None:
    candles = [
        Candle("2026-08-17 10:00:00", 100.0, 102.0, 90.0, 101.0),  # long
        Candle("2026-08-17 11:00:00", 102.0, 120.0, 101.0, 103.0),  # exit, no reverse
        Candle("2026-08-17 12:00:00", 103.0, 121.0, 102.0, 104.0),  # still upper → short
    ]
    r = simulate_wick(candles, tf="30m:raw", min_range=5, reenter=False)
    assert r.n_trades == 2
    assert r.trades[0].side == "LONG"
    assert r.trades[1].side == "SHORT"
    assert r.trades[1].entry_px == 104.0


def test_strict_exit_ignores_weak_opposite() -> None:
    candles = [
        Candle("2026-08-17 10:00:00", 100.0, 102.0, 90.0, 101.0),  # long (lower 10)
        Candle("2026-08-17 11:00:00", 101.0, 106.0, 100.0, 104.0),  # upper 2, not strict
        Candle("2026-08-17 12:00:00", 104.0, 130.0, 103.0, 105.0),  # upper 25, pin/frac → exit
    ]
    r = simulate_wick(
        candles, tf="30m:raw_strict", min_range=5, reenter=False, exit_strict=True
    )
    assert r.n_trades == 1
    assert r.trades[0].exit_px == 105.0


def test_all_presets_run_on_toy_tape() -> None:
    from backtest_wick_candles import PRESETS

    candles = [
        Candle("2026-08-17 10:00:00", 100.0, 102.0, 90.0, 101.0),  # hammer long
        Candle("2026-08-17 10:30:00", 101.0, 120.0, 100.0, 102.0),  # star → exit
        Candle("2026-08-17 11:00:00", 102.0, 115.0, 102.0, 115.0),  # green bald
        Candle("2026-08-17 11:30:00", 115.0, 115.0, 100.0, 100.0),  # red bald
    ]
    names = [n for n, _ in PRESETS]
    assert names == [
        "raw",
        "diff5",
        "diff10",
        "frac50",
        "pin2",
        "nowick",
        "raw_strict",
        "pin2_strict",
    ]
    for name, filt in PRESETS:
        r = simulate_wick(candles, tf=f"toy:{name}", min_range=5, **filt)
        assert r.n_bars == 4
        if name == "nowick":
            assert r.n_trades >= 1
            assert r.trades[0].side == "LONG"
            assert r.trades[0].entry_px == 115.0
        else:
            assert r.n_trades >= 1, name


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


def test_no_wick_either_side_uses_body() -> None:
    # Green marubozu: H=C, L=O
    assert wick_side(100.0, 110.0, 100.0, 110.0) == "long"
    # Red marubozu
    assert wick_side(110.0, 110.0, 100.0, 100.0) == "short"
    # Bald doji
    assert wick_side(100.0, 100.0, 100.0, 100.0, min_range=0) is None
    assert wick_side(100.0, 110.0, 100.0, 110.0, nowick_body=False) is None


def test_nowick_only_ignores_hammer() -> None:
    assert wick_side(100.0, 102.0, 90.0, 101.0, nowick_only=True) is None
    assert wick_side(100.0, 110.0, 100.0, 110.0, nowick_only=True) == "long"


def test_pin2_still_takes_bald_body() -> None:
    # pin2 would reject a marubozu (no wick), but nowick_body is along with it
    assert wick_side(100.0, 110.0, 100.0, 110.0, min_body_ratio=2.0) == "long"


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
    test_simulate_long_exits_flat_no_reverse()
    print("ok hold exit")
    test_flip_reverses_on_exit_candle()
    print("ok flip reverse")
    test_later_bar_can_enter_after_flat()
    print("ok later entry")
    test_strict_exit_ignores_weak_opposite()
    print("ok strict")
    test_equal_wick_holds()
    print("ok hold")
    test_no_wick_either_side_uses_body()
    print("ok nowick body")
    test_nowick_only_ignores_hammer()
    print("ok nowick only")
    test_pin2_still_takes_bald_body()
    print("ok pin+nowick")
    test_all_presets_run_on_toy_tape()
    print("ok preset grid")
    print("ALL test_wick_candles OK")
