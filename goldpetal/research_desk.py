"""Research Lab desk — approval station. Not ML. Not live.

The factory may write pending proposals. This module is the only place
the operator records Approve / Paper test / Reject / Investigate.
Approve does not ENABLE a book, does not paper-allowlist RESEARCH_FACTORY,
and never sets DRY_RUN=false.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from proposals import decide_proposal, get_proposal, load_proposals, save_proposals
from research_factory import LAST_RUN_PATH, LIBRARY_PATH, load_library
from strategy_genome import (
    LAB_NAME,
    RESEARCH_KIND,
    RESEARCH_STRATEGY,
    is_research_proposal,
    research_env_patch,
)

IST = ZoneInfo("Asia/Kolkata")
ROOT = Path(__file__).resolve().parent

LIVE_BLOCKED_REASON = (
    "Live approval is not on the Lab tab. The research factory cannot deploy. "
    "Approve records your yes only. Paper observation and live each need a later "
    "explicit request — this tab never sets DRY_RUN=false or ENABLE_*."
)

PAPER_REMINDER = (
    "Recorded as approved_paper. That is a queue flag only — no ENABLE_*, "
    "no paper allowlist, no Angel. Ask to wire a named book if you want it "
    "on the paper bot. Keep DRY_RUN=true."
)


def _now_iso() -> str:
    return datetime.now(IST).isoformat(timespec="seconds")


def _library_payload() -> dict[str, Any]:
    lib = load_library(LIBRARY_PATH)
    if lib:
        return lib
    if LAST_RUN_PATH.exists():
        import json

        return json.loads(LAST_RUN_PATH.read_text(encoding="utf-8"))
    return {}


def research_desk_payload(
    *,
    proposals_path: Path | None = None,
) -> dict[str, Any]:
    items = load_proposals(path=proposals_path) if proposals_path else load_proposals()
    pending = [p.to_dict() for p in items if p.status == "pending" and is_research_proposal(p)]
    decided = [
        p.to_dict()
        for p in items
        if p.status != "pending" and is_research_proposal(p)
    ]
    lib = _library_payload()
    counts = lib.get("counts") or {}
    return {
        "ok": True,
        "ts_ist": _now_iso(),
        "lab": LAB_NAME,
        "kind": RESEARCH_KIND,
        "strategy": RESEARCH_STRATEGY,
        "live_blocked": True,
        "live_blocked_reason": LIVE_BLOCKED_REASON,
        "env_patch_allowed": research_env_patch(),
        "champions": lib.get("champions") or {},
        "challengers": lib.get("challengers") or [],
        "discovery": lib.get("discovery") or [],
        "sklearn_importances": lib.get("sklearn_importances") or [],
        "tape": lib.get("tape") or {},
        "from": lib.get("from") or "",
        "to": lib.get("to") or "",
        "n_bars": lib.get("n_bars") or 0,
        "counts": {
            "found": counts.get("found", 0),
            "passed_validation": counts.get("passed_validation", 0),
            "awaiting_approval": len(pending),
            "rejected": counts.get("rejected", 0),
        },
        "pending": pending,
        "decided": decided[:30],
        "note": (
            "AI researches. You decide. New strategies found on the last lab run "
            "must beat S16 and S18 after charges, survive walk-forward, and pass "
            "2×-cost robustness before they appear here. Approve ≠ paper ENABLE. "
            "Paper ≠ live."
        ),
        "last_run_at": lib.get("updated_at_ist") or "",
    }


def _annotate_pending(proposal_id: str, note: str, *, path: Path | None = None) -> Any:
    items = load_proposals(path=path) if path else load_proposals()
    found = None
    for p in items:
        if p.id == proposal_id:
            found = p
            break
    if found is None:
        raise KeyError(f"proposal not found: {proposal_id}")
    if not is_research_proposal(found):
        raise ValueError("not a research factory proposal")
    if found.status != "pending":
        raise RuntimeError(f"proposal already decided: {found.status}")
    found.decision_note = note
    save_proposals(items, path=path) if path else save_proposals(items)
    return found


def decide_research(
    proposal_id: str,
    decision: str,
    note: str = "",
    *,
    proposals_path: Path | None = None,
) -> dict[str, Any]:
    """Approve / paper_test / reject / investigate. Never approved_live."""
    raw = str(decision or "").strip().lower()
    if raw in {"approved_live", "live"}:
        return {
            "ok": False,
            "error": LIVE_BLOCKED_REASON,
            "live_blocked": True,
        }
    found = (
        get_proposal(proposal_id, path=proposals_path)
        if proposals_path
        else get_proposal(proposal_id)
    )
    if found is None:
        raise KeyError(f"proposal not found: {proposal_id}")
    if not is_research_proposal(found):
        return {
            "ok": False,
            "error": "This is not a Research Lab proposal. Use the ML tab for S11/S18.",
        }
    if raw in {"investigate", "hold"}:
        text = (note or "investigate").strip()
        _annotate_pending(proposal_id, text, path=proposals_path)
        return {
            "ok": True,
            "id": proposal_id,
            "status": "pending",
            "decision": "investigate",
            "reminder": "Still pending. Lab will not ENABLE or paper this book.",
            "research": research_desk_payload(proposals_path=proposals_path),
        }
    mapped = raw
    if raw in {"approve", "approved", "approved_paper"}:
        mapped = "approved_paper"
    elif raw in {"paper", "paper_test", "paper-test"}:
        mapped = "approved_paper"
        note = ("paper_test requested — still not ENABLE. " + note).strip()
    elif raw in {"reject", "rejected"}:
        mapped = "rejected"
    else:
        raise ValueError(f"bad research decision: {decision}")

    kwargs: dict[str, Any] = {"touch_control": False}
    if proposals_path is not None:
        kwargs["path"] = proposals_path
    p = decide_proposal(proposal_id, mapped, note=note, **kwargs)
    reminder = PAPER_REMINDER if mapped == "approved_paper" else "Rejected. Archived. Not ENABLE."
    return {
        "ok": True,
        "id": p.id,
        "strategy": p.strategy,
        "status": p.status,
        "env_patch": p.env_patch,
        "env_applied": False,
        "control_touched": False,
        "restart_needed": False,
        "reminder": reminder,
        "proposal": p.to_dict(),
        "research": research_desk_payload(proposals_path=proposals_path),
    }
