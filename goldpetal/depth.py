"""Helpers to sum best-5 depth buy/sell quantities."""

from __future__ import annotations

from typing import Any


def _qty(item: Any) -> float:
    if not isinstance(item, dict):
        return 0.0
    for key in ("quantity", "qty", "Quantity", "size"):
        if key in item and item[key] is not None:
            try:
                return float(item[key])
            except (TypeError, ValueError):
                return 0.0
    return 0.0


def _collect_by_flag(message: dict[str, Any]) -> tuple[list[float], list[float]] | None:
    """Prefer flag-based split: flag 0 = buy, else sell (Angel packet meaning)."""
    buys: list[float] = []
    sells: list[float] = []
    found_flag = False
    for key in ("best_5_buy_data", "best_5_sell_data"):
        levels = message.get(key) or []
        if not isinstance(levels, list):
            continue
        for item in levels:
            if not isinstance(item, dict) or "flag" not in item:
                continue
            found_flag = True
            q = _qty(item)
            if int(item.get("flag", -1)) == 0:
                buys.append(q)
            else:
                sells.append(q)
    if not found_flag:
        return None
    return buys[:5], sells[:5]


def depth_buy_sell_sums(message: dict[str, Any]) -> tuple[float, float, dict[str, float]]:
    """Return (buy_sum, sell_sum, details) from buy1-5 / sell1-5 qty."""
    details: dict[str, float] = {}
    buy_qtys: list[float] = []
    sell_qtys: list[float] = []

    flagged = _collect_by_flag(message)
    if flagged is not None:
        buy_qtys, sell_qtys = flagged
    else:
        # Fallback to labeled arrays (may be swapped in some SDK versions).
        buy_levels = message.get("best_5_buy_data") or []
        sell_levels = message.get("best_5_sell_data") or []
        if isinstance(buy_levels, list):
            buy_qtys = [_qty(x) for x in buy_levels[:5]]
        if isinstance(sell_levels, list):
            sell_qtys = [_qty(x) for x in sell_levels[:5]]

    # Pad to 5 for stable detail keys.
    while len(buy_qtys) < 5:
        buy_qtys.append(0.0)
    while len(sell_qtys) < 5:
        sell_qtys.append(0.0)
    buy_qtys = buy_qtys[:5]
    sell_qtys = sell_qtys[:5]

    for i, q in enumerate(buy_qtys, start=1):
        details[f"buy{i}_qty"] = q
    for i, q in enumerate(sell_qtys, start=1):
        details[f"sell{i}_qty"] = q

    buy_sum = float(sum(buy_qtys))
    sell_sum = float(sum(sell_qtys))
    details["buy_sum"] = buy_sum
    details["sell_sum"] = sell_sum
    return buy_sum, sell_sum, details
