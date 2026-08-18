"""Self-learning P(win) gate: short warmup, then a 70% win-rate bar."""

from __future__ import annotations

import os

os.environ["EDGE_ML"] = "true"
os.environ["EDGE_TARGET_WINRATE"] = "0.70"
os.environ["EDGE_MIN_PROBA"] = "0.70"
os.environ["EDGE_WARMUP_MAX"] = "8"
os.environ["EDGE_MIN_SAMPLES"] = "20"

from control_state import ALL_STRATEGY_NAMES
from trade_learner import LEARN_STRATEGIES, STRAT_INDEX, TradeLearner, reset_learner


def _t(strategy: str, after: float, ts: str, side: str = "BUY") -> dict:
    return {
        "strategy": strategy,
        "side": side,
        "status": "CLOSED",
        "pnl_after_tax": after,
        "gross_pnl": after,
        "entry_ts": ts,
        "exit_ts": ts,
        "entry_price": 15000.0,
    }


def test_learns_every_strategy_name() -> None:
    assert LEARN_STRATEGIES == ALL_STRATEGY_NAMES
    for name in ALL_STRATEGY_NAMES:
        assert name in STRAT_INDEX
    assert "S1_NETDELTA" in STRAT_INDEX
    assert "S9_STATE30" in STRAT_INDEX
    assert "S14_WICK30_STRICT" in STRAT_INDEX


def test_warmup_does_not_block() -> None:
    lr = TradeLearner()
    feat = {"strategy": "S12_HHHL30", "side": 1.0, "hour": 11.0, "ltp": 15000.0}
    ok, why = lr.allow("S12_HHHL30", feat)
    assert ok is True
    assert "warmup" in why or "edge_ml_off" in why


def test_blocks_after_enough_losing_closes() -> None:
    lr = TradeLearner()
    trades = [
        _t("S12_HHHL30", -80.0, f"2026-08-11T10:{i:02d}:00") for i in range(16)
    ]
    lr.fit(trades)
    feat = {"strategy": "S12_HHHL30", "side": 1.0, "hour": 10.0, "ltp": 15000.0}
    ok, why = lr.allow("S12_HHHL30", feat)
    assert ok is False
    assert "ml_" in why


def test_blocks_low_winrate_book() -> None:
    """~40% after-tax (8 wins / 20) must not pass a 70% gate."""
    lr = TradeLearner()
    trades = []
    for i in range(20):
        after = 120.0 if i < 8 else -40.0
        trades.append(_t("S14_WICK30_STRICT", after, f"2026-08-12T10:{i:02d}:00"))
    lr.fit(trades)
    feat = {"strategy": "S14_WICK30_STRICT", "side": 1.0, "hour": 10.0, "ltp": 15000.0}
    ok, why = lr.allow("S14_WICK30_STRICT", feat)
    assert ok is False
    assert "ml_" in why


def test_blocks_non_slim_book() -> None:
    lr = TradeLearner()
    trades = [
        _t("S1_NETDELTA", -50.0, f"2026-08-11T11:{i:02d}:00") for i in range(12)
    ]
    lr.fit(trades)
    feat = {"strategy": "S1_NETDELTA", "side": 1.0, "hour": 11.0, "ltp": 15000.0}
    ok, why = lr.allow("S1_NETDELTA", feat)
    assert ok is False
    assert "ml_" in why
    assert int(lr.by_book["S1_NETDELTA"]["n"]) == 12


def test_allows_high_winrate_book() -> None:
    lr = TradeLearner()
    trades = []
    for i in range(20):
        after = 120.0 if i % 10 else -40.0
        trades.append(_t("S5_MINEDGE", after, f"2026-08-12T11:{i:02d}:00"))
    lr.fit(trades)
    feat = {"strategy": "S5_MINEDGE", "side": 1.0, "hour": 11.0, "ltp": 15000.0}
    ok, why = lr.allow("S5_MINEDGE", feat)
    assert ok is True
    assert "ml_p=" in why or "warmup" in why


def test_allows_positive_ev_book() -> None:
    test_allows_high_winrate_book()


def test_old_min_proba_cannot_undo_seventy() -> None:
    prev = os.environ.get("EDGE_TARGET_WINRATE")
    prev_p = os.environ.get("EDGE_MIN_PROBA")
    os.environ.pop("EDGE_TARGET_WINRATE", None)
    os.environ["EDGE_MIN_PROBA"] = "0.52"
    try:
        from trade_learner import _target_winrate

        assert _target_winrate() == 0.70
    finally:
        if prev is None:
            os.environ.pop("EDGE_TARGET_WINRATE", None)
        else:
            os.environ["EDGE_TARGET_WINRATE"] = prev
        if prev_p is None:
            os.environ.pop("EDGE_MIN_PROBA", None)
        else:
            os.environ["EDGE_MIN_PROBA"] = prev_p


def test_reset_clears_singleton() -> None:
    lr = reset_learner()
    assert lr.n == 0
    assert lr.note == "cold"


if __name__ == "__main__":
    test_learns_every_strategy_name()
    test_warmup_does_not_block()
    test_blocks_after_enough_losing_closes()
    test_blocks_low_winrate_book()
    test_blocks_non_slim_book()
    test_allows_high_winrate_book()
    test_allows_positive_ev_book()
    test_old_min_proba_cannot_undo_seventy()
    test_reset_clears_singleton()
    print("ALL test_trade_learner OK")
