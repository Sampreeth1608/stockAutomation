"""You-tab live queue — records always; Angel only when armed + YOU."""

from __future__ import annotations

import os
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import control_state
from control_state import set_emergency, set_live_unlocked, set_trading_enabled
from live_orders import OrderResult
from you_trade import (
    YOU_BOOK,
    finish_you_order,
    load_you_position,
    request_you_order,
    take_pending_you_order,
    you_live_status,
)

IST = ZoneInfo("Asia/Kolkata")


def _arm(state: Path) -> None:
    set_emergency(False, path=state)
    set_trading_enabled(True, path=state)
    set_live_unlocked(True, path=state)
    os.environ["DRY_RUN"] = "false"


def test_request_needs_you_word(tmp_path: Path) -> None:
    os.environ["DRY_RUN"] = "true"
    order = tmp_path / "you_order.json"
    pos = tmp_path / "you_position.json"
    res = request_you_order(
        "BUY",
        confirm="",
        order_path=order,
        pos_path=pos,
        require_bot=False,
    )
    assert res["queued"] is False
    assert "YOU" in res["error"]


def test_request_blocked_when_paper(tmp_path: Path) -> None:
    td = tempfile.TemporaryDirectory()
    try:
        state = Path(td.name) / "state.json"
        control_state.STATE_PATH = state
        os.environ["DRY_RUN"] = "true"
        set_emergency(False, path=state)
        set_trading_enabled(True, path=state)
        set_live_unlocked(True, path=state)
        order = tmp_path / "ord.json"
        pos = tmp_path / "pos.json"
        res = request_you_order(
            "BUY",
            confirm="YOU",
            order_path=order,
            pos_path=pos,
            require_bot=False,
        )
        assert res["queued"] is False
        assert "DRY_RUN" in res["error"] or "live blocked" in res["error"]
    finally:
        td.cleanup()
        control_state.STATE_PATH = control_state.CONTROL_DIR / "state.json"


def test_queue_and_finish(tmp_path: Path) -> None:
    td = tempfile.TemporaryDirectory()
    try:
        state = Path(td.name) / "state.json"
        control_state.STATE_PATH = state
        _arm(state)
        order = tmp_path / "ord.json"
        pos = tmp_path / "pos.json"
        queued = request_you_order(
            "buy",
            confirm="YOU",
            example_id="abc",
            entry_px=15300.0,
            order_path=order,
            pos_path=pos,
            require_bot=False,
        )
        assert queued["queued"] is True
        job = take_pending_you_order(path=order)
        assert job is not None
        assert job["action"] == "BUY"
        assert job["status"] == "in_flight"
        assert take_pending_you_order(path=order) is None
        finish_you_order(
            job,
            OrderResult(
                ok=True,
                dry_run=False,
                skipped=False,
                reason="placed",
                order_id="99",
                strategy=YOU_BOOK,
            ),
            order_path=order,
            pos_path=pos,
        )
        posn = load_you_position(path=pos)
        assert posn["side"] == "long"
        assert posn["entry_px"] == 15300.0
        st = you_live_status(order_path=order, pos_path=pos, now=datetime.now(IST))
        assert st["position"]["side"] == "long"
        assert st["book"] == YOU_BOOK
    finally:
        td.cleanup()
        control_state.STATE_PATH = control_state.CONTROL_DIR / "state.json"
        os.environ["DRY_RUN"] = "true"


def test_you_live_status_says_restart_when_armed_but_dry(tmp_path: Path) -> None:
    from unittest.mock import patch

    td = tempfile.TemporaryDirectory()
    try:
        state = Path(td.name) / "state.json"
        control_state.STATE_PATH = state
        set_emergency(False, path=state)
        set_trading_enabled(True, path=state)
        set_live_unlocked(True, path=state)
        order = tmp_path / "ord.json"
        pos = tmp_path / "pos.json"
        with patch("you_trade.read_live_env", return_value={"dry_run": True, "live_max_lots": 1}):
            st = you_live_status(order_path=order, pos_path=pos, now=datetime.now(IST))
        assert st["dry_run"] is True
        assert "RESTART" in st["why"]
        assert "Paper Positions is not a live fill" in st["why"]
    finally:
        td.cleanup()
        control_state.STATE_PATH = control_state.CONTROL_DIR / "state.json"
        os.environ["DRY_RUN"] = "true"


if __name__ == "__main__":
    td = Path(tempfile.mkdtemp())
    (td / "a").mkdir()
    test_request_needs_you_word(td / "a")
    (td / "b").mkdir()
    test_request_blocked_when_paper(td / "b")
    (td / "c").mkdir()
    test_queue_and_finish(td / "c")
    (td / "d").mkdir()
    test_you_live_status_says_restart_when_armed_but_dry(td / "d")
    print("ALL test_you_trade OK")
