"""S8 full ML evolution pipeline — LLM training stages mapped to trading.

ChatGPT-style stages → Gold Petal trading analogues (all coded here):

  Instruction tuning     → fine-tune MLP on winning / high-edge entry bars
  Human feedback         → data/s8_nn/feedback.csv (edit in Google Sheets, re-import)
  Preference optimization→ sample_weight: prefer wins / Sheets "prefer" rows
  Reasoning training     → 3 heads: entry_edge / hold_ok / exit_soon
  Safety training        → refuse enabling NN unless AUC/₹ gates pass; DRY_RUN only
  Tool-use training      → evolve_s8_ml.py subcommands (diagnose/train/sheets/apply)
  Conversation training  → stage journal JSONL (session log for Sheets notes)

Core optimization (all models):
  Prediction → Error → Backprop (sklearn) → Adam → parameter update
"""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, roc_auc_score
from sklearn.neural_network import MLPClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from s8_nn import DEFAULT_MODEL_PATH, add_edge_labels, predict_edge_proba
from s9_bar_ml import bars_to_frame, build_features, feature_columns

IST = ZoneInfo("Asia/Kolkata")
BUNDLE_VERSION = 2
FEEDBACK_PATH = Path("data/s8_nn/feedback.csv")
JOURNAL_PATH = Path("data/s8_nn/session_journal.jsonl")
PIPELINE_META = Path("data/s8_nn/pipeline_latest.json")

STAGE_MAP = {
    "instruction_tuning": "Fine-tune on high-edge / winning entry bars",
    "human_feedback": "Sheets feedback.csv prefer|reject|keep",
    "preference_optimization": "Adam fit with sample_weight from prefs + PnL",
    "reasoning_training": "Heads: entry_edge, hold_ok, exit_soon",
    "safety_training": "Enable gate only if AUC/₹ safety checks pass",
    "tool_use": "CLI tools: diagnose, train, sheets, apply",
    "conversation": "session_journal.jsonl stage log for human notes",
    "optimization": "Gradient descent via Adam (sklearn MLP)",
}


@dataclass
class SafetyReport:
    ok_to_enable_nn: bool
    reasons: list[str] = field(default_factory=list)
    auc: float | None = None
    nn_sum_inr: float | None = None
    baseline_sum_inr: float | None = None
    nn_n: int = 0
    min_auc: float = 0.55
    require_beat_baseline: bool = True


def journal(event: str, detail: dict[str, Any] | None = None) -> None:
    """Conversation-training analogue: append a session log line."""
    JOURNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "ts_ist": datetime.now(IST).isoformat(),
        "event": event,
        "detail": detail or {},
    }
    with JOURNAL_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


def ensure_feedback_template(path: Path = FEEDBACK_PATH) -> Path:
    """Human-feedback sheet template (import/export with Google Sheets)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > 0:
        return path
    cols = [
        "week_id",
        "entry_ts",
        "side",
        "preference",  # prefer | reject | keep
        "note",
        "pnl_inr",
    ]
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerow(
            {
                "week_id": "example",
                "entry_ts": "",
                "side": "long",
                "preference": "prefer",
                "note": "edit this sheet weekly; prefer=boost reject=downweight",
                "pnl_inr": "",
            }
        )
    return path


def load_feedback(path: Path = FEEDBACK_PATH) -> list[dict[str, str]]:
    ensure_feedback_template(path)
    with path.open(encoding="utf-8") as fh:
        rows = [r for r in csv.DictReader(fh) if (r.get("preference") or "").strip()]
    return [r for r in rows if (r.get("week_id") or "") != "example"]


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


def add_reasoning_labels(
    df: pd.DataFrame,
    *,
    horizon: int = 6,
    hold_bars: int = 2,
    tp_pts: float = 20.0,
    sl_pts: float = 25.0,
) -> pd.DataFrame:
    """Reasoning heads: entry edge, hold_ok, exit_soon."""
    out = add_edge_labels(df, horizon=horizon, tp_pts=tp_pts, sl_pts=sl_pts)
    closes = out["close"].to_numpy(dtype=float)
    nets = out["net"].to_numpy(dtype=float)
    n = len(out)
    hold = np.full(n, np.nan)
    exit_soon = np.full(n, np.nan)
    for i in range(n):
        if nets[i] == 0 or i + 1 >= n:
            continue
        direction = 1.0 if nets[i] > 0 else -1.0
        ep = closes[i]
        # hold_ok: after hold_bars, still not stopped and MFE growing or flat+
        j = min(n - 1, i + max(1, hold_bars))
        move_j = direction * (closes[j] - ep)
        mfe = 0.0
        mae = 0.0
        for k in range(i + 1, min(n, i + 1 + horizon)):
            mv = direction * (closes[k] - ep)
            mfe = max(mfe, mv)
            mae = min(mae, mv)
        hold[i] = 1.0 if (mae > -sl_pts and move_j >= 0) else 0.0
        # exit_soon: hit adverse before target in first half horizon
        half = max(1, horizon // 2)
        hit_sl_early = False
        hit_tp = False
        for k in range(i + 1, min(n, i + 1 + half)):
            mv = direction * (closes[k] - ep)
            if mv <= -sl_pts:
                hit_sl_early = True
                break
            if mv >= tp_pts:
                hit_tp = True
                break
        exit_soon[i] = 1.0 if (hit_sl_early and not hit_tp) else 0.0
    out["y_hold"] = hold
    out["y_exit_soon"] = exit_soon
    return out


def _sample_weights(
    df_labeled: pd.DataFrame,
    feedback: list[dict[str, str]],
    *,
    lags: int,
) -> np.ndarray:
    """Preference optimization: higher weight for prefer/wins."""
    cols = feature_columns(lags)
    use = df_labeled.dropna(subset=cols + ["y_edge"]).copy()
    use = use[use["y_edge"].isin([0.0, 1.0])]
    w = np.ones(len(use), dtype=float)

    # Auto preference from label: edge=1 slightly upweighted (instruction prior)
    y = use["y_edge"].to_numpy()
    w = np.where(y == 1.0, 1.35, 1.0)

    # Human feedback by matching entry_ts prefix to bar time if present
    pref_map: dict[str, float] = {}
    for r in feedback:
        p = (r.get("preference") or "").strip().lower()
        ts = (r.get("entry_ts") or "").strip()
        if not ts or not p:
            continue
        if p == "prefer":
            pref_map[ts[:16]] = 2.5
        elif p == "reject":
            pref_map[ts[:16]] = 0.35
        elif p == "keep":
            pref_map[ts[:16]] = 1.0
        # pnl boost
        try:
            pnl = float(r.get("pnl_inr") or 0)
            if pnl > 0 and ts[:16] in pref_map:
                pref_map[ts[:16]] *= 1.2
            elif pnl < 0 and ts[:16] not in pref_map:
                pref_map[ts[:16]] = 0.5
        except ValueError:
            pass

    if pref_map and "time" in use.columns:
        times = use["time"].astype(str).str[:16]
        for i, t in enumerate(times):
            if t in pref_map:
                w[i] *= pref_map[t]
    return w


def train_full_pipeline(
    bars: list[dict[str, Any]],
    *,
    lags: int = 3,
    train_frac: float = 0.75,
    horizon: int = 6,
    tp_pts: float = 20.0,
    sl_pts: float = 25.0,
    hidden: tuple[int, ...] = (64, 32),
    feedback: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Run optimization + instruction + preference + reasoning heads."""
    journal("optimization_start", {"n_bars": len(bars), "lags": lags})
    if len(bars) < 40:
        raise ValueError(f"need >=40 bars, got {len(bars)}")

    df0 = bars_to_frame(bars)
    df1 = build_features(df0, lags=lags)
    df2 = add_reasoning_labels(
        df1, horizon=horizon, tp_pts=tp_pts, sl_pts=sl_pts
    )
    cols = feature_columns(lags)
    use = df2.dropna(subset=cols + ["y_edge"]).copy()
    use = use[use["y_edge"].isin([0.0, 1.0])]
    if len(use) < 30:
        raise ValueError(f"too few labeled rows: {len(use)}")
    y = use["y_edge"].astype(int).to_numpy()
    if len(set(y.tolist())) < 2:
        raise ValueError("labels are single-class — need more varied ticks")

    X = use[cols].astype(float)
    cut = max(10, int(len(X) * train_frac))
    if cut >= len(X) - 5:
        cut = len(X) - 5
    Xtr, Xte = X.iloc[:cut], X.iloc[cut:]
    ytr, yte = y[:cut], y[cut:]

    fb = feedback if feedback is not None else load_feedback()
    weights_all = _sample_weights(df2, fb, lags=lags)
    wtr = weights_all[:cut]

    # --- instruction tuning: upweight high-edge / strong-IMB bars ---
    w_fit = wtr.copy()
    n_instr = 0
    if "imb_pct" in use.columns:
        imb_tr = use["imb_pct"].to_numpy()[:cut]
        thr = float(np.nanpercentile(imb_tr, 70))
        mask = (ytr == 1) | (imb_tr >= thr)
        n_instr = int(mask.sum())
        if n_instr >= 15:
            w_fit = np.where(mask, w_fit * 1.8, w_fit)
            journal("instruction_tuning", {"n_instruction_rows": n_instr, "imb_p70": thr})
        else:
            journal("instruction_tuning_skip", {"reason": "too few rows"})
    else:
        journal("instruction_tuning_skip", {"reason": "no imb_pct"})

    # --- supervised + preference weights (Adam / backprop inside sklearn) ---
    clf = make_mlp(hidden=hidden)
    clf.fit(Xtr, ytr, mlp__sample_weight=w_fit)
    journal(
        "preference_optimization_fit",
        {"n_feedback": len(fb), "n_train": int(len(ytr)), "n_instruction_rows": n_instr},
    )

    proba = clf.predict_proba(Xte)[:, 1]
    pred = (proba >= 0.5).astype(int)
    metrics: dict[str, Any] = {
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
        "model": "mlp_pipeline_v2",
        "n_feedback_rows": len(fb),
        "stages": list(STAGE_MAP.keys()),
    }

    # --- reasoning heads (hold / exit_soon) ---
    heads: dict[str, Any] = {}
    for name, ycol in (("hold_ok", "y_hold"), ("exit_soon", "y_exit_soon")):
        sub = use.dropna(subset=[ycol])
        sub = sub[sub[ycol].isin([0.0, 1.0])]
        ys = sub[ycol].astype(int).to_numpy()
        if len(ys) < 30 or len(set(ys.tolist())) < 2:
            heads[name] = {"trained": False, "reason": "insufficient labels"}
            continue
        Xs = sub[cols].astype(float)
        c2 = max(10, int(len(Xs) * train_frac))
        if c2 >= len(Xs) - 5:
            c2 = len(Xs) - 5
        h = make_mlp(hidden=(32, 16), max_iter=150)
        h.fit(Xs.iloc[:c2], ys[:c2])
        pt = h.predict_proba(Xs.iloc[c2:])[:, 1]
        yt = ys[c2:]
        heads[name] = {
            "trained": True,
            "accuracy": float(accuracy_score(yt, (pt >= 0.5).astype(int))),
            "auc": float(roc_auc_score(yt, pt)) if len(set(yt.tolist())) > 1 else None,
            "n_train": int(c2),
            "n_test": int(len(yt)),
        }
        # store model on bundle under heads
        heads[name]["_model"] = h
    journal("reasoning_training", {k: {kk: vv for kk, vv in v.items() if kk != "_model"} for k, v in heads.items()})

    head_models = {k: v.pop("_model") for k, v in heads.items() if v.get("trained") and "_model" in v}

    return {
        "version": BUNDLE_VERSION,
        "model": clf,
        "feature_cols": cols,
        "metrics": metrics,
        "base_cols": list(use.columns[:5]),
        "heads": heads,
        "head_models": head_models,
        "stage_map": STAGE_MAP,
    }


def safety_check(
    metrics: dict[str, Any],
    *,
    nn_sum_inr: float,
    baseline_sum_inr: float,
    nn_n: int,
    min_auc: float = 0.55,
) -> SafetyReport:
    """Safety training analogue: only recommend NN gate when checks pass."""
    reasons: list[str] = []
    auc = metrics.get("auc")
    ok = True
    if auc is None or float(auc) < min_auc:
        ok = False
        reasons.append(f"auc {auc} < min_auc {min_auc}")
    if nn_n < 5:
        ok = False
        reasons.append(f"nn trades {nn_n} < 5")
    if nn_sum_inr <= baseline_sum_inr:
        ok = False
        reasons.append(
            f"nn ₹ {nn_sum_inr:.1f} did not beat baseline ₹ {baseline_sum_inr:.1f}"
        )
    if ok:
        reasons.append("all safety gates passed — OK to set S8_REQUIRE_NN=true (DRY_RUN)")
    else:
        reasons.append("keep S8_REQUIRE_NN=false; collect more weeks / feedback")
    rep = SafetyReport(
        ok_to_enable_nn=ok,
        reasons=reasons,
        auc=float(auc) if auc is not None else None,
        nn_sum_inr=nn_sum_inr,
        baseline_sum_inr=baseline_sum_inr,
        nn_n=nn_n,
        min_auc=min_auc,
    )
    journal("safety_training", asdict(rep))
    return rep


def write_pipeline_meta(bundle: dict[str, Any], safety: SafetyReport, path: Path = PIPELINE_META) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at_ist": datetime.now(IST).isoformat(),
        "stage_map": STAGE_MAP,
        "metrics": bundle.get("metrics"),
        "heads": bundle.get("heads"),
        "safety": asdict(safety),
        "env_if_safe": {
            "DRY_RUN": "true",
            "S8_REQUIRE_NN": "true" if safety.ok_to_enable_nn else "false",
            "S8_NN_MODEL_PATH": str(DEFAULT_MODEL_PATH),
            "S8_NN_MIN_PROBA": "0.55",
        },
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def predict_reasoning(
    bundle: dict[str, Any], feat_row: dict[str, float]
) -> dict[str, float | None]:
    """Tool helper: entry proba + optional hold/exit head scores."""
    out: dict[str, float | None] = {
        "entry_edge": predict_edge_proba(bundle, feat_row),
        "hold_ok": None,
        "exit_soon": None,
    }
    cols = bundle["feature_cols"]
    x = pd.DataFrame([{c: float(feat_row.get(c, 0.0)) for c in cols}])
    for name in ("hold_ok", "exit_soon"):
        m = (bundle.get("head_models") or {}).get(name)
        if m is None:
            continue
        proba = m.predict_proba(x)[0]
        classes = list(m.named_steps["mlp"].classes_)
        out[name] = float(proba[classes.index(1)]) if 1 in classes else float(proba[-1])
    return out
