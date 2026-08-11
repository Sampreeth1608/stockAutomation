"""Weekend / weekly strategy proposals for control-panel approval.

The weekly ML job writes proposals with paper results. Nothing goes live
(or even into paper enable) until the operator approves in the panel.
"""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from control_state import (
    CONTROL_DIR,
    approve_strategy_live,
    approve_strategy_paper,
    ensure_control_dir,
    reject_strategy,
)

IST = ZoneInfo("Asia/Kolkata")
PROPOSALS_PATH = CONTROL_DIR / "proposals.json"
_lock = threading.Lock()


@dataclass
class PaperResult:
    n_trades: int = 0
    win_rate: float = 0.0
    gross_pnl_inr: float = 0.0
    after_tax_pnl_inr: float = 0.0
    baseline_after_tax_inr: float = 0.0
    delta_vs_baseline_inr: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


@dataclass
class StrategyProposal:
    id: str
    week_id: str
    kind: str  # "new" | "improved"
    strategy: str
    title: str
    summary: str
    paper: PaperResult
    model_path: str = ""
    safety_ok: bool = False
    safety_reasons: list[str] = field(default_factory=list)
    status: str = "pending"  # pending | approved_paper | approved_live | rejected
    created_at_ist: str = ""
    decided_at_ist: str = ""
    decision_note: str = ""
    env_patch: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


def _now_iso() -> str:
    return datetime.now(IST).isoformat(timespec="seconds")


def _empty_store() -> dict[str, Any]:
    return {"proposals": [], "updated_at_ist": _now_iso()}


def load_proposals(path: Path = PROPOSALS_PATH) -> list[StrategyProposal]:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        with _lock:
            path.write_text(json.dumps(_empty_store(), indent=2), encoding="utf-8")
        return []
    with _lock:
        raw = json.loads(path.read_text(encoding="utf-8"))
    out: list[StrategyProposal] = []
    for row in raw.get("proposals") or []:
        paper_raw = row.get("paper") or {}
        paper = PaperResult(
            n_trades=int(paper_raw.get("n_trades", 0)),
            win_rate=float(paper_raw.get("win_rate", 0)),
            gross_pnl_inr=float(paper_raw.get("gross_pnl_inr", 0)),
            after_tax_pnl_inr=float(paper_raw.get("after_tax_pnl_inr", 0)),
            baseline_after_tax_inr=float(paper_raw.get("baseline_after_tax_inr", 0)),
            delta_vs_baseline_inr=float(paper_raw.get("delta_vs_baseline_inr", 0)),
            extra=dict(paper_raw.get("extra") or {}),
        )
        out.append(
            StrategyProposal(
                id=str(row.get("id") or uuid.uuid4().hex[:10]),
                week_id=str(row.get("week_id") or ""),
                kind=str(row.get("kind") or "improved"),
                strategy=str(row.get("strategy") or ""),
                title=str(row.get("title") or ""),
                summary=str(row.get("summary") or ""),
                paper=paper,
                model_path=str(row.get("model_path") or ""),
                safety_ok=bool(row.get("safety_ok", False)),
                safety_reasons=list(row.get("safety_reasons") or []),
                status=str(row.get("status") or "pending"),
                created_at_ist=str(row.get("created_at_ist") or ""),
                decided_at_ist=str(row.get("decided_at_ist") or ""),
                decision_note=str(row.get("decision_note") or ""),
                env_patch={str(k): str(v) for k, v in (row.get("env_patch") or {}).items()},
            )
        )
    return out


def save_proposals(items: list[StrategyProposal], path: Path = PROPOSALS_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at_ist": _now_iso(),
        "proposals": [p.to_dict() for p in items],
    }
    with _lock:
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def add_proposal(proposal: StrategyProposal, path: Path = PROPOSALS_PATH) -> StrategyProposal:
    items = load_proposals(path)
    if not proposal.id:
        proposal.id = uuid.uuid4().hex[:10]
    if not proposal.created_at_ist:
        proposal.created_at_ist = _now_iso()
    # Replace same week+strategy+kind pending row if re-run.
    kept: list[StrategyProposal] = []
    for p in items:
        if (
            p.week_id == proposal.week_id
            and p.strategy == proposal.strategy
            and p.kind == proposal.kind
            and p.status == "pending"
        ):
            continue
        kept.append(p)
    kept.insert(0, proposal)
    save_proposals(kept, path=path)
    return proposal


def get_proposal(proposal_id: str, path: Path = PROPOSALS_PATH) -> StrategyProposal | None:
    for p in load_proposals(path):
        if p.id == proposal_id:
            return p
    return None


def decide_proposal(
    proposal_id: str,
    decision: str,
    note: str = "",
    path: Path = PROPOSALS_PATH,
    state_path: Path | None = None,
) -> StrategyProposal:
    """decision: approved_paper | approved_live | rejected"""
    if decision not in {"approved_paper", "approved_live", "rejected"}:
        raise ValueError(f"bad decision: {decision}")
    items = load_proposals(path)
    found: StrategyProposal | None = None
    for p in items:
        if p.id == proposal_id:
            found = p
            break
    if found is None:
        raise KeyError(f"proposal not found: {proposal_id}")
    if found.status != "pending":
        raise RuntimeError(f"proposal already decided: {found.status}")

    found.status = decision
    found.decided_at_ist = _now_iso()
    found.decision_note = note
    save_proposals(items, path=path)

    if decision == "approved_paper":
        approve_strategy_paper(found.strategy, path=state_path)
    elif decision == "approved_live":
        approve_strategy_live(found.strategy, path=state_path)
    else:
        reject_strategy(found.strategy, path=state_path)
    return found


def proposal_from_weekly_s8(
    *,
    week_id: str,
    summary: dict[str, Any],
    safety_ok: bool,
    safety_reasons: list[str],
    model_path: str,
    env_patch: dict[str, str] | None = None,
) -> StrategyProposal:
    """Build an 'improved' S8 proposal from evolve_s8_ml summary row."""
    nn_sum = float(summary.get("nn_sum_inr") or 0)
    base_sum = float(summary.get("baseline_sum_inr") or 0)
    nn_n = int(summary.get("nn_n") or 0)
    dir_pct = float(summary.get("nn_dir_pct") or 0)
    kind = "improved"
    arch = str(summary.get("deep_winner") or summary.get("nn_arch") or "mlp")
    title = f"S8 deep-NN weekly improve ({arch}) — week {week_id}"
    summary_txt = (
        f"NN gated paper ₹{nn_sum:+.0f} vs baseline ₹{base_sum:+.0f} "
        f"(Δ₹{nn_sum - base_sum:+.0f}), trades={nn_n}, dir%={dir_pct:.1f}. "
        f"arch={arch} deep={summary.get('deep_enabled')} safety_ok={safety_ok}"
    )
    paper = PaperResult(
        n_trades=nn_n,
        win_rate=dir_pct,
        gross_pnl_inr=nn_sum,
        after_tax_pnl_inr=nn_sum,
        baseline_after_tax_inr=base_sum,
        delta_vs_baseline_inr=round(nn_sum - base_sum, 2),
        extra={
            "nn_auc": summary.get("nn_auc"),
            "nn_accuracy": summary.get("nn_accuracy"),
            "ticks": summary.get("ticks"),
            "bars": summary.get("bars"),
            "note": summary.get("note"),
            "deep_winner": summary.get("deep_winner"),
            "nn_arch": summary.get("nn_arch"),
        },
    )
    return StrategyProposal(
        id=uuid.uuid4().hex[:10],
        week_id=week_id,
        kind=kind,
        strategy="S8_NET_ZIGZAG",
        title=title,
        summary=summary_txt,
        paper=paper,
        model_path=model_path,
        safety_ok=safety_ok,
        safety_reasons=list(safety_reasons),
        status="pending",
        created_at_ist=_now_iso(),
        env_patch=env_patch
        or {
            "ENABLE_S8": "true",
            "S8_REQUIRE_NN": "true" if safety_ok else "false",
            "S8_NN_MODEL_PATH": model_path,
            "DRY_RUN": "true",
        },
    )


def proposal_from_weekly_s4(
    *,
    week_id: str,
    summary: dict[str, Any],
    safety_ok: bool,
    safety_reasons: list[str],
    model_path: str,
    env_patch: dict[str, str] | None = None,
) -> StrategyProposal:
    """Build an improved S4 overnight proposal."""
    auc = float(summary.get("best_auc") or 0)
    n_days = int(summary.get("n_days") or 0)
    best = str(summary.get("best_model") or "overnight")
    paper = PaperResult(
        n_trades=n_days,
        win_rate=float(summary.get("best_accuracy") or 0) * 100.0,
        extra=dict(summary),
    )
    return StrategyProposal(
        id=uuid.uuid4().hex[:10],
        week_id=week_id,
        kind="improved",
        strategy="S4_OVERNIGHT",
        title=f"S4 overnight ML improve ({best}) — week {week_id}",
        summary=f"best={best} auc={auc:.3f} days={n_days} safety_ok={safety_ok}",
        paper=paper,
        model_path=model_path,
        safety_ok=safety_ok,
        safety_reasons=list(safety_reasons),
        status="pending",
        created_at_ist=_now_iso(),
        env_patch=env_patch
        or {
            "ENABLE_S4": "true",
            "S4_MODEL_PATH": model_path,
            "S4_REASONING": "true",
            "DRY_RUN": "true",
        },
    )


def proposal_from_weekly_s5(
    *,
    week_id: str,
    summary: dict[str, Any],
    safety_ok: bool,
    safety_reasons: list[str],
    model_path: str,
    env_patch: dict[str, str] | None = None,
) -> StrategyProposal:
    """Build an improved S5 minedge proposal."""
    auc = float(summary.get("auc") or 0)
    acc = float(summary.get("accuracy") or 0)
    paper = PaperResult(
        n_trades=int(summary.get("n_test") or 0),
        win_rate=acc * 100.0,
        extra=dict(summary),
    )
    return StrategyProposal(
        id=uuid.uuid4().hex[:10],
        week_id=week_id,
        kind="improved",
        strategy="S5_MINEDGE",
        title=f"S5 minedge ML improve — week {week_id}",
        summary=f"auc={auc:.3f} acc={acc:.3f} safety_ok={safety_ok}",
        paper=paper,
        model_path=model_path,
        safety_ok=safety_ok,
        safety_reasons=list(safety_reasons),
        status="pending",
        created_at_ist=_now_iso(),
        env_patch=env_patch
        or {
            "ENABLE_S5": "true",
            "S5_ML_MODEL_PATH": model_path,
            "S5_REQUIRE_ML": "true" if safety_ok else "false",
            "S5_REASONING": "true",
            "DRY_RUN": "true",
        },
    )


def proposals_snapshot(path: Path = PROPOSALS_PATH) -> dict[str, Any]:
    items = load_proposals(path)
    pending = [p for p in items if p.status == "pending"]
    decided = [p for p in items if p.status != "pending"]
    return {
        "pending": [p.to_dict() for p in pending],
        "decided": [p.to_dict() for p in decided[:40]],
        "counts": {
            "pending": len(pending),
            "approved_paper": sum(1 for p in items if p.status == "approved_paper"),
            "approved_live": sum(1 for p in items if p.status == "approved_live"),
            "rejected": sum(1 for p in items if p.status == "rejected"),
            "total": len(items),
        },
    }
