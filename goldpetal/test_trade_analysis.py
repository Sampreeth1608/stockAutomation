"""After-tax win rate is not the same as 'wrong guesses'."""

from __future__ import annotations

from trade_analysis import analyze_trades


def _t(
    *,
    strategy: str,
    gross: float,
    charges: float,
    tax: float,
    after: float,
    status: str = "CLOSED",
    side: str = "BUY",
) -> dict:
    return {
        "strategy": strategy,
        "side": side,
        "status": status,
        "gross_pnl": gross,
        "charges": charges,
        "tax": tax,
        "pnl_after_tax": after,
        "entry_ts": "2026-08-17T10:00:00",
        "exit_ts": "2026-08-17T10:30:00",
        "entry_price": 100,
        "exit_price": 100,
        "exit_reason": "test",
    }


def test_fee_killed_is_not_a_wrong_guess() -> None:
    trades = [
        _t(strategy="S14_WICK30_STRICT", gross=50, charges=80, tax=0, after=-30),
        _t(strategy="S14_WICK30_STRICT", gross=-40, charges=80, tax=0, after=-120),
        _t(
            strategy="S14_WICK30_STRICT",
            gross=200,
            charges=80,
            tax=36,
            after=84,
            status="CLOSED_FORCED",
        ),
    ]
    out = analyze_trades(trades)
    o = out["overall"]
    assert o["closed"] == 3
    assert o["wins_gross"] == 2
    assert o["wins_after_tax"] == 1
    assert o["fee_killed"] == 1
    assert o["flips"] == 1
    assert o["win_rate_gross"] == 66.7
    assert o["win_rate_after_tax"] == 33.3
    assert any("right direction" in n for n in out["notes"])
    assert any("FLIP" in n for n in out["notes"])


def test_empty_tape_explains_itself() -> None:
    out = analyze_trades([])
    assert out["overall"]["closed"] == 0
    assert "No closed trades" in out["notes"][0]


if __name__ == "__main__":
    test_fee_killed_is_not_a_wrong_guess()
    test_empty_tape_explains_itself()
    print("ALL test_trade_analysis OK")
