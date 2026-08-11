"""Tests for edge thresholds and S5 min-edge strategy."""

from __future__ import annotations

import os
from datetime import datetime, timezone

os.environ["IGNORE_FEES"] = "false"

from edge import PointATR, EdgeThresholds, fee_break_even_points
from strategy_minedge import MinEdgeStrategy


def _msg(buy_qty: float, sell_qty: float) -> dict:
    return {
        "best_5_buy_data": [
            {"flag": 0, "quantity": buy_qty, "price": 14000},
            {"flag": 0, "quantity": buy_qty, "price": 13999},
        ],
        "best_5_sell_data": [
            {"flag": 1, "quantity": sell_qty, "price": 14001},
            {"flag": 1, "quantity": sell_qty, "price": 14002},
        ],
    }


def test_fee_break_even_large() -> None:
    be = fee_break_even_points(14380.0)
    # Angel ~₹50 RT / ₹1 per point → roughly tens of points, not hundreds
    assert 30.0 < be < 120.0


def test_required_cover_fees() -> None:
    thr = EdgeThresholds(
        min_edge_points=20.0,
        fee_break_even_points=50.0,
        safety_mult=1.25,
        cover_fees=True,
    )
    assert thr.required_points == 62.5  # max(20, 50*1.25)


def test_required_user_only() -> None:
    thr = EdgeThresholds(
        min_edge_points=20.0,
        fee_break_even_points=400.0,
        safety_mult=1.25,
        cover_fees=False,
    )
    assert thr.required_points == 20.0


def test_point_atr_warms_then_moves() -> None:
    atr = PointATR(window=40)
    assert atr.update(14000.0) is None
    exp = None
    for i in range(30):
        exp = atr.update(14000.0 + i * 5.0)
    assert exp is not None
    assert exp >= 20.0


def test_s5_skips_small_edge() -> None:
    s = MinEdgeStrategy(min_edge_points=20, every_n_ticks=1, atr_window=40)
    s.cover_fees = False
    s.fee_break_even = 400.0
    now = datetime.now(timezone.utc)
    # flat prices → tiny expected move → no entry
    for i in range(25):
        r = s.on_tick(now, 14000.0 + (0.01 if i % 2 else -0.01), _msg(100, 10))
    assert s.position == "flat"
    assert r is None or r.action not in {"BUY", "SHORT"}


def test_s5_enters_on_large_move_and_bias() -> None:
    s = MinEdgeStrategy(
        min_edge_points=20,
        every_n_ticks=1,
        atr_window=40,
        imbalance_ratio=1.2,
    )
    s.cover_fees = False  # user 20pt floor for unit test
    s.fee_break_even = 400.0
    now = datetime.now(timezone.utc)
    buy = None
    for i in range(30):
        r = s.on_tick(now, 14000.0 + i * 3.0, _msg(200, 50))
        if r is not None and r.action == "BUY":
            buy = r
    assert buy is not None
    assert s.position == "long"


def test_s5_target_close() -> None:
    s = MinEdgeStrategy(min_edge_points=20, every_n_ticks=1, atr_window=40)
    s.cover_fees = False
    s.fee_break_even = 400.0
    now = datetime.now(timezone.utc)
    for i in range(30):
        s.on_tick(now, 14000.0 + i * 3.0, _msg(200, 50))
    assert s.position == "long"
    entry = s.entry_price
    assert entry is not None
    # jump past target
    tgt = s.target_points or 50.0
    r = s.on_tick(now, entry + tgt + 1.0, _msg(200, 50))
    assert r is not None
    assert r.action == "CLOSE"
    assert s.position == "flat"


def test_s6_requires_30_points() -> None:
    from strategy_minedge import min30_from_env
    import os

    os.environ["S6_MIN_POINTS"] = "30"
    s = min30_from_env()
    assert s.name == "S6_MIN30"
    assert s.cover_fees is False
    assert s.required_points == 30.0


if __name__ == "__main__":
    test_fee_break_even_large()
    test_required_cover_fees()
    test_required_user_only()
    test_point_atr_warms_then_moves()
    test_s5_skips_small_edge()
    test_s5_enters_on_large_move_and_bias()
    test_s5_target_close()
    test_s6_requires_30_points()
    print("ok")
