#!/usr/bin/env python3
"""Weekly S5 min-edge ML improve → model + proposal.

Builds a simple classifier: will the next N minutes move ≥ required points
in the book-bias direction? Saves data/models/s5_minedge_ml.joblib.

  python3 evolve_s5_ml.py train --db data/ticks.db
  ./weekly_s5.sh
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import joblib
import numpy as np
from dataclasses import asdict
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from mtf_bars import build_rich_bars, load_tick_rows
from proposals import PaperResult, StrategyProposal, add_proposal, proposal_from_weekly_s5

IST = ZoneInfo("Asia/Kolkata")
OUT_ROOT = Path(__file__).resolve().parent / "data" / "s5_ml"
DEFAULT_MODEL = Path(__file__).resolve().parent / "data" / "models" / "s5_minedge_ml.joblib"


def _now() -> datetime:
    return datetime.now(IST)


def _build_dataset(bars: list[dict], *, horizon: int, req_pts: float) -> tuple[np.ndarray, np.ndarray]:
    """X = [atr_proxy, imb_abs, range, net_sign]; y = 1 if forward move covers req."""
    xs: list[list[float]] = []
    ys: list[int] = []
    for i in range(len(bars) - horizon):
        b = bars[i]
        close = float(b.get("close") or 0)
        if close <= 0:
            continue
        high = float(b.get("high") or close)
        low = float(b.get("low") or close)
        atr = max(0.0, high - low)
        imb = abs(float(b.get("imb_pct") or 0.0))
        net = float(b.get("net") or 0.0)
        # forward excursion in net direction (or abs if flat net)
        fut = bars[i + 1 : i + 1 + horizon]
        if not fut:
            continue
        closes = [float(x.get("close") or close) for x in fut]
        if net >= 0:
            mfe = max(closes) - close
        else:
            mfe = close - min(closes)
        y = 1 if mfe >= req_pts else 0
        xs.append([atr, imb, abs(net), close / 1000.0])
        ys.append(y)
    if not xs:
        return np.zeros((0, 4)), np.zeros((0,))
    return np.asarray(xs, dtype=float), np.asarray(ys, dtype=int)


def cmd_train(args: argparse.Namespace) -> int:
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    Path(args.model_dir).mkdir(parents=True, exist_ok=True)
    rows = load_tick_rows(Path(args.db))
    if len(rows) < 500:
        msg = f"need more ticks for S5 ML (have {len(rows)})"
        print(msg)
        prop = StrategyProposal(
            id="",
            week_id=_now().strftime("%Y-%m-%d"),
            kind="improved",
            strategy="S5_MINEDGE",
            title=f"S5 minedge — need more ticks ({_now().strftime('%Y-%m-%d')})",
            summary=msg,
            paper=PaperResult(),
            safety_ok=False,
            safety_reasons=[msg],
            env_patch={"DRY_RUN": "true", "S5_REASONING": "true"},
        )
        add_proposal(prop)
        print(f"Control panel proposal: {prop.id}")
        return 0

    tf_min = int(args.tf)
    rich = build_rich_bars(rows, f"{tf_min}m", tf_min)
    bars = [asdict(b) for b in rich]
    X, y = _build_dataset(bars, horizon=int(args.horizon), req_pts=float(args.req_pts))
    if len(X) < 40 or int(np.unique(y).size) < 2:
        msg = f"S5 dataset too small/unbalanced n={len(X)} pos={int(y.sum()) if len(y) else 0}"
        print(msg)
        prop = StrategyProposal(
            id="",
            week_id=_now().strftime("%Y-%m-%d"),
            kind="improved",
            strategy="S5_MINEDGE",
            title=f"S5 minedge — weak sample ({_now().strftime('%Y-%m-%d')})",
            summary=msg,
            paper=PaperResult(),
            safety_ok=False,
            safety_reasons=[msg],
            env_patch={"DRY_RUN": "true", "S5_REASONING": "true"},
        )
        add_proposal(prop)
        return 0

    cut = max(10, int(len(X) * 0.7))
    if cut >= len(X):
        cut = len(X) - 5
    Xtr, Xte = X[:cut], X[cut:]
    ytr, yte = y[:cut], y[cut:]
    pipe = Pipeline(
        [
            ("scaler", StandardScaler()),
            ("clf", LogisticRegression(max_iter=400, class_weight="balanced")),
        ]
    )
    pipe.fit(Xtr, ytr)
    pred = pipe.predict(Xte)
    proba = pipe.predict_proba(Xte)[:, 1]
    acc = float(accuracy_score(yte, pred))
    try:
        auc = float(roc_auc_score(yte, proba))
    except ValueError:
        auc = 0.0

    model_path = Path(args.model_path)
    joblib.dump(
        {
            "model": pipe,
            "features": ["atr", "imb_abs", "net_abs", "px_k"],
            "req_pts": float(args.req_pts),
            "horizon": int(args.horizon),
            "tf": int(args.tf),
            "metrics": {"accuracy": acc, "auc": auc, "n_train": len(Xtr), "n_test": len(Xte)},
        },
        model_path,
    )

    ok = auc >= 0.55 and len(Xte) >= 10
    reasons = (
        ["S5 ML OK for paper — set S5_ML_MODEL_PATH + S5_REASONING (+ optional S5_REQUIRE_ML)"]
        if ok
        else [f"auc={auc:.3f} or small test — keep reasoning rules, ML optional"]
    )
    summary = {
        "week_id": _now().strftime("%Y-%m-%d"),
        "accuracy": acc,
        "auc": auc,
        "n_train": len(Xtr),
        "n_test": len(Xte),
        "pos_rate": float(y.mean()),
        "model_path": str(model_path),
        "note": args.budget_note,
    }
    week_dir = OUT_ROOT / f"week_{summary['week_id']}"
    week_dir.mkdir(parents=True, exist_ok=True)
    (week_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    with (week_dir / "summary.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(summary.keys()))
        w.writeheader()
        w.writerow(summary)

    prop = proposal_from_weekly_s5(
        week_id=summary["week_id"],
        summary=summary,
        safety_ok=ok,
        safety_reasons=reasons,
        model_path=str(model_path),
    )
    add_proposal(prop)
    print(json.dumps(summary, indent=2))
    print(f"SAFETY: {ok} {reasons}")
    print(f"Control panel proposal: {prop.id} status=pending")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="Weekly S5 minedge ML evolve")
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("train")
    t.add_argument("--db", default="data/ticks.db")
    t.add_argument("--tf", type=int, default=5)
    t.add_argument("--horizon", type=int, default=6)
    t.add_argument("--req-pts", type=float, default=50.0)
    t.add_argument("--model-dir", default="data/models")
    t.add_argument("--model-path", default=str(DEFAULT_MODEL))
    t.add_argument("--budget-note", default="")
    t.set_defaults(func=cmd_train)
    args = ap.parse_args()
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
