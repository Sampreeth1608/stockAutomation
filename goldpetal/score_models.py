#!/usr/bin/env python3
"""Score latest ticks with a trained predicting model.

Example:
  python score_models.py --csv data/goldpetal_full_ticks.csv --model data/models/random_forest.joblib
  python score_models.py --csv data/goldpetal_full_ticks.csv --model data/models/best
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import joblib
import pandas as pd

from ml_features import FEATURE_COLUMNS, build_features, load_ticks_csv


def _resolve_model(path: Path) -> Path:
    if path.is_file():
        return path
    if path.name == "best" or path.is_dir():
        report = (path if path.is_dir() else path.parent) / "report.json"
        if not report.exists():
            # try data/models/report.json
            report = Path("data/models/report.json")
        if report.exists():
            meta = json.loads(report.read_text(encoding="utf-8"))
            best = meta.get("best_model")
            if best:
                return Path(meta["models"][best]["path"])
        raise SystemExit("No best model found. Train first: python train_models.py")
    raise SystemExit(f"Model not found: {path}")


def score(csv_path: Path, model_path: Path, out_csv: Path, tail: int) -> pd.DataFrame:
    bundle = joblib.load(_resolve_model(model_path))
    model = bundle["model"]
    features = bundle.get("features") or FEATURE_COLUMNS

    raw = load_ticks_csv(csv_path)
    feat = build_features(raw)
    usable = feat.dropna(subset=features).copy()
    if tail > 0:
        usable = usable.tail(tail)

    X = usable[features].astype(float)
    prob_up = model.predict_proba(X)[:, 1]
    pred = (prob_up >= 0.55).astype(int)

    out = usable[["time", "ltp"]].copy()
    out["prob_up"] = prob_up
    out["pred"] = pred
    out["signal"] = out["pred"].map({1: "BUY", 0: "SHORT"})
    # include key drivers for inspection
    for col in ("imb_l1", "imb_l5", "imb_total", "microprice_gap", "spread_bps"):
        if col in usable.columns:
            out[col] = usable[col].values

    out_csv.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(out_csv, index=False)

    latest = out.iloc[-1]
    print(
        f"Latest @ {latest['time']}: LTP={latest['ltp']} "
        f"prob_up={latest['prob_up']:.3f} => {latest['signal']}"
    )
    print(f"Wrote {len(out)} rows -> {out_csv}")
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Score ticks with trained model")
    parser.add_argument("--csv", default="data/goldpetal_full_ticks.csv")
    parser.add_argument(
        "--model",
        default="data/models/best",
        help="Path to .joblib or 'data/models/best'",
    )
    parser.add_argument(
        "--out",
        default="data/predictions.csv",
        help="Output predictions CSV",
    )
    parser.add_argument(
        "--tail",
        type=int,
        default=500,
        help="Score only last N feature rows (0 = all)",
    )
    args = parser.parse_args()
    score(Path(args.csv), Path(args.model), Path(args.out), args.tail)


if __name__ == "__main__":
    main()
