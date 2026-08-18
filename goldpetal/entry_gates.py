"""Unified entry gates: emergency, trading master, capital, force-disable, ML."""

from __future__ import annotations

from typing import Any

from capital import can_open_trade
from charges import paper_lots
from control_state import strategy_entries_allowed


def allow_new_entry(
    strategy: str,
    lots: int | None = None,
    features: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    """Return (ok, reason). CLOSE is never gated here — only BUY/SHORT."""
    ok, reason = strategy_entries_allowed(strategy)
    if not ok:
        return False, reason
    n = int(lots) if lots is not None else int(paper_lots())
    try:
        from capital import load_capital

        sb = load_capital().strategies.get(strategy)
        if sb is not None and int(sb.max_lots) > 0:
            n = min(n, int(sb.max_lots))
    except Exception:
        pass
    ok, reason = can_open_trade(strategy, lots=max(1, n))
    if not ok:
        return False, reason
    from trade_learner import get_learner

    feat = dict(features or {})
    feat.setdefault("strategy", strategy)
    return get_learner().allow(strategy, feat)
