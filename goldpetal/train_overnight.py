#!/usr/bin/env python3
"""Train overnight next-open direction models (S4).

Predicts whether tomorrow's open > today's close using day-level
quant features (Parkinson/GK vol, CLV, late imbalance, gap lags, EWMA).

  python archive_ticks.py
  python train_overnight.py
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, classification_report, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from archive_ticks import ARCHIVE_ROOT, archive_days
from overnight_features import (
    DAY_FEATURE_COLS,
    day_bars_from_ticks,
    load_archive_days,
    model_matrix,
)


def train(model_dir: Path, late_minutes: int, skip_archive: bool) -> dict:
    if not skip_archive:
        archive_days()

    ticks = load_archive_days(ARCHIVE_ROOT)
    if ticks.empty:
        raise SystemExit("No archived ticks. Run: python archive_ticks.py")

    days = day_bars_from_ticks(ticks, late_minutes=late_minutes)
    # Need enough days with next-day label
    labeled = days.dropna(subset=["y_gap_up"])
    if len(labeled) < 1:
        raise SystemExit(
            f"Need at least 1 complete day→next-open pair to train (have {len(labeled)}). "
            "Keep collecting; S4 uses heuristic until then."
        )
    if len(labeled) < 2:
        print(
            f"Warning: only {len(labeled)} labeled day(s). "
            "Model will be weak; heuristic remains available as fallback."
        )

    X, y, gaps = model_matrix(days)
    if len(X) < 1:
        raise SystemExit("Feature matrix empty after cleaning.")

    model_dir.mkdir(parents=True, exist_ok=True)
    report: dict = {
        "n_days": int(len(days)),
        "n_labeled": int(len(X)),
        "features": DAY_FEATURE_COLS,
        "models": {},
    }

    # Too few classes for sklearn classifiers — persist quant heuristic instead.
    if int(y.nunique()) < 2:
        from strategy_overnight import HeuristicGapModel

        print(
            f"Only one gap class in labels {sorted(y.unique().tolist())}. "
            "Saving overnight_heuristic.joblib (sigmoid late_ret/imb/CLV)."
        )
        path = model_dir / "overnight_heuristic.joblib"
        model = HeuristicGapModel()
        joblib.dump(
            {
                "model": model,
                "features": DAY_FEATURE_COLS,
                "name": "overnight_heuristic",
                "late_minutes": late_minutes,
            },
            path,
        )
        report["models"]["overnight_heuristic"] = {
            "accuracy": None,
            "auc": None,
            "n_test": 0,
            "path": str(path),
            "note": "single_class_fallback",
        }
        report["best_model"] = "overnight_heuristic"
        (model_dir / "overnight_report.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
        days_path = model_dir / "day_features.csv"
        days.to_csv(days_path, index=False)
        print(f"Saved {path}")
        print(f"Day features: {days_path}")
        return report

    # chronological split
    cut = max(1, int(len(X) * 0.7))
    if cut >= len(X):
        cut = max(1, len(X) - 1)
    X_tr, X_te = X.iloc[:cut], X.iloc[cut:]
    y_tr, y_te = y.iloc[:cut], y.iloc[cut:]
    g_te = gaps.iloc[cut:]

    if y_tr.nunique() < 2:
        print("Warning: train fold has one class; fitting on all labeled days.")
        X_tr, y_tr = X, y
        X_te, y_te, g_te = X, y, gaps
        if int(y_tr.nunique()) < 2:
            from strategy_overnight import HeuristicGapModel

            path = model_dir / "overnight_heuristic.joblib"
            joblib.dump(
                {
                    "model": HeuristicGapModel(),
                    "features": DAY_FEATURE_COLS,
                    "name": "overnight_heuristic",
                    "late_minutes": late_minutes,
                },
                path,
            )
            report["models"]["overnight_heuristic"] = {
                "path": str(path),
                "note": "single_class_fallback",
            }
            report["best_model"] = "overnight_heuristic"
            (model_dir / "overnight_report.json").write_text(
                json.dumps(report, indent=2), encoding="utf-8"
            )
            days.to_csv(model_dir / "day_features.csv", index=False)
            print(f"Saved heuristic fallback -> {path}")
            return report

    models = {
        "overnight_logreg": Pipeline(
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
        "overnight_rf": RandomForestClassifier(
            n_estimators=200,
            max_depth=4,
            min_samples_leaf=1,
            class_weight="balanced_subsample",
            random_state=42,
        ),
        "overnight_gb": GradientBoostingClassifier(
            n_estimators=100,
            max_depth=2,
            learning_rate=0.05,
            random_state=42,
        ),
    }

    best_name = None
    best_auc = -1.0

    for name, model in models.items():
        try:
            model.fit(X_tr, y_tr)
        except ValueError as exc:
            print(f"{name}: fit failed ({exc}); saving heuristic fallback")
            from strategy_overnight import HeuristicGapModel

            path = model_dir / "overnight_heuristic.joblib"
            joblib.dump(
                {
                    "model": HeuristicGapModel(),
                    "features": DAY_FEATURE_COLS,
                    "name": "overnight_heuristic",
                    "late_minutes": late_minutes,
                },
                path,
            )
            report["models"]["overnight_heuristic"] = {
                "path": str(path),
                "note": f"fit_fallback:{exc}",
            }
            report["best_model"] = "overnight_heuristic"
            (model_dir / "overnight_report.json").write_text(
                json.dumps(report, indent=2), encoding="utf-8"
            )
            days.to_csv(model_dir / "day_features.csv", index=False)
            print(f"Saved heuristic fallback -> {path}")
            return report
        if len(X_te) and y_te.nunique() >= 1:
            prob = model.predict_proba(X_te)[:, 1]
            pred = (prob >= 0.55).astype(int)
            mets = {
                "accuracy": float(accuracy_score(y_te, pred)),
                "n_test": int(len(y_te)),
            }
            try:
                mets["auc"] = float(roc_auc_score(y_te, prob))
            except ValueError:
                mets["auc"] = None
            # gap capture proxy: long if pred up else short
            side = np.where(pred == 1, 1.0, -1.0)
            mets["proxy_gap_sum"] = float(np.nansum(side * g_te.to_numpy()))
            print(f"\n=== {name} ===")
            print(json.dumps(mets, indent=2))
            if len(set(y_te)) > 1:
                print(classification_report(y_te, pred, digits=3, zero_division=0))
        else:
            mets = {"accuracy": None, "auc": None, "n_test": 0}
            print(f"\n=== {name} === fitted (insufficient test fold)")

        path = model_dir / f"{name}.joblib"
        joblib.dump(
            {
                "model": model,
                "features": DAY_FEATURE_COLS,
                "name": name,
                "late_minutes": late_minutes,
            },
            path,
        )
        report["models"][name] = {**mets, "path": str(path)}
        auc = mets.get("auc") if mets.get("auc") is not None else -1.0
        if auc > best_auc:
            best_auc = auc
            best_name = name

    if best_name is None:
        best_name = "overnight_logreg"
    report["best_model"] = best_name
    (model_dir / "overnight_report.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    days_path = model_dir / "day_features.csv"
    days.to_csv(days_path, index=False)
    print(f"\nSaved overnight models to {model_dir}")
    print(f"Best: {best_name} auc={best_auc}")
    print(f"Day features: {days_path}")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Train S4 overnight gap models")
    parser.add_argument("--model-dir", default="data/models")
    parser.add_argument("--late-minutes", type=int, default=30)
    parser.add_argument("--skip-archive", action="store_true")
    args = parser.parse_args()
    train(Path(args.model_dir), args.late_minutes, args.skip_archive)


if __name__ == "__main__":
    main()
