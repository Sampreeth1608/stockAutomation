"""S19: 1h aligned green/red + close vs prev. Paper only. Not live."""

from __future__ import annotations

from pathlib import Path

from control_state import ALL_STRATEGY_NAMES, SLIM_PAPER_STRATEGIES
from live_readiness import PAPER_ONLY_BOOKS
from s18_ohlc_vol_htf import VolBar
from s19_body_close import (
    S19_NAME,
    after_charges_inr,
    s19_bar_decision,
    s19_close_follow_decision,
    simulate_close_follow,
    simulate_s19,
)


def _b(t: str, o: float, h: float, l: float, c: float) -> VolBar:
    return VolBar(t, o, h, l, c)


PREV = _b("2026-08-17 10:00:00", 100.0, 105.0, 99.0, 104.0)


def test_aligned_long_and_short() -> None:
    long_bar = _b("2026-08-17 11:00:00", 104.0, 120.0, 103.0, 110.0)
    short_bar = _b("2026-08-17 11:00:00", 104.0, 105.0, 90.0, 92.0)
    assert s19_bar_decision(PREV, long_bar) == ("long", "aligned:long green C>prevC")
    assert s19_bar_decision(PREV, short_bar) == ("short", "aligned:short red C<prevC")


def test_mixed_doji_equal_skip() -> None:
    mixed_green_dn = _b("2026-08-17 11:00:00", 90.0, 110.0, 89.0, 100.0)
    mixed_red_up = _b("2026-08-17 11:00:00", 120.0, 125.0, 103.0, 110.0)
    doji = _b("2026-08-17 11:00:00", 110.0, 120.0, 90.0, 110.0)
    eq = _b("2026-08-17 11:00:00", 100.0, 110.0, 90.0, 104.0)
    assert s19_bar_decision(PREV, mixed_green_dn)[0] is None
    assert "mixed" in s19_bar_decision(PREV, mixed_green_dn)[1]
    assert s19_bar_decision(PREV, mixed_red_up)[0] is None
    assert "mixed" in s19_bar_decision(PREV, mixed_red_up)[1]
    assert s19_bar_decision(PREV, doji) == (None, "doji")
    assert s19_bar_decision(PREV, eq) == (None, "close equals prev")


def test_close_follow_takes_mixed_hours() -> None:
    mixed_green_dn = _b("2026-08-17 11:00:00", 90.0, 110.0, 89.0, 100.0)
    side, why = s19_close_follow_decision(PREV, mixed_green_dn)
    assert side == "short"
    assert "follow" in why
    assert s19_bar_decision(PREV, mixed_green_dn)[0] is None


def test_hold_through_mixed_then_flip() -> None:
    hours = [
        PREV,
        _b("2026-08-17 11:00:00", 104.0, 120.0, 103.0, 110.0),  # aligned long
        _b("2026-08-17 12:00:00", 90.0, 111.0, 89.0, 100.0),  # mixed green down — hold
        _b("2026-08-17 13:00:00", 120.0, 121.0, 104.0, 105.0),  # mixed red up — hold
        _b("2026-08-17 14:00:00", 105.0, 106.0, 80.0, 85.0),  # aligned short — flip
    ]
    r = simulate_s19(hours, lots=1.0, fees=False, session_filter=False)
    assert r.n_trades == 2
    assert r.trades[0].side == "LONG"
    assert r.trades[0].entry_time == "2026-08-17 11:00:00"
    assert r.trades[0].exit_time == "2026-08-17 14:00:00"
    assert r.trades[0].entry_px == 110.0
    assert r.trades[0].exit_px == 85.0
    assert r.trades[1].side == "SHORT"
    assert r.trades[1].entry_time == "2026-08-17 14:00:00"
    follow = simulate_close_follow(hours, lots=1.0, fees=False, session_filter=False)
    assert follow.n_trades > r.n_trades
    assert follow.trades[0].exit_time == "2026-08-17 12:00:00"


def test_after_charges_excludes_tax() -> None:
    hours = [
        PREV,
        _b("2026-08-17 11:00:00", 104.0, 120.0, 103.0, 110.0),
        _b("2026-08-17 12:00:00", 110.0, 111.0, 80.0, 85.0),
    ]
    r = simulate_s19(hours, lots=1.0, fees=True, session_filter=False)
    ac = after_charges_inr(r)
    assert abs(ac - (r.gross_pnl_inr - r.fees_inr)) < 1e-9
    assert ac >= r.after_tax_pnl_inr - 1e-9


def test_paper_wired_not_live() -> None:
    assert S19_NAME in ALL_STRATEGY_NAMES
    assert S19_NAME not in SLIM_PAPER_STRATEGIES
    assert S19_NAME in PAPER_ONLY_BOOKS
    assert "S17_CLOSE_HIGH_BODY" not in ALL_STRATEGY_NAMES
    root = Path(__file__).resolve().parent
    station = (root / "station.html").read_text(encoding="utf-8")
    runner = (root / "run_strategy.py").read_text(encoding="utf-8")
    portfolio = (root / "portfolio.py").read_text(encoding="utf-8")
    env_bridge = (root / "analytics" / "env_bridge.py").read_text(encoding="utf-8")
    assert S19_NAME in station
    paper = station.split("const PAPER_BOOKS")[1].split("];")[0]
    assert S19_NAME not in paper
    assert "s19_from_env" in runner
    assert "ENABLE_S19" in runner
    assert "ENABLE_S17" not in runner
    assert 'on("ENABLE_S19", "false")' in portfolio
    assert '"ENABLE_S19": "false"' in env_bridge


def test_hours_from_ohlc_keeps_volume() -> None:
    from s19_body_close import hours_from_ohlc

    bars = hours_from_ohlc(
        [
            {
                "time": "2026-08-17T11:00:00+05:30",
                "open": 104,
                "high": 120,
                "low": 103,
                "close": 110,
                "volume": 2000,
            }
        ]
    )
    assert bars[0].time == "2026-08-17 11:00:00"
    assert bars[0].close == 110.0
    assert bars[0].volume == 2000.0


if __name__ == "__main__":
    test_aligned_long_and_short()
    test_mixed_doji_equal_skip()
    test_close_follow_takes_mixed_hours()
    test_hold_through_mixed_then_flip()
    test_after_charges_excludes_tax()
    test_paper_wired_not_live()
    test_hours_from_ohlc_keeps_volume()
    print("ALL test_s19_body_close OK")
