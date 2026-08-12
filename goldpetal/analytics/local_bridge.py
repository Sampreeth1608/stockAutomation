"""Local (on-VM) control actions for Streamlit desk — no gcloud."""

from __future__ import annotations

from pathlib import Path
from typing import Any


SLIM_STRATEGIES = (
    "S4_OVERNIGHT",
    "S5_MINEDGE",
    "S8_NET_ZIGZAG",
    "S11_DISCOVERED",
    "S12_HHHL30",
    "S13_HHHL_DAY",
)


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
        plan.daily_loss_limit_inr = float(payload["day_loss_limit_inr"])
    if "daily_loss_limit_inr" in payload:
        plan.daily_loss_limit_inr = float(payload["daily_loss_limit_inr"])
    if "max_lots_total" in payload:
        plan.max_lots_total = int(payload["max_lots_total"])
    save_capital(plan)
    for row in _strategy_rows(payload.get("strategies")):
        update_strategy_budget(
            row["strategy"],
            budget_inr=float(row.get("budget_inr", 0)),
            max_lots=int(row.get("max_lots", 1)),
            max_open_trades=int(row.get("max_open_trades", 1)),
            enabled=bool(row.get("enabled", True)),
        )
    return {"ok": True, "capital": capital_snapshot()}


def save_live_allocation_local(payload: dict[str, Any]) -> dict[str, Any]:
    """Select live strategies + assign ₹ capital / lot caps for real trades."""
    from capital import apply_live_capital_allocation, capital_snapshot
    from control_state import (
        is_live_mode_allowed,
        load_state,
        set_live_approved,
        set_paper_allowlist,
    )

    allocations = _strategy_rows(payload.get("allocations") or payload.get("strategies"))
    live_names = [
        str(row["strategy"])
        for row in allocations
        if bool(row.get("live", row.get("enabled", True)))
    ]
    # If caller passed explicit live_approved list, prefer it.
    if "live_approved" in payload:
        live_names = [
            str(s).strip() for s in (payload.get("live_approved") or []) if str(s).strip()
        ]

    st = set_live_approved(live_names, note=str(payload.get("note") or "desk live allocation"))
    if payload.get("paper_allowlist") is not None:
        paper_names = [
            str(s).strip() for s in (payload.get("paper_allowlist") or []) if str(s).strip()
        ]
        st = set_paper_allowlist(
            paper_names,
            note=str(payload.get("paper_note") or "desk paper allowlist"),
        )
    apply_live_capital_allocation(
        allocations,
        total_capital_inr=(
            float(payload["total_capital_inr"]) if "total_capital_inr" in payload else None
        ),
        cash_reserve_pct=(
            float(payload["cash_reserve_pct"]) if "cash_reserve_pct" in payload else None
        ),
        daily_loss_limit_inr=(
            float(payload.get("daily_loss_limit_inr", payload.get("day_loss_limit_inr")))
            if ("daily_loss_limit_inr" in payload or "day_loss_limit_inr" in payload)
            else None
        ),
        max_lots_total=(
            int(payload["max_lots_total"]) if "max_lots_total" in payload else None
        ),
        disable_others=bool(payload.get("disable_others", False)),
        known_strategies=SLIM_STRATEGIES,
    )
    live_ok, live_why = is_live_mode_allowed()
    st = load_state()
    return {
        "ok": True,
        "live_approved": list(st.live_approved),
        "force_disabled": list(st.force_disabled),
        "paper_approved": list(st.paper_approved),
        "live_mode_ok": live_ok,
        "live_mode_reason": live_why,
        "capital": capital_snapshot(),
        "note": st.note,
        "reminder": (
            "Live orders still need: Unlock live in Control + DRY_RUN=false on VM .env. "
            "Order size = strategy max_lots capped by LIVE_MAX_LOTS. "
            "For paper: set ENABLE_S9=false (etc.) in .env and restart supervise so "
            "force-disabled strategies are not loaded."
        ),
    }


def save_paper_allowlist_local(payload: dict[str, Any]) -> dict[str, Any]:
    """Lock paper trading to an explicit strategy allowlist."""
    from control_state import load_state, set_paper_allowlist

    names = [str(s).strip() for s in (payload.get("paper_allowlist") or []) if str(s).strip()]
    st = set_paper_allowlist(
        names,
        note=str(payload.get("note") or "desk paper allowlist"),
    )
    return {
        "ok": True,
        "paper_allowlist": names,
        "force_disabled": list(st.force_disabled),
        "paper_approved": list(st.paper_approved),
        "note": st.note,
        "reminder": (
            "Also set ENABLE_S1/S2/S3/S6/S9/S10=false in .env and restart supervise "
            "so those strategies stop emitting completely."
        ),
    }


def _strategy_rows(raw: Any) -> list[dict[str, Any]]:
    """Normalize capital strategies dict|list into row dicts."""
    if raw is None:
        return []
    if isinstance(raw, dict):
        rows: list[dict[str, Any]] = []
        for key, val in raw.items():
            if isinstance(val, dict):
                row = dict(val)
                row.setdefault("strategy", key)
                rows.append(row)
            else:
                rows.append({"strategy": str(key)})
        return rows
    if isinstance(raw, list):
        out: list[dict[str, Any]] = []
        for item in raw:
            if isinstance(item, dict) and item.get("strategy"):
                out.append(dict(item))
            elif isinstance(item, str) and item.strip():
                out.append({"strategy": item.strip()})
        return out
    return []


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
