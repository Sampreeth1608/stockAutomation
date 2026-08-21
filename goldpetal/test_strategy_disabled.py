"""Disabled books must not crash the 30m bar path."""

from __future__ import annotations

from pathlib import Path

from strategy import BarSnapshot
from strategy_disabled import DisabledStrategy

ROOT = Path(__file__).resolve().parent


def test_on_bar_returns_hold_not_none() -> None:
    s = DisabledStrategy("S1_NETDELTA")
    result = s.on_bar(
        BarSnapshot(time_label="2026-08-18T14:00:00", cmp=15470.0, bp=1.0, sp=1.0)
    )
    assert result is not None
    assert result.action == "HOLD"
    assert result.net == 0.0
    assert s.position == "flat"


def test_run_strategy_does_not_import_s19_at_startup() -> None:
    src = (ROOT / "run_strategy.py").read_text(encoding="utf-8")
    head = src.split("def run_once")[0]
    assert "from strategy_s19 import" not in head
    assert "from strategy_s20 import" not in head
    assert "from strategy_overnight_gap import" not in head
    assert "from strategy_amise import" not in head
    assert "def _optional_book" in src
    assert "def _load_amise_slots" in src


if __name__ == "__main__":
    test_on_bar_returns_hold_not_none()
    test_run_strategy_does_not_import_s19_at_startup()
    print("ALL test_strategy_disabled OK")
