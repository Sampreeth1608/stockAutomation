"""Live-money readiness for the control panel (gates, size, .env writes).

Paper 100 lots is not live size: live qty = min(capital.max_lots, LIVE_MAX_LOTS).
DRY_RUN=false from this panel requires typing LIVE. LIVE_MAX_LOTS is capped at 10.
Save writes .env only; Restart supervise loads it into the bot.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from control_state import SLIM_PAPER_STRATEGIES, is_live_mode_allowed, load_state
from operator_desk import OPERATOR_PANEL, OPERATOR_URL
from live_orders import live_lots, live_lots_for
from position_safety import read_bot_health

IST = ZoneInfo("Asia/Kolkata")
PANEL_LIVE_MAX_LOTS = 10
LIVE_CONFIRM_WORD = "LIVE"
RESTART_CONFIRM_WORD = "RESTART"

# S4 daily HH/LL lost the Angel/ticks backtest to S13 (daily S16). Stay off.
DESK_FORCE_OFF = frozenset({"S4_OVERNIGHT"})
PAPER_ONLY_BOOKS = frozenset({"S18_OHLC_VOL_HTF"})


def _truthy(raw: str | None, default: str = "true") -> bool:
    v = (raw if raw is not None else default).strip().lower()
    return v in {"1", "true", "yes", "y"}


def read_live_env(*, path: Path | None = None) -> dict[str, Any]:
    """DRY_RUN / LIVE_MAX_LOTS from .env, falling back to process env."""
    from analytics.env_bridge import read_env

    e = read_env(path)
    dry_raw = e.get("DRY_RUN")
    if dry_raw is None:
        dry_raw = os.getenv("DRY_RUN", "true")
    lots_raw = e.get("LIVE_MAX_LOTS")
    if lots_raw is None:
        lots_raw = os.getenv("LIVE_MAX_LOTS", "1")
    try:
        lots = max(1, int(float(lots_raw or "1")))
    except ValueError:
        lots = 1
    return {"dry_run": _truthy(str(dry_raw)), "live_max_lots": lots}


def _dry_run() -> bool:
    return bool(read_live_env()["dry_run"])


def _live_max() -> int:
    return int(read_live_env()["live_max_lots"])


def apply_panel_live_env(
    *,
    dry_run: bool,
    live_max_lots: int,
    confirm: str = "",
    path: Path | None = None,
    sync_environ: bool = True,
) -> dict[str, Any]:
    """Write DRY_RUN + LIVE_MAX_LOTS. DRY_RUN=false requires confirm==LIVE. Lots 1–10."""
    try:
        lots = int(live_max_lots)
    except (TypeError, ValueError):
        return {"ok": False, "error": "LIVE_MAX_LOTS must be an integer"}
    if lots < 1 or lots > PANEL_LIVE_MAX_LOTS:
        return {
            "ok": False,
            "error": (
                f"LIVE_MAX_LOTS from this panel must be 1–{PANEL_LIVE_MAX_LOTS} "
                f"(got {lots}). Paper 100 is not live size."
            ),
        }
    if not dry_run and str(confirm).strip() != LIVE_CONFIRM_WORD:
        return {
            "ok": False,
            "error": "Type LIVE to set DRY_RUN=false. Leave Paper only checked for paper.",
        }
    from analytics.env_bridge import write_env_updates

    applied_dry = "true" if dry_run else "false"
    res = write_env_updates(
        {"DRY_RUN": applied_dry, "LIVE_MAX_LOTS": lots},
        path=path,
    )
    if res.get("ok") and sync_environ:
        os.environ["DRY_RUN"] = applied_dry
        os.environ["LIVE_MAX_LOTS"] = str(lots)
    res["restart_needed"] = True
    res["note"] = (
        "Saved .env. Restart supervise to load into the bot. "
        "This save does not place Angel orders."
    )
    return res


def panel_restart_allowed(confirm: str) -> tuple[bool, str]:
    if str(confirm).strip() != RESTART_CONFIRM_WORD:
        return False, "Type RESTART to restart supervise"
    return True, "ok"


def apply_desk_books(
    in_bot: list[str],
    live: list[str],
    *,
    path: Path | None = None,
    state_path: Path | None = None,
) -> dict[str, Any]:
    """One save: ENABLE_* (in bot) + live_approved. Live pick requires in-bot.

    Does not change DRY_RUN, does not restart, does not unlock live.
    """
    from control_state import SLIM_PAPER_STRATEGIES, set_live_approved

    known = list(SLIM_PAPER_STRATEGIES)
    in_set = [str(n).strip() for n in in_bot if str(n).strip() in known]
    live_raw = [str(n).strip() for n in live if str(n).strip() in known]
    live_set = [n for n in live_raw if n in in_set and n not in PAPER_ONLY_BOOKS]
    skipped = [n for n in live_raw if n not in in_set]
    skipped_paper_only = [n for n in live_raw if n in PAPER_ONLY_BOOKS]
    if skipped_paper_only:
        skipped = list(dict.fromkeys(skipped + skipped_paper_only))
    en = apply_panel_enables(in_set, path=path)
    st = set_live_approved(
        live_set,
        path=state_path,
        note="desk books: live picks (still need unlock + DRY_RUN=false)",
    )
    return {
        "ok": bool(en.get("ok")),
        "enabled": in_set,
        "live_approved": list(st.live_approved),
        "skipped_live_not_in_bot": skipped,
        "restart_needed": True,
        "applied": en.get("applied") or {},
        "note": (
            "Saved In-bot and Live picks. Restart the engine to load In-bot into RAM. "
            "Live picks do not send Angel orders until money is LIVE and unlocked. "
            + (
                f"Ignored live picks not in-bot: {', '.join(skipped)}. "
                if skipped
                else ""
            )
        ),
    }


def apply_panel_enables(
    enabled_names: list[str],
    *,
    path: Path | None = None,
) -> dict[str, Any]:
    """Write ENABLE_* for known strategies. Unchecked known names become false."""
    from analytics.env_bridge import STRATEGY_ENABLE, apply_strategy_enables

    known = list(STRATEGY_ENABLE.keys())
    want = [
        str(n).strip()
        for n in enabled_names
        if str(n).strip() in STRATEGY_ENABLE and str(n).strip() not in DESK_FORCE_OFF
    ]
    res = apply_strategy_enables(want, known=known, path=path)
    res["enabled"] = want
    res["restart_needed"] = True
    res["note"] = (
        "Saved ENABLE_*. Restart supervise to load into RAM. "
        "This is not live-approved and does not set DRY_RUN."
    )
    return res


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


def desk_snapshot() -> dict[str, Any]:
    """Minimal Desk-page state. No scoreboard, no per-book live qty, no checklist."""
    st = load_state()
    env = read_live_env()
    dry = bool(env["dry_run"])
    live_ok, _why = is_live_mode_allowed()
    approved = list(st.live_approved or [])
    from analytics.env_bridge import strategy_enable_snapshot

    enables_all = strategy_enable_snapshot()
    enables = {name: bool(enables_all.get(name)) for name in SLIM_PAPER_STRATEGIES}
    books = [
        {"strategy": name, "live_approved": name in approved}
        for name in SLIM_PAPER_STRATEGIES
    ]
    return {
        "dry_run": dry,
        "live_max_lots": env["live_max_lots"],
        "enables": enables,
        "books": books,
        "would_place_real_orders": bool(live_ok and not dry and approved),
    }


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
                "still true — paper only. Uncheck Paper only, type LIVE, Save, then Restart."
                if dry
                else "false — Angel orders can fire when other gates pass + after Restart"
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
    from analytics.env_bridge import strategy_enable_snapshot

    enables_all = strategy_enable_snapshot()
    enables = {name: bool(enables_all.get(name)) for name in SLIM_PAPER_STRATEGIES}
    return {
        "operator_panel": OPERATOR_PANEL,
        "operator_url": OPERATOR_URL,
        "dry_run": dry,
        "live_allowed": [live_ok, live_why],
        "live_max_lots": cap,
        "default_live_lots": live_lots(),
        "would_place_real_orders": bool(live_ok and not dry and approved),
        "steps_ok": n_ok,
        "steps_n": len(steps),
        "steps": steps,
        "enables": enables,
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
            f"Operator desk is {OPERATOR_PANEL} ({OPERATOR_URL}). "
            "All of: emergency clear, trading ON, Unlock live, live_approved, "
            "DRY_RUN=false, Restart supervise. Size is LIVE_MAX_LOTS (panel cap 10), not paper 100."
        ),
    }
