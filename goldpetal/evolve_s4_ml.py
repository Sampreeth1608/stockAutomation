#!/usr/bin/env python3
"""Weekly S4 overnight ML improve → Sheets pack + control-panel proposal.

  python3 evolve_s4_ml.py train
  ./weekly_s4.sh
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from proposals import PaperResult, StrategyProposal, add_proposal, proposal_from_weekly_s4
from train_overnight import train

IST = ZoneInfo("Asia/Kolkata")
OUT_ROOT = Path(__file__).resolve().parent / "data" / "s4_ml"


def _now() -> datetime:
    return datetime.now(IST)


def cmd_train(args: argparse.Namespace) -> int:
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    model_dir = Path(args.model_dir)
    model_dir.mkdir(parents=True, exist_ok=True)
    try:
        report = train(
            model_dir=model_dir,
            late_minutes=int(args.late_minutes),
            skip_archive=bool(args.skip_archive),
        )
    except SystemExit as exc:
        print(f"S4 train blocked: {exc}")
        # Still write a pending note proposal so operator sees it.
        week_id = _now().strftime("%Y-%m-%d")
        prop = StrategyProposal(
            id="",
            week_id=week_id,
            kind="improved",
            strategy="S4_OVERNIGHT",
            title=f"S4 overnight — need more days ({week_id})",
            summary=str(exc),
            paper=PaperResult(),
            model_path="",
            safety_ok=False,
            safety_reasons=[str(exc)],
            env_patch={"DRY_RUN": "true", "S4_REASONING": "true"},
        )
        add_proposal(prop)
        print(f"Control panel proposal: {prop.id} (not enough data)")
        return 0

    best = str(report.get("best_model") or "")
    models = report.get("models") or {}
    best_meta = models.get(best) or {}
    model_path = str(best_meta.get("path") or "")
    auc = float(best_meta.get("auc") or best_meta.get("roc_auc") or 0)
    acc = float(best_meta.get("accuracy") or 0)
    n_days = int(report.get("n_labeled") or report.get("n_days") or 0)
    # Safety: need a few days and non-terrible AUC when scored
    reasons = []
    ok = True
    if n_days < 5:
        ok = False
        reasons.append(f"need ≥5 labeled days (have {n_days})")
    if auc > 0 and auc < 0.52:
        ok = False
        reasons.append(f"auc={auc:.3f} < 0.52")
    if ok:
        reasons.append("overnight model OK for paper — set S4_MODEL_PATH + S4_REASONING")

    summary = {
        "week_id": _now().strftime("%Y-%m-%d"),
        "best_model": best,
        "best_auc": auc,
        "best_accuracy": acc,
        "n_days": n_days,
        "model_path": model_path,
        "note": args.budget_note,
    }
    week_dir = OUT_ROOT / f"week_{summary['week_id']}"
    week_dir.mkdir(parents=True, exist_ok=True)
    (week_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    with (week_dir / "summary.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(summary.keys()))
        w.writeheader()
        w.writerow(summary)

    prop = proposal_from_weekly_s4(
        week_id=summary["week_id"],
        summary=summary,
        safety_ok=ok,
        safety_reasons=reasons,
        model_path=model_path,
    )
    add_proposal(prop)
    print(json.dumps(summary, indent=2))
    print(f"SAFETY: {ok} {reasons}")
    print(f"Control panel proposal: {prop.id} status=pending")
    print("Enable after approve: S4_MODEL_PATH + S4_REASONING=true (DRY_RUN)")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="Weekly S4 overnight ML evolve")
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("train")
    t.add_argument("--model-dir", default="data/models")
    t.add_argument("--late-minutes", type=int, default=30)
    t.add_argument("--skip-archive", action="store_true")
    t.add_argument("--budget-note", default="")
    t.set_defaults(func=cmd_train)
    args = ap.parse_args()
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
