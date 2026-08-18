"""Disabled books must not crash the 30m bar path."""

from __future__ import annotations

from strategy import BarSnapshot
from strategy_disabled import DisabledStrategy


def test_on_bar_returns_hold_not_none() -> None:
    s = DisabledStrategy("S1_NETDELTA")
    result = s.on_bar(
        BarSnapshot(time_label="2026-08-18T14:00:00", cmp=15470.0, bp=1.0, sp=1.0)
    )
    assert result is not None
    assert result.action == "HOLD"
    assert result.net == 0.0
    assert s.position == "flat"


if __name__ == "__main__":
    test_on_bar_returns_hold_not_none()
    print("ALL test_strategy_disabled OK")
