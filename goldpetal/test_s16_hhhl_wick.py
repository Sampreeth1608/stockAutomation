"""S16 = HH/LL structure AND wick on the same finished candle, then FLIP."""

from __future__ import annotations

from pathlib import Path

from backtest_hhhl_candles import Candle
from control_state import ALL_STRATEGY_NAMES
from s16_hhhl_wick import s16_bar_decision, simulate_s16, walk_candles


def _c(t: str, o: float, h: float, l: float, c: float) -> Candle:
    return Candle(t, o, h, l, c)


PREV = _c("2026-08-17 10:00:00", 100.0, 105.0, 99.0, 104.0)


def test_formula_skips_hh_green_with_upper_wick() -> None:
    cur = _c("2026-08-17 10:30:00", 100.0, 120.0, 100.0, 110.0)
    side, why = s16_bar_decision(PREV, cur)
    assert side is None
    assert "wick=short" in why


def test_formula_long_on_hh_green_hammer() -> None:
    cur = _c("2026-08-17 10:30:00", 104.0, 112.0, 90.0, 110.0)
    side, why = s16_bar_decision(PREV, cur)
    assert side == "long"
    assert "HH+green" in why


def test_formula_short_on_ll_red_upper_wick() -> None:
    cur = _c("2026-08-17 10:30:00", 104.0, 130.0, 80.0, 90.0)
    side, why = s16_bar_decision(PREV, cur)
    assert side == "short"
    assert "LL+red" in why


def test_equal_wick_skips_even_if_hh_green() -> None:
    cur = _c("2026-08-17 10:30:00", 104.0, 115.0, 99.0, 110.0)
    side, why = s16_bar_decision(PREV, cur)
    assert side is None
    assert "wick=none" in why


def test_enter_then_flip_on_opposite_and() -> None:
    candles = [
        PREV,
        _c("2026-08-17 10:30:00", 104.0, 112.0, 90.0, 110.0),  # AND long @ 110
        _c("2026-08-17 11:00:00", 110.0, 140.0, 70.0, 80.0),  # LL+red + upper wick → FLIP short @ 80
        _c("2026-08-17 11:30:00", 80.0, 81.0, 79.0, 80.5),  # skip — leftover flat at last close
    ]
    r = simulate_s16(candles, tf="toy:s16", lots=1, fees=False, session_filter=False)
    assert r.n_trades == 2
    assert r.trades[0].side == "LONG"
    assert r.trades[0].entry_px == 110.0
    assert r.trades[0].exit_px == 80.0
    assert r.trades[1].side == "SHORT"
    assert r.trades[1].entry_px == 80.0
    assert r.trades[1].exit_px == 80.5


def test_skip_bar_holds_open_trade() -> None:
    candles = [
        PREV,
        _c("2026-08-17 10:30:00", 104.0, 112.0, 90.0, 110.0),  # long
        _c("2026-08-17 11:00:00", 110.0, 111.0, 109.0, 110.5),  # no HH/LL, tiny wicks
        _c("2026-08-17 11:30:00", 110.5, 150.0, 80.0, 90.0),  # LL+red + upper → FLIP
    ]
    r = simulate_s16(candles, tf="toy:s16", lots=1, fees=False, session_filter=False)
    assert r.trades[0].side == "LONG"
    assert r.trades[0].entry_time == "2026-08-17 10:30:00"
    assert r.trades[0].exit_time == "2026-08-17 11:30:00"


def test_walk_marks_flip() -> None:
    candles = [
        PREV,
        _c("2026-08-17 10:30:00", 104.0, 112.0, 90.0, 110.0),
        _c("2026-08-17 11:00:00", 110.0, 140.0, 70.0, 80.0),
    ]
    rows = walk_candles(candles)
    assert rows[0]["action"] == "enter"
    assert rows[0]["pos_after"] == "long"
    assert rows[1]["action"] == "FLIP"
    assert rows[1]["pos_after"] == "short"


def test_not_wired_to_paper_or_station() -> None:
    assert "S16_HHHL_WICK" not in ALL_STRATEGY_NAMES
    station = (Path(__file__).resolve().parent / "station.html").read_text(encoding="utf-8")
    assert "S16_HHHL_WICK" not in station
    runner = (Path(__file__).resolve().parent / "run_strategy.py").read_text(encoding="utf-8")
    assert "s16_hhhl_wick" not in runner
    assert "ENABLE_S16" not in runner


if __name__ == "__main__":
    test_formula_skips_hh_green_with_upper_wick()
    test_formula_long_on_hh_green_hammer()
    test_formula_short_on_ll_red_upper_wick()
    test_equal_wick_skips_even_if_hh_green()
    test_enter_then_flip_on_opposite_and()
    test_skip_bar_holds_open_trade()
    test_walk_marks_flip()
    test_not_wired_to_paper_or_station()
    print("ALL test_s16_hhhl_wick OK")
