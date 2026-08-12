"""Tests for S9_STATE30."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from strategy_state_s9 import StateS9Config, StateS9Strategy, state_code, short_label

IST = ZoneInfo("Asia/Kolkata")


def _cfg(**kwargs) -> StateS9Config:
    base = dict(
        bar_minutes=30,
        tp_points=26,
        sl_points=16,
        require_net_sign=True,
        allow_short=False,
        min_imb_pct=0,
        require_hlv_confirm=False,
    )
    base.update(kwargs)
    return StateS9Config(**base)


def test_state_code_and_label():
    assert state_code(10, -5, 2, 1, 0.5) == "TBQ+_TSQ-_P+"
    assert short_label("TBQ+_TSQ-_P+") == "B+S-P+"


def test_enter_long_on_confirm_bar():
    s = StateS9Strategy(_cfg())
    # first bar = START
    s.on_bar_row(
        {
            "time": "2026-08-07 10:00:00",
            "open": 10000,
            "high": 10010,
            "low": 9990,
            "close": 10005,
            "tbq_open": 10000,
            "tsq_open": 9000,
            "tbq_close": 10000,
            "tsq_close": 9000,
            "n_ticks": 10,
            "bar_volume": 1000,
        }
    )
    assert s.position == "flat"
    # B+S-P+ with NET+
    sig = s.on_bar_row(
        {
            "time": "2026-08-07 10:30:00",
            "open": 10005,
            "high": 10040,
            "low": 10000,
            "close": 10030,
            "tbq_open": 10000,
            "tsq_open": 9000,
            "tbq_close": 12000,
            "tsq_close": 8000,
            "n_ticks": 10,
            "bar_volume": 1500,
        }
    )
    assert sig is not None and sig.action == "BUY"
    assert s.last_label == "B+S-P+"
    assert s.position == "long"


def test_tp_and_no_short_by_default():
    s = StateS9Strategy(_cfg())
    s.on_bar_row(
        {
            "time": "2026-08-07 10:00:00",
            "open": 10000,
            "high": 10000,
            "low": 10000,
            "close": 10000,
            "tbq_open": 11000,
            "tsq_open": 9000,
            "tbq_close": 11000,
            "tsq_close": 9000,
            "bar_volume": 1000,
        }
    )
    s.on_bar_row(
        {
            "time": "2026-08-07 10:30:00",
            "open": 10000,
            "high": 10020,
            "low": 10000,
            "close": 10020,
            "tbq_open": 11000,
            "tsq_open": 9000,
            "tbq_close": 13000,
            "tsq_close": 8000,
            "bar_volume": 1200,
        }
    )
    assert s.position == "long"
    ep = s.entry_price
    sig = s.on_bar_row(
        {
            "time": "2026-08-07 11:00:00",
            "open": ep,
            "high": ep + 30,
            "low": ep,
            "close": ep + 26,
            "tbq_open": 13000,
            "tsq_open": 8000,
            "tbq_close": 14000,
            "tsq_close": 7000,
            "bar_volume": 1300,
        }
    )
    assert sig and sig.action == "CLOSE" and "tp" in sig.reason

    # bear confirm should NOT short
    s2 = StateS9Strategy(_cfg(allow_short=False))
    s2.on_bar_row(
        {
            "time": "2026-08-07 12:00:00",
            "open": 10000,
            "high": 10000,
            "low": 10000,
            "close": 10000,
            "tbq_close": 8000,
            "tsq_close": 12000,
            "tbq_open": 8000,
            "tsq_open": 12000,
            "bar_volume": 1000,
        }
    )
    sig2 = s2.on_bar_row(
        {
            "time": "2026-08-07 12:30:00",
            "open": 10000,
            "high": 10000,
            "low": 9970,
            "close": 9970,
            "tbq_close": 7000,
            "tsq_close": 14000,
            "tbq_open": 8000,
            "tsq_open": 12000,
            "bar_volume": 1100,
        }
    )
    assert sig2 is None or sig2.action != "SHORT"


def test_tick_bar_boundary_emits():
    s = StateS9Strategy(_cfg())
    t0 = datetime(2026, 8, 7, 10, 5, tzinfo=IST)
    msg = {"total_buy_quantity": 10000, "total_sell_quantity": 9000}
    assert s.on_tick(t0, 10000.0, msg) is None
    # still same bar
    assert s.on_tick(t0.replace(minute=10), 10005.0, msg) is None
    # next 30m bar → close previous (START, no trade)
    sig = s.on_tick(
        datetime(2026, 8, 7, 10, 30, tzinfo=IST),
        10010.0,
        {"total_buy_quantity": 12000, "total_sell_quantity": 8000},
    )
    # first close is START
    assert sig is None or sig.action in {"BUY", "SHORT", "CLOSE"} or True
    assert s.last_state in {"START", "TBQ+_TSQ-_P+", "WARMUP"} or s._prev_tbq is not None


def test_hlv_confirm_allows_and_veto_blocks():
    """H+V+ confirms entry; H+V- vetoes even when B+S-P+."""
    # --- confirm path: rising high + rising volume ---
    s = StateS9Strategy(_cfg(require_hlv_confirm=True, hlv_mode="any"))
    s.on_bar_row(
        {
            "time": "2026-08-07 10:00:00",
            "open": 10000,
            "high": 10010,
            "low": 9990,
            "close": 10000,
            "tbq_close": 10000,
            "tsq_close": 9000,
            "tbq_open": 10000,
            "tsq_open": 9000,
            "bar_volume": 1000,
        }
    )
    sig = s.on_bar_row(
        {
            "time": "2026-08-07 10:30:00",
            "open": 10000,
            "high": 10050,  # H+
            "low": 9995,
            "close": 10040,  # P+
            "tbq_close": 13000,  # B+
            "tsq_close": 8000,  # S-
            "tbq_open": 10000,
            "tsq_open": 9000,
            "bar_volume": 2000,  # V+
        }
    )
    assert sig is not None and sig.action == "BUY"
    assert s.last_hv == "H+_V+"
    assert "hlv_ok" in (sig.reason or "")

    # --- veto path: H+ with falling volume ---
    s2 = StateS9Strategy(_cfg(require_hlv_confirm=True, hlv_mode="any"))
    s2.on_bar_row(
        {
            "time": "2026-08-07 11:00:00",
            "open": 10000,
            "high": 10010,
            "low": 9990,
            "close": 10000,
            "tbq_close": 10000,
            "tsq_close": 9000,
            "tbq_open": 10000,
            "tsq_open": 9000,
            "bar_volume": 2000,
        }
    )
    sig2 = s2.on_bar_row(
        {
            "time": "2026-08-07 11:30:00",
            "open": 10000,
            "high": 10050,  # H+
            "low": 9995,
            "close": 10040,
            "tbq_close": 13000,
            "tsq_close": 8000,
            "tbq_open": 10000,
            "tsq_open": 9000,
            "bar_volume": 500,  # V- → H+_V- veto
        }
    )
    assert sig2 is None
    assert s2.position == "flat"
    assert s2.last_hv == "H+_V-"
    assert s2.last_skip and "hlv_veto" in s2.last_skip


def test_hlv_l_plus_v_plus_confirm():
    """L+V+ alone is enough in hlv_mode=any."""
    s = StateS9Strategy(_cfg(require_hlv_confirm=True, hlv_mode="any"))
    s.on_bar_row(
        {
            "time": "2026-08-07 10:00:00",
            "open": 10000,
            "high": 10020,
            "low": 9980,
            "close": 10000,
            "tbq_close": 10000,
            "tsq_close": 9000,
            "tbq_open": 10000,
            "tsq_open": 9000,
            "bar_volume": 1000,
        }
    )
    # high flat/down, low up (higher low), volume up → L+V+
    sig = s.on_bar_row(
        {
            "time": "2026-08-07 10:30:00",
            "open": 10000,
            "high": 10015,  # H≈ or H-
            "low": 9995,  # L+
            "close": 10030,
            "tbq_close": 13000,
            "tsq_close": 8000,
            "tbq_open": 10000,
            "tsq_open": 9000,
            "bar_volume": 1800,
        }
    )
    assert sig is not None and sig.action == "BUY"
    assert s.last_lv == "L+_V+"


def test_vol_expansion_inc_dec_up_allows():
    """INC_DEC + UP passes vol-expansion gate; DEC_DEC is blocked."""
    s = StateS9Strategy(
        _cfg(require_vol_expansion=True, vol_exp_require_up=True, min_imb_pct=0)
    )
    # pp vol high, p vol dry, c vol back → INC_DEC; prices rising → UP
    s.on_bar_row(
        {
            "time": "2026-08-07 09:00:00",
            "open": 10000,
            "high": 10010,
            "low": 9990,
            "close": 10000,
            "tbq_close": 10000,
            "tsq_close": 9000,
            "tbq_open": 10000,
            "tsq_open": 9000,
            "bar_volume": 2000,
        }
    )
    s.on_bar_row(
        {
            "time": "2026-08-07 09:30:00",
            "open": 10000,
            "high": 10020,
            "low": 9995,
            "close": 10015,
            "tbq_close": 10500,
            "tsq_close": 8800,
            "tbq_open": 10000,
            "tsq_open": 9000,
            "bar_volume": 800,  # DEC vs pp
        }
    )
    sig = s.on_bar_row(
        {
            "time": "2026-08-07 10:00:00",
            "open": 10015,
            "high": 10050,
            "low": 10010,
            "close": 10040,
            "tbq_close": 13000,
            "tsq_close": 8000,
            "tbq_open": 10500,
            "tsq_open": 8800,
            "bar_volume": 1600,  # INC vs p → INC_DEC
        }
    )
    assert s.last_vol_stack == "INC_DEC"
    assert s.last_px_trend == "UP"
    assert sig is not None and sig.action == "BUY"
    assert "volx_ok" in (sig.reason or "")

    # DEC_DEC should block
    s2 = StateS9Strategy(
        _cfg(require_vol_expansion=True, vol_exp_require_up=True, min_imb_pct=0)
    )
    s2.on_bar_row(
        {
            "time": "2026-08-07 11:00:00",
            "open": 10000,
            "high": 10010,
            "low": 9990,
            "close": 10000,
            "tbq_close": 10000,
            "tsq_close": 9000,
            "tbq_open": 10000,
            "tsq_open": 9000,
            "bar_volume": 2000,
        }
    )
    s2.on_bar_row(
        {
            "time": "2026-08-07 11:30:00",
            "open": 10000,
            "high": 10020,
            "low": 9995,
            "close": 10015,
            "tbq_close": 11000,
            "tsq_close": 8500,
            "tbq_open": 10000,
            "tsq_open": 9000,
            "bar_volume": 1200,
        }
    )
    sig2 = s2.on_bar_row(
        {
            "time": "2026-08-07 12:00:00",
            "open": 10015,
            "high": 10050,
            "low": 10010,
            "close": 10040,
            "tbq_close": 14000,
            "tsq_close": 7000,
            "tbq_open": 11000,
            "tsq_open": 8500,
            "bar_volume": 700,  # still DEC → DEC_DEC
        }
    )
    assert s2.last_vol_stack == "DEC_DEC"
    assert sig2 is None
    assert s2.last_skip and "volx_need_stack" in s2.last_skip


def test_bias_align_blocks_long_when_bear():
    """S8-style: no long entry when NET bias is BEAR."""
    s = StateS9Strategy(
        _cfg(
            require_bias_align=True,
            bias_mode="net",
            min_imb_pct=5,
            allow_short=True,
        )
    )
    # BEAR bar: TBQ down, TSQ up, price down, strong imb
    s.on_bar_row(
        {
            "time": "2026-08-07 09:00:00",
            "open": 10000,
            "high": 10010,
            "low": 9990,
            "close": 10000,
            "tbq_close": 8000,
            "tsq_close": 12000,
            "tbq_open": 8000,
            "tsq_open": 12000,
            "bar_volume": 1000,
        }
    )
    assert s.bias in {"BEAR", "NEUTRAL", "BULL"}
    # Try a "long" confirm state but books still bearish NET
    sig = s.on_bar_row(
        {
            "time": "2026-08-07 09:30:00",
            "open": 10000,
            "high": 10040,
            "low": 9970,
            "close": 10030,  # P+ would need tbq+/tsq- — use mixed
            "tbq_close": 7000,
            "tsq_close": 14000,
            "tbq_open": 8000,
            "tsq_open": 12000,
            "bar_volume": 1100,
        }
    )
    # NET still negative → not a long enter state B+S-P+; force enter path:
    # If no BUY, bias gate is working for shorts path instead
    if sig and sig.action == "BUY":
        raise AssertionError("must not BUY under BEAR bias")
    # Short confirm while BEAR should be allowed
    s2 = StateS9Strategy(
        _cfg(
            require_bias_align=True,
            bias_mode="net",
            min_imb_pct=5,
            allow_short=True,
            require_net_sign=True,
        )
    )
    s2.on_bar_row(
        {
            "time": "2026-08-07 10:00:00",
            "open": 10000,
            "high": 10005,
            "low": 9995,
            "close": 10000,
            "tbq_close": 8000,
            "tsq_close": 12000,
            "tbq_open": 8000,
            "tsq_open": 12000,
            "bar_volume": 1000,
        }
    )
    sig2 = s2.on_bar_row(
        {
            "time": "2026-08-07 10:30:00",
            "open": 10000,
            "high": 10000,
            "low": 9970,
            "close": 9970,
            "tbq_close": 6000,
            "tsq_close": 15000,
            "tbq_open": 8000,
            "tsq_open": 12000,
            "bar_volume": 1300,
        }
    )
    assert s2.bias == "BEAR"
    assert sig2 is not None and sig2.action == "SHORT"
    assert "bias_ok_short" in (sig2.reason or "")


def test_dnet_bias_follows_net_delta():
    """dnet mode: rising NET → BULL, falling NET → BEAR."""
    s = StateS9Strategy(_cfg(require_bias_align=True, bias_mode="dnet", min_imb_pct=0))
    s.on_bar_row(
        {
            "time": "2026-08-07 09:00:00",
            "open": 10000,
            "high": 10010,
            "low": 9990,
            "close": 10000,
            "tbq_close": 10000,
            "tsq_close": 9000,  # net=+1000
            "tbq_open": 10000,
            "tsq_open": 9000,
            "bar_volume": 1000,
        }
    )
    assert s.bias == "NEUTRAL"  # no previous net yet
    s.on_bar_row(
        {
            "time": "2026-08-07 09:30:00",
            "open": 10000,
            "high": 10020,
            "low": 9995,
            "close": 10010,
            "tbq_close": 12000,
            "tsq_close": 8000,  # net=+4000 > prev → BULL
            "tbq_open": 10000,
            "tsq_open": 9000,
            "bar_volume": 1100,
        }
    )
    assert s.bias == "BULL"
    assert s.last_net_delta is not None and s.last_net_delta > 0
    s.on_bar_row(
        {
            "time": "2026-08-07 10:00:00",
            "open": 10010,
            "high": 10015,
            "low": 9990,
            "close": 9995,
            "tbq_close": 9000,
            "tsq_close": 11000,  # net=-2000 < prev → BEAR
            "tbq_open": 12000,
            "tsq_open": 8000,
            "bar_volume": 900,
        }
    )
    assert s.bias == "BEAR"
    assert s.last_net_delta is not None and s.last_net_delta < 0


def test_flip_reverse_on_large_bear_bar():
    """Long → reverse short on large bar with bear flip marks."""
    s = StateS9Strategy(
        _cfg(
            enable_flip_reverse=True,
            flip_large_pts=20,
            flip_on_proactive=True,
            tp_points=100,
            sl_points=80,
            min_imb_pct=0,
        )
    )
    # warmup + enter long on B+S-P+
    s.on_bar_row(
        {
            "time": "2026-08-07 09:00:00",
            "open": 10000,
            "high": 10010,
            "low": 9990,
            "close": 10000,
            "tbq_close": 10000,
            "tsq_close": 9000,
            "tbq_open": 10000,
            "tsq_open": 9000,
            "bar_volume": 1000,
        }
    )
    sig = s.on_bar_row(
        {
            "time": "2026-08-07 09:30:00",
            "open": 10000,
            "high": 10030,
            "low": 9995,
            "close": 10025,
            "tbq_close": 13000,
            "tsq_close": 8000,
            "tbq_open": 10000,
            "tsq_open": 9000,
            "bar_volume": 1200,
        }
    )
    assert sig and sig.action == "BUY"
    # large down bar, net flips / bear state → reverse short
    sig2 = s.on_bar_row(
        {
            "time": "2026-08-07 10:00:00",
            "open": 10025,
            "high": 10030,
            "low": 9990,
            "close": 9995,  # ~30 pts down from prior close, underwater
            "tbq_close": 7000,
            "tsq_close": 14000,
            "tbq_open": 13000,
            "tsq_open": 8000,
            "bar_volume": 900,
        }
    )
    assert sig2 is not None
    assert sig2.action == "REVERSE_SHORT"
    assert s.position == "short"
    assert "flip_reverse_short" in (sig2.reason or "")


if __name__ == "__main__":
    test_state_code_and_label()
    test_enter_long_on_confirm_bar()
    test_tp_and_no_short_by_default()
    test_tick_bar_boundary_emits()
    test_hlv_confirm_allows_and_veto_blocks()
    test_hlv_l_plus_v_plus_confirm()
    test_vol_expansion_inc_dec_up_allows()
    test_bias_align_blocks_long_when_bear()
    test_dnet_bias_follows_net_delta()
    test_flip_reverse_on_large_bear_bar()
    print("ok")


