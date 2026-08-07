"""Unit tests for S8_NESTED_TREND (session bias + nested zigzags)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from strategy_nested_trend import NestedTrendConfig, NestedTrendStrategy, net_imbalance

IST = ZoneInfo("Asia/Kolkata")


def _msg(tbq: float, tsq: float) -> dict:
    return {"total_buy_quantity": tbq, "total_sell_quantity": tsq}


def _now() -> datetime:
    return datetime(2026, 8, 7, 10, 0, 0, tzinfo=IST)


def test_net_imbalance():
    net, imb = net_imbalance(11000, 9000)
    assert net == 2000
    assert abs(imb - (2000 / 11000 * 100)) < 1e-6


def test_bull_session_bias_and_long_entry():
    cfg = NestedTrendConfig(
        min_imb_pct=10.0,
        weaken_pct=10.0,
        tp_points=25.0,
        sl_points=20.0,
        pullback_points=8.0,
        resume_points=5.0,
        swing_ticks=10,
        net_ema_alpha=0.5,
        use_fee_gate=False,
    )
    s = NestedTrendStrategy(cfg)
    px = 10000.0
    # Establish rising price + strong NET+
    for i in range(15):
        px += 1.0
        s.on_tick(_now(), px, _msg(12000, 8000))
    assert s.session_bias == "BULL"
    # First entry on true uptrend once swing window filled
    assert s.position in {"flat", "long"}
    # Force more upticks until entry
    for i in range(20):
        px += 0.5
        sig = s.on_tick(_now(), px, _msg(12000, 8000))
        if sig and sig.action == "BUY":
            break
    assert s.position == "long"
    assert s.entry_price is not None


def test_nested_pullback_resume_reentry():
    cfg = NestedTrendConfig(
        min_imb_pct=10.0,
        weaken_pct=50.0,  # don't weaken-exit during test
        tp_points=25.0,
        sl_points=40.0,
        pullback_points=8.0,
        resume_points=5.0,
        swing_ticks=10,
        net_ema_alpha=0.5,
        use_fee_gate=False,
    )
    s = NestedTrendStrategy(cfg)
    px = 10000.0
    for _ in range(20):
        px += 1.0
        s.on_tick(_now(), px, _msg(12000, 8000))
    assert s.position == "long"
    entry1 = s.entry_price

    # Hit TP
    px = entry1 + 25.0
    sig = s.on_tick(_now(), px, _msg(12000, 8000))
    assert sig is not None and sig.action == "CLOSE"
    assert s.position == "flat"
    assert s._trades_this_bias >= 1

    # Mark new high then pullback >= 8
    high = px
    for _ in range(5):
        high += 1.0
        s.on_tick(_now(), high, _msg(12000, 8000))
    pb = high - 9.0
    for step in range(10):
        s.on_tick(_now(), high - step, _msg(12000, 8000))
    assert s.swing == "PULLBACK_BULL"

    # Resume +5 from pullback low
    low = s.pullback_extreme or pb
    sig2 = None
    for add in range(1, 12):
        sig2 = s.on_tick(_now(), low + add, _msg(12000, 8000))
        if sig2 and sig2.action == "BUY":
            break
    assert sig2 is not None and sig2.action == "BUY"
    assert s.position == "long"
    assert "pullback_resume" in (sig2.reason or "")


def test_no_short_while_bull_bias():
    cfg = NestedTrendConfig(
        min_imb_pct=10.0,
        swing_ticks=8,
        net_ema_alpha=0.5,
        pullback_points=8.0,
        resume_points=5.0,
        use_fee_gate=False,
    )
    s = NestedTrendStrategy(cfg)
    px = 10000.0
    for _ in range(15):
        px += 1.0
        s.on_tick(_now(), px, _msg(12000, 8000))
    # Dump price while NET still + → pullback, never SHORT
    for _ in range(20):
        px -= 1.0
        sig = s.on_tick(_now(), px, _msg(12000, 8000))
        if sig:
            assert sig.action != "SHORT"


def test_session_flip_closes_long():
    cfg = NestedTrendConfig(
        min_imb_pct=10.0,
        swing_ticks=8,
        net_ema_alpha=0.8,
        weaken_pct=90.0,
        tp_points=100.0,
        sl_points=100.0,
        use_fee_gate=False,
    )
    s = NestedTrendStrategy(cfg)
    px = 10000.0
    for _ in range(15):
        px += 1.0
        s.on_tick(_now(), px, _msg(12000, 8000))
    assert s.position == "long"
    # Flip NET strongly bearish
    sig = None
    for _ in range(10):
        px -= 0.2
        sig = s.on_tick(_now(), px, _msg(5000, 15000))
        if sig and sig.action == "CLOSE":
            break
    assert sig is not None and sig.action == "CLOSE"
    assert "flip" in (sig.reason or "")
    assert s.position == "flat"


def test_fee_gate_blocks_small_tp():
    cfg = NestedTrendConfig(
        tp_points=25.0,
        fee_be_points=50.0,
        use_fee_gate=True,
        swing_ticks=8,
        net_ema_alpha=0.5,
    )
    s = NestedTrendStrategy(cfg)
    px = 10000.0
    for _ in range(30):
        px += 1.0
        sig = s.on_tick(_now(), px, _msg(12000, 8000))
        assert sig is None or sig.action != "BUY"
    assert s.position == "flat"
    assert s.last_skip == "fee_gate"


def test_depth_blocks_long_when_sell_book_heavy():
    cfg = NestedTrendConfig(
        min_imb_pct=10.0,
        swing_ticks=8,
        net_ema_alpha=0.5,
        require_depth=True,
        depth_ratio=1.20,
        use_fee_gate=False,
    )
    s = NestedTrendStrategy(cfg)
    px = 10000.0
    # NET bullish but depth sell-heavy
    heavy_sell = {
        "total_buy_quantity": 12000,
        "total_sell_quantity": 8000,
        "last_traded_quantity": 5,
        "best_5_buy_data": [{"flag": 0, "quantity": 10}] * 5,
        "best_5_sell_data": [{"flag": 1, "quantity": 100}] * 5,
    }
    for _ in range(25):
        px += 1.0
        sig = s.on_tick(_now(), px, heavy_sell)
        if sig:
            assert sig.action != "BUY"
    assert s.position == "flat"
    assert s.last_skip and "depth_block" in s.last_skip


if __name__ == "__main__":
    test_net_imbalance()
    test_bull_session_bias_and_long_entry()
    test_nested_pullback_resume_reentry()
    test_no_short_while_bull_bias()
    test_session_flip_closes_long()
    test_fee_gate_blocks_small_tp()
    test_depth_blocks_long_when_sell_book_heavy()
    print("ok")
