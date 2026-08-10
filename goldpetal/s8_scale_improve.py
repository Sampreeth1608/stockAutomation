#!/usr/bin/env python3
"""At-scale S8 improvement engine: math, logic, science, planning, multi-step.

This is the trading analogue of “reasoning at scale” — not a chat LLM.

  Mathematics   fee BE, R:R, expectancy, multi-horizon MFE/MAE paths
  Programming   walk-forward folds + feature matrix pipeline
  Logic         gate features (IMB floor/rise, NET sign, book pressure)
  Science       OOS AUC/Brier/calibration across folds; head metrics
  Planning      weekly curriculum (what to tighten next)
  Multi-step    path labels over a horizon (enter→hold→exit quality)

CLI:
  python3 s8_scale_improve.py --db data/ticks.db --tf 10 --folds 5
"""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, brier_score_loss, roc_auc_score
from sklearn.neural_network import MLPClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from s8_ml_pipeline import STAGE_MAP, journal, safety_check, write_pipeline_meta
from s8_nn import DEFAULT_MODEL_PATH, add_edge_labels, save_bundle
from s8_reasoner import fee_be_points
from s9_bar_ml import bars_to_frame, build_features, feature_columns
from mtf_bars import DB, build_rich_bars, load_tick_rows

IST = ZoneInfo("Asia/Kolkata")
BUNDLE_VERSION = 3
SCALE_OUT = Path("data/s8_nn")


REASON_FEATURE_COLS = [
    "rz_fee_be",
    "rz_rr",
    "rz_raw_edge",
    "rz_imb_delta",
    "rz_imb_floor",
    "rz_net_sign",
    "rz_imb_rising",
    "rz_book_pressure",
    "rz_plan_score",
]


@dataclass
class FoldScore:
    fold: int
    n_train: int
    n_test: int
    accuracy: float
    auc: float | None
    brier: float | None


def make_mlp(*, hidden=(96, 48, 24), max_iter: int = 250) -> Pipeline:
    """Larger MLP for scale training (still sklearn Adam/backprop)."""
    return Pipeline(
        [
            ("scaler", StandardScaler()),
            (
                "mlp",
                MLPClassifier(
                    hidden_layer_sizes=hidden,
                    activation="relu",
                    solver="adam",
                    alpha=1e-4,
                    batch_size=128,
                    learning_rate_init=1e-3,
                    max_iter=max_iter,
                    early_stopping=True,
                    n_iter_no_change=20,
                    validation_fraction=0.12,
                    random_state=42,
                ),
            ),
        ]
    )


def add_reasoner_features(
    df: pd.DataFrame, *, tp: float = 20.0, sl: float = 25.0, lots: float = 100.0
) -> pd.DataFrame:
    """Mathematics + logic features derived like s8_reasoner (vectorized)."""
    out = df.copy()
    px = out["close"].astype(float)
    imb = out["imb_pct"].astype(float)
    net = out["net"].astype(float)
    prev_imb = imb.shift(1).fillna(imb)
    be = px.map(lambda p: fee_be_points(float(p), lots))
    rr = float(tp) / max(float(sl), 1e-9)
    raw_edge = 0.5 * tp - 0.5 * sl - be
    dimb = imb - prev_imb
    # book pressure: signed expansion proxy
    dtbq = out["tbq"].diff().fillna(0.0)
    dtsq = out["tsq"].diff().fillna(0.0)
    book_pressure = np.where(net > 0, dtbq, np.where(net < 0, dtsq, 0.0))

    out["rz_fee_be"] = be
    out["rz_rr"] = rr
    out["rz_raw_edge"] = raw_edge
    out["rz_imb_delta"] = dimb
    out["rz_imb_floor"] = (imb >= 20.0).astype(float)
    out["rz_net_sign"] = np.sign(net).astype(float)
    out["rz_imb_rising"] = (dimb > 0).astype(float)
    out["rz_book_pressure"] = book_pressure
    # planning score proxy (soft): combine normalized signals
    plan = (
        0.25 * (dimb.clip(-20, 20) / 20.0 + 1) / 2
        + 0.25 * out["rz_imb_floor"]
        + 0.25 * (out["rz_net_sign"].abs())
        + 0.25 * ((pd.Series(book_pressure) > 0).astype(float))
    )
    out["rz_plan_score"] = plan.astype(float)
    return out


def add_multistep_path_labels(
    df: pd.DataFrame,
    *,
    horizon: int = 8,
    tp_pts: float = 20.0,
    sl_pts: float = 25.0,
) -> pd.DataFrame:
    """Multi-step path quality: 1 if path hits TP before SL and mid-path not wrecked."""
    out = df.copy()
    closes = out["close"].to_numpy(dtype=float)
    nets = out["net"].to_numpy(dtype=float)
    plan = out.get("rz_plan_score", pd.Series(np.zeros(len(out)))).to_numpy(dtype=float)
    n = len(out)
    y_path = np.full(n, np.nan)
    path_mfe = np.full(n, np.nan)
    path_mae = np.full(n, np.nan)
    for i in range(n):
        if nets[i] == 0 or i + 1 >= n:
            continue
        # planning gate: weak plan → harder label (still labeled)
        direction = 1.0 if nets[i] > 0 else -1.0
        ep = closes[i]
        mfe = 0.0
        mae = 0.0
        hit = 0.0
        end = min(n, i + 1 + max(1, horizon))
        mid_ok = True
        for j in range(i + 1, end):
            move = direction * (closes[j] - ep)
            mfe = max(mfe, move)
            mae = min(mae, move)
            # multi-step: if deep adverse early, path fails
            frac = (j - i) / max(1, horizon)
            if frac <= 0.5 and mae <= -0.7 * sl_pts:
                mid_ok = False
            if mfe >= tp_pts:
                hit = 1.0 if mid_ok else 0.0
                break
            if mae <= -sl_pts:
                hit = 0.0
                break
        # require decent plan score for positive path (logic∘planning)
        if hit == 1.0 and plan[i] < 0.35:
            hit = 0.0
        y_path[i] = hit
        path_mfe[i] = mfe
        path_mae[i] = mae
    out["y_path"] = y_path
    out["path_mfe"] = path_mfe
    out["path_mae"] = path_mae
    return out


def scale_feature_columns(lags: int) -> list[str]:
    return feature_columns(lags) + list(REASON_FEATURE_COLS)


def build_scale_frame(
    bars: list[dict[str, Any]],
    *,
    lags: int = 3,
    horizon: int = 8,
    tp_pts: float = 20.0,
    sl_pts: float = 25.0,
    lots: float = 100.0,
) -> pd.DataFrame:
    df0 = bars_to_frame(bars)
    df1 = build_features(df0, lags=lags)
    df2 = add_reasoner_features(df1, tp=tp_pts, sl=sl_pts, lots=lots)
    df3 = add_edge_labels(df2, horizon=horizon, tp_pts=tp_pts, sl_pts=sl_pts)
    df4 = add_multistep_path_labels(
        df3, horizon=horizon, tp_pts=tp_pts, sl_pts=sl_pts
    )
    return df4


def _xy(df: pd.DataFrame, lags: int, label: str = "y_path") -> tuple[pd.DataFrame, np.ndarray]:
    cols = scale_feature_columns(lags)
    use = df.dropna(subset=cols + [label]).copy()
    use = use[use[label].isin([0.0, 1.0])]
    return use[cols].astype(float), use[label].astype(int).to_numpy()


def walk_forward_train(
    df: pd.DataFrame,
    *,
    lags: int = 3,
    folds: int = 5,
    hidden: tuple[int, ...] = (96, 48, 24),
    label: str = "y_path",
) -> tuple[dict[str, Any], list[FoldScore]]:
    """Science at scale: walk-forward OOS metrics, then final fit."""
    X, y = _xy(df, lags, label=label)
    if len(y) < 60:
        raise ValueError(f"need >=60 labeled rows for scale train, got {len(y)}")
    if len(set(y.tolist())) < 2:
        raise ValueError("single-class labels — need more varied ticks")

    n = len(X)
    fold_sizes = max(15, n // max(2, folds))
    scores: list[FoldScore] = []
    # expanding window
    for f in range(max(2, folds) - 1):
        cut = fold_sizes * (f + 1)
        te_end = min(n, cut + fold_sizes)
        if cut < 40 or te_end - cut < 10:
            continue
        Xtr, ytr = X.iloc[:cut], y[:cut]
        Xte, yte = X.iloc[cut:te_end], y[cut:te_end]
        if len(set(ytr.tolist())) < 2 or len(set(yte.tolist())) < 2:
            continue
        clf = make_mlp(hidden=hidden)
        clf.fit(Xtr, ytr)
        proba = clf.predict_proba(Xte)[:, 1]
        pred = (proba >= 0.5).astype(int)
        auc = float(roc_auc_score(yte, proba))
        scores.append(
            FoldScore(
                fold=f,
                n_train=int(len(ytr)),
                n_test=int(len(yte)),
                accuracy=float(accuracy_score(yte, pred)),
                auc=auc,
                brier=float(brier_score_loss(yte, proba)),
            )
        )
        journal(
            "scale_fold",
            {"fold": f, "auc": auc, "n_train": int(len(ytr)), "n_test": int(len(yte))},
        )

    # final model: train on first 80%
    cut = max(40, int(0.8 * n))
    if cut >= n - 10:
        cut = n - 10
    clf = make_mlp(hidden=hidden)
    clf.fit(X.iloc[:cut], y[:cut])
    proba = clf.predict_proba(X.iloc[cut:])[:, 1]
    yte = y[cut:]
    pred = (proba >= 0.5).astype(int)
    oos_auc = (
        float(roc_auc_score(yte, proba)) if len(set(yte.tolist())) > 1 else None
    )
    fold_aucs = [s.auc for s in scores if s.auc is not None]
    metrics = {
        "n_total": int(len(y)),
        "n_train": int(cut),
        "n_test": int(len(yte)),
        "pos_rate_train": float(y[:cut].mean()),
        "pos_rate_test": float(yte.mean()),
        "accuracy": float(accuracy_score(yte, pred)),
        "auc": oos_auc,
        "brier": float(brier_score_loss(yte, proba)) if len(yte) else None,
        "walk_forward_auc_mean": float(np.mean(fold_aucs)) if fold_aucs else None,
        "walk_forward_auc_std": float(np.std(fold_aucs)) if fold_aucs else None,
        "n_folds_scored": len(scores),
        "lags": lags,
        "hidden": list(hidden),
        "model": "mlp_scale_v3",
        "label": label,
        "feature_count": len(scale_feature_columns(lags)),
        "stages": list(STAGE_MAP.keys()) + ["scale_walk_forward", "multistep_path"],
    }
    bundle = {
        "version": BUNDLE_VERSION,
        "model": clf,
        "feature_cols": scale_feature_columns(lags),
        "metrics": metrics,
        "folds": [asdict(s) for s in scores],
        "scale": True,
        "reason_features": list(REASON_FEATURE_COLS),
        "stage_map": STAGE_MAP,
    }
    return bundle, scores


def plan_curriculum(metrics: dict[str, Any], fold_scores: list[FoldScore]) -> list[dict[str, str]]:
    """Planning: what to improve next week (Sheets-readable)."""
    rows: list[dict[str, str]] = []
    auc = metrics.get("auc")
    wf = metrics.get("walk_forward_auc_mean")
    if auc is None or float(auc) < 0.55:
        rows.append(
            {
                "priority": "1",
                "domain": "science",
                "action": "collect_more_ticks_and_feedback",
                "detail": f"OOS AUC={auc}; keep S8_REQUIRE_NN=false",
            }
        )
    if wf is not None and float(wf) < 0.55:
        rows.append(
            {
                "priority": "2",
                "domain": "mathematics",
                "action": "raise_label_quality",
                "detail": "Try tp/sl retune or longer horizon in weekly job",
            }
        )
    rows.append(
        {
            "priority": "3",
            "domain": "logic",
            "action": "keep_learned_rules",
            "detail": "S8_MODEL=learned (IMB floor + book_drop) until NN safety OK",
        }
    )
    rows.append(
        {
            "priority": "4",
            "domain": "planning",
            "action": "weekly_retrain",
            "detail": "Cron ./weekly_s8_nn.sh; import evolution.csv + curriculum.csv",
        }
    )
    rows.append(
        {
            "priority": "5",
            "domain": "multi-step",
            "action": "fill_feedback_csv",
            "detail": "Mark prefer/reject on week_*/feedback_stub.csv → feedback.csv",
        }
    )
    if fold_scores:
        best = max(fold_scores, key=lambda s: s.auc or 0.0)
        rows.append(
            {
                "priority": "6",
                "domain": "science",
                "action": "review_best_fold",
                "detail": f"best fold={best.fold} auc={best.auc} n_test={best.n_test}",
            }
        )
    return rows


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


def run_scale(
    *,
    db: Path,
    tf: int = 10,
    lags: int = 3,
    folds: int = 5,
    horizon: int = 8,
    tp_pts: float = 20.0,
    sl_pts: float = 25.0,
    lots: float = 100.0,
    out_model: Path = DEFAULT_MODEL_PATH,
    out_dir: Path = SCALE_OUT,
) -> dict[str, Any]:
    journal("scale_improve_start", {"db": str(db), "tf": tf, "folds": folds})
    rows = load_tick_rows(db)
    if len(rows) < 500:
        raise SystemExit("need more ticks (>=500) for scale improve")
    bars = [b.to_row() for b in build_rich_bars(rows, f"{tf}m", tf)]
    print(f"scale: ticks={len(rows)} bars={len(bars)} folds={folds}")

    df = build_scale_frame(
        bars, lags=lags, horizon=horizon, tp_pts=tp_pts, sl_pts=sl_pts, lots=lots
    )
    bundle, fold_scores = walk_forward_train(df, lags=lags, folds=folds)
    save_bundle(bundle, out_model)
    print(json.dumps(bundle["metrics"], indent=2))

    curriculum = plan_curriculum(bundle["metrics"], fold_scores)
    week_id = datetime.now(IST).strftime("%Y-%m-%d")
    week_dir = out_dir / f"week_{week_id}"
    week_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(week_dir / "curriculum.csv", curriculum)
    _write_csv(out_dir / "curriculum_latest.csv", curriculum)
    _write_csv(
        week_dir / "walk_forward_folds.csv",
        [asdict(s) for s in fold_scores],
    )

    # Safety vs dummy baseline placeholder (scale script focuses on model quality)
    auc = bundle["metrics"].get("auc")
    safety = safety_check(
        bundle["metrics"],
        nn_sum_inr=0.0,
        baseline_sum_inr=0.0,
        nn_n=int(bundle["metrics"].get("n_test") or 0),
        min_auc=0.55,
    )
    # Override: scale job alone shouldn't enable NN without paper replay beat
    if safety.ok_to_enable_nn:
        safety.ok_to_enable_nn = False
        safety.reasons.append(
            "scale job: re-run evolve_s8_ml.py train for paper ₹ gate before enable"
        )
    write_pipeline_meta(bundle, safety)

    summary = {
        "week_id": week_id,
        "generated_at_ist": datetime.now(IST).isoformat(),
        "ticks": len(rows),
        "bars": len(bars),
        "auc": auc,
        "walk_forward_auc_mean": bundle["metrics"].get("walk_forward_auc_mean"),
        "brier": bundle["metrics"].get("brier"),
        "n_folds": len(fold_scores),
        "feature_count": bundle["metrics"].get("feature_count"),
        "model_path": str(out_model),
        "curriculum_top": curriculum[0]["action"] if curriculum else "",
        "pipeline": "v3_scale_improve",
    }
    _write_csv(week_dir / "scale_summary.csv", [summary])
    (week_dir / "scale_metrics.json").write_text(
        json.dumps({"metrics": bundle["metrics"], "folds": [asdict(s) for s in fold_scores], "curriculum": curriculum}, indent=2),
        encoding="utf-8",
    )
    journal("scale_improve_done", summary)
    print("--- SCALE CURRICULUM ---")
    for r in curriculum:
        print(f"  [{r['priority']}] {r['domain']}: {r['action']} — {r['detail']}")
    print(f"Sheets: {week_dir / 'curriculum.csv'}")
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--tf", type=int, default=10)
    ap.add_argument("--lags", type=int, default=3)
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--horizon", type=int, default=8)
    ap.add_argument("--tp-pts", type=float, default=20.0)
    ap.add_argument("--sl-pts", type=float, default=25.0)
    ap.add_argument("--lots", type=float, default=100.0)
    ap.add_argument("--out-model", default=str(DEFAULT_MODEL_PATH))
    ap.add_argument("--out-dir", default=str(SCALE_OUT))
    args = ap.parse_args()
    run_scale(
        db=Path(args.db),
        tf=args.tf,
        lags=args.lags,
        folds=args.folds,
        horizon=args.horizon,
        tp_pts=args.tp_pts,
        sl_pts=args.sl_pts,
        lots=args.lots,
        out_model=Path(args.out_model),
        out_dir=Path(args.out_dir),
    )


if __name__ == "__main__":
    main()
