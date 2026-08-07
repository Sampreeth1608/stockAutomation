"""Tests for S8_NET_ZIGZAG (best fixed params) + recorder."""

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


def test_long_entry_and_tp():
    s = NetZigzagStrategy(
        NetZigzagConfig(min_imb_pct=10, weaken_pct=10, tp_points=25, sl_points=20)
    )
    sig = s.on_tick(_now(), 10000.0, _msg(12000, 8000))
    assert sig is not None and sig.action == "BUY"
    assert s.position == "long"
    # TP
    sig2 = s.on_tick(_now(), 10025.0, _msg(12000, 8000))
    assert sig2 is not None and sig2.action == "CLOSE"
    assert "tp" in (sig2.reason or "")
    assert s.position == "flat"


def test_no_short_while_bull():
    s = NetZigzagStrategy(NetZigzagConfig())
    s.on_tick(_now(), 10000.0, _msg(12000, 8000))
    assert s.position == "long"
    # price dumps but NET still bull — manage SL/weaken, never flip to short while long
    for px in range(10000, 9975, -1):
        sig = s.on_tick(_now(), float(px), _msg(12000, 8000))
        if sig and sig.action == "CLOSE":
            break
    # after close, still bull → re-enter long not short
    sig = s.on_tick(_now(), 9970.0, _msg(12000, 8000))
    assert sig is None or sig.action == "BUY"


def test_flip_closes():
    s = NetZigzagStrategy(NetZigzagConfig(tp_points=100, sl_points=100, weaken_pct=90))
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
    rec.on_tick_snapshot(
        ts="2026-08-07T11:00:01",
        ltp=10005.0,
        tbq=12000.0,
        tsq=8000.0,
        position="long",
        entry_ltp=10000.0,
    )
    out = base.with_suffix(".csv")
    n = rec.export_csv(out)
    assert n >= 2
    text = out.read_text()
    assert "BUY" in text and "SNAP" in text
    assert "12000" in text
    if base.exists():
        base.unlink()
    if out.exists():
        out.unlink()


if __name__ == "__main__":
    test_long_entry_and_tp()
    test_no_short_while_bull()
    test_flip_closes()
    test_recorder_writes()
    print("ok")
