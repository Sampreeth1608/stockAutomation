"""Tests for S8_ALIGN (price ∩ TBQ/TSQ, range SL, pullback entry)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from strategy_s8_align import AlignS8Config, AlignS8Strategy

IST = ZoneInfo("Asia/Kolkata")


def _now(i: int = 0) -> datetime:
    return datetime(2026, 8, 10, 11, 0, i % 60, tzinfo=IST)


def _msg(tbq: float, tsq: float) -> dict:
    return {"total_buy_quantity": tbq, "total_sell_quantity": tsq}


def test_bull_align_pullback_entry_and_range_sl():
    s = AlignS8Strategy(
        AlignS8Config(
            min_imb_pct=10,
            book_frac_of_net=0.10,
            pullback_points=8,
            resume_points=5,
            range_tick_window=5,
            range_lookback=5,
            use_range_stops=True,
            stall_ticks=10_000,
            cooldown_ticks=0,
            weaken_pct=90,
        )
    )
    # Seed mini-ranges ~40 pts so SL is scientific (~36) not 25
    px = 10000.0
    tbq, tsq = 10000.0, 8000.0
    for i in range(40):
        # swing within segment
        p = px + (8 if i % 2 == 0 else -8)
        s.on_tick(_now(i), p, _msg(tbq, tsq))
    # Drive bull align: price up + TBQ up big vs |NET|
    # NET=2000 → need ΔTBQ >= 200
    for i in range(5):
        px += 3
        tbq += 250
        s.on_tick(_now(50 + i), px, _msg(tbq, tsq))
    assert s.bias == "BULL"
    # Pullback 8+ pts then resume 5+
    peak = px
    for i in range(4):
        px -= 3  # -12 from peak
        s.on_tick(_now(60 + i), px, _msg(tbq, tsq))
    assert s._in_pullback
    # Resume with TBQ still allowing
    for i in range(3):
        px += 3
        tbq += 250
        sig = s.on_tick(_now(70 + i), px, _msg(tbq, tsq))
        if sig and sig.action == "BUY":
            break
    assert s.position == "long"
    assert s.active_sl is not None
    # Scientific SL should reflect ~period range, not fixed 25
    assert s.active_sl >= 15


def test_fixed25_unscientific_vs_range():
    """If period swings ~40, SL should be wider than a naive 25."""
    s = AlignS8Strategy(
        AlignS8Config(
            range_tick_window=10,
            range_lookback=8,
            sl_range_mult=0.90,
            sl_min=15,
            sl_max=50,
            use_range_stops=True,
        )
    )
    # Build clear 40-pt mini-ranges
    for seg in range(12):
        base = 10000.0
        for j in range(10):
            p = base + (40 if j >= 5 else 0)
            s.on_tick(_now(seg * 10 + j), p, _msg(12000, 8000))
    s._set_stops_from_range()
    assert s.last_exp_range is not None and s.last_exp_range >= 30
    assert s.active_sl is not None and s.active_sl > 25


def test_factory_align_default(monkeypatch=None):
    import os

    os.environ["S8_LOGIC"] = "align"
    from strategy_net_zigzag import net_zigzag_from_env

    s = net_zigzag_from_env()
    assert "ALIGN" in s.status_line
    os.environ.pop("S8_LOGIC", None)


def main() -> None:
    test_bull_align_pullback_entry_and_range_sl()
    test_fixed25_unscientific_vs_range()
    test_factory_align_default()
    print("test_s8_align: OK")


if __name__ == "__main__":
    main()
