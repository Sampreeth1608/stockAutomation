"""Unified entry gates: emergency, trading master, capital, force-disable."""

from __future__ import annotations

from capital import can_open_trade
from control_state import strategy_entries_allowed


def allow_new_entry(strategy: str, lots: int = 1) -> tuple[bool, str]:
    """Return (ok, reason). CLOSE is never gated here — only BUY/SHORT."""
    ok, reason = strategy_entries_allowed(strategy)
    if not ok:
        return False, reason
    return can_open_trade(strategy, lots=lots)
