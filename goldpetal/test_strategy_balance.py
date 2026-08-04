"""Offline checks for 1-minute depth balance strategy (S2)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from depth import depth_buy_sell_sums
from strategy import BarSnapshot
from strategy_balance import BalanceStrategy


def _msg(buy_qtys: list[float], sell_qtys: list[float]) -> dict:
    return {
        "best_5_buy_data": [
            {"flag": 0, "quantity": q} for q in buy_qtys
        ],
        "best_5_sell_data": [
            {"flag": 1, "quantity": q} for q in sell_qtys
        ],
    }


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

    # --- 1-minute accumulation ---
    s2 = BalanceStrategy(window_seconds=60)
    t0 = datetime(2026, 8, 4, 12, 0, 10, tzinfo=timezone.utc)
    # first minute: accumulate 3 ticks, no signal yet
    assert s2.on_tick(t0, 14000.0, _msg([10, 0, 0, 0, 0], [1, 0, 0, 0, 0])) is None
    assert s2.on_tick(t0 + timedelta(seconds=5), 14001.0, _msg([10, 0, 0, 0, 0], [1, 0, 0, 0, 0])) is None
    assert s2.on_tick(t0 + timedelta(seconds=20), 14002.0, _msg([10, 0, 0, 0, 0], [1, 0, 0, 0, 0])) is None

    # roll to next minute → flush: buy=30 sell=3 → BUY
    r = s2.on_tick(t0 + timedelta(seconds=60), 14010.0, _msg([5, 0, 0, 0, 0], [20, 0, 0, 0, 0]))
    assert r is not None
    assert r.action == "BUY", r
    assert r.net == 27.0, r.net  # 30-3
    assert s2.position == "long"
    # current minute already has the new tick (buy5 sell20)
    assert s2._tick_count == 1

    # next roll: previous minute buy5-sell20 = -15 → SHORT
    r2 = s2.on_tick(t0 + timedelta(seconds=120), 14020.0, _msg([1, 0, 0, 0, 0], [1, 0, 0, 0, 0]))
    assert r2 is not None
    assert r2.action == "SHORT", r2
    assert s2.position == "short"

    print("depth balance strategy checks passed")


if __name__ == "__main__":
    main()
