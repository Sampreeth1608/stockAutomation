"""Local (on-VM) control actions for Streamlit desk — no gcloud."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def decide_proposal_local(proposal_id: str, decision: str, note: str = "") -> dict[str, Any]:
    from proposals import decide_proposal

    p = decide_proposal(proposal_id, decision, note=note)
    return {
        "ok": True,
        "id": p.id,
        "strategy": p.strategy,
        "status": p.status,
        "safety_ok": p.safety_ok,
        "env_patch": p.env_patch,
        "title": p.title,
    }


def set_control_local(
    *,
    emergency_off: bool | None = None,
    trading_enabled: bool | None = None,
    live_unlocked: bool | None = None,
) -> dict[str, Any]:
    from control_state import (
        load_state,
        set_emergency,
        set_live_unlocked,
        set_trading_enabled,
    )

    if emergency_off is not None:
        set_emergency(bool(emergency_off))
    if trading_enabled is not None:
        set_trading_enabled(bool(trading_enabled))
    if live_unlocked is not None:
        set_live_unlocked(bool(live_unlocked))
    return {"ok": True, **load_state().to_dict()}


def save_capital_local(payload: dict[str, Any]) -> dict[str, Any]:
    from capital import capital_snapshot, load_capital, save_capital, update_strategy_budget

    plan = load_capital()
    if "total_capital_inr" in payload:
        plan.total_capital_inr = float(payload["total_capital_inr"])
    if "cash_reserve_pct" in payload:
        plan.cash_reserve_pct = float(payload["cash_reserve_pct"])
    if "day_loss_limit_inr" in payload:
        plan.day_loss_limit_inr = float(payload["day_loss_limit_inr"])
    if "max_lots_total" in payload:
        plan.max_lots_total = int(payload["max_lots_total"])
    save_capital(plan)
    for row in payload.get("strategies") or []:
        update_strategy_budget(
            row["strategy"],
            budget_inr=float(row.get("budget_inr", 0)),
            max_lots=int(row.get("max_lots", 1)),
            max_open_trades=int(row.get("max_open_trades", 1)),
            enabled=bool(row.get("enabled", True)),
        )
    return {"ok": True, "capital": capital_snapshot()}


def desk_data_dir() -> Path:
    import os

    raw = os.getenv("GP_DATA_DIR", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    # On VM: use live data/. On Mac snapshot: analytics_mac if present.
    root = Path(__file__).resolve().parents[1]
    local = root / "data"
    mac = root / "data" / "analytics_mac"
    if os.getenv("GP_DESK_LOCAL", "").strip().lower() in {"1", "true", "yes", "y"}:
        return local
    if (local / "ticks.db").exists() and not (mac / "ticks.db").exists():
        return local
    if mac.exists():
        return mac
    return local
