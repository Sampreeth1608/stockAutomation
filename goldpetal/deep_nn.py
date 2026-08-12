"""Deep neural-network trainers for Gold Petal S8 (feed-forward DL on tick bars).

We already ship a shallow MLP. This module adds deeper architectures:

  shallow  (64, 32)           — fast baseline
  deep     (128, 64, 32, 16)  — default deep net
  deeper   (256, 128, 64, 32) — heavier; needs more bars

All use Adam + early stopping + L2. Still tabular DL (right fit for TBQ/TSQ/IMB
features). PyTorch/Transformers can be layered later once tick history is large.

Usage:
  python3 deep_nn.py compare --db data/ticks.db --tf 30
  from deep_nn import train_architecture, compare_architectures
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.metrics import accuracy_score, roc_auc_score
from sklearn.neural_network import MLPClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from mtf_bars import DB, build_rich_bars, load_tick_rows
from s8_nn import (
    DEFAULT_MODEL_PATH,
    add_edge_labels,
    matrix_xy,
    save_bundle,
)
from s9_bar_ml import bars_to_frame, build_features

ARCHS: dict[str, tuple[int, ...]] = {
    "shallow": (64, 32),
    "deep": (128, 64, 32, 16),
    "deeper": (256, 128, 64, 32),
}


def make_deep_mlp(
    *,
    hidden: tuple[int, ...],
    max_iter: int = 400,
    alpha: float = 3e-4,
    learning_rate_init: float = 8e-4,
) -> Pipeline:
    """Deep feed-forward net (ReLU + Adam). Deeper ⇒ slightly stronger L2."""
    depth = len(hidden)
    # Deeper nets: more regularization, longer patience, smaller batches.
    alpha_use = alpha * (1.0 + 0.25 * max(0, depth - 2))
    batch = 32 if depth >= 4 else 64
    return Pipeline(
        [
            ("scaler", StandardScaler()),
            (
                "mlp",
                MLPClassifier(
                    hidden_layer_sizes=hidden,
                    activation="relu",
                    solver="adam",
                    alpha=alpha_use,
                    batch_size=batch,
                    learning_rate_init=learning_rate_init,
                    max_iter=max_iter,
                    early_stopping=True,
                    n_iter_no_change=20 if depth >= 4 else 15,
                    validation_fraction=0.15,
                    random_state=42,
                ),
            ),
        ]
    )


def _prepare_xy(
    bars: list[dict[str, Any]],
    *,
    lags: int,
    horizon: int,
    tp_pts: float,
    sl_pts: float,
) -> tuple[Any, np.ndarray, list[str]]:
    if len(bars) < 40:
        raise ValueError(f"need >=40 bars, got {len(bars)}")
    df0 = bars_to_frame(bars)
    df1 = build_features(df0, lags=lags)
    df2 = add_edge_labels(df1, horizon=horizon, tp_pts=tp_pts, sl_pts=sl_pts)
    X, y, cols = matrix_xy(df2, lags=lags)
    if len(y) < 30:
        raise ValueError(f"too few labeled rows: {len(y)}")
    if len(set(y.tolist())) < 2:
        raise ValueError("labels are single-class — need more varied ticks")
    return X, y, cols


def train_architecture(
    bars: list[dict[str, Any]],
    *,
    arch: str = "deep",
    lags: int = 3,
    train_frac: float = 0.75,
    horizon: int = 6,
    tp_pts: float = 20.0,
    sl_pts: float = 25.0,
    max_iter: int = 400,
) -> dict[str, Any]:
    """Train one NN architecture; return joblib-ready bundle + metrics."""
    if arch not in ARCHS:
        raise ValueError(f"unknown arch={arch}; choose {sorted(ARCHS)}")
    hidden = ARCHS[arch]
    X, y, cols = _prepare_xy(
        bars, lags=lags, horizon=horizon, tp_pts=tp_pts, sl_pts=sl_pts
    )
    cut = max(10, int(len(X) * train_frac))
    if cut >= len(X) - 5:
        cut = len(X) - 5
    Xtr, Xte = X.iloc[:cut], X.iloc[cut:]
    ytr, yte = y[:cut], y[cut:]

    clf = make_deep_mlp(hidden=hidden, max_iter=max_iter)
    clf.fit(Xtr, ytr)
    proba = clf.predict_proba(Xte)[:, 1]
    pred = (proba >= 0.5).astype(int)
    auc = (
        float(roc_auc_score(yte, proba)) if len(set(yte.tolist())) > 1 else None
    )
    metrics = {
        "n_total": int(len(y)),
        "n_train": int(len(ytr)),
        "n_test": int(len(yte)),
        "pos_rate_train": float(ytr.mean()),
        "pos_rate_test": float(yte.mean()),
        "accuracy": float(accuracy_score(yte, pred)),
        "auc": auc,
        "horizon": horizon,
        "tp_pts": tp_pts,
        "sl_pts": sl_pts,
        "lags": lags,
        "hidden": list(hidden),
        "arch": arch,
        "model": f"deep_mlp_{arch}",
        "n_layers": len(hidden),
        "n_params_est": int(sum(hidden) * (len(cols) + sum(hidden))),
    }
    return {
        "version": 4,
        "model": clf,
        "feature_cols": cols,
        "metrics": metrics,
        "arch": arch,
        "family": "deep_neural_net",
    }


def compare_architectures(
    bars: list[dict[str, Any]],
    *,
    arches: list[str] | None = None,
    lags: int = 3,
    horizon: int = 6,
    tp_pts: float = 20.0,
    sl_pts: float = 25.0,
) -> dict[str, Any]:
    """Train shallow + deep (+ optional deeper); pick best by OOS AUC then accuracy."""
    arches = arches or ["shallow", "deep"]
    # Deeper only if enough bars — otherwise it overfits hard.
    if len(bars) >= 200 and "deeper" not in arches:
        arches = list(arches) + ["deeper"]

    results: list[dict[str, Any]] = []
    bundles: dict[str, dict[str, Any]] = {}
    for arch in arches:
        try:
            bundle = train_architecture(
                bars,
                arch=arch,
                lags=lags,
                horizon=horizon,
                tp_pts=tp_pts,
                sl_pts=sl_pts,
            )
            bundles[arch] = bundle
            results.append({"arch": arch, **bundle["metrics"], "ok": True})
        except Exception as exc:
            results.append({"arch": arch, "ok": False, "error": str(exc)})

    ok_rows = [r for r in results if r.get("ok") and r.get("auc") is not None]
    if not ok_rows:
        ok_rows = [r for r in results if r.get("ok")]
    if not ok_rows:
        raise RuntimeError(f"all architectures failed: {results}")

    def _key(r: dict[str, Any]) -> tuple:
        return (float(r.get("auc") or 0.0), float(r.get("accuracy") or 0.0))

    winner = max(ok_rows, key=_key)
    win_arch = str(winner["arch"])
    return {
        "winner": win_arch,
        "winner_metrics": winner,
        "results": results,
        "bundle": bundles[win_arch],
        "note": (
            "Deep NN = multi-layer ReLU MLP (Adam). "
            "Winner chosen by out-of-sample AUC, then accuracy. "
            "Still paper-gated before live."
        ),
    }


def cmd_compare(args: argparse.Namespace) -> int:
    rows = load_tick_rows(Path(args.db))
    if len(rows) < 200:
        raise SystemExit("need more ticks in data/ticks.db")
    bars = [b.to_row() for b in build_rich_bars(rows, f"{args.tf}m", args.tf)]
    print(f"ticks={len(rows)} bars={len(bars)} tf={args.tf}m")
    arches = [a.strip() for a in args.arches.split(",") if a.strip()]
    report = compare_architectures(
        bars,
        arches=arches,
        lags=args.lags,
        horizon=args.horizon,
        tp_pts=args.tp_pts,
        sl_pts=args.sl_pts,
    )
    out = Path(args.out)
    save_bundle(report["bundle"], out)
    summary = {
        "winner": report["winner"],
        "results": report["results"],
        "note": report["note"],
        "model_path": str(out),
    }
    out.with_suffix(".compare.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))
    print(f"saved winner bundle → {out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("compare", help="train shallow/deep/deeper and keep best")
    p.add_argument("--db", default=str(DB))
    p.add_argument("--tf", type=int, default=30)
    p.add_argument("--lags", type=int, default=3)
    p.add_argument("--horizon", type=int, default=6)
    p.add_argument("--tp-pts", type=float, default=20.0)
    p.add_argument("--sl-pts", type=float, default=25.0)
    p.add_argument("--arches", default="shallow,deep", help="comma list")
    p.add_argument("--out", default=str(DEFAULT_MODEL_PATH))
    p.set_defaults(func=cmd_compare)
    return ap


def main() -> None:
    ap = build_parser()
    args = ap.parse_args()
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
