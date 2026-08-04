"""Offline checks for depth-based balance strategy (S2)."""

from __future__ import annotations

from depth import depth_buy_sell_sums
from strategy import BarSnapshot
from strategy_balance import BalanceStrategy


def main() -> None:
    # Depth helper with flags
    msg = {
        "best_5_buy_data": [
            {"flag": 0, "quantity": 10},
            {"flag": 0, "quantity": 20},
            {"flag": 0, "quantity": 5},
            {"flag": 0, "quantity": 1},
            {"flag": 0, "quantity": 4},
        ],
        "best_5_sell_data": [
            {"flag": 1, "quantity": 3},
            {"flag": 1, "quantity": 7},
            {"flag": 1, "quantity": 2},
            {"flag": 1, "quantity": 1},
            {"flag": 1, "quantity": 2},
        ],
    }
    buy_sum, sell_sum, details = depth_buy_sell_sums(msg)
    assert buy_sum == 40, buy_sum
    assert sell_sum == 15, sell_sum
    assert details["buy1_qty"] == 10
    assert details["sell5_qty"] == 2

    s = BalanceStrategy()
    r1 = s.on_bar(BarSnapshot("t1", 100, buy_sum, sell_sum), details=details)
    assert r1.action == "BUY", r1

    r2 = s.on_bar(BarSnapshot("t2", 100, 10, 50), details=None)
    assert r2.action == "SHORT", r2

    r3 = s.on_bar(BarSnapshot("t3", 100, 12, 40), details=None)
    assert r3.action == "HOLD", r3

    r4 = s.on_bar(BarSnapshot("t4", 100, 0, 0), details=None)
    assert r4.action == "CLOSE", r4

    print("depth balance strategy checks passed")


if __name__ == "__main__":
    main()
