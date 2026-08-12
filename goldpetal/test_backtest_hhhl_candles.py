"""Unit tests for HH/LL candle breakout rules (no DB required)."""

from __future__ import annotations

from backtest_hhhl_candles import (
    Candle,
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
    # higher high + green
    cur_in = C("2026-08-11 10:01:00", 104, 110, 103, 109)
    assert long_entry(cur_in, prev)
    assert not short_entry(cur_in, prev)
    # lower high + red → long exit
    cur_out = C("2026-08-11 10:02:00", 109, 108, 100, 101)
    assert long_exit(cur_out, cur_in)
    assert short_entry(cur_out, cur_in)  # same bar can flip short


def test_short_entry_and_exit_rules() -> None:
    prev = C("2026-08-11 11:00:00", 110, 111, 105, 106)
    cur_in = C("2026-08-11 11:01:00", 106, 107, 100, 101)  # LL + red
    assert short_entry(cur_in, prev)
    cur_out = C("2026-08-11 11:02:00", 101, 108, 101.5, 107)  # higher low + green
    assert short_exit(cur_out, cur_in)
    assert long_entry(cur_out, cur_in)


def test_simulate_one_long_roundtrip() -> None:
    candles = [
        C("2026-08-11 10:00:00", 100, 102, 99, 101),
        C("2026-08-11 10:01:00", 101, 110, 100, 109),  # HH green → long @109
        C("2026-08-11 10:02:00", 109, 112, 108, 111),  # hold (HH green again)
        C("2026-08-11 10:03:00", 111, 110, 100, 101),  # LH red → exit @101
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
        C("2026-08-11 10:01:00", 101, 110, 100, 109),  # long
        C("2026-08-11 10:02:00", 109, 108, 95, 96),  # LH red + LL red → flip short
        C("2026-08-11 10:03:00", 96, 100, 97, 99),  # HL green → exit short
    ]
    res = simulate(candles, tf="1m", lots=1.0)
    assert res.n_trades == 2
    assert res.trades[0].side == "LONG"
    assert res.trades[1].side == "SHORT"
    assert res.trades[1].entry_px == 96
    assert res.trades[1].exit_px == 99
    assert res.trades[1].gross_pts == -3


if __name__ == "__main__":
    test_long_entry_and_exit_rules()
    test_short_entry_and_exit_rules()
    test_simulate_one_long_roundtrip()
    test_simulate_flip_long_to_short()
    print("ALL test_backtest_hhhl_candles OK")
