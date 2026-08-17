"""Tests for walk-forward wick/HHHL gates (no neural net)."""

from __future__ import annotations

from backtest_hhhl_candles import Candle
from learn_hhhl_wick import (
    Gate,
    default_gate,
    pick_gate,
    score_trades,
    trades_on_days,
    unique_days,
)
from backtest_hhhl_candles import Trade


def _t(day: str, pnl: float) -> Trade:
    return Trade(
        tf="t",
        side="LONG",
        entry_time=f"{day} 10:29:00",
        entry_px=1.0,
        exit_time=f"{day} 10:59:00",
        exit_px=1.0,
        gross_pts=pnl,
        gross_pnl_inr=pnl,
        after_tax_pnl_inr=pnl,
        fees_inr=0.0,
        lots=100.0,
    )


def test_pick_gate_uses_train_days_only() -> None:
    good = Gate(15.0, 0.5, 0.0, None, None)
    bad = default_gate()
    by_gate = {
        bad: [_t("2026-08-03", 10.0), _t("2026-08-04", 10.0), _t("2026-08-05", -1000.0), _t("2026-08-06", 10.0)],
        good: [_t("2026-08-03", 50.0), _t("2026-08-04", 50.0), _t("2026-08-05", 50.0), _t("2026-08-06", 50.0)],
    }
    chosen = pick_gate(by_gate, {"2026-08-03", "2026-08-04", "2026-08-05", "2026-08-06"})
    assert chosen == good


def test_trades_on_days_filters() -> None:
    trades = [_t("2026-08-03", 1.0), _t("2026-08-04", 2.0)]
    got = trades_on_days(trades, {"2026-08-04"})
    assert len(got) == 1
    assert score_trades(got)["pnl"] == 2.0


def test_unique_days() -> None:
    candles = [
        Candle("2026-08-03 10:00:00", 1, 2, 0, 1),
        Candle("2026-08-03 10:30:00", 1, 2, 0, 1),
        Candle("2026-08-04 10:00:00", 1, 2, 0, 1),
    ]
    assert unique_days(candles) == ["2026-08-03", "2026-08-04"]


if __name__ == "__main__":
    test_pick_gate_uses_train_days_only()
    test_trades_on_days_filters()
    test_unique_days()
    print("ALL test_learn_hhhl_wick OK")
