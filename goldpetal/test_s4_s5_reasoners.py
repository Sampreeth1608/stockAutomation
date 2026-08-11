"""Tests for S4/S5 reasoners."""

from __future__ import annotations

from s4_reasoner import reason_entry as s4_entry
from s4_reasoner import reason_exit as s4_exit
from s4_reasoner import reason_hold as s4_hold
from s5_reasoner import reason_entry as s5_entry
from s5_reasoner import reason_exit as s5_exit
from s5_reasoner import reason_hold as s5_hold


def test_s4_entry_long() -> None:
    t = s4_entry(
        px=15000,
        prob_bullish=0.72,
        bias="BULLISH",
        buy_prob=0.58,
        in_entry_window=True,
        min_score=0.3,
    )
    assert t.action == "ENTER_LONG"
    assert t.score >= 0.3


def test_s4_entry_skip_outside_window() -> None:
    t = s4_entry(
        px=15000,
        prob_bullish=0.8,
        bias="BULLISH",
        in_entry_window=False,
    )
    assert t.action == "SKIP"


def test_s4_hold_exit() -> None:
    h = s4_hold(side="long", held_overnight=True, next_session=False)
    assert h.action == "HOLD"
    e = s4_exit(side="long", next_session=True, in_exit_window=True)
    assert e.action == "EXIT"


def test_s5_entry_and_manage() -> None:
    t = s5_entry(
        px=15000,
        expected_pts=80,
        required_pts=62,
        bias="long",
        imb_ratio=1.8,
        imbalance_threshold=1.35,
        min_score=0.3,
    )
    assert t.action == "ENTER_LONG"
    h = s5_hold(side="long", move_pts=10, target_pts=50, stop_pts=25, expected_pts=70)
    assert h.action == "HOLD"
    e = s5_exit(side="long", move_pts=55, target_pts=50, stop_pts=25)
    assert e.action == "EXIT"


def test_s5_weak_edge_waits() -> None:
    t = s5_entry(
        px=15000,
        expected_pts=20,
        required_pts=62,
        bias="long",
        imb_ratio=2.0,
    )
    assert t.action == "WAIT"


if __name__ == "__main__":
    test_s4_entry_long()
    print("ok s4_entry_long")
    test_s4_entry_skip_outside_window()
    print("ok s4_skip")
    test_s4_hold_exit()
    print("ok s4_hold_exit")
    test_s5_entry_and_manage()
    print("ok s5_entry_manage")
    test_s5_weak_edge_waits()
    print("ok s5_wait")
    print("ALL test_s4_s5_reasoners OK")
