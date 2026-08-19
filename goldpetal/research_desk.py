"""Research Lab desk — approval station. Not ML. Not a live unlock.

The factory may write pending proposals. This module is the only place
the operator records Approve / Paper test / Reject / Investigate.
Approve names the next AMISE slot (S21, S22, … S25 after S24), writes
paper ENABLE for that slot, and paper-allowlists it. It never sets
DRY_RUN=false. Angel still needs Unlock + LIVE on the desk.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from amise_slots import (
    amise_books_now,
    apply_slot_enable,
    assign_slot,
    desk_slots_payload,
)
from control_state import approve_strategy_live, approve_strategy_paper
from proposals import decide_proposal, get_proposal, load_proposals, save_proposals
from strategy_genome import (
    LAB_NAME,
    RESEARCH_KIND,
    RESEARCH_STRATEGY,
    env_patch_is_safe,
    genome_from_dict,
    is_research_proposal,
    research_env_patch,
)

IST = ZoneInfo("Asia/Kolkata")
ROOT = Path(__file__).resolve().parent
RESEARCH_DIR = ROOT / "data" / "research"
LIBRARY_PATH = RESEARCH_DIR / "library.json"
LAST_RUN_PATH = RESEARCH_DIR / "last_run.json"

LIVE_BLOCKED_REASON = (
    "Live unlock is not on the Lab tab. Approve names the next AMISE slot "
    "(S21, S22, …) and turns paper on for that slot. This tab never sets "
    "DRY_RUN=false. Angel still needs Unlock + type LIVE on Live money, then Restart."
)

PAPER_REMINDER = (
    "Approved. AMISE named the next free slot (S21, then S22, then S25 after "
    "S24), wrote paper ENABLE, and queued it for Angel after you Unlock. "
    "Restart the bot to load RAM. Keep DRY_RUN=true until you type LIVE yourself."
)


def _now_iso() -> str:
    return datetime.now(IST).isoformat(timespec="seconds")


def _research_dirs() -> list[Path]:
    """Desk may run from ~/goldpetal while the factory wrote into a worktree."""
    out: list[Path] = []
    env_dir = (os.getenv("GP_DATA_DIR") or "").strip()
    if env_dir:
        out.append(Path(env_dir) / "research")
    out.append(RESEARCH_DIR)
    home_lab = Path.home() / "goldpetal" / "data" / "research"
    if home_lab not in out:
        out.append(home_lab)
    return out


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _library_payload() -> dict[str, Any]:
    for folder in _research_dirs():
        lib = _read_json(folder / "library.json")
        if lib:
            return lib
        last = _read_json(folder / "last_run.json")
        if last:
            return last
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
        "amise_slots": amise_books_now(),
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
        "slots": desk_slots_payload(),
        "note": (
            "AI researches. You decide. New strategies found on the last lab run "
            "must beat S16 and S18 after charges with a 10% margin, pass PF/drawdown/"
            "both-sides, survive 3-fold walk-forward, pass 2× and 3× costs, and pass "
            "holdout when the tape is long enough. Approve names the next "
            "slot (S21, S22, …) and turns that slot's paper ENABLE on. Restart "
            "the bot. This tab never sets DRY_RUN=false."
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
    env_path: Path | None = None,
    slots_dir: Path | None = None,
    state_path: Path | None = None,
    apply_env: bool = True,
    queue_live: bool = True,
    sync_environ: bool = True,
) -> dict[str, Any]:
    """Approve / paper_test / reject / investigate. Never DRY_RUN=false."""
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
    elif raw in {"reject", "rejected"}:
        mapped = "rejected"
    else:
        raise ValueError(f"bad research decision: {decision}")

    kwargs: dict[str, Any] = {"touch_control": False}
    if proposals_path is not None:
        kwargs["path"] = proposals_path
    if state_path is not None:
        kwargs["state_path"] = state_path
    p = decide_proposal(proposal_id, mapped, note=note, **kwargs)
    slot_info: dict[str, Any] = {}
    env_applied = False
    control_touched = False
    reminder = "Rejected. Archived. Not ENABLE."
    if mapped == "approved_paper":
        extra = (p.paper.extra if p.paper is not None else None) or {}
        if not isinstance(extra, dict):
            extra = {}
        graw = extra.get("genome")
        if not isinstance(graw, dict):
            reminder = (
                "Approved as a Lab flag only — proposal has no genome, so no AMISE slot."
            )
        else:
            genome = genome_from_dict(graw)
            slot_info = assign_slot(
                genome,
                proposal_id=p.id,
                folder=slots_dir,
                note=note,
            )
            if not slot_info.get("ok"):
                reminder = str(slot_info.get("error") or "slot assign failed")
            else:
                slot = str(slot_info["slot"])
                patch = slot_info.get("env_patch") or {}
                if apply_env and env_patch_is_safe(patch):
                    applied = apply_slot_enable(
                        slot, env_path=env_path, sync_environ=sync_environ
                    )
                    env_applied = bool(applied.get("ok"))
                    slot_info["env"] = applied
                approve_strategy_paper(slot, path=state_path)
                if queue_live:
                    approve_strategy_live(slot, path=state_path)
                control_touched = True
                reminder = (
                    f"Named {slot}. Paper ENABLE written. Restart the bot to load it. "
                    "Angel is queued on Live pick — Unlock + type LIVE still required. "
                    "This tab did not set DRY_RUN=false."
                )
    return {
        "ok": True,
        "id": p.id,
        "strategy": (slot_info.get("slot") if slot_info.get("ok") else p.strategy),
        "status": p.status,
        "slot": slot_info.get("slot") if slot_info.get("ok") else None,
        "slot_info": slot_info,
        "env_patch": (slot_info.get("env_patch") if slot_info.get("ok") else p.env_patch),
        "env_applied": env_applied,
        "control_touched": control_touched,
        "restart_needed": bool(slot_info.get("ok")),
        "live_unlocked": False,
        "dry_run_forced": True,
        "reminder": reminder if mapped == "approved_paper" else "Rejected. Archived. Not ENABLE.",
        "proposal": p.to_dict(),
        "research": research_desk_payload(proposals_path=proposals_path),
    }
