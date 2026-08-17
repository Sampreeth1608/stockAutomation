"""Light trade history for the operator desk (no S14 / sheets / HTTP imports)."""

from __future__ import annotations

import time
from typing import Any

from control_state import SLIM_PAPER_STRATEGIES
from live_orders import recent_orders
from paper_report import summarize_trades
from storage import build_trades, count_ticks, latest_ltp, latest_signals, latest_ticks

_TRADE_CACHE: dict[str, Any] = {"at": 0.0, "rows": []}


def row_to_dict(row: Any) -> dict[str, Any]:
    if hasattr(row, "keys"):
        return {k: row[k] for k in row.keys()}
    return dict(row)


def all_trades_cached() -> list[dict[str, Any]]:
    now = time.time()
    if now - float(_TRADE_CACHE["at"]) < 12 and _TRADE_CACHE["rows"]:
        return list(_TRADE_CACHE["rows"])
    try:
        rows = build_trades(strategy=None)
    except Exception:
        rows = []
    _TRADE_CACHE["at"] = now
    _TRADE_CACHE["rows"] = rows
    return list(rows)


def recent_trades(limit: int = 8) -> list[dict[str, Any]]:
    rows = all_trades_cached()
    open_t = [t for t in rows if t.get("status") == "OPEN"]
    closed = [t for t in rows if str(t.get("status", "")).startswith("CLOSED")]
    return (open_t + list(reversed(closed)))[:limit]


def history_payload(*, limit: int = 80, strategy: str | None = None) -> dict[str, Any]:
    """Trade history, ticks, live orders, scoreboard."""
    rows = all_trades_cached()
    if strategy:
        rows = [t for t in rows if t.get("strategy") == strategy]
    open_t = [t for t in rows if t.get("status") == "OPEN"]
    closed = [t for t in rows if str(t.get("status", "")).startswith("CLOSED")]
    closed_rev = list(reversed(closed))
    all_rows = all_trades_cached()
    scoreboard = [summarize_trades(all_rows, s) for s in SLIM_PAPER_STRATEGIES]
    scoreboard.append(summarize_trades(all_rows, None))
    return {
        "strategy": strategy or "",
        "open": open_t,
        "closed": closed_rev[:limit],
        "trades": (open_t + closed_rev)[:limit],
        "total_open": len(open_t),
        "total_closed": len(closed),
        "ticks": [row_to_dict(r) for r in latest_ticks(limit=40)],
        "signals": [row_to_dict(r) for r in latest_signals(limit=40)],
        "live_orders": recent_orders(limit=40),
        "scoreboard": scoreboard,
        "tick_count": count_ticks(),
        "ltp": latest_ltp(),
    }
