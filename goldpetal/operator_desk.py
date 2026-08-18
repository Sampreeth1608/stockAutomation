"""Operator writes: lightweight HTML desk on 8501."""

from __future__ import annotations

from typing import Any

OPERATOR_PANEL = "8501"
OPERATOR_URL = "http://127.0.0.1:8501/"
OPERATOR_WRITES = (
    "Emergency & trading, start/stop/feed, ENABLE_*, DRY_RUN, LIVE_MAX_LOTS, "
    "live_approved, Restart bot, Capital — HTML desk on 8501"
)


def streamlit_write_blocked(action: str) -> dict[str, Any]:
    return {
        "ok": False,
        "error": (
            f"{action} is disabled on Streamlit. "
            "Use the trading station at http://127.0.0.1:8501/ "
            "(8787 is the same desk if that tunnel is running). "
            "Streamlit stays read-only so it cannot overwrite the HTML desk."
        ),
        "use": OPERATOR_URL,
    }


def streamlit_control_allowed(kwargs: dict[str, Any]) -> tuple[bool, str]:
    """Non-Desk Streamlit tabs may only stop. Desk tab writes the rest."""
    em = kwargs.get("emergency_off")
    tr = kwargs.get("trading_enabled")
    lu = kwargs.get("live_unlocked")
    present = {k: v for k, v in kwargs.items() if v is not None}
    if present == {"emergency_off": True}:
        return True, ""
    if present == {"trading_enabled": False}:
        return True, ""
    if present == {"live_unlocked": False}:
        return True, ""
    return False, "Clear emergency / resume trading / unlock live"


def operator_readonly_markdown() -> str:
    return (
        f"**Writes live on the trading station** ({OPERATOR_URL}). "
        "This Streamlit page is read-only."
    )
