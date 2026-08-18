"""S17: six close×high×body columns. Counts + hhhl/wick/and/or books."""

from __future__ import annotations

from pathlib import Path

from backtest_hhhl_candles import Candle
from control_state import ALL_STRATEGY_NAMES
from s17_close_high_body import (
    LISTED_BUCKETS,
    MISSING_BUCKETS,
    classify_bar,
    s17_bar_decision,
    simulate_s17,
    tally_rows,
    walk_candles,
)


def _c(t: str, o: float, h: float, l: float, c: float) -> Candle:
    return Candle(t, o, h, l, c)


PREV = _c("2026-08-17 10:00:00", 100.0, 105.0, 99.0, 104.0)


def _cls(o: float, h: float, l: float, c: float) -> dict:
    return classify_bar(PREV, _c("2026-08-17 10:30:00", o, h, l, c))


def test_six_listed_columns() -> None:
    assert _cls(100.0, 120.0, 100.0, 110.0)["bucket"] == "up_hh_green"  # 1 C>pC H>pH green
    assert _cls(112.0, 120.0, 100.0, 110.0)["bucket"] == "up_hh_red"  # 2
    assert _cls(100.0, 104.0, 90.0, 103.0)["bucket"] == "dn_lh_green"  # 3
    assert _cls(103.0, 104.0, 90.0, 100.0)["bucket"] == "dn_lh_red"  # 4
    assert _cls(100.0, 110.0, 90.0, 102.0)["bucket"] == "dn_hh_green"  # 5
    assert _cls(104.8, 104.9, 104.2, 104.4)["bucket"] == "up_lh_red"  # 6
    for row in (
        _cls(100.0, 120.0, 100.0, 110.0),
        _cls(112.0, 120.0, 100.0, 110.0),
        _cls(100.0, 104.0, 90.0, 103.0),
        _cls(103.0, 104.0, 90.0, 100.0),
        _cls(100.0, 110.0, 90.0, 102.0),
        _cls(104.8, 104.9, 104.2, 104.4),
    ):
        assert row["kind"] == "listed"
        assert row["bucket"] in LISTED_BUCKETS


def test_two_missing_grid_cells() -> None:
    g = _cls(100.0, 104.8, 99.0, 104.5)  # C>pC H<pH green
    r = _cls(110.0, 120.0, 90.0, 100.0)  # C<pC H>pH red
    assert g["bucket"] == "up_lh_green"
    assert r["bucket"] == "dn_hh_red"
    assert g["kind"] == r["kind"] == "missing"
    assert g["bucket"] in MISSING_BUCKETS
    assert r["bucket"] in MISSING_BUCKETS


def test_equals_are_leftover() -> None:
    eq_c = _cls(100.0, 120.0, 90.0, 104.0)
    eq_h = _cls(100.0, 105.0, 90.0, 104.5)
    doji = _cls(110.0, 120.0, 90.0, 110.0)
    assert eq_c["kind"] == "leftover"
    assert eq_h["kind"] == "leftover"
    assert doji["kind"] == "leftover"
    assert eq_c["close_vs"] == "eq"
    assert eq_h["high_vs"] == "eq"
    assert doji["body"] == "doji"


def test_records_low_and_wick() -> None:
    row = _cls(100.0, 120.0, 80.0, 110.0)  # HH green, LL, long lower wick
    assert row["bucket"] == "up_hh_green"
    assert row["low_vs"] == "ll"
    assert row["wick"] == "long"
    assert row["hhhl"] == "long"
    assert row["agree"] == "agree"


def test_hhhl_and_wick_can_fight() -> None:
    row = _cls(114.0, 120.0, 113.0, 115.0)  # HH green, no LL, upper wick
    assert row["hhhl"] == "long"
    assert row["wick"] == "short"
    assert row["agree"] == "fight"
    assert row["low_vs"] == "hl"


def test_tally_and_or() -> None:
    rows = walk_candles(
        [
            PREV,
            _c("2026-08-17 10:30:00", 100.0, 120.0, 80.0, 110.0),
            _c("2026-08-17 11:00:00", 110.0, 111.0, 109.0, 110.5),
        ]
    )
    by = tally_rows(rows)
    agree = by["up_hh_green"]
    assert agree.n == 1
    assert agree.n_agree == 1
    assert agree.n_or == 1
    assert rows[1]["bucket"] == "up_lh_green"
    assert rows[1]["kind"] == "missing"


def test_books_skip_leftover() -> None:
    cur = _c("2026-08-17 10:30:00", 100.0, 120.0, 90.0, 104.0)  # C=pC
    for mode in ("hhhl", "wick", "and", "or"):
        side, why = s17_bar_decision(PREV, cur, mode=mode)
        assert side is None
        assert "leftover" in why


def test_col1_hhhl_long_wick_may_fight() -> None:
    # HH green, upper wick
    cur = _c("2026-08-17 10:30:00", 114.0, 120.0, 113.0, 115.0)
    hh, _ = s17_bar_decision(PREV, cur, mode="hhhl")
    wk, _ = s17_bar_decision(PREV, cur, mode="wick", min_wick_gap=0)
    both, why_and = s17_bar_decision(PREV, cur, mode="and", min_wick_gap=0)
    either, why_or = s17_bar_decision(PREV, cur, mode="or", min_wick_gap=0)
    assert hh == "long"
    assert wk == "short"
    assert both is None
    assert "and skip" in why_and
    assert either is None
    assert "fight" in why_or


def test_missing_up_lh_green_wick_only() -> None:
    cur = _c("2026-08-17 10:30:00", 100.0, 104.8, 90.0, 104.5)
    hh, _ = s17_bar_decision(PREV, cur, mode="hhhl")
    wk, _ = s17_bar_decision(PREV, cur, mode="wick", min_wick_gap=0)
    either, _ = s17_bar_decision(PREV, cur, mode="or", min_wick_gap=0)
    assert hh is None
    assert wk == "long"
    assert either == "long"


def test_flip_hhhl_book() -> None:
    candles = [
        PREV,
        _c("2026-08-17 10:30:00", 100.0, 120.0, 100.0, 110.0),  # col1 LONG @ 110
        _c("2026-08-17 11:00:00", 108.0, 109.0, 80.0, 90.0),  # col4 LL+red SHORT @ 90
        _c("2026-08-17 11:30:00", 90.0, 91.0, 89.0, 90.5),
    ]
    r = simulate_s17(
        candles, tf="toy:hhhl", mode="hhhl", lots=1, fees=False, session_filter=False
    )
    assert r.n_trades == 2
    assert r.trades[0].side == "LONG"
    assert r.trades[0].entry_px == 110.0
    assert r.trades[0].exit_px == 90.0
    assert r.trades[1].side == "SHORT"


def test_not_wired_to_paper_or_station() -> None:
    assert "S17_CLOSE_HIGH_BODY" not in ALL_STRATEGY_NAMES
    station = (Path(__file__).resolve().parent / "station.html").read_text(encoding="utf-8")
    assert "S17_CLOSE_HIGH_BODY" not in station
    runner = (Path(__file__).resolve().parent / "run_strategy.py").read_text(encoding="utf-8")
    assert "s17_close_high_body" not in runner
    assert "ENABLE_S17" not in runner


if __name__ == "__main__":
    test_six_listed_columns()
    test_two_missing_grid_cells()
    test_equals_are_leftover()
    test_records_low_and_wick()
    test_hhhl_and_wick_can_fight()
    test_tally_and_or()
    test_books_skip_leftover()
    test_col1_hhhl_long_wick_may_fight()
    test_missing_up_lh_green_wick_only()
    test_flip_hhhl_book()
    test_not_wired_to_paper_or_station()
    print("ALL test_s17_close_high_body OK")
