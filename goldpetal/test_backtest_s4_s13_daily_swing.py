"""S4 HH/LL vs S13 S16 on 1d bars — hold until opposite, fill at day close."""

from __future__ import annotations

from backtest_hhhl_candles import Candle, in_session
from backtest_s4_s13_daily_swing import (
    S13_ROW,
    S4_ROW,
    run_books,
    walk_books,
)
from s16_hhhl_wick import hhhl_bar_decision, s16_bar_decision, simulate_s16


def _d(day: str, o: float, h: float, l: float, c: float) -> Candle:
    return Candle(f"{day} 00:00:00", o, h, l, c)


def _toy_trend() -> list[Candle]:
    """HH+green, inside hold, LL+red FLIP. Then a leftover mark-at-last-close."""
    return [
        _d("2026-08-10", 15000, 15100, 14900, 15050),  # warmup
        _d("2026-08-11", 15080, 15200, 15040, 15180),  # HH+green → LONG @ 15180
        _d("2026-08-12", 15170, 15190, 15110, 15140),  # inside — no HH/LL
        _d("2026-08-13", 15120, 15300, 14850, 14880),  # LL+red + upper wick → FLIP SHORT @ 14880
        _d("2026-08-14", 14870, 14910, 14840, 14890),  # hold short; flatten @ 14890
    ]


def test_hhhl_ignores_close_vs_prev_and_wicks() -> None:
    prev = _d("2026-08-10", 15000, 15100, 14900, 15050)
    # Down close, longer lower wick, but low does not break prev low.
    cur = _d("2026-08-11", 15040, 15050, 14910, 14960)
    assert cur.close < prev.close
    assert cur.low > prev.low
    s4, why4 = hhhl_bar_decision(prev, cur)
    s13, why13 = s16_bar_decision(prev, cur, min_wick_gap=0)
    assert s4 is None
    assert "no HH/LL" in why4
    assert s13 == "long"
    assert "lower wick" in why13


def test_s4_enters_holds_inside_flips_on_ll() -> None:
    r = simulate_s16(
        _toy_trend(),
        tf="toy:s4",
        lots=1,
        fees=False,
        session_filter=False,
        decide=hhhl_bar_decision,
    )
    assert r.n_trades == 2
    assert r.trades[0].side == "LONG"
    assert r.trades[0].entry_px == 15180.0
    assert r.trades[0].entry_time.startswith("2026-08-11")
    assert r.trades[0].exit_time.startswith("2026-08-13")
    assert r.trades[0].exit_px == 14880.0
    assert r.trades[1].side == "SHORT"
    assert r.trades[1].entry_px == 14880.0
    assert r.trades[1].exit_time.startswith("2026-08-14")
    assert r.trades[1].exit_px == 14890.0


def test_inside_day_does_not_flatten_at_next_open() -> None:
    """00:00 1d bars are outside 09:00–23:30. session_filter must stay off."""
    days = _toy_trend()
    assert not in_session(days[1])
    r = simulate_s16(
        days,
        tf="toy:hold",
        lots=1,
        fees=False,
        session_filter=False,
        decide=hhhl_bar_decision,
    )
    assert r.trades[0].exit_time.startswith("2026-08-13")
    killed = simulate_s16(
        days,
        tf="toy:session",
        lots=1,
        fees=False,
        session_filter=True,
        decide=hhhl_bar_decision,
    )
    assert killed.n_trades == 0


def test_s13_buys_lower_wick_when_s4_skips() -> None:
    candles = [
        _d("2026-08-10", 15000, 15100, 14900, 15050),
        _d("2026-08-11", 15040, 15050, 14910, 14960),  # down close, lower wick, no LL
        _d("2026-08-12", 14950, 14965, 14920, 14962),
    ]
    s4, s13 = run_books(candles, lots=1, fees=False)
    assert s4.tf == S4_ROW
    assert s13.tf == S13_ROW
    assert s4.n_trades == 0
    assert s13.n_trades == 1
    assert s13.trades[0].side == "LONG"
    assert s13.trades[0].entry_px == 14960.0
    w4, w13 = walk_books(candles)
    assert w4[0]["action"] == "skip"
    assert w13[0]["action"] == "enter"
    assert w13[0]["side"] == "long"


def test_run_books_same_tape_100_lots_fees() -> None:
    s4, s13 = run_books(_toy_trend(), lots=100, fees=True)
    assert s4.n_trades == 2
    assert s13.n_trades == 2
    assert s4.fees_inr > 0
    assert s13.fees_inr > 0
    assert s4.trades[0].lots == 100.0


if __name__ == "__main__":
    test_hhhl_ignores_close_vs_prev_and_wicks()
    print("ok diverge")
    test_s4_enters_holds_inside_flips_on_ll()
    print("ok s4 swing")
    test_inside_day_does_not_flatten_at_next_open()
    print("ok no session kill")
    test_s13_buys_lower_wick_when_s4_skips()
    print("ok s13 wick")
    test_run_books_same_tape_100_lots_fees()
    print("ok 100 lots fees")
    print("ALL test_backtest_s4_s13_daily_swing OK")
