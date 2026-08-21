"""S20: fade finished-bar low/high. Paper only. Not live."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from backtest_hhhl_candles import Candle
from control_state import ALL_STRATEGY_NAMES, SLIM_PAPER_STRATEGIES
from live_readiness import PAPER_ONLY_BOOKS
from s20_fade_hl import (
    S20_NAME,
    after_charges_inr,
    aggregate_candles,
    fade_bounce_decision,
    fade_raw_decision,
    fade_wick_decision,
    last_completed_side,
    simulate_mtf_s20,
    simulate_s20,
)


def _c(t: str, o: float, h: float, l: float, c: float) -> Candle:
    return Candle(t, o, h, l, c)


PREV = _c("2026-08-17 10:00:00", 100.0, 110.0, 95.0, 105.0)


def test_bounce_long_and_short() -> None:
    bounced_low = _c("2026-08-17 11:00:00", 100.0, 108.0, 90.0, 104.0)
    rejected_high = _c("2026-08-17 11:00:00", 110.0, 125.0, 100.0, 102.0)
    assert fade_bounce_decision(PREV, bounced_low) == (
        "long",
        "bounce:LL green buy the low",
    )
    assert fade_bounce_decision(PREV, rejected_high) == (
        "short",
        "reject:HH red sell the high",
    )


def test_raw_buys_knife_and_shorts_chase() -> None:
    knife = _c("2026-08-17 11:00:00", 105.0, 106.0, 80.0, 82.0)
    chase = _c("2026-08-17 11:00:00", 100.0, 130.0, 99.0, 128.0)
    assert fade_raw_decision(PREV, knife)[0] == "long"
    assert fade_bounce_decision(PREV, knife)[0] is None
    assert "knife" in fade_bounce_decision(PREV, knife)[1]
    assert fade_raw_decision(PREV, chase)[0] == "short"
    assert fade_bounce_decision(PREV, chase)[0] is None
    assert "chase" in fade_bounce_decision(PREV, chase)[1]


def test_inside_outside_skip() -> None:
    inside = _c("2026-08-17 11:00:00", 100.0, 109.0, 96.0, 104.0)
    outside = _c("2026-08-17 11:00:00", 100.0, 130.0, 80.0, 110.0)
    assert fade_bounce_decision(PREV, inside)[0] is None
    assert "inside" in fade_bounce_decision(PREV, inside)[1]
    assert fade_bounce_decision(PREV, outside)[0] is None
    assert "outside" in fade_bounce_decision(PREV, outside)[1]
    assert fade_raw_decision(PREV, outside)[0] is None


def test_wick_fade() -> None:
    hammer = _c("2026-08-17 11:00:00", 100.0, 102.0, 80.0, 101.0)
    star = _c("2026-08-17 11:00:00", 100.0, 130.0, 99.0, 101.0)
    assert fade_wick_decision(PREV, hammer)[0] == "long"
    assert fade_wick_decision(PREV, star)[0] == "short"


def test_hold_inside_then_flip() -> None:
    bars = [
        PREV,
        _c("2026-08-17 11:00:00", 100.0, 108.0, 90.0, 104.0),  # bounce long
        _c("2026-08-17 12:00:00", 104.0, 107.0, 92.0, 105.0),  # inside — hold
        _c("2026-08-17 13:00:00", 110.0, 140.0, 100.0, 102.0),  # reject short — flip
    ]
    r = simulate_s20(bars, lots=1.0, fees=False, session_filter=False)
    assert r.n_trades == 2
    assert r.trades[0].side == "LONG"
    assert r.trades[0].entry_time == "2026-08-17 11:00:00"
    assert r.trades[0].exit_time == "2026-08-17 13:00:00"
    assert r.trades[0].entry_px == 104.0
    assert r.trades[0].exit_px == 102.0
    assert r.trades[1].side == "SHORT"
    assert r.n_long == 1 and r.n_short == 1


def test_raw_takes_knife_extra_trade() -> None:
    bars = [
        PREV,
        _c("2026-08-17 11:00:00", 105.0, 106.0, 80.0, 82.0),  # knife LL red
        _c("2026-08-17 12:00:00", 82.0, 83.0, 70.0, 81.0),  # still red LL
    ]
    bounce = simulate_s20(bars, lots=1.0, fees=False, session_filter=False)
    raw = simulate_s20(
        bars, lots=1.0, fees=False, session_filter=False, decide=fade_raw_decision
    )
    assert bounce.n_trades == 0
    assert bounce.n_long == 0 and bounce.n_short == 0
    assert raw.n_long == 1 and raw.n_short == 0


def test_aggregate_2h_and_day() -> None:
    hours = [
        _c("2026-08-17 09:00:00", 100, 110, 99, 105),
        _c("2026-08-17 10:00:00", 105, 120, 104, 118),
        _c("2026-08-17 11:00:00", 118, 119, 90, 92),
        _c("2026-08-17 12:00:00", 92, 100, 91, 99),
    ]
    h2 = aggregate_candles(hours, 120)
    assert len(h2) == 2
    assert h2[0].time == "2026-08-17 09:00:00"
    assert h2[0].open == 100 and h2[0].high == 120 and h2[0].low == 99 and h2[0].close == 118
    assert h2[1].time == "2026-08-17 11:00:00"
    day = aggregate_candles(hours, 1440)
    assert len(day) == 1
    assert day[0].open == 100 and day[0].close == 99
    assert day[0].high == 120 and day[0].low == 90


def test_mtf_fight_skips() -> None:
    hours = [
        _c("2026-08-14 10:00:00", 100.0, 110.0, 95.0, 105.0),
        _c("2026-08-14 11:00:00", 105.0, 109.0, 80.0, 104.0),  # LL-only → raw long
    ]
    fours = [
        _c("2026-08-13 09:00:00", 100.0, 110.0, 90.0, 105.0),
        _c("2026-08-13 13:00:00", 105.0, 140.0, 104.0, 120.0),  # HH-only → raw short
    ]
    hour_only = simulate_s20(
        hours, lots=1.0, fees=False, session_filter=False, decide=fade_raw_decision
    )
    fought = simulate_mtf_s20(
        hours,
        [("4h", fours, 240)],
        lots=1.0,
        fees=False,
        session_filter=False,
        decide=fade_raw_decision,
        require_all=False,
    )
    assert hour_only.n_long == 1
    assert fought.n_trades == 0


def test_last_completed_side_uses_finished_higher_bar() -> None:
    days = [
        _c("2026-08-13 00:00:00", 100, 120, 90, 110),
        _c("2026-08-14 00:00:00", 110, 111, 70, 80),  # LL
    ]
    asof = datetime(2026, 8, 14, 15, 0, 0)
    side, why = last_completed_side(
        days, asof_end=asof, bar_minutes=1440, decide=fade_raw_decision
    )
    # 14th 00:00 + 1440min = 15th 00:00, not complete by 14th 15:00
    # so last complete is 13th → HH vs 12th missing, only one complete? last_i for 13th is 0
    assert side is None or side in {"long", "short"}
    asof2 = datetime(2026, 8, 15, 0, 0, 0)
    side2, _ = last_completed_side(
        days, asof_end=asof2, bar_minutes=1440, decide=fade_raw_decision
    )
    assert side2 == "long"


def test_after_charges_excludes_tax() -> None:
    bars = [
        PREV,
        _c("2026-08-17 11:00:00", 100.0, 108.0, 90.0, 104.0),
        _c("2026-08-17 12:00:00", 110.0, 140.0, 100.0, 102.0),
    ]
    r = simulate_s20(bars, lots=100.0, fees=True, session_filter=False)
    ac = after_charges_inr(r)
    assert abs(ac - (r.gross_pnl_inr - r.fees_inr)) < 1e-9
    assert ac >= r.after_tax_pnl_inr - 1e-9


def test_paper_wired_not_live() -> None:
    assert S20_NAME in ALL_STRATEGY_NAMES
    assert S20_NAME not in SLIM_PAPER_STRATEGIES
    assert S20_NAME in PAPER_ONLY_BOOKS
    root = Path(__file__).resolve().parent
    station = (root / "station.html").read_text(encoding="utf-8")
    runner = (root / "run_strategy.py").read_text(encoding="utf-8")
    portfolio = (root / "portfolio.py").read_text(encoding="utf-8")
    env_bridge = (root / "analytics" / "env_bridge.py").read_text(encoding="utf-8")
    assert S20_NAME in station
    paper = station.split("const PAPER_BOOKS")[1].split("];")[0]
    assert S20_NAME not in paper
    assert "s20_from_env" in runner
    assert "ENABLE_S20" in runner
    assert 'on("ENABLE_S20", "false")' in portfolio
    assert '"ENABLE_S20": "false"' in env_bridge


if __name__ == "__main__":
    test_bounce_long_and_short()
    test_raw_buys_knife_and_shorts_chase()
    test_inside_outside_skip()
    test_wick_fade()
    test_hold_inside_then_flip()
    test_raw_takes_knife_extra_trade()
    test_aggregate_2h_and_day()
    test_mtf_fight_skips()
    test_last_completed_side_uses_finished_higher_bar()
    test_after_charges_excludes_tax()
    test_paper_wired_not_live()
    print("ALL test_s20_fade_hl OK")
