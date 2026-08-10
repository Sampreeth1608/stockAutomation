"""S8 weekly neural net (sklearn MLP) for entry edge prediction.

Trains on multi-day bars built from ticks.db:
  features = relative book/price (imb, dtbq, dtsq, dnet, lags, …)
  label    = 1 if a NET-aligned entry would hit MFE>=tp before MAE<=-sl
             within the next `horizon` bars (else 0)

Live use (optional): AlignS8Strategy calls predict_proba() as an entry gate.
Weekly use: train_s8_nn_weekly.py retrain + Google Sheets CSV pack.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, roc_auc_score
from sklearn.neural_network import MLPClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from s9_bar_ml import BASE_COLS, bars_to_frame, build_features, feature_columns

BUNDLE_VERSION = 1
DEFAULT_MODEL_PATH = Path("data/models/s8_nn_mlp.joblib")


def add_edge_labels(
    df: pd.DataFrame,
    *,
    horizon: int = 6,
    tp_pts: float = 20.0,
    sl_pts: float = 25.0,
) -> pd.DataFrame:
    """Label bars where NET-aligned entry has edge in the forward window."""
    out = df.copy()
    closes = out["close"].to_numpy(dtype=float)
    nets = out["net"].to_numpy(dtype=float)
    n = len(out)
    y = np.full(n, np.nan)
    side = np.zeros(n, dtype=float)  # +1 long, -1 short
    for i in range(n):
        if nets[i] == 0 or i + 1 >= n:
            continue
        direction = 1.0 if nets[i] > 0 else -1.0
        side[i] = direction
        ep = closes[i]
        mfe = 0.0
        mae = 0.0
        hit = 0.0
        end = min(n, i + 1 + max(1, horizon))
        for j in range(i + 1, end):
            move = direction * (closes[j] - ep)
            mfe = max(mfe, move)
            mae = min(mae, move)
            if mfe >= tp_pts:
                hit = 1.0
                break
            if mae <= -sl_pts:
                hit = 0.0
                break
        y[i] = hit
    out["y_edge"] = y
    out["side_sign"] = side
    return out


def matrix_xy(
    df: pd.DataFrame, lags: int = 3
) -> tuple[pd.DataFrame, np.ndarray, list[str]]:
    cols = feature_columns(lags)
    use = df.dropna(subset=cols + ["y_edge"]).copy()
    use = use[use["y_edge"].isin([0.0, 1.0])]
    X = use[cols].astype(float)
    y = use["y_edge"].astype(int).to_numpy()
    return X, y, cols


def make_mlp(*, hidden=(64, 32), max_iter: int = 200) -> Pipeline:
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
                    batch_size=64,
                    learning_rate_init=1e-3,
                    max_iter=max_iter,
                    early_stopping=True,
                    n_iter_no_change=15,
                    validation_fraction=0.15,
                    random_state=42,
                ),
            ),
        ]
    )


def time_split_train(
    bars: list[dict[str, Any]],
    *,
    lags: int = 3,
    train_frac: float = 0.75,
    horizon: int = 6,
    tp_pts: float = 20.0,
    sl_pts: float = 25.0,
    hidden: tuple[int, ...] = (64, 32),
) -> dict[str, Any]:
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

    cut = max(10, int(len(X) * train_frac))
    if cut >= len(X) - 5:
        cut = len(X) - 5
    Xtr, Xte = X.iloc[:cut], X.iloc[cut:]
    ytr, yte = y[:cut], y[cut:]

    clf = make_mlp(hidden=hidden)
    clf.fit(Xtr, ytr)
    proba = clf.predict_proba(Xte)[:, 1]
    pred = (proba >= 0.5).astype(int)
    metrics = {
        "n_total": int(len(y)),
        "n_train": int(len(ytr)),
        "n_test": int(len(yte)),
        "pos_rate_train": float(ytr.mean()),
        "pos_rate_test": float(yte.mean()),
        "accuracy": float(accuracy_score(yte, pred)),
        "auc": float(roc_auc_score(yte, proba)) if len(set(yte.tolist())) > 1 else None,
        "horizon": horizon,
        "tp_pts": tp_pts,
        "sl_pts": sl_pts,
        "lags": lags,
        "hidden": list(hidden),
        "model": "mlp",
    }
    return {
        "version": BUNDLE_VERSION,
        "model": clf,
        "feature_cols": cols,
        "metrics": metrics,
        "base_cols": list(BASE_COLS),
    }


def save_bundle(bundle: dict[str, Any], path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(bundle, path)
    meta = {
        "version": bundle.get("version"),
        "metrics": bundle.get("metrics"),
        "feature_cols": bundle.get("feature_cols"),
    }
    path.with_suffix(".json").write_text(json.dumps(meta, indent=2), encoding="utf-8")


def load_bundle(path: Path | str) -> dict[str, Any]:
    return joblib.load(Path(path))


def predict_edge_proba(bundle: dict[str, Any], feat_row: dict[str, float]) -> float:
    cols = bundle["feature_cols"]
    x = pd.DataFrame([{c: float(feat_row.get(c, 0.0)) for c in cols}])
    proba = bundle["model"].predict_proba(x)[0]
    # class 1 = edge
    classes = list(bundle["model"].named_steps["mlp"].classes_)
    if 1 in classes:
        return float(proba[classes.index(1)])
    return float(proba[-1])


def features_from_hist(
    hist: list[dict[str, float]], *, lags: int = 3
) -> dict[str, float] | None:
    """Build s9-style features from a rolling decision-step history.

    Each hist item: {px, tbq, tsq, imb, net}
    Needs enough steps for rolling vol/net stats (≥25 recommended).
    """
    need = max(25, lags + 12)
    if len(hist) < need:
        return None
    rows = []
    prev_px = prev_tbq = prev_tsq = None
    for h in hist:
        tbq = float(h["tbq"])
        tsq = float(h["tsq"])
        net = float(h.get("net", tbq - tsq))
        px = float(h["px"])
        if prev_px is None:
            dpx = 0.0
            dtbq = 0.0
            dtsq = 0.0
        else:
            dpx = px - float(prev_px)
            dtbq = tbq - float(prev_tbq)
            dtsq = tsq - float(prev_tsq)
        rng = max(abs(dpx), 0.5)
        rows.append(
            {
                "time": "",
                "open": px - dpx,
                "high": max(px, px - dpx) + 0.25,
                "low": min(px, px - dpx) - 0.25,
                "close": px,
                "tbq": tbq,
                "tsq": tsq,
                "net": net,
                "imb_pct": float(h.get("imb", abs(net) / max(tbq, tsq, 1e-9) * 100)),
                "range_pts": rng,
                "bar_volume": abs(dtbq) + abs(dtsq) + 1.0,
            }
        )
        prev_px, prev_tbq, prev_tsq = px, tbq, tsq
    df = build_features(pd.DataFrame(rows), lags=lags)
    # Ratio cols can be NaN if rolling mean is 0 — fill for live gate
    for c in ("vol_ratio", "range_ratio", "net_z"):
        if c in df.columns:
            df[c] = df[c].fillna(1.0 if c != "net_z" else 0.0)
    for lag in range(1, max(1, int(lags)) + 1):
        col = f"vol_ratio_l{lag}"
        if col in df.columns:
            df[col] = df[col].fillna(1.0)
    last = df.iloc[-1]
    cols = feature_columns(lags)
    if last[cols].isna().any():
        return None
    return {c: float(last[c]) for c in cols}
