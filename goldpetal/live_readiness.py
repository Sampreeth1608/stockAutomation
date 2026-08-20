"""Live-money readiness for the control panel (gates, size, .env writes).

Paper 100 lots is not live size: tick Lots (contracts) or tick ₹ (budget / LTP).
Both are capped by LIVE_MAX_LOTS (1–10).
DRY_RUN=false from this panel requires typing LIVE. LIVE_MAX_LOTS is capped at 10.
Save writes .env only; Restart supervise loads it into the bot.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from control_state import (
    is_live_mode_allowed,
    load_state,
    paper_strategy_names,
    set_live_unlocked,
)
from operator_desk import OPERATOR_PANEL, OPERATOR_URL
from live_orders import live_lots, live_lots_for
from position_safety import read_bot_health

IST = ZoneInfo("Asia/Kolkata")
PANEL_LIVE_MAX_LOTS = 10
LIVE_CONFIRM_WORD = "LIVE"
RESTART_CONFIRM_WORD = "RESTART"

# S4 daily HH/LL lost the Angel/ticks backtest to S13 (daily S16). Stay off.
DESK_FORCE_OFF = frozenset({"S4_OVERNIGHT"})
PAPER_ONLY_BOOKS = frozenset(
    {"S18_OHLC_VOL_HTF", "S19_BODY_CLOSE_1H", "S20_FADE_HL", "FLOW_BRAIN"}
)
# First live-capital test: 1 lot, these four. Other paper books may join
# the Live tab (and Angel) only after closed trades and WR% AC ≥ 40.
LIVE_ELIGIBLE_BOOKS = frozenset(
    {"S5_MINEDGE", "S8_NET_ZIGZAG", "S13_HHHL_DAY", "S16_HHHL_WICK_1H"}
)
LIVE_WR_MIN_PCT = 40.0
LIVE_MIN_CLOSED = 1
NEVER_LIVE_BOOKS = DESK_FORCE_OFF | frozenset({"FLOW_BRAIN"})


def summary_qualifies_live(summary: dict[str, Any] | None) -> bool:
    """Closed paper trades and WR% after Angel charges (tax excluded) ≥ 40."""
    if not summary:
        return False
    try:
        closed = int(summary.get("closed") or 0)
    except (TypeError, ValueError):
        closed = 0
    if closed < LIVE_MIN_CLOSED:
        return False
    raw = summary.get("win_rate_after_charges")
    if raw is None or raw == "":
        raw = summary.get("win_rate")
    try:
        wr = float(raw)
    except (TypeError, ValueError):
        return False
    return wr >= LIVE_WR_MIN_PCT


def paper_summaries_for_live() -> dict[str, dict[str, Any]]:
    try:
        from desk_data import paper_strategy_summaries

        return dict(paper_strategy_summaries() or {})
    except Exception:
        return {}


def qualified_live_names(
    *, summaries: dict[str, dict[str, Any]] | None = None
) -> frozenset[str]:
    stats = summaries if summaries is not None else paper_summaries_for_live()
    names: list[str] = []
    for name in paper_strategy_names():
        if name in NEVER_LIVE_BOOKS:
            continue
        if summary_qualifies_live(stats.get(name)):
            names.append(name)
    return frozenset(names)


def book_may_go_live(
    name: str,
    *,
    summaries: dict[str, dict[str, Any]] | None = None,
) -> bool:
    """Angel may use this book: seed four, or any paper book at ≥40% WR% AC."""
    n = str(name).strip()
    if n in NEVER_LIVE_BOOKS or n not in set(paper_strategy_names()):
        return False
    if n in LIVE_ELIGIBLE_BOOKS:
        return True
    return n in qualified_live_names(summaries=summaries)


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


def current_in_bot_names(*, path: Path | None = None) -> list[str]:
    """ENABLE_* true names already in paper. Live tab does not edit this list."""
    from analytics.env_bridge import strategy_enable_snapshot

    snap = strategy_enable_snapshot(path=path)
    return [
        name
        for name in paper_strategy_names()
        if snap.get(name) and name not in DESK_FORCE_OFF
    ]


def apply_desk_books(
    in_bot: list[str],
    live: list[str],
    *,
    path: Path | None = None,
    state_path: Path | None = None,
    qualified: list[str] | frozenset[str] | None = None,
) -> dict[str, Any]:
    """One save: ENABLE_* (in bot) + live_approved. Live pick requires in-bot.

    Does not change DRY_RUN, does not restart, does not unlock live.
    Live pick is seed four, or a paper book that currently qualifies at 40% WR% AC.
    """
    from control_state import load_state, paper_strategy_names, set_live_approved

    known = list(paper_strategy_names())
    in_set = [str(n).strip() for n in in_bot if str(n).strip() in known]
    live_raw = [str(n).strip() for n in live if str(n).strip() in known]
    extra = (
        {str(n).strip() for n in qualified if str(n).strip()}
        if qualified is not None
        else set(qualified_live_names())
    )
    prev = set(load_state(path=state_path).live_approved or [])
    allowed = (set(LIVE_ELIGIBLE_BOOKS) | extra | prev) - set(NEVER_LIVE_BOOKS)
    live_set = [n for n in live_raw if n in in_set and n in allowed]
    skipped = [n for n in live_raw if n not in live_set]
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
                f"Ignored live picks: {', '.join(skipped)}. "
                if skipped
                else ""
            )
        ),
    }


def apply_desk_arm(
    *,
    mode: str,
    confirm: str = "",
    live_max_lots: int = 1,
    in_bot: list[str] | None = None,
    live: list[str] | None = None,
    total_capital_inr: float | None = None,
    daily_loss_limit_inr: float | None = None,
    allocations: list[dict[str, Any]] | None = None,
    live_size_mode: str | None = None,
    path: Path | None = None,
    state_path: Path | None = None,
    capital_path: Path | None = None,
) -> dict[str, Any]:
    """One save: live picks + Lots or ₹ per book + Paper or Live. Type LIVE to arm Angel.

    Tick Lots or tick ₹ on a row — that book is the live pick. Lots = contracts.
    ₹ = floor(budget / LTP), then ceiling lots. Do not write empty ₹ as 0.
    Paper: DRY_RUN=true and live stays locked. Live: DRY_RUN=false and unlock.
    Does not restart the bot. Empty in_bot keeps current ENABLE_* (Approve
    already papers). Live picks are unioned into in_bot so Angel is not skipped.
    Does not ENABLE research books that are not live-eligible.
    """
    want = str(mode or "paper").strip().lower()
    if want in {"armed", "angel", "on"}:
        want = "live"
    if want not in {"paper", "live"}:
        return {"ok": False, "error": "mode must be paper or live"}
    if want == "live" and str(confirm or "").strip() != LIVE_CONFIRM_WORD:
        return {
            "ok": False,
            "error": "Type LIVE to arm. Paper stays on until you do.",
        }

    incoming = [str(n).strip() for n in (in_bot or []) if str(n).strip()]
    live_names = [str(n).strip() for n in (live or []) if str(n).strip()]
    if not incoming:
        incoming = current_in_bot_names(path=path)
    can_live = set(LIVE_ELIGIBLE_BOOKS) | set(qualified_live_names())
    for name in live_names:
        if name in can_live and name not in incoming:
            incoming.append(name)
    books = apply_desk_books(
        incoming,
        live_names,
        path=path,
        state_path=state_path,
    )
    if not books.get("ok"):
        return {**books, "ok": False}

    from capital import apply_live_capital_allocation, capital_snapshot

    alloc_rows = [
        row
        for row in (allocations or [])
        if isinstance(row, dict) and str(row.get("strategy") or "").strip()
    ]
    if (
        alloc_rows
        or total_capital_inr is not None
        or daily_loss_limit_inr is not None
    ):
        apply_live_capital_allocation(
            alloc_rows,
            total_capital_inr=total_capital_inr,
            daily_loss_limit_inr=daily_loss_limit_inr,
            live_size_mode=live_size_mode,
            disable_others=False,
            path=capital_path,
        )

    dry = want != "live"
    env = apply_panel_live_env(
        dry_run=dry,
        live_max_lots=live_max_lots,
        confirm=confirm,
        path=path,
        sync_environ=path is None,
    )
    if not env.get("ok"):
        return {
            "ok": False,
            "error": env.get("error") or "money save failed",
            "books": books,
            "env": env,
        }

    if want == "live":
        st = set_live_unlocked(True, path=state_path, note="desk Arm live")
        live_note = (
            "ARMED setup saved. Type RESTART on Engine so the bot loads it. "
            "Angel fires only on live-picked books at ≥40% WR% AC (seed four S5/S8/S13/S16 included)."
        )
        if not books.get("live_approved"):
            live_note += " No live book is picked yet — check Live on those rows first."
    else:
        st = set_live_unlocked(False, path=state_path, note="desk Paper mode")
        live_note = (
            "Paper mode saved. Angel is off. Type RESTART to load RAM."
        )

    return {
        "ok": True,
        "mode": want,
        "books": books,
        "env": env,
        "state": st.to_dict(),
        "capital": capital_snapshot(path=capital_path),
        "live_desk": live_readiness(),
        "restart_needed": True,
        "note": live_note,
    }


def apply_panel_enables(
    enabled_names: list[str],
    *,
    path: Path | None = None,
) -> dict[str, Any]:
    """Write ENABLE_* for known strategies. Unchecked known names become false."""
    from analytics.env_bridge import apply_strategy_enables, strategy_enable_map

    mapping = strategy_enable_map()
    known = list(mapping.keys())
    want = [
        str(n).strip()
        for n in enabled_names
        if str(n).strip() in mapping and str(n).strip() not in DESK_FORCE_OFF
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


def _live_book_row(
    name: str,
    *,
    approved: list[str],
    summaries: dict[str, dict[str, Any]],
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    sc = summaries.get(name) or {}
    qualifies = name not in NEVER_LIVE_BOOKS and summary_qualifies_live(sc)
    was_picked = name in approved and name not in NEVER_LIVE_BOOKS
    closed = sc.get("closed")
    try:
        closed_n = int(closed or 0)
    except (TypeError, ValueError):
        closed_n = 0
    wr = sc.get("win_rate_after_charges")
    if wr is None or wr == "":
        wr = sc.get("win_rate")
    row = {
        "strategy": name,
        "live_approved": was_picked,
        "live_eligible": bool(qualifies or was_picked),
        "qualifies_live": bool(qualifies),
        "closed": closed_n,
        "win_rate_after_charges": wr,
        "pnl_after_charges": sc.get("pnl_after_charges"),
    }
    if extra:
        row.update(extra)
    return row


def desk_snapshot(*, summaries: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """Minimal Desk-page state. No full scoreboard, no checklist."""
    st = load_state()
    env = read_live_env()
    dry = bool(env["dry_run"])
    live_ok, _why = is_live_mode_allowed()
    approved = list(st.live_approved or [])
    stats = summaries if summaries is not None else paper_summaries_for_live()
    armed = any(book_may_go_live(n, summaries=stats) for n in approved)
    from analytics.env_bridge import strategy_enable_snapshot

    enables_all = strategy_enable_snapshot()
    try:
        from capital import load_capital, live_qty_for, normalize_size_mode

        plan = load_capital()
        cap = int(env["live_max_lots"] or 1)
    except Exception:
        plan = None
        cap = 1
    books = []
    for name in paper_strategy_names():
        extra: dict[str, Any] = {}
        if plan is not None:
            sb = plan.strategies.get(name)
            mode = normalize_size_mode(sb.live_size_mode) if sb else ""
            if name in approved and not mode:
                mode = "lots"
            extra = {
                "live_size_mode": mode,
                "live_lots": int(sb.live_lots) if sb and int(sb.live_lots or 0) > 0 else 1,
                "budget_inr": float(sb.budget_inr) if sb is not None else 50_000.0,
                "live_qty": live_qty_for(name, cap=cap, plan=plan) if name in approved else 0,
            }
        books.append(
            _live_book_row(name, approved=approved, summaries=stats, extra=extra or None)
        )
    enables = {name: bool(enables_all.get(name)) for name in paper_strategy_names()}
    return {
        "dry_run": dry,
        "live_max_lots": env["live_max_lots"],
        "enables": enables,
        "books": books,
        "live_wr_min_pct": LIVE_WR_MIN_PCT,
        "would_place_real_orders": bool(live_ok and not dry and armed),
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
    stats = paper_summaries_for_live()
    armed = any(book_may_go_live(n, summaries=stats) for n in approved)

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
            "ok": armed,
            "label": "At least one strategy live-approved",
            "detail": (
                ", ".join(n for n in approved if book_may_go_live(n, summaries=stats))
                if any(book_may_go_live(n, summaries=stats) for n in approved)
                else "none — paper stays paper. Live tab lists books at ≥40% WR% AC."
            ),
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
    for name in paper_strategy_names():
        sb = plan.strategies.get(name)
        paper_lots = int(sb.max_lots) if sb is not None else 0
        on_live = name in approved and book_may_go_live(name, summaries=stats)
        qty = live_lots_for(name) if on_live else 0
        ram_key = name.split("_")[0]  # S14 from S14_WICK30_STRICT
        ram_pos = ram.get(ram_key) or ram.get(name) or "—"
        books.append(
            _live_book_row(
                name,
                approved=approved,
                summaries=stats,
                extra={
                    "paper_max_lots": paper_lots,
                    "live_qty": qty,
                    "ram": ram_pos,
                    "warn_100": paper_lots >= 100,
                },
            )
        )

    n_ok = sum(1 for s in steps if s["ok"])
    from analytics.env_bridge import strategy_enable_snapshot

    enables_all = strategy_enable_snapshot()
    enables = {name: bool(enables_all.get(name)) for name in paper_strategy_names()}
    return {
        "operator_panel": OPERATOR_PANEL,
        "operator_url": OPERATOR_URL,
        "dry_run": dry,
        "live_allowed": [live_ok, live_why],
        "live_max_lots": cap,
        "default_live_lots": live_lots(),
        "would_place_real_orders": bool(live_ok and not dry and armed),
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
            "DRY_RUN=false, Restart supervise. First live test: S5/S8/S13/S16 at LIVE_MAX_LOTS=1. "
            "S18/S19/S20/AMISE stay paper. Size is LIVE_MAX_LOTS (panel cap 10), not paper 100."
        ),
    }
