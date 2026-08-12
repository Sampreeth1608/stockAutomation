#!/usr/bin/env python3
"""Train S9 bar ML (TBQ/TSQ/H/L/Vol → next-bar up/down).

Examples:
  python3 train_s9_bar_ml.py --db data/ticks.db --tf 30 --model logreg
  python3 train_s9_bar_ml.py --db data/ticks.db --tf 30 --model lightgbm
  python3 train_s9_bar_ml.py --bars-csv fixtures/s9_hlv_sample_bars.csv --model logreg
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from mtf_bars import DB, build_rich_bars, load_tick_rows
from paper_sim_s9_state import enrich_bar_volume, load_bars_csv
from s9_bar_ml import ModelKind, save_bundle, train_from_bars


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--tf", type=int, default=30)
    ap.add_argument("--bars-csv", default="")
    ap.add_argument("--lags", type=int, default=3)
    ap.add_argument("--train-frac", type=float, default=0.7)
    ap.add_argument(
        "--model",
        choices=("logreg", "random_forest", "grad_boost", "lightgbm"),
        default="logreg",
    )
    ap.add_argument(
        "--out",
        default="data/models/s9_bar_ml.joblib",
        help="joblib bundle path",
    )
    args = ap.parse_args()

    if args.bars_csv:
        bars = load_bars_csv(Path(args.bars_csv))
        print(f"bars_csv={args.bars_csv} bars={len(bars)}")
    else:
        rows = load_tick_rows(Path(args.db))
        bars = enrich_bar_volume(
            [b.to_row() for b in build_rich_bars(rows, f"{args.tf}m", args.tf)]
        )
        print(f"ticks={len(rows)} bars={len(bars)} TF={args.tf}m")

    kind: ModelKind = args.model  # type: ignore[assignment]
    try:
        bundle = train_from_bars(
            bars,
            lags=args.lags,
            train_frac=args.train_frac,
            model_kind=kind,
        )
    except Exception as e:
        raise SystemExit(f"train failed: {e}") from e

    out = Path(args.out)
    save_bundle(bundle, out)
    m = bundle["metrics"]
    print(json.dumps(m, indent=2))
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
