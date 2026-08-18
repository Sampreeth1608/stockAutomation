"""Self-learning P(win) gate: warmup, then skip negative-EV books."""

from __future__ import annotations

from trade_learner import TradeLearner, reset_learner


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


def test_allows_positive_ev_book() -> None:
    lr = TradeLearner()
    trades = []
    for i in range(20):
        after = 120.0 if i % 5 else -40.0
        trades.append(_t("S5_MINEDGE", after, f"2026-08-12T11:{i:02d}:00"))
    lr.fit(trades)
    feat = {"strategy": "S5_MINEDGE", "side": 1.0, "hour": 11.0, "ltp": 15000.0}
    ok, why = lr.allow("S5_MINEDGE", feat)
    assert ok is True
    assert "ml_p=" in why or "warmup" in why


def test_reset_clears_singleton() -> None:
    lr = reset_learner()
    assert lr.n == 0
    assert lr.note == "cold"


if __name__ == "__main__":
    test_warmup_does_not_block()
    test_blocks_after_enough_losing_closes()
    test_allows_positive_ev_book()
    test_reset_clears_singleton()
    print("ALL test_trade_learner OK")
