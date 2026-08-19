"""Nested inner-candle net → next-timeframe bias. Research only. Not live."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from control_state import ALL_STRATEGY_NAMES, SLIM_PAPER_STRATEGIES
from flow_lab import FlowBar, flow_bars_from_tick_rows
from live_readiness import PAPER_ONLY_BOOKS
from nested_tf_net import (
    LAB_NAME,
    PARENTS,
    collect_inners,
    enough_inners,
    expected_inner_count,
    inner_minutes_for,
    score_nested,
    simulate_all,
    simulate_nested,
    tf_label,
)

IST = ZoneInfo("Asia/Kolkata")


def _b(
    t: str,
    o: float,
    h: float,
    l: float,
    c: float,
    *,
    tbq: float = 0.0,
    tsq: float = 0.0,
) -> FlowBar:
    return FlowBar(t, o, h, l, c, 100.0, 2.0, tbq, tsq)


def _tick(t: datetime, px: float, tbq: float, tsq: float, vol: float) -> dict:
    return {
        "received_at": t.isoformat(),
        "ltp": px,
        "bp": tbq,
        "sp": tsq,
        "volume": vol,
        "raw_json": "",
        "token": "GOLDPETAL",
        "symbol": "GOLDPETAL",
    }


def test_inner_rungs_match_user_stack() -> None:
    assert inner_minutes_for(15) == (1, 3, 5)
    assert inner_minutes_for(30) == (1, 3, 5, 15)
    assert inner_minutes_for(45) == (1, 3, 5, 15)
    assert inner_minutes_for(60) == (1, 3, 5, 15, 30)
    assert inner_minutes_for(75) == (1, 3, 5, 15)
    assert inner_minutes_for(120) == (1, 3, 5, 15, 30, 60)
    assert inner_minutes_for(180) == (1, 3, 5, 15, 30, 45, 60)
    assert inner_minutes_for(1440) == (1, 3, 5, 15, 30, 45, 60, 75, 120, 180)
    assert expected_inner_count(15, 1) == 15
    assert expected_inner_count(15, 3) == 5
    assert expected_inner_count(15, 5) == 3
    assert [p[0] for p in PARENTS] == [
        "15m",
        "30m",
        "45m",
        "1h",
        "1h15",
        "2h",
        "3h",
        "1d",
    ]


def _window_bars() -> tuple[list[FlowBar], dict[int, list[FlowBar]]]:
    """Bullish 10:15–10:30 then continuation 10:30–10:45."""
    parents = [
        _b("2026-08-10 10:15:00", 100.0, 116.0, 100.0, 115.0, tbq=2000, tsq=800),
        _b("2026-08-10 10:30:00", 115.0, 131.0, 115.0, 130.0, tbq=2000, tsq=800),
        _b("2026-08-10 10:45:00", 130.0, 130.0, 110.0, 112.0, tbq=700, tsq=1800),
    ]
    ones: list[FlowBar] = []
    threes: list[FlowBar] = []
    fives: list[FlowBar] = []
    t0 = datetime(2026, 8, 10, 10, 15, 0)
    for i in range(15):
        ts = t0 + timedelta(minutes=i)
        o = 100.0 + i
        c = o + 1.0
        ones.append(
            _b(
                ts.strftime("%Y-%m-%d %H:%M:%S"),
                o,
                c,
                o,
                c,
                tbq=2000,
                tsq=800,
            )
        )
    for i in range(5):
        ts = t0 + timedelta(minutes=3 * i)
        o = 100.0 + 3 * i
        c = o + 3.0
        threes.append(
            _b(ts.strftime("%Y-%m-%d %H:%M:%S"), o, c, o, c, tbq=2000, tsq=800)
        )
    for i in range(3):
        ts = t0 + timedelta(minutes=5 * i)
        o = 100.0 + 5 * i
        c = o + 5.0
        fives.append(
            _b(ts.strftime("%Y-%m-%d %H:%M:%S"), o, c, o, c, tbq=2000, tsq=800)
        )
    t1 = datetime(2026, 8, 10, 10, 30, 0)
    for i in range(15):
        ts = t1 + timedelta(minutes=i)
        o = 115.0 + i
        c = o + 1.0
        ones.append(
            _b(ts.strftime("%Y-%m-%d %H:%M:%S"), o, c, o, c, tbq=2000, tsq=800)
        )
    bars_by_min = {1: ones, 3: threes, 5: fives, 15: parents}
    return parents, bars_by_min


def test_15m_1015_has_15_1m_5_3m_3_5m() -> None:
    parents, bars_by_min = _window_bars()
    collected = collect_inners(parents[0], 15, bars_by_min)
    assert collected["1m"] and len(collected["1m"]) == 15
    assert len(collected["3m"]) == 5
    assert len(collected["5m"]) == 3
    assert enough_inners(15, collected)
    net = score_nested(
        collected, parent="15m", parent_time=parents[0].time, mode="sum"
    )
    assert net.body_net > 0
    assert net.book_net > 0
    assert net.bias == 1
    assert net.side == "LONG"
    assert net.counts == {"1m": 15, "3m": 5, "5m": 3}


def test_bullish_inner_net_goes_long_next_15m() -> None:
    parents, bars_by_min = _window_bars()
    r = simulate_nested(
        parents, bars_by_min, tf="15m", parent_min=15, mode="sum", fees=False
    )
    assert r.n_trades >= 1
    t0 = r.trades[0]
    assert t0.side == "LONG"
    assert t0.entry_time == "2026-08-10 10:15:00"
    assert t0.exit_time == "2026-08-10 10:30:00"
    assert t0.entry_px == 115.0
    assert t0.exit_px == 130.0
    assert t0.gross_pnl_inr > 0


def test_bearish_inner_net_goes_short_next() -> None:
    parents = [
        _b("2026-08-10 10:15:00", 130.0, 130.0, 114.0, 115.0, tbq=500, tsq=2000),
        _b("2026-08-10 10:30:00", 115.0, 115.0, 99.0, 100.0, tbq=500, tsq=2000),
    ]
    t0 = datetime(2026, 8, 10, 10, 15, 0)
    ones = []
    threes = []
    fives = []
    for i in range(15):
        ts = t0 + timedelta(minutes=i)
        o = 130.0 - i
        c = o - 1.0
        ones.append(
            _b(ts.strftime("%Y-%m-%d %H:%M:%S"), o, o, c, c, tbq=500, tsq=2000)
        )
    for i in range(5):
        ts = t0 + timedelta(minutes=3 * i)
        o = 130.0 - 3 * i
        c = o - 3.0
        threes.append(
            _b(ts.strftime("%Y-%m-%d %H:%M:%S"), o, o, c, c, tbq=500, tsq=2000)
        )
    for i in range(3):
        ts = t0 + timedelta(minutes=5 * i)
        o = 130.0 - 5 * i
        c = o - 5.0
        fives.append(
            _b(ts.strftime("%Y-%m-%d %H:%M:%S"), o, o, c, c, tbq=500, tsq=2000)
        )
    r = simulate_nested(
        parents,
        {1: ones, 3: threes, 5: fives, 15: parents},
        tf="15m",
        parent_min=15,
        mode="sum",
        fees=False,
    )
    assert r.n_trades == 1
    assert r.trades[0].side == "SHORT"
    assert r.trades[0].gross_pnl_inr > 0


def test_skip_overnight_intraday() -> None:
    parents = [
        _b("2026-08-10 23:15:00", 100.0, 110.0, 100.0, 109.0, tbq=2000, tsq=100),
        _b("2026-08-11 09:00:00", 109.0, 120.0, 109.0, 118.0, tbq=2000, tsq=100),
    ]
    t0 = datetime(2026, 8, 10, 23, 15, 0)
    ones = [
        _b(
            (t0 + timedelta(minutes=i)).strftime("%Y-%m-%d %H:%M:%S"),
            100.0 + i,
            101.0 + i,
            100.0 + i,
            101.0 + i,
            tbq=2000,
            tsq=100,
        )
        for i in range(15)
    ]
    threes = [
        _b(
            (t0 + timedelta(minutes=3 * i)).strftime("%Y-%m-%d %H:%M:%S"),
            100.0,
            103.0,
            100.0,
            103.0,
            tbq=2000,
            tsq=100,
        )
        for i in range(5)
    ]
    fives = [
        _b(
            (t0 + timedelta(minutes=5 * i)).strftime("%Y-%m-%d %H:%M:%S"),
            100.0,
            105.0,
            100.0,
            105.0,
            tbq=2000,
            tsq=100,
        )
        for i in range(3)
    ]
    r = simulate_nested(
        parents,
        {1: ones, 3: threes, 5: fives, 15: parents},
        tf="15m",
        parent_min=15,
        mode="sum",
        fees=False,
    )
    assert r.n_trades == 0


def test_zero_net_skips() -> None:
    parents = [
        _b("2026-08-10 10:15:00", 100.0, 100.0, 100.0, 100.0),
        _b("2026-08-10 10:30:00", 100.0, 110.0, 100.0, 110.0),
    ]
    t0 = datetime(2026, 8, 10, 10, 15, 0)
    dojis = [
        _b(
            (t0 + timedelta(minutes=i)).strftime("%Y-%m-%d %H:%M:%S"),
            100.0,
            100.0,
            100.0,
            100.0,
        )
        for i in range(15)
    ]
    threes = [
        _b(
            (t0 + timedelta(minutes=3 * i)).strftime("%Y-%m-%d %H:%M:%S"),
            100.0,
            100.0,
            100.0,
            100.0,
        )
        for i in range(5)
    ]
    fives = [
        _b(
            (t0 + timedelta(minutes=5 * i)).strftime("%Y-%m-%d %H:%M:%S"),
            100.0,
            100.0,
            100.0,
            100.0,
        )
        for i in range(3)
    ]
    r = simulate_nested(
        parents,
        {1: dojis, 3: threes, 5: fives, 15: parents},
        tf="15m",
        parent_min=15,
        mode="sum",
        fees=False,
    )
    assert r.n_trades == 0


def test_vote_uses_inner_direction_count() -> None:
    """14 tiny greens then one huge red: body sum is red, vote is still green."""
    t0 = datetime(2026, 8, 10, 10, 15, 0)
    ones: list[FlowBar] = []
    for i in range(14):
        ts = t0 + timedelta(minutes=i)
        ones.append(
            _b(
                ts.strftime("%Y-%m-%d %H:%M:%S"),
                100.0 + i,
                101.0 + i,
                100.0 + i,
                101.0 + i,
                tbq=100,
                tsq=100,
            )
        )
    ones.append(
        _b("2026-08-10 10:29:00", 114.0, 114.0, 90.0, 90.0, tbq=100, tsq=100)
    )
    collected = {"1m": ones, "3m": [], "5m": []}
    vote = score_nested(
        collected, parent="15m", parent_time="2026-08-10 10:15:00", mode="vote"
    )
    body = score_nested(
        collected, parent="15m", parent_time="2026-08-10 10:15:00", mode="sum"
    )
    assert vote.vote_body == 13
    assert vote.bias == 1
    assert body.body_net < 0
    assert body.bias == -1


def test_ticks_build_session_aligned_15m_inners() -> None:
    start = datetime(2026, 8, 10, 10, 15, 0, tzinfo=IST)
    rows = []
    px = 10000.0
    vol = 1.0
    for i in range(30):
        t = start + timedelta(minutes=i)
        tbq, tsq = (2000.0, 800.0) if i < 15 else (800.0, 2000.0)
        step = 1.0 if i < 15 else -1.0
        rows.append(_tick(t, px, tbq, tsq, vol))
        vol += 1
        px += step
        rows.append(_tick(t + timedelta(seconds=45), px, tbq, tsq, vol))
        vol += 1
    ones = flow_bars_from_tick_rows(
        rows, 1, session_align=True, session_ticks=True, split_token=True
    )
    threes = flow_bars_from_tick_rows(
        rows, 3, session_align=True, session_ticks=True, split_token=True
    )
    fives = flow_bars_from_tick_rows(
        rows, 5, session_align=True, session_ticks=True, split_token=True
    )
    fifteens = flow_bars_from_tick_rows(
        rows, 15, session_align=True, session_ticks=True, split_token=True
    )
    parent = next(b for b in fifteens if b.time.endswith("10:15:00"))
    collected = collect_inners(
        parent, 15, {1: ones, 3: threes, 5: fives, 15: fifteens}
    )
    assert len(collected["1m"]) == 15
    assert len(collected["3m"]) == 5
    assert len(collected["5m"]) == 3
    net = score_nested(
        collected, parent="15m", parent_time=parent.time, mode="sum"
    )
    assert net.bias == 1


def test_simulate_all_modes_on_synthetic_parents() -> None:
    parents, bars_by_min = _window_bars()
    results = simulate_all(
        bars_by_min,
        lots=1.0,
        fees=False,
        modes=("sum",),
        parents=(("15m", 15),),
    )
    assert len(results) == 1
    assert results[0].n_trades >= 1


def test_not_wired_to_paper_or_live() -> None:
    assert LAB_NAME not in ALL_STRATEGY_NAMES
    assert LAB_NAME not in SLIM_PAPER_STRATEGIES
    assert LAB_NAME not in PAPER_ONLY_BOOKS
    root = Path(__file__).resolve().parent
    station = (root / "station.html").read_text(encoding="utf-8")
    paper = station.split("const PAPER_BOOKS")[1].split("];")[0]
    portfolio = (root / "portfolio.py").read_text(encoding="utf-8")
    runner = (root / "run_strategy.py").read_text(encoding="utf-8")
    assert "NESTED_TF_NET" not in paper
    assert "ENABLE_NESTED" not in portfolio
    assert "nested_tf_net" not in runner
    assert tf_label(75) == "1h15"


if __name__ == "__main__":
    test_inner_rungs_match_user_stack()
    test_15m_1015_has_15_1m_5_3m_3_5m()
    test_bullish_inner_net_goes_long_next_15m()
    test_bearish_inner_net_goes_short_next()
    test_skip_overnight_intraday()
    test_zero_net_skips()
    test_vote_uses_inner_direction_count()
    test_ticks_build_session_aligned_15m_inners()
    test_simulate_all_modes_on_synthetic_parents()
    test_not_wired_to_paper_or_live()
    print("nested_tf_net tests ok")
