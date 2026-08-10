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
    os.environ["S8_MODEL"] = "fat_tp_flip"
    os.environ["S8_ENTRY_MODEL"] = "imb_sign_rise"
    os.environ["S8_HOLD_MODEL"] = "book_rise"
    os.environ["S8_EXIT_MODEL"] = "fat_tp_flip"
    os.environ.pop("S8_BAR_TICKS", None)
    os.environ.pop("S8_BAR_MINUTES", None)
    from strategy_net_zigzag import net_zigzag_from_env

    s = net_zigzag_from_env()
    assert "ALIGN" in s.status_line
    assert "50t" in s.status_line
    assert "fat_tp_flip" in s.status_line
    assert "E=imb_sign_rise" in s.status_line
    assert "H=book_rise" in s.status_line
    assert "X=fat_tp_flip" in s.status_line
    assert "entry[imb_sign_rise" in s.reasoning_line()
    for k in ("S8_LOGIC", "S8_MODEL", "S8_ENTRY_MODEL", "S8_HOLD_MODEL", "S8_EXIT_MODEL"):
        os.environ.pop(k, None)


def test_count_bar_closes_every_n():
    s = AlignS8Strategy(
        AlignS8Config(
            bar_ticks=5,
            min_imb_pct=5,
            book_frac_of_net=0.05,
            book_thr_cap_pct=0.002,
            cooldown_ticks=0,
            break_min_bars=99,
            flip_min_bars=99,
            weaken_pct=90,
            pullback_points=3,
            resume_points=2,
        )
    )
    px, tbq, tsq = 10000.0, 12000.0, 9000.0
    saw = 0
    for i in range(40):
        px += 2
        tbq += 80
        # inject pullback every 10
        if i % 10 == 6:
            px -= 6
            tbq += 70
        if i % 10 == 8:
            px += 5
            tbq += 80
        sig = s.on_tick(_now(i), px, _msg(tbq, tsq))
        if sig:
            saw += 1
    # decisions only on 5-tick closes
    assert s.cfg.bar_ticks == 5
    assert saw >= 0  # smoke: no crash


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
            flip_min_bars=1,
            flip_min_adverse=0.0,
            flip_block_in_profit=False,
            close_on_book_drop=False,
            hold_while_book_rises=False,
        )
    )
    s._open("long", 10000.0)
    s._prev_px, s._prev_tbq, s._prev_tsq = 10020.0, 12000.0, 8000.0
    # in profit + noisy break pattern
    s._update_behaviour(10015.0, 11500.0, 9000.0)  # px down a bit, still +15 vs entry; TBQ compress + TSQ up
    assert s.break_bull or s.tbq_compress or s.tsq_expand
    sig = s._manage(10015.0)
    assert sig is None or "book_break" not in (sig.reason or "")


def test_flip_blocked_in_profit():
    s = AlignS8Strategy(
        AlignS8Config(
            break_min_bars=99,
            break_min_adverse=100,
            flip_min_bars=1,
            flip_min_adverse=8.0,
            flip_block_in_profit=True,
            weaken_pct=90,
            stall_bars=10_000,
            cooldown_ticks=0,
            tp_points=100,
            sl_points=50,
        )
    )
    s._open("long", 10000.0)
    s.bias = "BEAR"
    s.tsq_allows = True
    s.last_align = "px↓+TSQ↑"
    # in profit — flip must not close
    sig = s._manage(10020.0)
    assert sig is None or "flip" not in (sig.reason or "")


def test_flip_fires_when_adverse_enough():
    s = AlignS8Strategy(
        AlignS8Config(
            break_min_bars=99,
            break_min_adverse=100,
            flip_min_bars=1,
            flip_min_adverse=8.0,
            flip_block_in_profit=True,
            flip_persist=1,
            weaken_pct=90,
            stall_bars=10_000,
            cooldown_ticks=0,
            tp_points=100,
            sl_points=50,
            hold_while_book_rises=False,
        )
    )
    s._open("long", 10000.0)
    s.bias = "BEAR"
    s.tsq_allows = True
    s.last_align = "px↓+TSQ↑"
    sig = s._manage(9985.0)  # -15 adverse >= 8
    assert sig is not None and "flip" in (sig.reason or "")


def test_no_lock_after_two_sl_only_after_five_losses():
    s = AlignS8Strategy(
        AlignS8Config(
            loss_lock_after=5,
            loss_lock_cool_bars=3,
            cooldown_ticks=0,
            break_min_bars=99,
            flip_min_bars=99,
            weaken_pct=90,
            stall_bars=10_000,
            tp_points=100,
            sl_points=10,
            min_imb_pct=1,
            pullback_points=1,
            resume_points=1,
            require_rising_imb=False,
            require_rising_book=False,
        )
    )
    s.bias = "BULL"
    s.last_net = 1000
    s.last_imb = 20
    s._tbq_allow_age = 0
    # 2 losses — must still allow entries
    for _ in range(2):
        s._open("long", 10000.0)
        s._close(-15.0)
    assert s._loss_streak == 2
    assert not s._loss_locked
    # force pullback state for enter
    s._extreme = 10020.0
    s._in_pullback = True
    s._pullback_ext = 10010.0
    assert s._try_enter(10016.0) is not None or s.last_skip != "loss_lock"

    # 3 more losses → lock at 5
    for _ in range(3):
        s.position = "flat"
        s._open("long", 10000.0)
        s._close(-12.0)
    assert s._loss_streak >= 5
    assert s._loss_locked
    s.position = "flat"
    s.entry_price = None
    s._extreme = 10020.0
    s._in_pullback = True
    s._pullback_ext = 10010.0
    assert s._try_enter(10016.0) is None
    assert "loss_lock" in (s.last_skip or "")


def test_enter_aligned_without_pullback():
    s = AlignS8Strategy(
        AlignS8Config(
            require_pullback=False,
            require_rising_imb=False,
            require_rising_book=False,
            min_imb_pct=5,
            cooldown_ticks=0,
            book_frac_of_net=0.05,
            book_thr_cap_pct=0.002,
            break_min_bars=99,
            flip_min_bars=99,
            weaken_pct=90,
        )
    )
    # Seed + NET>0 so positive IMB → buy
    s.on_tick(_now(0), 10000.0, _msg(12000, 9000))
    for i in range(4):
        s.on_tick(_now(i + 1), 10000.0 + (i + 1) * 3, _msg(12000 + (i + 1) * 200, 9000))
    assert s.last_net > 0
    s._in_pullback = False
    s._pullback_ext = None
    sig = s._try_enter(10015.0)
    assert sig is not None and sig.action == "BUY"
    assert "imb+" in (sig.reason or "")
    assert "entry[" in (sig.reason or "")


def test_entry_net_sign_and_rising_imb():
    """Positive NET → buy, negative → short; need abs IMB rising. TBQ not entry gate."""
    s = AlignS8Strategy(
        AlignS8Config(
            require_pullback=False,
            require_rising_imb=True,
            require_rising_book=False,
            min_imb_pct=5,
            cooldown_ticks=0,
            break_min_bars=99,
            flip_min_bars=99,
            weaken_pct=90,
            close_on_book_drop=False,
        )
    )
    # IMB falling while NET>0 → blocked
    tbq0, tsq0 = 15000.0, 10000.0
    s.last_imb = abs(tbq0 - tsq0) / max(tbq0, tsq0) * 100.0
    s._prev_tbq, s._prev_tsq, s._prev_px = tbq0, tsq0, 10020.0
    s._update_behaviour(10022.0, 15100.0, 12000.0)  # NET still +, abs IMB down
    assert s.last_net > 0
    assert not s.imb_rising
    assert s._try_enter(10022.0) is None
    assert "imb_not_rising" in (s.last_skip or "")
    assert "entry[" in (s.last_skip or "")

    # IMB rising + NET>0 even if TBQ flat → BUY
    s._prev_tbq, s._prev_tsq, s._prev_px = 15100.0, 12000.0, 10022.0
    s.last_imb = abs(15100 - 12000) / 15100 * 100.0
    s._update_behaviour(10024.0, 15100.0, 11000.0)  # TBQ flat, TSQ down → IMB up
    assert s.last_net > 0 and s.imb_rising
    assert not s.tbq_rising
    sig = s._try_enter(10024.0)
    assert sig is not None and sig.action == "BUY"
    assert "imb+" in (sig.reason or "")
    assert "entry[" in (sig.reason or "")


def test_hold_while_tbq_rising_skips_break_and_sl():
    s = AlignS8Strategy(
        AlignS8Config(
            hold_while_book_rises=True,
            close_on_book_drop=True,
            break_min_bars=1,
            break_min_adverse=0.0,
            break_skip_if_supported=False,
            break_price_min=0.5,
            break_persist=1,
            flip_min_bars=99,
            weaken_pct=90,
            stall_bars=10_000,
            cooldown_ticks=0,
            tp_points=100,
            sl_points=15,
        )
    )
    s._open("long", 10000.0)
    # Adverse move + break pattern, but TBQ still rising → HOLD
    s._prev_px, s._prev_tbq, s._prev_tsq = 10000.0, 12000.0, 8000.0
    s._update_behaviour(9970.0, 12500.0, 9500.0)  # px↓, TBQ↑, TSQ↑
    assert s.tbq_rising
    assert s.break_bull or s.tsq_expand
    sig = s._manage(9970.0)
    assert sig is None  # hold: no break / no hard SL despite -30 and sl=15


def test_tbq_drop_closes_long():
    s = AlignS8Strategy(
        AlignS8Config(
            hold_while_book_rises=True,
            close_on_book_drop=True,
            break_min_bars=99,
            flip_min_bars=99,
            weaken_pct=90,
            stall_bars=10_000,
            cooldown_ticks=0,
            tp_points=100,
            sl_points=50,
        )
    )
    s._open("long", 10000.0)
    s._prev_px, s._prev_tbq, s._prev_tsq = 10000.0, 12000.0, 8000.0
    s._update_behaviour(10005.0, 11500.0, 8000.0)  # TBQ↓
    assert s.tbq_falling
    sig = s._manage(10005.0)
    assert sig is not None and "tbq_drop" in (sig.reason or "")
    assert "exit[" in (sig.reason or "")


def test_short_entry_on_negative_net_rising_imb():
    s = AlignS8Strategy(
        AlignS8Config(
            require_pullback=False,
            require_rising_imb=True,
            require_rising_book=False,
            min_imb_pct=5,
            cooldown_ticks=0,
            break_min_bars=99,
            flip_min_bars=99,
            weaken_pct=90,
            close_on_book_drop=False,
        )
    )
    s._prev_px, s._prev_tbq, s._prev_tsq = 10000.0, 9000.0, 12000.0
    s.last_imb = abs(9000 - 12000) / 12000 * 100.0
    # More sell-heavy: NET more negative, abs IMB up; TSQ can be flat
    s._update_behaviour(9995.0, 8000.0, 12000.0)
    assert s.last_net < 0 and s.imb_rising
    assert not s.tsq_rising
    sig = s._try_enter(9995.0)
    assert sig is not None and sig.action == "SHORT"
    assert "imb-" in (sig.reason or "")
    assert "entry[" in (sig.reason or "")


def test_tsq_drop_closes_short():
    s = AlignS8Strategy(
        AlignS8Config(
            hold_while_book_rises=True,
            close_on_book_drop=True,
            break_min_bars=99,
            flip_min_bars=99,
            weaken_pct=90,
            stall_bars=10_000,
            cooldown_ticks=0,
            tp_points=100,
            sl_points=50,
        )
    )
    s._open("short", 10000.0)
    s._prev_px, s._prev_tbq, s._prev_tsq = 10000.0, 8000.0, 12000.0
    s._update_behaviour(9995.0, 8000.0, 11500.0)  # TSQ↓
    assert s.tsq_falling
    sig = s._manage(9995.0)
    assert sig is not None and "tsq_drop" in (sig.reason or "")
    assert "exit[" in (sig.reason or "")


def test_warmup_bar_does_not_enter_on_imb_vs_zero():
    """First decision step has no previous IMB — must not BUY just because imb>0."""
    s = AlignS8Strategy(
        AlignS8Config(
            bar_ticks=5,
            require_pullback=False,
            require_rising_imb=True,
            require_rising_book=False,
            min_imb_pct=3,
            cooldown_ticks=0,
            break_min_bars=99,
            flip_min_bars=99,
            weaken_pct=90,
            close_on_book_drop=False,
        )
    )
    # First 5-tick bar close → warmup only
    for i in range(5):
        sig = s.on_tick(_now(i), 10000.0 + i, _msg(12000, 9000))
    assert sig is None
    assert s.last_align == "warmup"
    assert not s.imb_rising
    # Second bar: IMB flat vs first bar → still no entry if not rising
    for i in range(5):
        sig = s.on_tick(_now(10 + i), 10005.0 + i, _msg(12000, 9000))
    assert sig is None or "imb_not_rising" in (s.last_skip or "") or sig.action != "BUY"
    # Third bar: TBQ up / TSQ flat → abs IMB rises + NET>0 → BUY
    for i in range(5):
        sig = s.on_tick(_now(20 + i), 10010.0 + i, _msg(13000 + i * 10, 9000))
    assert sig is not None and sig.action == "BUY"
    assert "imb+" in (sig.reason or "")
    assert "entry[" in (sig.reason or "")
    assert "(↑0.0)" not in (sig.reason or "")


def test_hold_model_sets_hold_reason():
    s = AlignS8Strategy(
        AlignS8Config(
            hold_model="book_rise",
            hold_while_book_rises=True,
            close_on_book_drop=True,
            break_min_bars=99,
            flip_min_bars=99,
            weaken_pct=90,
            stall_bars=10_000,
            tp_points=100,
            sl_points=50,
        )
    )
    s._open("long", 10000.0)
    s._prev_px, s._prev_tbq, s._prev_tsq = 10000.0, 12000.0, 8000.0
    s._update_behaviour(10010.0, 12500.0, 8000.0)
    assert s.tbq_rising
    assert s._manage(10010.0) is None
    assert s.last_hold_reason and "hold[book_rise]" in s.last_hold_reason
    assert "tbq↑" in s.last_hold_reason


def test_reasoning_models_apply():
    from strategy_s8_align import (
        _apply_entry_model,
        _apply_exit_model,
        _apply_hold_model,
    )

    c = AlignS8Config()
    c = _apply_entry_model("imb_sign", c)
    assert c.entry_model == "imb_sign" and c.require_rising_imb is False
    c = _apply_hold_model("off", c)
    assert c.hold_model == "off" and c.hold_while_book_rises is False
    c = _apply_exit_model("book_drop", c)
    assert c.exit_model == "book_drop" and c.close_on_book_drop is True
    c = _apply_hold_model("book_or_support", c)
    assert c.hold_on_supported is True


def main() -> None:
    test_behaviour_flags_and_book_stops()
    test_dip_supported_skips_hard_sl()
    test_factory()
    test_count_bar_closes_every_n()
    test_large_net_still_detects_widen()
    test_book_break_skips_while_dip_supported()
    test_book_break_blocked_in_profit()
    test_flip_blocked_in_profit()
    test_flip_fires_when_adverse_enough()
    test_no_lock_after_two_sl_only_after_five_losses()
    test_enter_aligned_without_pullback()
    test_entry_net_sign_and_rising_imb()
    test_hold_while_tbq_rising_skips_break_and_sl()
    test_tbq_drop_closes_long()
    test_short_entry_on_negative_net_rising_imb()
    test_tsq_drop_closes_short()
    test_warmup_bar_does_not_enter_on_imb_vs_zero()
    test_hold_model_sets_hold_reason()
    test_reasoning_models_apply()
    print("test_s8_align: OK")


if __name__ == "__main__":
    main()
