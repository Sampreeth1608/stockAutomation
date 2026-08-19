"""S16 + same-hour TBQ/TSQ strength. Research overlay. Not a paper rewrite."""

from __future__ import annotations

from pathlib import Path

from control_state import ALL_STRATEGY_NAMES, SLIM_PAPER_STRATEGIES
from live_readiness import PAPER_ONLY_BOOKS
from s16_hhhl_wick import simulate_s16
from s16_tbq_net import (
    LAB_NAME,
    MIXED,
    STRONG_DOWN,
    STRONG_UP,
    WEAK_DOWN,
    WEAK_UP,
    flow_kind,
    gate_s16_want,
    overlay_want,
    simulate_s16_tbq_net,
    volbar,
)


def test_flow_kind_matches_user_sheet() -> None:
    assert flow_kind(volbar("t", 100, 110, 100, 108, tbq=2000, tsq=800)) == STRONG_UP
    assert flow_kind(volbar("t", 100, 110, 100, 108, tbq=800, tsq=2000)) == WEAK_UP
    assert flow_kind(volbar("t", 108, 108, 90, 95, tbq=800, tsq=2000)) == STRONG_DOWN
    assert flow_kind(volbar("t", 108, 108, 90, 95, tbq=2000, tsq=800)) == WEAK_DOWN
    assert flow_kind(volbar("t", 100, 110, 90, 100, tbq=2000, tsq=800)) == MIXED
    assert flow_kind(volbar("t", 100, 110, 100, 108, tbq=100, tsq=100)) == MIXED


def test_gate_blocks_weak_and_allows_strong() -> None:
    assert gate_s16_want("long", STRONG_UP) == "long"
    assert gate_s16_want("long", WEAK_UP) is None
    assert gate_s16_want("long", STRONG_DOWN) is None
    assert gate_s16_want("short", STRONG_DOWN) == "short"
    assert gate_s16_want("short", WEAK_DOWN) is None
    assert gate_s16_want("short", STRONG_UP) is None


def test_overlay_want_fade_reverses_weak_hours() -> None:
    assert overlay_want("long", WEAK_UP, weak="fade") == "short"
    assert overlay_want("short", WEAK_DOWN, weak="fade") == "long"
    assert overlay_want(None, WEAK_UP, weak="fade") == "short"
    assert overlay_want(None, WEAK_DOWN, weak="fade") == "long"
    assert overlay_want("long", STRONG_UP, weak="fade") == "long"
    assert overlay_want("short", STRONG_DOWN, weak="fade") == "short"
    assert overlay_want("long", WEAK_UP, weak="drop") is None
    assert overlay_want("short", WEAK_DOWN, weak="drop") is None


def test_weak_up_does_not_enter_s16_long() -> None:
    """HH+green would be S16 long, but TBQ<TSQ is a weak up → skip."""
    bars = [
        volbar("2026-08-17 09:00:00", 100, 105, 99, 104, tbq=2000, tsq=800),
        volbar("2026-08-17 10:00:00", 100, 120, 100, 110, tbq=500, tsq=2000),
        volbar("2026-08-17 11:00:00", 110, 111, 109, 110.5, tbq=2000, tsq=800),
    ]
    candles = [
        __import__("backtest_hhhl_candles", fromlist=["Candle"]).Candle(
            b.time, b.open, b.high, b.low, b.close
        )
        for b in bars
    ]
    plain = simulate_s16(
        candles, tf="1h:s16", lots=1, fees=False, session_filter=False, min_wick_gap=0
    )
    overlay = simulate_s16_tbq_net(
        bars, tf="1h:s16_tbq", lots=1, fees=False, session_filter=False, min_wick_gap=0
    )
    assert plain.n_trades >= 1
    assert plain.trades[0].side == "LONG"
    assert overlay.n_trades == 0


def test_strong_up_keeps_s16_long() -> None:
    bars = [
        volbar("2026-08-17 09:00:00", 100, 105, 99, 104, tbq=2000, tsq=800),
        volbar("2026-08-17 10:00:00", 100, 120, 100, 110, tbq=2000, tsq=500),
        volbar("2026-08-17 11:00:00", 110, 111, 109, 110.5, tbq=2000, tsq=500),
    ]
    overlay = simulate_s16_tbq_net(
        bars, tf="1h:s16_tbq", lots=1, fees=False, session_filter=False, min_wick_gap=0
    )
    assert overlay.n_trades == 1
    assert overlay.trades[0].side == "LONG"
    assert overlay.trades[0].entry_px == 110.0


def test_weak_up_exits_open_long() -> None:
    bars = [
        volbar("2026-08-17 09:00:00", 100, 105, 99, 104, tbq=2000, tsq=800),
        volbar("2026-08-17 10:00:00", 100, 120, 100, 110, tbq=2000, tsq=500),
        volbar("2026-08-17 11:00:00", 110, 125, 110, 120, tbq=400, tsq=2000),
        volbar("2026-08-17 12:00:00", 120, 121, 119, 120.5, tbq=2000, tsq=500),
    ]
    overlay = simulate_s16_tbq_net(
        bars, tf="1h:s16_tbq", lots=1, fees=False, session_filter=False, min_wick_gap=0
    )
    assert overlay.n_trades == 1
    assert overlay.trades[0].side == "LONG"
    assert overlay.trades[0].entry_time == "2026-08-17 10:00:00"
    assert overlay.trades[0].exit_time == "2026-08-17 11:00:00"
    assert overlay.trades[0].exit_px == 120.0


def test_strong_down_allows_s16_short_flip() -> None:
    bars = [
        volbar("2026-08-17 09:00:00", 100, 105, 99, 104, tbq=2000, tsq=800),
        volbar("2026-08-17 10:00:00", 100, 120, 100, 110, tbq=2000, tsq=500),
        volbar("2026-08-17 11:00:00", 110, 140, 70, 80, tbq=400, tsq=2000),
        volbar("2026-08-17 12:00:00", 80, 81, 79, 80.5, tbq=400, tsq=2000),
    ]
    overlay = simulate_s16_tbq_net(
        bars, tf="1h:s16_tbq", lots=1, fees=False, session_filter=False, min_wick_gap=0
    )
    assert overlay.n_trades == 2
    assert overlay.trades[0].side == "LONG"
    assert overlay.trades[1].side == "SHORT"
    assert overlay.trades[1].entry_px == 80.0


def test_weak_down_does_not_enter_short() -> None:
    bars = [
        volbar("2026-08-17 09:00:00", 110, 120, 100, 115, tbq=2000, tsq=800),
        volbar("2026-08-17 10:00:00", 115, 140, 70, 80, tbq=2000, tsq=400),
        volbar("2026-08-17 11:00:00", 80, 81, 79, 80.5, tbq=2000, tsq=400),
    ]
    overlay = simulate_s16_tbq_net(
        bars, tf="1h:s16_tbq", lots=1, fees=False, session_filter=False, min_wick_gap=0
    )
    assert overlay.n_trades == 0


def test_fade_weak_up_enters_short() -> None:
    """HH+green would be S16 long; weak up fades to SHORT."""
    bars = [
        volbar("2026-08-17 09:00:00", 100, 105, 99, 104, tbq=2000, tsq=800),
        volbar("2026-08-17 10:00:00", 100, 120, 100, 110, tbq=500, tsq=2000),
        volbar("2026-08-17 11:00:00", 110, 111, 109, 110.5, tbq=2000, tsq=800),
    ]
    overlay = simulate_s16_tbq_net(
        bars,
        tf="1h:s16_tbq_fade",
        lots=1,
        fees=False,
        session_filter=False,
        min_wick_gap=0,
        weak="fade",
    )
    assert overlay.n_trades == 1
    assert overlay.trades[0].side == "SHORT"
    assert overlay.trades[0].entry_px == 110.0


def test_fade_weak_down_enters_long() -> None:
    """Red hour with TBQ>TSQ is a fake dump → BUY."""
    bars = [
        volbar("2026-08-17 09:00:00", 110, 120, 100, 115, tbq=2000, tsq=800),
        volbar("2026-08-17 10:00:00", 115, 140, 70, 80, tbq=2000, tsq=400),
        volbar("2026-08-17 11:00:00", 80, 81, 79, 80.5, tbq=2000, tsq=400),
    ]
    overlay = simulate_s16_tbq_net(
        bars,
        tf="1h:s16_tbq_fade",
        lots=1,
        fees=False,
        session_filter=False,
        min_wick_gap=0,
        weak="fade",
    )
    assert overlay.n_trades == 1
    assert overlay.trades[0].side == "LONG"
    assert overlay.trades[0].entry_px == 80.0


def test_fade_weak_up_flips_long_to_short() -> None:
    bars = [
        volbar("2026-08-17 09:00:00", 100, 105, 99, 104, tbq=2000, tsq=800),
        volbar("2026-08-17 10:00:00", 100, 120, 100, 110, tbq=2000, tsq=500),
        volbar("2026-08-17 11:00:00", 110, 125, 110, 120, tbq=400, tsq=2000),
        volbar("2026-08-17 12:00:00", 120, 121, 119, 120.5, tbq=2000, tsq=500),
    ]
    overlay = simulate_s16_tbq_net(
        bars,
        tf="1h:s16_tbq_fade",
        lots=1,
        fees=False,
        session_filter=False,
        min_wick_gap=0,
        weak="fade",
    )
    assert overlay.n_trades == 2
    assert overlay.trades[0].side == "LONG"
    assert overlay.trades[0].entry_time == "2026-08-17 10:00:00"
    assert overlay.trades[0].exit_time == "2026-08-17 11:00:00"
    assert overlay.trades[0].exit_px == 120.0
    assert overlay.trades[1].side == "SHORT"
    assert overlay.trades[1].entry_time == "2026-08-17 11:00:00"
    assert overlay.trades[1].entry_px == 120.0


def test_fade_weak_down_flips_short_to_long() -> None:
    bars = [
        volbar("2026-08-17 09:00:00", 110, 120, 100, 115, tbq=2000, tsq=800),
        volbar("2026-08-17 10:00:00", 115, 140, 70, 80, tbq=400, tsq=2000),
        volbar("2026-08-17 11:00:00", 80, 85, 70, 72, tbq=2000, tsq=400),
        volbar("2026-08-17 12:00:00", 72, 73, 71, 72.5, tbq=2000, tsq=400),
    ]
    overlay = simulate_s16_tbq_net(
        bars,
        tf="1h:s16_tbq_fade",
        lots=1,
        fees=False,
        session_filter=False,
        min_wick_gap=0,
        weak="fade",
    )
    assert overlay.n_trades == 2
    assert overlay.trades[0].side == "SHORT"
    assert overlay.trades[0].entry_px == 80.0
    assert overlay.trades[0].exit_px == 72.0
    assert overlay.trades[1].side == "LONG"
    assert overlay.trades[1].entry_px == 72.0


def test_not_wired_to_paper_or_live() -> None:
    assert LAB_NAME not in ALL_STRATEGY_NAMES
    assert LAB_NAME not in SLIM_PAPER_STRATEGIES
    assert LAB_NAME not in PAPER_ONLY_BOOKS
    root = Path(__file__).resolve().parent
    station = (root / "station.html").read_text(encoding="utf-8")
    paper = station.split("const PAPER_BOOKS")[1].split("];")[0]
    s16 = (root / "strategy_s16.py").read_text(encoding="utf-8")
    runner = (root / "run_strategy.py").read_text(encoding="utf-8")
    assert "S16_TBQ_NET" not in paper
    assert "s16_tbq_net" not in runner
    assert "flow_kind" not in s16
    assert "ENABLE_S16" in (root / "portfolio.py").read_text(encoding="utf-8")


if __name__ == "__main__":
    test_flow_kind_matches_user_sheet()
    test_gate_blocks_weak_and_allows_strong()
    test_overlay_want_fade_reverses_weak_hours()
    test_weak_up_does_not_enter_s16_long()
    test_strong_up_keeps_s16_long()
    test_weak_up_exits_open_long()
    test_strong_down_allows_s16_short_flip()
    test_weak_down_does_not_enter_short()
    test_fade_weak_up_enters_short()
    test_fade_weak_down_enters_long()
    test_fade_weak_up_flips_long_to_short()
    test_fade_weak_down_flips_short_to_long()
    test_not_wired_to_paper_or_live()
    print("s16_tbq_net tests ok")
