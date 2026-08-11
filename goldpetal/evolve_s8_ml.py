#!/usr/bin/env python3
"""Full S8 ML evolution — all training stages + Sheets pack (tool-use CLI).

Stages (see s8_ml_pipeline.STAGE_MAP):
  optimization, instruction_tuning, human_feedback, preference_optimization,
  reasoning_training, safety_training, tool_use, conversation (journal)

Examples:
  python3 evolve_s8_ml.py stages
  python3 evolve_s8_ml.py train --db data/ticks.db --tf 30
  python3 evolve_s8_ml.py feedback-init
  python3 evolve_s8_ml.py apply-env
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from learn_s8_align import baseline_cfg, run_align_labeled, summarize
from mtf_bars import DB, build_rich_bars, load_tick_rows
from s8_ml_pipeline import (
    FEEDBACK_PATH,
    JOURNAL_PATH,
    PIPELINE_META,
    STAGE_MAP,
    ensure_feedback_template,
    journal,
    load_feedback,
    safety_check,
    train_full_pipeline,
    write_pipeline_meta,
)
from s8_nn import DEFAULT_MODEL_PATH, save_bundle
from s8_scale_improve import build_scale_frame, plan_curriculum, walk_forward_train
from train_s8_nn_weekly import run_nn_gated

IST = ZoneInfo("Asia/Kolkata")


def _write_csv(path: Path, rows: list[dict], fieldnames: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    cols = fieldnames or list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def cmd_stages(_: argparse.Namespace) -> int:
    print("S8 ML stages (LLM concept → trading code)")
    print("=" * 60)
    for k, v in STAGE_MAP.items():
        print(f"  {k:28}  {v}")
    print()
    print("Feedback file:", FEEDBACK_PATH)
    print("Journal:      ", JOURNAL_PATH)
    print("Pipeline meta:", PIPELINE_META)
    return 0


def cmd_feedback_init(_: argparse.Namespace) -> int:
    p = ensure_feedback_template()
    print(f"wrote/kept {p}")
    print("Edit in Sheets: preference=prefer|reject|keep, then scp back to VM.")
    return 0


def cmd_train(args: argparse.Namespace) -> int:
    journal("tool_use_train_start", {"db": args.db, "tf": args.tf})
    ensure_feedback_template()
    fb = load_feedback()
    print(f"human_feedback rows={len(fb)}")

    rows = load_tick_rows(Path(args.db))
    print(f"ticks={len(rows)} tf={args.tf}m")
    if len(rows) < 200:
        raise SystemExit("need more ticks in data/ticks.db")

    bars = [b.to_row() for b in build_rich_bars(rows, f"{args.tf}m", args.tf)]
    print(f"bars={len(bars)}")

    curriculum: list[dict] = []
    if getattr(args, "scale", True):
        # At-scale: reasoner features + multi-step path labels + walk-forward
        print("training scale walk-forward (math/logic/science/planning/multi-step)…")
        frame = build_scale_frame(
            bars,
            lags=args.lags,
            horizon=max(args.horizon, 8),
            tp_pts=args.tp_pts,
            sl_pts=args.sl_pts,
            lots=args.lots,
        )
        bundle, fold_scores = walk_forward_train(
            frame, lags=args.lags, folds=int(getattr(args, "folds", 5))
        )
        curriculum = plan_curriculum(bundle["metrics"], fold_scores)
        # Also attach classic reasoning heads from v2 pipeline (optional enrichment)
        try:
            v2 = train_full_pipeline(
                bars,
                lags=args.lags,
                horizon=args.horizon,
                tp_pts=args.tp_pts,
                sl_pts=args.sl_pts,
                feedback=fb,
            )
            bundle["heads"] = v2.get("heads")
            bundle["head_models"] = v2.get("head_models")
        except Exception as exc:
            journal("v2_heads_skip", {"error": str(exc)})
            bundle.setdefault("heads", {})
    else:
        bundle = train_full_pipeline(
            bars,
            lags=args.lags,
            horizon=args.horizon,
            tp_pts=args.tp_pts,
            sl_pts=args.sl_pts,
            feedback=fb,
        )
    model_path = Path(args.out_model)
    save_bundle(bundle, model_path)
    print(json.dumps(bundle["metrics"], indent=2))
    if bundle.get("heads"):
        print("heads:", json.dumps(bundle.get("heads"), indent=2))
    print(f"saved model {model_path}")

    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row
    tick_rows = con.execute(
        "SELECT id, received_at, ltp, bp, sp, raw_json FROM ticks "
        "WHERE ltp IS NOT NULL ORDER BY received_at ASC, id ASC"
    ).fetchall()
    con.close()

    base = baseline_cfg(bar_minutes=args.bar_minutes, bar_ticks=args.bar_ticks)
    base_res = run_align_labeled(list(tick_rows), base, args.lots)
    base_sum = summarize("BASELINE", base_res)
    nn_res = run_nn_gated(
        list(tick_rows),
        base,
        model_path=str(model_path),
        min_proba=args.min_proba,
        lags=args.lags,
        lots=args.lots,
        bundle=bundle,
    )
    nn_sum = summarize("NN_GATED", nn_res)

    safety = safety_check(
        bundle["metrics"],
        nn_sum_inr=float(nn_sum["sum_inr"]),
        baseline_sum_inr=float(base_sum["sum_inr"]),
        nn_n=int(nn_sum["n"]),
        min_auc=args.min_auc,
    )
    write_pipeline_meta(bundle, safety)

    week_id = datetime.now(IST).strftime("%Y-%m-%d")
    out_root = Path(args.out_dir)
    week_dir = out_root / f"week_{week_id}"
    week_dir.mkdir(parents=True, exist_ok=True)

    def trades_rows(trades: list[dict], tag: str) -> list[dict]:
        out = []
        for i, t in enumerate(trades, 1):
            out.append(
                {
                    "run": tag,
                    "n": i,
                    "side": t.get("side"),
                    "entry_ts": t.get("entry_ts"),
                    "exit_ts": t.get("exit_ts"),
                    "gross_pts": t.get("gross_pts"),
                    "pnl_inr": t.get("pnl_inr"),
                    "exit_tag": t.get("exit_tag"),
                    "win": t.get("win"),
                    "preference": "",  # fill in Sheets → save as feedback.csv
                    "note": "",
                }
            )
        return out

    _write_csv(week_dir / "trades_baseline.csv", trades_rows(base_res["trades"], "baseline"))
    nn_trade_rows = trades_rows(nn_res["trades"], "nn_gated")
    _write_csv(week_dir / "trades_nn.csv", nn_trade_rows)
    if curriculum:
        _write_csv(week_dir / "curriculum.csv", curriculum)
        _write_csv(out_root / "curriculum_latest.csv", curriculum)
    # Sheets-ready feedback stub from this week's NN trades
    fb_week = [
        {
            "week_id": week_id,
            "entry_ts": r["entry_ts"],
            "side": r["side"],
            "preference": "keep",
            "note": "",
            "pnl_inr": r["pnl_inr"],
        }
        for r in nn_trade_rows
    ]
    _write_csv(week_dir / "feedback_stub.csv", fb_week)

    summary_row = {
        "week_id": week_id,
        "generated_at_ist": datetime.now(IST).isoformat(),
        "note": args.budget_note or "evolve_s8_ml",
        "ticks": len(tick_rows),
        "bars": len(bars),
        "tf_minutes": args.tf,
        "nn_accuracy": bundle["metrics"].get("accuracy"),
        "nn_auc": bundle["metrics"].get("auc"),
        "nn_n_train": bundle["metrics"].get("n_train"),
        "nn_n_test": bundle["metrics"].get("n_test"),
        "n_feedback": len(fb),
        "baseline_n": base_sum["n"],
        "baseline_sum_inr": base_sum["sum_inr"],
        "nn_n": nn_sum["n"],
        "nn_dir_pct": nn_sum["dir_pct"],
        "nn_sum_inr": nn_sum["sum_inr"],
        "delta_inr_nn_minus_base": round(nn_sum["sum_inr"] - base_sum["sum_inr"], 1),
        "safety_ok_enable_nn": safety.ok_to_enable_nn,
        "safety_reasons": " | ".join(safety.reasons),
        "model_path": str(model_path),
        "pipeline": "v2_full_stages",
    }
    _write_csv(week_dir / "summary.csv", [summary_row])
    (week_dir / "metrics.json").write_text(
        json.dumps(
            {
                "nn": bundle["metrics"],
                "heads": bundle.get("heads"),
                "baseline": base_sum,
                "nn_gated": nn_sum,
                "safety": {
                    "ok_to_enable_nn": safety.ok_to_enable_nn,
                    "reasons": safety.reasons,
                },
                "stage_map": STAGE_MAP,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    evo_path = out_root / "evolution.csv"
    evo_rows: list[dict] = []
    if evo_path.exists() and evo_path.stat().st_size > 0:
        with evo_path.open(encoding="utf-8") as fh:
            evo_rows = list(csv.DictReader(fh))
    # unify columns across old/new evolution rows
    evo_rows.append(summary_row)
    all_keys: list[str] = []
    for r in evo_rows:
        for k in r:
            if k not in all_keys:
                all_keys.append(k)
    _write_csv(evo_path, evo_rows, fieldnames=all_keys)
    _write_csv(out_root / "latest_summary.csv", [summary_row])

    (out_root / "HOW_TO_GOOGLE_SHEETS.txt").write_text(
        "\n".join(
            [
                "Google Sheets — full ML pipeline",
                "1. Import data/s8_nn/evolution.csv",
                "2. Import week_*/trades_nn.csv",
                "3. HUMAN FEEDBACK: edit week_*/feedback_stub.csv → set preference=prefer|reject",
                "   save as data/s8_nn/feedback.csv on the VM, then re-run evolve_s8_ml.py train",
                "4. Read pipeline_latest.json safety.ok_to_enable_nn before turning NN on",
                "",
                "Stages coded: " + ", ".join(STAGE_MAP),
                "",
                f"This run safety_ok={safety.ok_to_enable_nn}",
                f"Δ₹ NN−baseline = {summary_row['delta_inr_nn_minus_base']:+}",
                "Reasons: " + " | ".join(safety.reasons),
            ]
        ),
        encoding="utf-8",
    )

    print("--- EVOLVE SUMMARY ---")
    print(json.dumps(summary_row, indent=2))
    print("SAFETY:", safety.ok_to_enable_nn, safety.reasons)
    print(f"Sheets pack: {week_dir}")
    print(f"Evolution:   {evo_path}")
    journal("tool_use_train_done", summary_row)
    return 0


def cmd_apply_env(_: argparse.Namespace) -> int:
    if not PIPELINE_META.exists():
        print("missing", PIPELINE_META, "— run: python3 evolve_s8_ml.py train")
        return 1
    meta = json.loads(PIPELINE_META.read_text(encoding="utf-8"))
    env = meta.get("env_if_safe") or {}
    safety = meta.get("safety") or {}
    print("# from data/s8_nn/pipeline_latest.json")
    print(f"# safety_ok={safety.get('ok_to_enable_nn')}")
    for r in safety.get("reasons") or []:
        print(f"# {r}")
    print("DRY_RUN=true")
    for k, v in env.items():
        if k == "DRY_RUN":
            continue
        print(f"{k}={v}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("stages", help="list ML stages")
    p.set_defaults(func=cmd_stages)

    p = sub.add_parser("feedback-init", help="create feedback.csv template")
    p.set_defaults(func=cmd_feedback_init)

    p = sub.add_parser("train", help="full weekly evolve + Sheets pack")
    p.add_argument("--db", default=str(DB))
    p.add_argument("--tf", type=int, default=30)
    p.add_argument("--lots", type=float, default=100.0)
    p.add_argument("--lags", type=int, default=3)
    p.add_argument("--horizon", type=int, default=6)
    p.add_argument("--tp-pts", type=float, default=20.0)
    p.add_argument("--sl-pts", type=float, default=25.0)
    p.add_argument("--min-proba", type=float, default=0.55)
    p.add_argument("--min-auc", type=float, default=0.55)
    p.add_argument("--out-model", default=str(DEFAULT_MODEL_PATH))
    p.add_argument("--out-dir", default="data/s8_nn")
    p.add_argument("--budget-note", default="")
    p.add_argument("--bar-minutes", type=int, default=30)
    p.add_argument("--bar-ticks", type=int, default=0)
    p.add_argument(
        "--scale",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="walk-forward + reasoner features + multi-step path labels (default on)",
    )
    p.add_argument("--folds", type=int, default=5, help="walk-forward folds")
    p.set_defaults(func=cmd_train)

    p = sub.add_parser("apply-env", help="print .env lines if safety passed")
    p.set_defaults(func=cmd_apply_env)
    return ap


def main() -> None:
    ap = build_parser()
    args = ap.parse_args()
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
