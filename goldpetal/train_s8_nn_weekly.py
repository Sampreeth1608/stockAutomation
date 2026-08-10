#!/usr/bin/env python3
"""Weekly S8 neural-net retrain + Google Sheets CSV pack.

Uses ALL ticks in data/ticks.db, trains an MLP on bar edge labels, compares
baseline vs NN-gated paper replay, appends evolution history, and writes CSVs
you can File→Import into Google Sheets.

Examples:
  python3 train_s8_nn_weekly.py --db data/ticks.db --tf 30 --lots 100
  python3 train_s8_nn_weekly.py --db data/ticks.db --tf 30 --budget-note "week-33"

Cron (Sunday 18:00 IST example):
  0 18 * * 0 cd ~/goldpetal && ./weekly_s8_nn.sh >> data/s8_nn/cron.log 2>&1
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from learn_s8_align import AlignS8Config, AlignS8Strategy, baseline_cfg, run_align_labeled, summarize
from mtf_bars import DB, build_rich_bars, load_tick_rows
from s8_nn import DEFAULT_MODEL_PATH, save_bundle, time_split_train

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


def _trades_to_rows(trades: list[dict], tag: str) -> list[dict]:
    out = []
    for i, t in enumerate(trades, 1):
        out.append(
            {
                "run": tag,
                "n": i,
                "side": t.get("side"),
                "entry_ts": t.get("entry_ts"),
                "exit_ts": t.get("exit_ts"),
                "entry_px": t.get("entry_px"),
                "exit_px": t.get("exit_px"),
                "gross_pts": t.get("gross_pts"),
                "pnl_inr": t.get("pnl_inr"),
                "mfe": t.get("mfe"),
                "mae": t.get("mae"),
                "exit_tag": t.get("exit_tag"),
                "exit_reason": t.get("exit_reason"),
                "entry_imb": t.get("entry_imb"),
                "win": t.get("win"),
            }
        )
    return out


def run_nn_gated(
    rows: list,
    base: AlignS8Config,
    *,
    model_path: str,
    min_proba: float,
    lags: int,
    lots: float,
    bundle: dict | None = None,
) -> dict:
    """Replay ALIGN with strategy MLP entry gate enabled."""
    cfg = replace(
        base,
        require_nn_filter=True,
        nn_model_path=str(model_path),
        nn_min_proba=float(min_proba),
        nn_lags=int(lags),
    )
    # Preload bundle onto strategy instances created inside run_align_labeled.
    orig_init = AlignS8Strategy.__init__

    def _init(self, cfg_in=None):  # type: ignore[no-untyped-def]
        orig_init(self, cfg_in)
        if bundle is not None:
            self._nn_bundle = bundle

    AlignS8Strategy.__init__ = _init  # type: ignore[method-assign]
    try:
        return run_align_labeled(list(rows), cfg, lots)
    finally:
        AlignS8Strategy.__init__ = orig_init  # type: ignore[method-assign]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--tf", type=int, default=30, help="bar minutes for NN features")
    ap.add_argument("--lots", type=float, default=100.0)
    ap.add_argument("--lags", type=int, default=3)
    ap.add_argument("--horizon", type=int, default=6)
    ap.add_argument("--tp-pts", type=float, default=20.0)
    ap.add_argument("--sl-pts", type=float, default=25.0)
    ap.add_argument("--min-proba", type=float, default=0.55)
    ap.add_argument("--out-model", default=str(DEFAULT_MODEL_PATH))
    ap.add_argument("--out-dir", default="data/s8_nn")
    ap.add_argument("--budget-note", default="", help="note stored in Sheets row")
    ap.add_argument("--bar-minutes", type=int, default=30, help="S8 decision TF")
    ap.add_argument("--bar-ticks", type=int, default=0)
    args = ap.parse_args()

    week_id = datetime.now(IST).strftime("%Y-%m-%d")
    out_root = Path(args.out_dir)
    week_dir = out_root / f"week_{week_id}"
    week_dir.mkdir(parents=True, exist_ok=True)

    rows = load_tick_rows(Path(args.db))
    print(f"ticks={len(rows)} tf={args.tf}m")
    if len(rows) < 200:
        raise SystemExit("need more ticks in data/ticks.db (collect for days/weeks)")

    bars = [b.to_row() for b in build_rich_bars(rows, f"{args.tf}m", args.tf)]
    print(f"bars={len(bars)}")

    bundle = time_split_train(
        bars,
        lags=args.lags,
        horizon=args.horizon,
        tp_pts=args.tp_pts,
        sl_pts=args.sl_pts,
    )
    model_path = Path(args.out_model)
    save_bundle(bundle, model_path)
    print(json.dumps(bundle["metrics"], indent=2))
    print(f"saved model {model_path}")

    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row
    tick_rows = con.execute(
        "SELECT id, received_at, ltp, bp, sp, raw_json FROM ticks "
        "WHERE ltp IS NOT NULL ORDER BY received_at ASC, id ASC"
    ).fetchall()
    con.close()

    base = baseline_cfg(
        bar_minutes=args.bar_minutes,
        bar_ticks=args.bar_ticks,
    )
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
    nn_sum["nn_skipped"] = nn_res.get("nn_skipped", 0)

    _write_csv(week_dir / "trades_baseline.csv", _trades_to_rows(base_res["trades"], "baseline"))
    _write_csv(week_dir / "trades_nn.csv", _trades_to_rows(nn_res["trades"], "nn_gated"))

    summary_row = {
        "week_id": week_id,
        "generated_at_ist": datetime.now(IST).isoformat(),
        "note": args.budget_note,
        "ticks": len(tick_rows),
        "bars": len(bars),
        "tf_minutes": args.tf,
        "nn_accuracy": bundle["metrics"].get("accuracy"),
        "nn_auc": bundle["metrics"].get("auc"),
        "nn_n_train": bundle["metrics"].get("n_train"),
        "nn_n_test": bundle["metrics"].get("n_test"),
        "baseline_n": base_sum["n"],
        "baseline_dir_pct": base_sum["dir_pct"],
        "baseline_sum_inr": base_sum["sum_inr"],
        "nn_n": nn_sum["n"],
        "nn_dir_pct": nn_sum["dir_pct"],
        "nn_sum_inr": nn_sum["sum_inr"],
        "delta_inr_nn_minus_base": round(nn_sum["sum_inr"] - base_sum["sum_inr"], 1),
        "nn_skipped_entries": nn_res.get("nn_skipped", 0),
        "min_proba": args.min_proba,
        "model_path": str(model_path),
    }
    _write_csv(week_dir / "summary.csv", [summary_row])
    (week_dir / "metrics.json").write_text(
        json.dumps(
            {"nn": bundle["metrics"], "baseline": base_sum, "nn_gated": nn_sum},
            indent=2,
        ),
        encoding="utf-8",
    )

    evo_path = out_root / "evolution.csv"
    evo_rows: list[dict] = []
    if evo_path.exists() and evo_path.stat().st_size > 0:
        with evo_path.open(encoding="utf-8") as fh:
            evo_rows = list(csv.DictReader(fh))
    evo_rows.append(summary_row)
    _write_csv(evo_path, evo_rows, fieldnames=list(summary_row.keys()))

    latest = out_root / "latest_summary.csv"
    _write_csv(latest, [summary_row])
    (out_root / "HOW_TO_GOOGLE_SHEETS.txt").write_text(
        "\n".join(
            [
                "Google Sheets import",
                "1. Open a Sheet → File → Import → Upload",
                "2. Import data/s8_nn/evolution.csv  (weekly history)",
                "3. Import data/s8_nn/week_YYYY-MM-DD/trades_nn.csv (this week trades)",
                "4. Import trades_baseline.csv to compare",
                "",
                "Enable NN gate on paper runner:",
                "  S8_REQUIRE_NN=true",
                "  S8_NN_MODEL_PATH=data/models/s8_nn_mlp.joblib",
                "  S8_NN_MIN_PROBA=0.55",
                "  DRY_RUN=true",
                "",
                f"This run: Δ₹ NN−baseline = {summary_row['delta_inr_nn_minus_base']:+}",
            ]
        ),
        encoding="utf-8",
    )

    print("--- WEEKLY SUMMARY ---")
    print(json.dumps(summary_row, indent=2))
    print(f"Sheets pack: {week_dir}")
    print(f"Evolution:   {evo_path}")
    print("Import evolution.csv into Google Sheets to track weekly improvement.")


if __name__ == "__main__":
    main()
