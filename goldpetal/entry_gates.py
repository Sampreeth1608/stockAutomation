"""Unified entry gates: emergency, trading master, capital, force-disable, ML."""

from __future__ import annotations

from typing import Any

from capital import can_open_trade
from control_state import strategy_entries_allowed


def allow_new_entry(
    strategy: str,
    lots: int = 1,
    features: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    """Return (ok, reason). CLOSE is never gated here — only BUY/SHORT."""
    ok, reason = strategy_entries_allowed(strategy)
    if not ok:
        return False, reason
    ok, reason = can_open_trade(strategy, lots=lots)
    if not ok:
        return False, reason
    from trade_learner import get_learner

    feat = dict(features or {})
    feat.setdefault("strategy", strategy)
    return get_learner().allow(strategy, feat)
