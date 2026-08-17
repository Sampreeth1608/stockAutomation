"""One operator desk: 8787 writes; Streamlit must not overwrite the same switches."""

from __future__ import annotations

from typing import Any

OPERATOR_PANEL = "8787"
OPERATOR_URL = "http://127.0.0.1:8787/"
OPERATOR_WRITES = (
    "Emergency & trading, Live money (ENABLE_*, DRY_RUN, LIVE_MAX_LOTS, "
    "live_approved, Restart supervise), Capital management"
)


def streamlit_write_blocked(action: str) -> dict[str, Any]:
    return {
        "ok": False,
        "error": (
            f"{action} is disabled on Streamlit. Use the 8787 control panel. "
            "Streamlit is research-only for operator switches so the two desks "
            "cannot overwrite each other."
        ),
        "use": OPERATOR_URL,
    }


def streamlit_control_allowed(kwargs: dict[str, Any]) -> tuple[bool, str]:
    """Streamlit may only stop. 8787 is what starts, clears, and unlocks."""
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
        f"**Operator writes live only on the 8787 panel** ({OPERATOR_URL}). "
        f"This Streamlit tab is read-only: {OPERATOR_WRITES}. "
        "Do not save the same switches here."
    )
