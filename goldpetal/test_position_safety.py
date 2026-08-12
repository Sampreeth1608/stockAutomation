"""Tests for restart / orphan / EOD safety helpers."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from position_safety import (
    OpenPosition,
    apply_position_to_strategy,
    in_eod_flatten_window,
    startup_reconcile,
)

IST = ZoneInfo("Asia/Kolkata")


class _Fake:
    def __init__(self, name: str) -> None:
        self.name = name
        self.position = "flat"
        self.entry_price = None
        self.target_points = None
        self.stop_points = None
        self.required_points = 50.0

    @property
    def status_line(self) -> str:
        return f"pos={self.position}"


class _Disabled:
    name = "S5_MINEDGE"
    position = "flat"
    entry_price = None
    enabled = False

    @property
    def status_line(self) -> str:
        return "DISABLED (not loaded — frees RAM)"


def test_apply_restore_s5() -> None:
    s = _Fake("S5_MINEDGE")
    ok = apply_position_to_strategy(
        s,
        OpenPosition("S5_MINEDGE", "short", 15300.0, "t", "SHORT", 15300.0),
    )
    assert ok
    assert s.position == "short"
    assert s.entry_price == 15300.0
    assert s.target_points == 50.0


def test_disabled_not_restorable() -> None:
    s = _Disabled()
    ok = apply_position_to_strategy(
        s,
        OpenPosition("S5_MINEDGE", "long", 1.0, "t", "BUY", 1.0),
    )
    assert not ok


def test_eod_window() -> None:
    # MARKET_CLOSE 23:30, last 5 minutes → 23:25–23:30
    assert in_eod_flatten_window(
        datetime(2026, 8, 12, 23, 27, tzinfo=IST),
        market_close="23:30",
        minutes=5,
    )
    assert not in_eod_flatten_window(
        datetime(2026, 8, 12, 23, 20, tzinfo=IST),
        market_close="23:30",
        minutes=5,
    )
    # weekend
    assert not in_eod_flatten_window(
        datetime(2026, 8, 15, 23, 27, tzinfo=IST),  # Saturday
        market_close="23:30",
        minutes=5,
    )


def test_startup_reconcile_restore(monkeypatch_signals=None) -> None:
    # Unit-level: monkey via injecting fake last_open by patching module
    import position_safety as ps

    calls: list = []

    def fake_last(name: str):
        if name == "S8_NET_ZIGZAG":
            return OpenPosition(name, "long", 15366.0, "2026-08-11T16:30:00", "BUY", 15366.0)
        return None

    orig = ps.last_open_position
    ps.last_open_position = fake_last  # type: ignore[assignment]
    try:
        s8 = _Fake("S8_NET_ZIGZAG")
        res = startup_reconcile({"S8_NET_ZIGZAG": s8}, mode="restore")
        assert s8.position == "long"
        assert len(res["restored"]) == 1
        assert res["closes"] == []
        res2 = startup_reconcile({"S8_NET_ZIGZAG": s8}, mode="close")
        assert len(res2["closes"]) == 1
    finally:
        ps.last_open_position = orig  # type: ignore[assignment]
    del calls


if __name__ == "__main__":
    test_apply_restore_s5()
    print("ok apply")
    test_disabled_not_restorable()
    print("ok disabled")
    test_eod_window()
    print("ok eod")
    test_startup_reconcile_restore()
    print("ok reconcile")
    print("ALL test_position_safety OK")
