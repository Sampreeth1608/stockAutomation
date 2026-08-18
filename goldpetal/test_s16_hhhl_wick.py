"""S16: up-close uses HH/LL, down-close uses wick, then FLIP."""

from __future__ import annotations

from pathlib import Path

from backtest_hhhl_candles import Candle
from control_state import ALL_STRATEGY_NAMES
from s16_hhhl_wick import s16_bar_decision, simulate_s16, walk_candles


def _c(t: str, o: float, h: float, l: float, c: float) -> Candle:
    return Candle(t, o, h, l, c)


PREV = _c("2026-08-17 10:00:00", 100.0, 105.0, 99.0, 104.0)


def test_up_close_takes_hh_green_even_with_upper_wick() -> None:
    """C>prev → HH/LL only. Slap wick is ignored."""
    cur = _c("2026-08-17 10:30:00", 100.0, 120.0, 100.0, 110.0)
    assert cur.close > PREV.close
    side, why = s16_bar_decision(PREV, cur)
    assert side == "long"
    assert "HH+green" in why


def test_up_close_without_hhhl_skips_even_if_wick() -> None:
    cur = _c("2026-08-17 10:30:00", 104.0, 105.0, 90.0, 104.5)
    assert cur.close > PREV.close
    side, why = s16_bar_decision(PREV, cur)
    assert side is None
    assert "no HH/LL" in why


def test_down_close_uses_wick_not_hhhl() -> None:
    """C<prev → wick. LL+red with a longer lower wick goes LONG."""
    cur = _c("2026-08-17 10:30:00", 103.0, 104.0, 80.0, 100.0)
    assert cur.close < PREV.close
    assert cur.low < PREV.low and cur.close < cur.open
    side, why = s16_bar_decision(PREV, cur)
    assert side == "long"
    assert "lower wick" in why


def test_down_close_upper_wick_short() -> None:
    cur = _c("2026-08-17 10:30:00", 104.0, 130.0, 80.0, 90.0)
    assert cur.close < PREV.close
    side, why = s16_bar_decision(PREV, cur)
    assert side == "short"
    assert "upper wick" in why


def test_equal_close_skips() -> None:
    cur = _c("2026-08-17 10:30:00", 104.0, 120.0, 90.0, 104.0)
    side, why = s16_bar_decision(PREV, cur)
    assert side is None
    assert "C=prev" in why


def test_enter_then_flip() -> None:
    candles = [
        PREV,
        _c("2026-08-17 10:30:00", 100.0, 120.0, 100.0, 110.0),  # C>prev HH+green → long @ 110
        _c("2026-08-17 11:00:00", 110.0, 140.0, 70.0, 80.0),  # C<prev upper wick → FLIP short @ 80
        _c("2026-08-17 11:30:00", 80.0, 81.0, 79.0, 80.5),
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
        _c("2026-08-17 10:30:00", 100.0, 120.0, 100.0, 110.0),  # long
        _c("2026-08-17 11:00:00", 110.0, 111.0, 109.0, 110.5),  # C>prev, no HH/LL
        _c("2026-08-17 11:30:00", 110.5, 150.0, 80.0, 90.0),  # C<prev upper wick → FLIP
    ]
    r = simulate_s16(candles, tf="toy:s16", lots=1, fees=False, session_filter=False)
    assert r.trades[0].side == "LONG"
    assert r.trades[0].entry_time == "2026-08-17 10:30:00"
    assert r.trades[0].exit_time == "2026-08-17 11:30:00"


def test_walk_marks_flip() -> None:
    candles = [
        PREV,
        _c("2026-08-17 10:30:00", 100.0, 120.0, 100.0, 110.0),
        _c("2026-08-17 11:00:00", 110.0, 140.0, 70.0, 80.0),
    ]
    rows = walk_candles(candles)
    assert rows[0]["gate"] == "hhhl"
    assert rows[0]["action"] == "enter"
    assert rows[1]["gate"] == "wick"
    assert rows[1]["action"] == "FLIP"


def test_not_wired_to_paper_or_station() -> None:
    assert "S16_HHHL_WICK" not in ALL_STRATEGY_NAMES
    station = (Path(__file__).resolve().parent / "station.html").read_text(encoding="utf-8")
    assert "S16_HHHL_WICK" not in station
    runner = (Path(__file__).resolve().parent / "run_strategy.py").read_text(encoding="utf-8")
    assert "s16_hhhl_wick" not in runner
    assert "ENABLE_S16" not in runner


if __name__ == "__main__":
    test_up_close_takes_hh_green_even_with_upper_wick()
    test_up_close_without_hhhl_skips_even_if_wick()
    test_down_close_uses_wick_not_hhhl()
    test_down_close_upper_wick_short()
    test_equal_close_skips()
    test_enter_then_flip()
    test_skip_bar_holds_open_trade()
    test_walk_marks_flip()
    test_not_wired_to_paper_or_station()
    print("ALL test_s16_hhhl_wick OK")
