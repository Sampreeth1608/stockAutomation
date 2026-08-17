"""Tests for HH/LL + wick AND combo."""

from __future__ import annotations

from backtest_hhhl_candles import Candle
from backtest_hhhl_wick import simulate_combo


def _c(t: str, o: float, h: float, l: float, c: float) -> Candle:
    return Candle(t, o, h, l, c)


def test_and_skips_hh_green_with_upper_wick() -> None:
    """S12 would buy HH+green; wick is short (upper wick). AND stays out."""
    candles = [
        _c("2026-08-17 10:00:00", 100.0, 105.0, 99.0, 104.0),
        # HH (H=120>105) green (C=110>100) but upper wick 10 vs lower 0
        _c("2026-08-17 10:30:00", 100.0, 120.0, 100.0, 110.0),
    ]
    r = simulate_combo(candles, tf="30m:and", join="and", min_range=5, fees=False)
    assert r.n_trades == 0


def test_and_takes_hh_green_hammer() -> None:
    """HH+green and lower wick → long."""
    candles = [
        _c("2026-08-17 10:00:00", 100.0, 105.0, 99.0, 104.0),
        # H=112>105, C=110>104, lower=14, upper=1
        _c("2026-08-17 10:30:00", 104.0, 112.0, 90.0, 110.0),
        # later LH+red + upper wick → exit
        _c("2026-08-17 11:00:00", 110.0, 111.0, 100.0, 101.0),
    ]
    r = simulate_combo(
        candles, tf="30m:and", join="and", exit_mode="either", min_range=5, fees=False
    )
    assert r.n_trades == 1
    assert r.trades[0].side == "LONG"
    assert r.trades[0].entry_px == 110.0


def test_and_hold_no_reverse() -> None:
    candles = [
        _c("2026-08-17 10:00:00", 100.0, 105.0, 99.0, 104.0),
        _c("2026-08-17 10:30:00", 104.0, 112.0, 90.0, 110.0),  # AND long
        # LL+red + long upper wick: wick+structure both short. HOLD must not reverse.
        _c("2026-08-17 11:00:00", 110.0, 130.0, 80.0, 90.0),
    ]
    r = simulate_combo(
        candles, tf="30m:and", join="and", exit_mode="either", min_range=5, fees=False
    )
    assert r.n_trades == 1
    assert r.trades[0].side == "LONG"
    assert r.trades[0].exit_px == 90.0


def test_hhhl_only_would_buy_upper_wick_breakout() -> None:
    candles = [
        _c("2026-08-17 10:00:00", 100.0, 105.0, 99.0, 104.0),
        _c("2026-08-17 10:30:00", 100.0, 120.0, 100.0, 110.0),
        _c("2026-08-17 11:00:00", 110.0, 111.0, 100.0, 101.0),
    ]
    hh = simulate_combo(candles, tf="30m:hhhl", join="hhhl", min_range=5, fees=False)
    both = simulate_combo(candles, tf="30m:and", join="and", min_range=5, fees=False)
    assert hh.n_trades == 1
    assert both.n_trades == 0


if __name__ == "__main__":
    test_and_skips_hh_green_with_upper_wick()
    test_and_takes_hh_green_hammer()
    test_and_hold_no_reverse()
    test_hhhl_only_would_buy_upper_wick_breakout()
    print("ALL test_backtest_hhhl_wick OK")
