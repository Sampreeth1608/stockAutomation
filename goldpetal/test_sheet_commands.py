"""Tests for Sheets COMMANDS — pause/emergency only, never micro-live."""

from __future__ import annotations

import tempfile
from pathlib import Path

from control_state import load_state
from sheet_commands import apply_command_rows


def test_pause_and_emergency_apply() -> None:
    with tempfile.TemporaryDirectory() as td:
        state = Path(td) / "state.json"
        rows = [
            ["command", "request", "allowed", "last_result", "note"],
            ["pause_all", "YES", "YES", "", ""],
            ["emergency_off", "YES", "YES", "", ""],
        ]
        results = apply_command_rows(rows, state_path=state)
        assert "applied" in results["pause_all"]
        assert "applied" in results["emergency_off"]
        st = load_state(state)
        assert st.trading_enabled is False
        assert st.emergency_off is True


def test_refuses_micro_live_and_unlock() -> None:
    with tempfile.TemporaryDirectory() as td:
        state = Path(td) / "state.json"
        rows = [
            {"command": "micro_live", "request": "YES"},
            {"command": "unlock_live", "request": "YES"},
            {"command": "dry_run_false", "request": "YES"},
            {"command": "approve_live", "request": "YES"},
            {"command": "start_bot", "request": "YES"},
            {"command": "approve_strategy", "request": "YES"},
        ]
        results = apply_command_rows(rows, state_path=state)
        st = load_state(state)
        assert st.live_unlocked is False
        assert st.trading_enabled is True
        assert st.emergency_off is False
        for cmd in (
            "micro_live",
            "unlock_live",
            "dry_run_false",
            "approve_live",
            "start_bot",
            "approve_strategy",
        ):
            assert "refused" in results[cmd]


def test_blank_request_is_noop() -> None:
    with tempfile.TemporaryDirectory() as td:
        state = Path(td) / "state.json"
        results = apply_command_rows(
            [{"command": "pause_all", "request": ""}],
            state_path=state,
        )
        assert results == {}
        assert load_state(state).trading_enabled is True


if __name__ == "__main__":
    test_pause_and_emergency_apply()
    print("ok pause")
    test_refuses_micro_live_and_unlock()
    print("ok refuse live")
    test_blank_request_is_noop()
    print("ok noop")
    print("ALL test_sheet_commands OK")
