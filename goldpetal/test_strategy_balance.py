"""Offline checks for balance strategy (S2)."""

from __future__ import annotations

from strategy import BarSnapshot
from strategy_balance import BalanceStrategy


def main() -> None:
    s = BalanceStrategy()

    r1 = s.on_bar(BarSnapshot("t1", cmp=100, bp=120, sp=80))  # net +40
    assert r1.action == "BUY" and r1.position_after == "long", r1

    r2 = s.on_bar(BarSnapshot("t2", cmp=101, bp=150, sp=90))  # still +
    assert r2.action == "HOLD" and r2.position_after == "long", r2

    r3 = s.on_bar(BarSnapshot("t3", cmp=99, bp=70, sp=100))  # net -30
    assert r3.action == "SHORT" and r3.position_after == "short", r3

    r4 = s.on_bar(BarSnapshot("t4", cmp=98, bp=60, sp=110))  # still -
    assert r4.action == "HOLD" and r4.position_after == "short", r4

    r5 = s.on_bar(BarSnapshot("t5", cmp=100, bp=100, sp=100))  # 0
    assert r5.action == "CLOSE" and r5.position_after == "flat", r5

    print("balance strategy checks passed")


if __name__ == "__main__":
    main()
