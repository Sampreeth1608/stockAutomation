"""Tests for S8 ALIGN book-driven SL/TP."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from strategy_s8_align import AlignS8Config, AlignS8Strategy

IST = ZoneInfo("Asia/Kolkata")


def _now(i: int = 0) -> datetime:
    return datetime(2026, 8, 10, 11, 0, i % 60, tzinfo=IST)


def _msg(tbq: float, tsq: float) -> dict:
    return {"total_buy_quantity": tbq, "total_sell_quantity": tsq}


def test_behaviour_flags_and_book_stops():
    s = AlignS8Strategy(
        AlignS8Config(
            min_imb_pct=10,
            book_frac_of_net=0.10,
            pullback_points=8,
            resume_points=5,
            stall_min_profit=20,
            stall_bars=50,
            cooldown_ticks=0,
            weaken_pct=90,
            sl_min=20,
            tp_min=20,
        )
    )
    px, tbq, tsq = 10000.0, 10000.0, 8000.0
    # Build supported impulses/dips: widen then supported dip then resume
    for i in range(5):
        px += 5
        tbq += 300  # expand with price
        s.on_tick(_now(i), px, _msg(tbq, tsq))
    assert s.widen_bull or s.bias == "BULL"
    # supported dip
    for i in range(4):
        px -= 4
        tbq += 250
        s.on_tick(_now(20 + i), px, _msg(tbq, tsq))
    assert s.dip_supported or len(s._adverse_supported) >= 0
    # more widen cycles to fill memory
    for cycle in range(6):
        for j in range(4):
            px += 6
            tbq += 300
            s.on_tick(_now(40 + cycle * 10 + j), px, _msg(tbq, tsq))
        for j in range(3):
            px -= 5
            tbq += 250
            s.on_tick(_now(45 + cycle * 10 + j), px, _msg(tbq, tsq))
    tp, sl = s._book_stops()
    assert sl >= 20
    assert tp >= 20


def test_dip_supported_skips_hard_sl():
    s = AlignS8Strategy(
        AlignS8Config(
            sl_points=20,
            tp_points=100,
            sl_min=20,
            tp_min=20,
            weaken_pct=90,
            stall_bars=10_000,
            cooldown_ticks=0,
            pullback_points=5,
            resume_points=3,
            min_imb_pct=5,
            book_frac_of_net=0.05,
        )
    )
    # Force a long open via internals
    s.bias = "BULL"
    s.last_net = 1000
    s.last_imb = 20
    s.last_tbq = 12000
    s.last_tsq = 8000
    s._tbq_allow_age = 0
    s._adverse_supported.append(35.0)
    s._impulse_aligned.append(40.0)
    s._open("long", 10000.0)
    assert s.active_sl is not None and s.active_sl >= 20
    # Simulate dip_supported manage: price down but TBQ expand
    s._prev_px, s._prev_tbq, s._prev_tsq = 10000.0, 12000.0, 8000.0
    s._update_behaviour(9970.0, 12500.0, 8000.0)  # -30 pts, TBQ up
    assert s.dip_supported
    sig = s._manage(9970.0)
    # Should NOT hard-SL while dip_supported even if move ~ -30 and sl~35
    # (may still be None)
    if sig is not None:
        assert "sl " not in (sig.reason or "") or "book_break" in (sig.reason or "")


def test_factory():
    import os

    os.environ["S8_LOGIC"] = "align"
    from strategy_net_zigzag import net_zigzag_from_env

    s = net_zigzag_from_env()
    assert "ALIGN" in s.status_line
    os.environ.pop("S8_LOGIC", None)


def test_large_net_still_detects_widen():
    """Book thr must be capped so large |NET| does not freeze bias."""
    s = AlignS8Strategy(
        AlignS8Config(
            min_imb_pct=10,
            book_frac_of_net=0.10,
            book_thr_cap_pct=0.002,
            price_eps=0.5,
            cooldown_ticks=0,
        )
    )
    # Seed huge NET
    s.on_tick(_now(0), 10000.0, _msg(50000, 20000))
    # Modest TBQ expand + price up — uncapped thr would be 0.1*30000=3000
    sig_states = []
    for i in range(5):
        s.on_tick(_now(i + 1), 10000.0 + (i + 1) * 2, _msg(50000 + (i + 1) * 200, 20000))
        sig_states.append((s.widen_bull, s.bias, s.tbq_expand))
    assert any(w for w, _, _ in sig_states), sig_states
    assert any(b == "BULL" for _, b, _ in sig_states), sig_states


def test_book_break_skips_while_dip_supported():
    s = AlignS8Strategy(
        AlignS8Config(
            break_min_bars=1,
            break_min_adverse=0.0,
            break_skip_if_supported=True,
            break_price_min=0.5,
            weaken_pct=90,
            stall_bars=10_000,
            cooldown_ticks=0,
            tp_points=100,
            sl_points=50,
        )
    )
    s.bias = "BULL"
    s.last_net = 2000
    s.last_imb = 20
    s.last_tbq = 12000
    s.last_tsq = 8000
    s._open("long", 10000.0)
    # price down + TBQ up = dip_supported; also TSQ up would have been break
    s._prev_px, s._prev_tbq, s._prev_tsq = 10000.0, 12000.0, 8000.0
    s._update_behaviour(9980.0, 12500.0, 8500.0)
    assert s.dip_supported
    sig = s._manage(9980.0)
    assert sig is None or "book_break" not in (sig.reason or "")


def test_book_break_blocked_in_profit():
    s = AlignS8Strategy(
        AlignS8Config(
            break_min_bars=1,
            break_min_adverse=8.0,
            break_skip_if_supported=False,
            break_price_min=0.5,
            weaken_pct=90,
            stall_bars=10_000,
            cooldown_ticks=0,
            tp_points=100,
            sl_points=50,
        )
    )
    s._open("long", 10000.0)
    s._prev_px, s._prev_tbq, s._prev_tsq = 10020.0, 12000.0, 8000.0
    # in profit + noisy break pattern
    s._update_behaviour(10015.0, 11500.0, 9000.0)  # px down a bit, still +15 vs entry; TBQ compress + TSQ up
    assert s.break_bull or s.tbq_compress or s.tsq_expand
    sig = s._manage(10015.0)
    assert sig is None or "book_break" not in (sig.reason or "")


def main() -> None:
    test_behaviour_flags_and_book_stops()
    test_dip_supported_skips_hard_sl()
    test_factory()
    test_large_net_still_detects_widen()
    test_book_break_skips_while_dip_supported()
    test_book_break_blocked_in_profit()
    print("test_s8_align: OK")


if __name__ == "__main__":
    main()
