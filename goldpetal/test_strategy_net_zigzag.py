"""Tests for S8_NET_ZIGZAG (gated re-entry) + recorder."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from strategy_net_zigzag import NetZigzagConfig, NetZigzagStrategy
from zigzag_recorder import ZigzagRecorder, init_zigzag_db

IST = ZoneInfo("Asia/Kolkata")


def _now() -> datetime:
    return datetime(2026, 8, 7, 11, 0, 0, tzinfo=IST)


def _msg(tbq: float, tsq: float) -> dict:
    return {"total_buy_quantity": tbq, "total_sell_quantity": tsq}


def test_long_entry_on_imb_edge_and_tp():
    s = NetZigzagStrategy(
        NetZigzagConfig(
            min_imb_pct=10,
            weaken_pct=10,
            tp_points=25,
            sl_points=20,
            cooldown_ticks=0,
            entry_mode="edge",
        )
    )
    # soft first
    s.on_tick(_now(), 10000.0, _msg(10500, 10000))  # imb soft
    # edge to strong bull
    sig = s.on_tick(_now(), 10001.0, _msg(12000, 8000))
    assert sig is not None and sig.action == "BUY"
    assert s.position == "long"
    sig2 = s.on_tick(_now(), 10026.0, _msg(12000, 8000))
    assert sig2 is not None and sig2.action == "CLOSE"
    assert "tp" in (sig2.reason or "")


def test_no_immediate_reentry_after_sl():
    s = NetZigzagStrategy(
        NetZigzagConfig(
            tp_points=100,
            sl_points=10,
            weaken_pct=90,
            cooldown_ticks=0,
            entry_mode="edge",
        )
    )
    s.on_tick(_now(), 10000.0, _msg(10500, 10000))
    s.on_tick(_now(), 10000.0, _msg(12000, 8000))
    assert s.position == "long"
    # hit SL
    sig = s.on_tick(_now(), 9989.0, _msg(12000, 8000))
    assert sig and sig.action == "CLOSE" and "sl" in (sig.reason or "")
    # still strong bull — must NOT re-enter until IMB goes soft then strong
    sig2 = s.on_tick(_now(), 9990.0, _msg(12000, 8000))
    assert sig2 is None
    assert s.position == "flat"
    assert s._need_reset is True


def test_flip_closes():
    s = NetZigzagStrategy(
        NetZigzagConfig(tp_points=100, sl_points=100, weaken_pct=90, entry_mode="edge")
    )
    s.on_tick(_now(), 10000.0, _msg(10500, 10000))
    s.on_tick(_now(), 10000.0, _msg(12000, 8000))
    assert s.position == "long"
    sig = s.on_tick(_now(), 10001.0, _msg(5000, 15000))
    assert sig is not None and sig.action == "CLOSE"
    assert "flip" in (sig.reason or "")


def test_recorder_writes(tmp_path: Path | None = None):
    base = Path(tmp_path) if tmp_path else Path("data") / "_test_zigzag_retune.db"
    if base.exists():
        base.unlink()
    init_zigzag_db(base)
    rec = ZigzagRecorder(db_path=base, snap_every_n=1, enabled=True)
    rec.record_params({"tp": 25, "sl": 20})
    rec.log(
        ts="2026-08-07T11:00:00",
        action="BUY",
        ltp=10000.0,
        tbq=12000.0,
        tsq=8000.0,
        side="long",
        position="long",
        reason="test",
    )
    out = base.with_suffix(".csv")
    n = rec.export_csv(out)
    assert n >= 1
    if base.exists():
        base.unlink()
    if out.exists():
        out.unlink()


if __name__ == "__main__":
    test_long_entry_on_imb_edge_and_tp()
    test_no_immediate_reentry_after_sl()
    test_flip_closes()
    test_recorder_writes()
    print("ok")
