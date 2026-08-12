#!/usr/bin/env python3
"""Train predicting models from full-depth Gold Petal ticks.

Uses: LTP, OHLC, volume, LTQ, VWAP, total buy/sell, buy1-5/sell1-5, OI.

Example:
  python export_full_ticks.py --export data/goldpetal_full_ticks.csv
  python train_models.py --csv data/goldpetal_full_ticks.csv
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    classification_report,
    mean_absolute_error,
    roc_auc_score,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from ml_features import (
    FEATURE_COLUMNS,
    add_labels,
    build_features,
    load_ticks_csv,
    model_matrix,
    time_split,
)


def _metrics(y_true, y_prob, y_pred) -> dict:
    out = {
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "n": int(len(y_true)),
        "pred_up_rate": float(np.mean(y_pred)),
        "actual_up_rate": float(np.mean(y_true)),
    }
    try:
        out["auc"] = float(roc_auc_score(y_true, y_prob))
    except ValueError:
        out["auc"] = None
    return out


def train(
    csv_path: Path,
    model_dir: Path,
    horizon: int,
    threshold_bps: float,
    train_frac: float,
) -> dict:
    raw = load_ticks_csv(csv_path)
    if len(raw) < 100:
        raise SystemExit(
            f"Need more ticks to train (have {len(raw)}). "
            "Collect during market hours, then re-export."
        )

    feat = build_features(raw)
    labeled = add_labels(feat, horizon=horizon, threshold_bps=threshold_bps)
    usable = labeled.dropna(subset=FEATURE_COLUMNS + ["y_dir", "y_ret"]).copy()
    train_df, test_df = time_split(usable, train_frac=train_frac)

    X_train, y_train, ret_train = model_matrix(train_df)
    X_test, y_test, ret_test = model_matrix(test_df)

    if y_train.nunique() < 2:
        raise SystemExit(
            "Training labels have only one class (price mostly one-way). "
            "Collect more varied market data / try a different --horizon."
        )

    models = {
        "logreg": Pipeline(
            [
                ("scaler", StandardScaler()),
                (
                    "clf",
                    LogisticRegression(
                        max_iter=2000,
                        class_weight="balanced",
                        random_state=42,
                    ),
                ),
            ]
        ),
        "random_forest": RandomForestClassifier(
            n_estimators=200,
            max_depth=8,
            min_samples_leaf=20,
            class_weight="balanced_subsample",
            random_state=42,
            n_jobs=-1,
        ),
        "grad_boost": GradientBoostingClassifier(
            n_estimators=150,
            max_depth=3,
            learning_rate=0.05,
            random_state=42,
        ),
    }

    model_dir.mkdir(parents=True, exist_ok=True)
    report: dict = {
        "csv": str(csv_path),
        "horizon_rows": horizon,
        "threshold_bps": threshold_bps,
        "train_rows": int(len(X_train)),
        "test_rows": int(len(X_test)),
        "features": FEATURE_COLUMNS,
        "models": {},
    }

    best_name = None
    best_auc = -1.0

    for name, model in models.items():
        model.fit(X_train, y_train)
        prob = model.predict_proba(X_test)[:, 1]
        pred = (prob >= 0.55).astype(int)  # slight confidence filter
        mets = _metrics(y_test, prob, pred)
        # simple PnL proxy: long if pred up else short, using realized ret
        side = np.where(pred == 1, 1.0, -1.0)
        proxy = side * ret_test.to_numpy()
        mets["proxy_mean_ret"] = float(np.mean(proxy))
        mets["proxy_sum_ret"] = float(np.sum(proxy))
        mets["mae_vs_zero"] = float(mean_absolute_error(ret_test, np.zeros_like(ret_test)))

        print(f"\n=== {name} ===")
        print(json.dumps(mets, indent=2))
        print(classification_report(y_test, pred, digits=3, zero_division=0))

        path = model_dir / f"{name}.joblib"
        joblib.dump(
            {
                "model": model,
                "features": FEATURE_COLUMNS,
                "horizon": horizon,
                "threshold_bps": threshold_bps,
                "name": name,
            },
            path,
        )
        report["models"][name] = {**mets, "path": str(path)}
        auc = mets.get("auc") or -1.0
        if auc > best_auc:
            best_auc = auc
            best_name = name

    # imbalance baseline (no ML): sign of imb_l5
    base_pred = (test_df["imb_l5"].fillna(0.0) > 0).astype(int)
    base_prob = (test_df["imb_l5"].fillna(0.0) + 1.0) / 2.0
    base_prob = base_prob.clip(0, 1)
    report["baseline_imb_l5"] = _metrics(y_test, base_prob, base_pred)
    print("\n=== baseline imb_l5 ===")
    print(json.dumps(report["baseline_imb_l5"], indent=2))

    report["best_model"] = best_name
    meta_path = model_dir / "report.json"
    meta_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nSaved models to {model_dir}")
    print(f"Best by AUC: {best_name} (auc={best_auc})")
    print(f"Report: {meta_path}")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Train Gold Petal predicting models")
    parser.add_argument(
        "--csv",
        default="data/goldpetal_full_ticks.csv",
        help="Full-depth ticks CSV from export_full_ticks.py",
    )
    parser.add_argument(
        "--model-dir",
        default="data/models",
        help="Where to save .joblib models",
    )
    parser.add_argument(
        "--horizon",
        type=int,
        default=20,
        help="Predict LTP direction N rows ahead (default 20 ticks)",
    )
    parser.add_argument(
        "--threshold-bps",
        type=float,
        default=2.0,
        help="Unused for binary y_dir; kept for future 3-class",
    )
    parser.add_argument(
        "--train-frac",
        type=float,
        default=0.7,
        help="Chronological train fraction",
    )
    args = parser.parse_args()
    train(
        csv_path=Path(args.csv),
        model_dir=Path(args.model_dir),
        horizon=args.horizon,
        threshold_bps=args.threshold_bps,
        train_frac=args.train_frac,
    )


if __name__ == "__main__":
    main()
