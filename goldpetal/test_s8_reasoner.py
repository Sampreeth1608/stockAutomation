"""Tests for multi-step S8 reasoner (math/logic/science/planning)."""

from __future__ import annotations

from s8_reasoner import reason_entry, reason_manage


def test_entry_long_plan():
    tr = reason_entry(
        px=15200.0,
        net=500.0,
        imb=25.0,
        prev_imb=18.0,
        tp=45.0,
        sl=35.0,
        min_imb=20.0,
        imb_rising=True,
        require_rising_imb=True,
        tbq_rising=True,
        tsq_rising=False,
        loss_locked=False,
        in_cooldown=False,
        entry_proba=0.62,
        hold_proba=0.55,
        exit_soon_proba=0.30,
    )
    assert tr.action == "ENTER_LONG"
    domains = {s.domain for s in tr.steps}
    assert {"mathematics", "logic", "science", "planning", "programming"} <= domains


def test_entry_skips_soft_imb():
    tr = reason_entry(
        px=15200.0,
        net=500.0,
        imb=10.0,
        prev_imb=8.0,
        tp=45.0,
        sl=35.0,
        min_imb=20.0,
        imb_rising=True,
        require_rising_imb=True,
        tbq_rising=True,
        tsq_rising=False,
        loss_locked=False,
        in_cooldown=False,
        entry_proba=0.70,
    )
    assert tr.action == "SKIP"
    assert any(s.name == "imb_floor" and not s.ok for s in tr.steps)


def test_manage_hold_vs_exit():
    hold = reason_manage(
        side="long",
        move=12.0,
        tp=45.0,
        sl=35.0,
        tbq_falling=False,
        tsq_falling=False,
        hold_proba=0.6,
        exit_soon_proba=0.2,
    )
    assert hold.action == "HOLD"
    ex = reason_manage(
        side="long",
        move=5.0,
        tp=45.0,
        sl=35.0,
        tbq_falling=True,
        tsq_falling=False,
    )
    assert ex.action == "EXIT"


def test_trace_line():
    tr = reason_entry(
        px=15000.0,
        net=-200.0,
        imb=22.0,
        prev_imb=21.0,
        tp=40.0,
        sl=30.0,
        min_imb=20.0,
        imb_rising=True,
        require_rising_imb=True,
        tbq_rising=False,
        tsq_rising=True,
        loss_locked=False,
        in_cooldown=False,
        entry_proba=0.58,
    )
    assert "plan=" in tr.line()
    assert tr.to_dict()["action"] in {"ENTER_SHORT", "SKIP", "WAIT"}


if __name__ == "__main__":
    test_entry_long_plan()
    test_entry_skips_soft_imb()
    test_manage_hold_vs_exit()
    test_trace_line()
    print("test_s8_reasoner: OK")
