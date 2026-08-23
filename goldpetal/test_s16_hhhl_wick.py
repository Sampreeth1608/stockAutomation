"""S16: up-close uses HH/LL, down-close uses wick, then FLIP."""

from __future__ import annotations

from pathlib import Path

from backtest_hhhl_candles import Candle
from control_state import ALL_STRATEGY_NAMES, SLIM_PAPER_STRATEGIES
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


def test_down_close_upper_then_lower_wick_flips() -> None:
    """20 Aug 15:00 C=15770 → 16:00 C=15753 upper wick SHORT; 17:00 C=15680 lower wick BUY.

    Fill is at that bar's close. The long is held through 17:00–18:00 (the
    '18:00 buy'); it does not wait for the 18:00 close.
    """
    c15 = _c("2026-08-20 14:00:00", 15770.0, 15780.0, 15760.0, 15770.0)
    c16 = _c("2026-08-20 15:00:00", 15768.0, 15800.0, 15750.0, 15753.0)
    c17 = _c("2026-08-20 16:00:00", 15750.0, 15755.0, 15640.0, 15680.0)
    hold = _c("2026-08-20 17:00:00", 15680.0, 15690.0, 15670.0, 15685.0)
    assert c16.close < c15.close
    side16, why16 = s16_bar_decision(c15, c16, min_wick_gap=0)
    assert side16 == "short" and "upper wick" in why16
    assert c17.close < c16.close
    side17, why17 = s16_bar_decision(c16, c17, min_wick_gap=0)
    assert side17 == "long" and "lower wick" in why17
    r = simulate_s16(
        [c15, c16, c17, hold],
        tf="toy:20aug-pm",
        lots=1,
        fees=False,
        session_filter=False,
        min_wick_gap=0,
    )
    assert r.n_trades == 2
    assert r.trades[0].side == "SHORT"
    assert r.trades[0].entry_px == 15753.0
    assert r.trades[0].exit_px == 15680.0
    assert r.trades[1].side == "LONG"
    assert r.trades[1].entry_px == 15680.0
    assert r.trades[1].entry_time == "2026-08-20 16:00:00"


def test_tiny_wick_gap_skips_when_gap_is_3() -> None:
    """3 Aug 10:30 style: U=2 Lw=3 must not FLIP when gap=3."""
    cur = _c("2026-08-17 10:30:00", 104.0, 106.0, 100.0, 103.0)
    assert cur.close < PREV.close
    m_long, _why0 = s16_bar_decision(PREV, cur, min_wick_gap=0)
    m_gap, why3 = s16_bar_decision(PREV, cur, min_wick_gap=3)
    assert m_long == "long"
    assert m_gap is None
    assert "wick gap 1.0<3" in why3


def test_clear_wick_still_fires_with_gap_3() -> None:
    cur = _c("2026-08-17 10:30:00", 104.0, 120.0, 103.0, 103.5)
    side, why = s16_bar_decision(PREV, cur, min_wick_gap=3)
    assert side == "short"
    assert "upper wick" in why


def test_up_close_ignores_wick_gap() -> None:
    cur = _c("2026-08-17 10:30:00", 100.0, 120.0, 100.0, 110.0)
    side, _ = s16_bar_decision(PREV, cur, min_wick_gap=10)
    assert side == "long"


def test_equal_close_skips() -> None:
    cur = _c("2026-08-17 10:30:00", 104.0, 120.0, 90.0, 104.0)
    side, why = s16_bar_decision(PREV, cur)
    assert side is None
    assert "C=prev" in why


def test_tiny_down_wick_does_not_flip_when_gap_3() -> None:
    """Down-close U=2 Lw=3 FLIPs at gap=0, holds at gap=3."""
    candles = [
        PREV,
        _c("2026-08-17 10:30:00", 104.0, 130.0, 80.0, 90.0),  # C<prev upper wick → short @ 90
        _c("2026-08-17 11:00:00", 89.0, 91.0, 85.0, 88.0),  # C<prev U=2 Lw=3
        _c("2026-08-17 11:30:00", 88.0, 89.0, 87.0, 88.5),
    ]
    r0 = simulate_s16(
        candles, tf="toy:g0", lots=1, fees=False, session_filter=False, min_wick_gap=0
    )
    r3 = simulate_s16(
        candles, tf="toy:g3", lots=1, fees=False, session_filter=False, min_wick_gap=3
    )
    assert r0.n_trades == 2
    assert r0.trades[0].side == "SHORT"
    assert r0.trades[0].exit_time == "2026-08-17 11:00:00"
    assert r0.trades[1].side == "LONG"
    assert r3.n_trades == 1
    assert r3.trades[0].side == "SHORT"
    assert r3.trades[0].entry_time == "2026-08-17 10:30:00"
    assert r3.trades[0].exit_time == "2026-08-17 11:30:00"
    skip_row = walk_candles(candles, min_wick_gap=3)[1]
    assert skip_row["action"] == "skip"
    assert skip_row["wick_gap"] == 1.0


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


def test_session_filter_skips_first_hour_vs_prior_day() -> None:
    candles = [
        _c("2026-08-17 23:00:00", 90.0, 140.0, 80.0, 80.0),
        _c("2026-08-18 09:00:00", 100.0, 130.0, 100.0, 120.0),
        _c("2026-08-18 10:00:00", 120.0, 140.0, 119.0, 130.0),
        _c("2026-08-18 11:00:00", 130.0, 131.0, 129.0, 130.5),
    ]
    r = simulate_s16(
        candles, tf="toy:sess", lots=1, fees=False, session_filter=True, min_wick_gap=0
    )
    assert r.n_trades == 1
    assert r.trades[0].entry_time == "2026-08-18 10:00:00"
    assert r.trades[0].entry_px == 130.0


def test_session_filter_skips_first_hour_vs_preopen() -> None:
    candles = [
        _c("2026-08-17 08:00:00", 90.0, 140.0, 80.0, 80.0),
        _c("2026-08-17 09:00:00", 100.0, 130.0, 100.0, 120.0),
        _c("2026-08-17 10:00:00", 120.0, 140.0, 119.0, 130.0),
        _c("2026-08-17 11:00:00", 130.0, 131.0, 129.0, 130.5),
    ]
    r = simulate_s16(
        candles, tf="toy:pre", lots=1, fees=False, session_filter=True, min_wick_gap=0
    )
    assert r.n_trades == 1
    assert r.trades[0].entry_time == "2026-08-17 10:00:00"
    assert r.trades[0].entry_px == 130.0


def test_paper_wired_1h_not_s17() -> None:
    assert "S16_HHHL_WICK_1H" in ALL_STRATEGY_NAMES
    assert "S16_HHHL_WICK_1H" in SLIM_PAPER_STRATEGIES
    assert "S18_OHLC_VOL_HTF" not in SLIM_PAPER_STRATEGIES
    assert "S19_BODY_CLOSE_1H" not in SLIM_PAPER_STRATEGIES
    assert "S20_FADE_HL" not in SLIM_PAPER_STRATEGIES
    assert "OVERNIGHT_GAP" not in SLIM_PAPER_STRATEGIES
    assert "S13_HHHL_DAY" not in SLIM_PAPER_STRATEGIES
    assert "S4_OVERNIGHT" not in SLIM_PAPER_STRATEGIES
    assert "S12_HHHL30" not in SLIM_PAPER_STRATEGIES
    assert "S14_WICK30_STRICT" not in SLIM_PAPER_STRATEGIES
    assert "S15_WICK30_NOWICK" not in SLIM_PAPER_STRATEGIES
    station = (Path(__file__).resolve().parent / "station.html").read_text(encoding="utf-8")
    assert "S16_HHHL_WICK_1H" in station
    runner = (Path(__file__).resolve().parent / "run_strategy.py").read_text(encoding="utf-8")
    assert "ENABLE_S16" in runner or "S16_HHHL_WICK_1H" in runner
    assert "s16_from_env" in runner
    assert "FORMULA_GATE_BOOKS" in runner
    assert "S17_CLOSE_HIGH_BODY" not in ALL_STRATEGY_NAMES
    assert "ENABLE_S17" not in runner
    archive = Path(__file__).resolve().parent / "docs" / "goldpetal_all_strategies.pdf"
    assert archive.is_file() and archive.stat().st_size > 1000


def test_gap_compare_groups_every_tf_and_gap() -> None:
    from backtest_s16_hhhl_wick import _group_gap_results, print_gap_compare

    candles = [
        PREV,
        _c("2026-08-17 10:30:00", 104.0, 130.0, 80.0, 90.0),
        _c("2026-08-17 11:00:00", 89.0, 91.0, 85.0, 88.0),
        _c("2026-08-17 11:30:00", 88.0, 89.0, 87.0, 88.5),
    ]
    results = []
    for tf in ("30m", "1h"):
        for gap, tag in ((0, "g0"), (3, "g3"), (5, "g5"), (10, "g10")):
            results.append(
                simulate_s16(
                    candles,
                    tf=f"{tf}:{tag}",
                    lots=1,
                    fees=False,
                    session_filter=False,
                    min_wick_gap=gap,
                )
            )
    tfs, tags, by = _group_gap_results(results)
    assert tfs == ["30m", "1h"]
    assert tags == ["g0", "g3", "g5", "g10"]
    assert by["30m"]["g0"].n_trades == 2
    assert by["30m"]["g3"].n_trades == 1
    assert by["1h"]["g10"].n_trades == 1
    print_gap_compare(results)


if __name__ == "__main__":
    test_up_close_takes_hh_green_even_with_upper_wick()
    test_up_close_without_hhhl_skips_even_if_wick()
    test_down_close_uses_wick_not_hhhl()
    test_down_close_upper_then_lower_wick_flips()
    test_equal_close_skips()
    test_tiny_wick_gap_skips_when_gap_is_3()
    test_clear_wick_still_fires_with_gap_3()
    test_up_close_ignores_wick_gap()
    test_tiny_down_wick_does_not_flip_when_gap_3()
    test_enter_then_flip()
    test_skip_bar_holds_open_trade()
    test_walk_marks_flip()
    test_session_filter_skips_first_hour_vs_prior_day()
    test_session_filter_skips_first_hour_vs_preopen()
    test_paper_wired_1h_not_s17()
    test_gap_compare_groups_every_tf_and_gap()
    print("ALL test_s16_hhhl_wick OK")
