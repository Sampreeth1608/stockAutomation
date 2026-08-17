"""Live-money readiness for the control panel (read-only gates + size).

Does not arm DRY_RUN=false. Paper 100 lots is not live size:
live qty = min(capital.max_lots, LIVE_MAX_LOTS).
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from control_state import SLIM_PAPER_STRATEGIES, is_live_mode_allowed, load_state
from live_orders import live_lots, live_lots_for
from position_safety import read_bot_health

IST = ZoneInfo("Asia/Kolkata")


def _dry_run() -> bool:
    return os.getenv("DRY_RUN", "true").strip().lower() in {"1", "true", "yes", "y"}


def _live_max() -> int:
    try:
        return max(1, int(os.getenv("LIVE_MAX_LOTS", "1") or "1"))
    except ValueError:
        return 1


def bot_age_seconds(health: dict[str, Any], *, now: datetime | None = None) -> float | None:
    raw = str(health.get("ts_ist") or "")
    if not raw:
        return None
    try:
        ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=IST)
    now = now or datetime.now(IST)
    return (now - ts.astimezone(IST)).total_seconds()


def live_readiness(*, now: datetime | None = None) -> dict[str, Any]:
    """Checklist + per-strategy live size for the 8787 panel."""
    st = load_state()
    dry = _dry_run()
    live_ok, live_why = is_live_mode_allowed()
    health = read_bot_health()
    age = bot_age_seconds(health, now=now)
    bot_ok = age is not None and age <= 180
    cap = _live_max()
    approved = list(st.live_approved or [])

    steps = [
        {
            "id": "emergency",
            "ok": not bool(st.emergency_off),
            "label": "Emergency clear",
            "detail": "OFF — entries blocked" if st.emergency_off else "clear",
        },
        {
            "id": "trading",
            "ok": bool(st.trading_enabled),
            "label": "Trading enabled",
            "detail": "ON" if st.trading_enabled else "OFF",
        },
        {
            "id": "dry_run",
            "ok": not dry,
            "label": "DRY_RUN=false in .env",
            "detail": (
                "still true — paper only. Edit .env then restart supervise. "
                "Do not flip this until you mean real money."
                if dry
                else "false — Angel orders can fire when other gates pass"
            ),
        },
        {
            "id": "unlocked",
            "ok": bool(st.live_unlocked),
            "label": "Live unlocked on this panel",
            "detail": "unlocked" if st.live_unlocked else "locked (safe)",
        },
        {
            "id": "approved",
            "ok": bool(approved),
            "label": "At least one strategy live-approved",
            "detail": ", ".join(approved) if approved else "none — paper stays paper",
        },
        {
            "id": "lots_cap",
            "ok": True,
            "label": f"LIVE_MAX_LOTS={cap} (hard ceiling)",
            "detail": (
                "Start live at 1. Paper 100 lots on S12/S14/S15 is NOT live size. "
                f"Live qty = min(strategy max_lots, {cap})."
            ),
        },
        {
            "id": "bot",
            "ok": bot_ok,
            "label": "supervise heartbeat",
            "detail": (
                f"age={age:.0f}s event={health.get('event') or '—'}"
                if age is not None
                else "no bot_health.json — is supervise running?"
            ),
        },
    ]

    from capital import load_capital

    plan = load_capital()
    ram = (health.get("positions") or {}) if isinstance(health.get("positions"), dict) else {}
    books: list[dict[str, Any]] = []
    for name in SLIM_PAPER_STRATEGIES:
        sb = plan.strategies.get(name)
        paper_lots = int(sb.max_lots) if sb is not None else 0
        on_live = name in approved
        qty = live_lots_for(name) if on_live else 0
        ram_key = name.split("_")[0]  # S14 from S14_WICK30_STRICT
        ram_pos = ram.get(ram_key) or ram.get(name) or "—"
        books.append(
            {
                "strategy": name,
                "paper_max_lots": paper_lots,
                "live_approved": on_live,
                "live_qty": qty,
                "ram": ram_pos,
                "warn_100": paper_lots >= 100,
            }
        )

    n_ok = sum(1 for s in steps if s["ok"])
    return {
        "dry_run": dry,
        "live_allowed": [live_ok, live_why],
        "live_max_lots": cap,
        "default_live_lots": live_lots(),
        "would_place_real_orders": bool(live_ok and not dry and approved),
        "steps_ok": n_ok,
        "steps_n": len(steps),
        "steps": steps,
        "books": books,
        "bot_health": {
            "event": health.get("event"),
            "ts_ist": health.get("ts_ist"),
            "age_sec": None if age is None else round(age, 1),
            "ltp": health.get("ltp"),
            "regime": health.get("regime"),
            "positions": ram,
            "alive": bot_ok,
        },
        "note": (
            "All of: emergency clear, trading ON, Unlock live, live_approved, "
            "DRY_RUN=false, restart supervise. Size is LIVE_MAX_LOTS, not paper 100."
        ),
    }
