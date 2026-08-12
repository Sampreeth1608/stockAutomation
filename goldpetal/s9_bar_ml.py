"""S9 bar-level ML: TBQ/TSQ/H/L/volume → next-bar direction.

Tabular models only (LogReg / RF / sklearn GB / optional LightGBM).
Use as an *entry filter* on top of S9 rules — must beat BIAS_NET in paper-sim.

Features are *relative* (deltas / ratios / z-scores), not raw TBQ/TSQ levels —
absolute book sizes drift and make LogReg predict "up" almost always on a
bullish sample.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

ModelKind = Literal["logreg", "random_forest", "grad_boost", "lightgbm"]

# Relative features only (no raw tbq/tsq/net levels)
BASE_COLS = [
    "net_sign",
    "imb_pct",
    "dtbq",
    "dtsq",
    "dnet",
    "dpx",
    "range_pts",
    "bar_volume",
    "ret_1",
    "hl_pos",
    "vol_ratio",
    "range_ratio",
    "net_z",
]


def normalize_bar_time(ts: Any) -> str:
    """Canonical key shared by mtf bars, S9, and ML walk-forward."""
    if ts is None:
        return ""
    s = str(ts).strip()
    if not s:
        return ""
    # "2026-08-07T09:00:00+05:30" / iso → "2026-08-07 09:00:00"
    s = s.replace("T", " ")
    if "+" in s:
        s = s.split("+", 1)[0]
    if s.endswith("Z"):
        s = s[:-1]
    if "." in s:
        s = s.split(".", 1)[0]
    return s.strip()


def feature_columns(lags: int = 3) -> list[str]:
    cols = list(BASE_COLS)
    for lag in range(1, max(1, int(lags)) + 1):
        for name in (
            "imb_pct",
            "range_pts",
            "bar_volume",
            "dpx",
            "dtbq",
            "dtsq",
            "dnet",
            "ret_1",
            "vol_ratio",
        ):
            cols.append(f"{name}_l{lag}")
    return cols


def bars_to_frame(bars: list[dict[str, Any]]) -> pd.DataFrame:
    """Normalize rich / S9 bar dicts into a flat frame."""
    rows = []
    prev_vol = None
    for br in bars:
        tbq = float(br.get("tbq_close", br.get("tbq", 0.0)) or 0.0)
        tsq = float(br.get("tsq_close", br.get("tsq", 0.0)) or 0.0)
        o = float(br.get("open", br.get("close", 0.0)) or 0.0)
        h = float(br.get("high", o) or o)
        l = float(br.get("low", o) or o)
        c = float(br.get("close", o) or o)
        net = tbq - tsq
        imb = abs(net) / max(tbq, tsq, 1e-9) * 100.0
        rng = float(br.get("range_pts") or (h - l))
        bv = br.get("bar_volume")
        if bv is None:
            vc = br.get("volume_close")
            try:
                vc_f = float(vc) if vc is not None else None
            except (TypeError, ValueError):
                vc_f = None
            if vc_f is not None and prev_vol is not None:
                bv = max(0.0, vc_f - prev_vol)
            else:
                bv = 0.0
            if vc_f is not None:
                prev_vol = vc_f
        else:
            bv = float(bv)
            vc = br.get("volume_close")
            try:
                if vc is not None:
                    prev_vol = float(vc)
            except (TypeError, ValueError):
                pass
        rows.append(
            {
                "time": normalize_bar_time(br.get("time")),
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "tbq": tbq,
                "tsq": tsq,
                "net": net,
                "imb_pct": imb,
                "range_pts": rng,
                "bar_volume": float(bv),
            }
        )
    return pd.DataFrame(rows)


def build_features(df: pd.DataFrame, lags: int = 3) -> pd.DataFrame:
    out = df.copy()
    out["dtbq"] = out["tbq"].diff()
    out["dtsq"] = out["tsq"].diff()
    out["dnet"] = out["net"].diff()
    out["dpx"] = out["close"].diff()
    out["ret_1"] = out["close"].pct_change()
    out["net_sign"] = np.sign(out["net"]).replace(0.0, 0.0)
    span = (out["high"] - out["low"]).replace(0.0, np.nan)
    out["hl_pos"] = ((out["close"] - out["low"]) / span).fillna(0.5)
    # Rolling context so absolute scale does not dominate
    vol_ma = out["bar_volume"].rolling(10, min_periods=3).mean()
    rng_ma = out["range_pts"].rolling(10, min_periods=3).mean()
    out["vol_ratio"] = out["bar_volume"] / vol_ma.replace(0.0, np.nan)
    out["range_ratio"] = out["range_pts"] / rng_ma.replace(0.0, np.nan)
    net_mu = out["net"].rolling(20, min_periods=5).mean()
    net_sd = out["net"].rolling(20, min_periods=5).std().replace(0.0, np.nan)
    out["net_z"] = (out["net"] - net_mu) / net_sd
    for lag in range(1, max(1, int(lags)) + 1):
        for name in (
            "imb_pct",
            "range_pts",
            "bar_volume",
            "dpx",
            "dtbq",
            "dtsq",
            "dnet",
            "ret_1",
            "vol_ratio",
        ):
            out[f"{name}_l{lag}"] = out[name].shift(lag)
    return out


def add_labels(df: pd.DataFrame) -> pd.DataFrame:
    """y_up = 1 if next bar close > this close."""
    out = df.copy()
    nxt = out["close"].shift(-1)
    out["y_up"] = (nxt > out["close"]).astype(float)
    out["y_ret"] = nxt / out["close"] - 1.0
    return out


def make_estimator(kind: ModelKind):
    if kind == "logreg":
        return Pipeline(
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
        )
    if kind == "random_forest":
        return RandomForestClassifier(
            n_estimators=150,
            max_depth=5,
            min_samples_leaf=5,
            class_weight="balanced_subsample",
            random_state=42,
            n_jobs=-1,
        )
    if kind == "lightgbm":
        try:
            import lightgbm as lgb  # type: ignore

            return lgb.LGBMClassifier(
                n_estimators=120,
                max_depth=4,
                learning_rate=0.05,
                subsample=0.9,
                colsample_bytree=0.9,
                class_weight="balanced",
                random_state=42,
                verbosity=-1,
            )
        except ImportError as e:
            raise ImportError(
                "lightgbm not installed. pip install lightgbm "
                "or use --model logreg|random_forest|grad_boost"
            ) from e
    return GradientBoostingClassifier(
        n_estimators=80,
        max_depth=3,
        learning_rate=0.05,
        random_state=42,
    )


def _metrics(y_true: np.ndarray, y_prob: np.ndarray, y_pred: np.ndarray) -> dict:
    out = {
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "n": int(len(y_true)),
        "pred_up_rate": float(np.mean(y_pred)),
        "actual_up_rate": float(np.mean(y_true)),
        "mean_p_up": float(np.mean(y_prob)),
        "p_up_p50": float(np.median(y_prob)),
        "p_up_p10": float(np.quantile(y_prob, 0.10)),
        "p_up_p90": float(np.quantile(y_prob, 0.90)),
    }
    try:
        out["auc"] = float(roc_auc_score(y_true, y_prob))
    except ValueError:
        out["auc"] = None
    return out


def train_from_bars(
    bars: list[dict[str, Any]],
    *,
    lags: int = 3,
    train_frac: float = 0.7,
    model_kind: ModelKind = "logreg",
) -> dict[str, Any]:
    """Chronological split train; return bundle dict + metrics."""
    cols = feature_columns(lags)
    feat = add_labels(build_features(bars_to_frame(bars), lags=lags))
    usable = feat.dropna(subset=cols + ["y_up"]).reset_index(drop=True)
    if len(usable) < 30:
        raise ValueError(f"need ≥30 labeled bars, have {len(usable)}")
    if usable["y_up"].nunique() < 2:
        raise ValueError("labels are one-class — need mixed up/down bars")

    split = max(10, int(len(usable) * train_frac))
    split = min(split, len(usable) - 5)
    train_df, test_df = usable.iloc[:split], usable.iloc[split:]
    X_tr = train_df[cols].to_numpy(dtype=float)
    y_tr = train_df["y_up"].to_numpy(dtype=int)
    X_te = test_df[cols].to_numpy(dtype=float)
    y_te = test_df["y_up"].to_numpy(dtype=int)

    est = make_estimator(model_kind)
    est.fit(X_tr, y_tr)
    prob = est.predict_proba(X_te)[:, 1]
    pred = (prob >= 0.5).astype(int)
    metrics = _metrics(y_te, prob, pred)
    metrics["model"] = model_kind
    metrics["lags"] = lags
    metrics["n_train"] = int(len(train_df))
    metrics["train_up_rate"] = float(np.mean(y_tr))
    metrics["feature_cols"] = cols

    bundle = {
        "model": est,
        "feature_cols": cols,
        "lags": lags,
        "model_kind": model_kind,
        "metrics": metrics,
    }
    return bundle


def save_bundle(bundle: dict[str, Any], path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(bundle, path)


def load_bundle(path: Path) -> dict[str, Any]:
    return joblib.load(Path(path))


def predict_p_up(bundle: dict[str, Any], feature_row: dict[str, float]) -> float:
    cols = bundle["feature_cols"]
    x = np.array([[float(feature_row.get(c, 0.0)) for c in cols]], dtype=float)
    x = np.nan_to_num(x, nan=0.0, posinf=0.0, neginf=0.0)
    return float(bundle["model"].predict_proba(x)[0, 1])


def walk_forward_p_up(
    bars: list[dict[str, Any]],
    *,
    lags: int = 3,
    min_train: int = 40,
    model_kind: ModelKind = "logreg",
    retrain_every: int = 5,
) -> dict[str, float]:
    """Expanding-window P(up) at each bar time (no look-ahead)."""
    cols = feature_columns(lags)
    feat = add_labels(build_features(bars_to_frame(bars), lags=lags))
    n = len(feat)
    out: dict[str, float] = {}
    model = None
    last_fit_t = -10**9

    for t in range(n):
        train_idx = [
            j
            for j in range(0, t)
            if pd.notna(feat.loc[j, "y_up"]) and feat.loc[j, cols].notna().all()
        ]
        row_ok = feat.loc[t, cols].notna().all()
        if not row_ok or len(train_idx) < min_train:
            continue
        if model is None or (t - last_fit_t) >= max(1, retrain_every):
            X = feat.loc[train_idx, cols].to_numpy(dtype=float)
            y = feat.loc[train_idx, "y_up"].to_numpy(dtype=int)
            if len(np.unique(y)) < 2:
                continue
            model = make_estimator(model_kind)
            model.fit(X, y)
            last_fit_t = t
        x = feat.loc[[t], cols].to_numpy(dtype=float)
        x = np.nan_to_num(x, nan=0.0, posinf=0.0, neginf=0.0)
        p = float(model.predict_proba(x)[0, 1])
        ts = normalize_bar_time(feat.loc[t, "time"])
        if ts:
            out[ts] = p
    return out


def make_entry_filter(
    p_by_time: dict[str, float],
    *,
    min_proba: float = 0.55,
    allow_if_missing: bool = False,
    stats: dict[str, int] | None = None,
):
    """S9 extra_entry_filters callback: (bar, strategy, side) -> bool.

    Default allow_if_missing=False so a key mismatch cannot silently pass everything.
    """

    def _filt(bar: dict, _strategy: Any, side: str) -> bool:
        ts = normalize_bar_time(bar.get("time"))
        p = p_by_time.get(ts)
        if stats is not None:
            stats["checked"] = stats.get("checked", 0) + 1
        if p is None:
            if stats is not None:
                stats["missing"] = stats.get("missing", 0) + 1
            return bool(allow_if_missing)
        if stats is not None:
            stats["scored"] = stats.get("scored", 0) + 1
        if side == "long":
            ok = p >= min_proba
        elif side == "short":
            ok = (1.0 - p) >= min_proba
        else:
            ok = False
        if stats is not None:
            stats["pass" if ok else "block"] = stats.get("pass" if ok else "block", 0) + 1
        return ok

    return _filt
