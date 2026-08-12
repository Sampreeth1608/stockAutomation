"""Unit tests for HH/LL candle breakout rules (no DB required)."""

from __future__ import annotations

from backtest_hhhl_candles import (
    Candle,
    in_session,
    long_entry,
    long_exit,
    short_entry,
    short_exit,
    simulate,
)


def C(t: str, o: float, h: float, l: float, c: float) -> Candle:
    return Candle(time=t, open=o, high=h, low=l, close=c)


def test_long_entry_and_exit_rules() -> None:
    prev = C("2026-08-11 10:00:00", 100, 105, 99, 104)
    cur_in = C("2026-08-11 10:01:00", 104, 110, 103, 109)
    assert long_entry(cur_in, prev)
    assert not short_entry(cur_in, prev)
    cur_out = C("2026-08-11 10:02:00", 109, 108, 100, 101)
    assert long_exit(cur_out, cur_in)
    assert short_entry(cur_out, cur_in)


def test_short_entry_and_exit_rules() -> None:
    prev = C("2026-08-11 11:00:00", 110, 111, 105, 106)
    cur_in = C("2026-08-11 11:01:00", 106, 107, 100, 101)
    assert short_entry(cur_in, prev)
    cur_out = C("2026-08-11 11:02:00", 101, 108, 101.5, 107)
    assert short_exit(cur_out, cur_in)
    assert long_entry(cur_out, cur_in)


def test_simulate_one_long_roundtrip() -> None:
    candles = [
        C("2026-08-11 10:00:00", 100, 102, 99, 101),
        C("2026-08-11 10:01:00", 101, 110, 100, 109),
        C("2026-08-11 10:02:00", 109, 112, 108, 111),
        C("2026-08-11 10:03:00", 111, 110, 100, 101),
    ]
    res = simulate(candles, tf="1m", lots=1.0, allow_short=False)
    assert res.n_trades == 1
    assert res.trades[0].side == "LONG"
    assert res.trades[0].entry_px == 109
    assert res.trades[0].exit_px == 101
    assert res.trades[0].gross_pts == -8


def test_simulate_flip_long_to_short() -> None:
    candles = [
        C("2026-08-11 10:00:00", 100, 102, 99, 101),
        C("2026-08-11 10:01:00", 101, 110, 100, 109),
        C("2026-08-11 10:02:00", 109, 108, 95, 96),
        C("2026-08-11 10:03:00", 96, 100, 97, 99),
    ]
    res = simulate(candles, tf="1m", lots=1.0, no_flip=False)
    assert res.n_trades == 2
    assert res.trades[0].side == "LONG"
    assert res.trades[1].side == "SHORT"
    assert res.trades[1].entry_px == 96


def test_no_flip_exits_flat_only() -> None:
    candles = [
        C("2026-08-11 10:00:00", 100, 102, 99, 101),
        C("2026-08-11 10:01:00", 101, 110, 100, 109),  # long
        C("2026-08-11 10:02:00", 109, 108, 95, 96),  # exit long, no flip
        C("2026-08-11 10:03:00", 96, 100, 97, 99),  # would be short exit — flat
    ]
    res = simulate(candles, tf="1m", lots=1.0, no_flip=True)
    assert res.n_trades == 1
    assert res.trades[0].side == "LONG"
    assert res.trades[0].exit_px == 96


def test_min_range_blocks_entry() -> None:
    candles = [
        C("2026-08-11 10:00:00", 100, 102, 99, 101),
        C("2026-08-11 10:01:00", 101, 103, 100.5, 102.5),  # HH green but range 2.5
        C("2026-08-11 10:02:00", 102.5, 101, 100, 100.5),
    ]
    res = simulate(candles, tf="1m", lots=1.0, min_range=5.0, allow_short=False)
    assert res.n_trades == 0


def test_session_weekend_blocked() -> None:
    sat = C("2026-08-08 10:00:00", 100, 110, 99, 109)  # Saturday
    assert not in_session(sat)
    mon = C("2026-08-10 10:00:00", 100, 110, 99, 109)
    assert in_session(mon)


def test_fees_deduct_brokerage() -> None:
    candles = [
        C("2026-08-11 10:00:00", 10000, 10002, 9999, 10001),
        C("2026-08-11 10:01:00", 10001, 10050, 10000, 10040),  # long
        C("2026-08-11 10:02:00", 10040, 10030, 10010, 10015),  # exit +15 pts
    ]
    free = simulate(candles, tf="1m", lots=1.0, allow_short=False, fees=False)
    paid = simulate(candles, tf="1m", lots=1.0, allow_short=False, fees=True)
    assert free.n_trades == 1 and paid.n_trades == 1
    assert paid.fees_inr > 0
    assert paid.after_tax_pnl_inr < free.after_tax_pnl_inr


if __name__ == "__main__":
    test_long_entry_and_exit_rules()
    test_short_entry_and_exit_rules()
    test_simulate_one_long_roundtrip()
    test_simulate_flip_long_to_short()
    test_no_flip_exits_flat_only()
    test_min_range_blocks_entry()
    test_session_weekend_blocked()
    test_fees_deduct_brokerage()
    print("ALL test_backtest_hhhl_candles OK")
