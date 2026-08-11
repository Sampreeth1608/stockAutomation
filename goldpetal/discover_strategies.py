#!/usr/bin/env python3
"""Multi-model ML discovery on tick data → new strategy packs + proposals.

Pipeline:
  1) Mathematics / statistics / reasoning over ALL tick-data families
     (price, book L1–L5, TBQ/TSQ, OI, volume, spread/microprice)
  2) Generate strategy recipes from that behavior understanding
  3) Train logreg / RF / GB on predictive features
  4) Fee-aware paper sim → weekend proposal (S11)

  python3 discover_strategies.py run --db data/ticks.db
  ./weekly_discover.sh

Nothing enables paper/live until you approve the proposal and set env_patch.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import joblib
import numpy as np
import pandas as pd
from sklearn.base import clone
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from charges import apply_charges_and_tax, charges_from_env
from export_full_ticks import export_full_ticks
from ml_features import (
    FEATURE_COLUMNS,
    add_labels,
    build_features,
    load_ticks_csv,
    time_split,
)
from proposals import PaperResult, StrategyProposal, add_proposal
from tick_behavior import analyze_ticks, write_behavior_report

IST = ZoneInfo("Asia/Kolkata")
DEFAULT_OUT = Path("data/discover")
DEFAULT_DB = Path("data/ticks.db")


@dataclass
class Template:
    name: str
    buy_prob: float
    short_prob: float
    min_hold: int
    min_imb: float = 0.0  # |imb_l1| gate; 0 = off
    every_n: int = 5
    features: list[str] = field(default_factory=lambda: list(FEATURE_COLUMNS))
    rationale: str = ""
    family: str = "baseline"


@dataclass
class Candidate:
    model_name: str
    template: Template
    auc: float
    n_trades: int
    win_rate: float
    gross_pnl: float
    after_tax_pnl: float
    safety_ok: bool
    safety_reasons: list[str] = field(default_factory=list)
    pack_path: str = ""
    model_path: str = ""
    behavior_summary: str = ""

    def score(self) -> float:
        return float(self.after_tax_pnl) + 0.5 * min(self.n_trades, 40)


TEMPLATES: list[Template] = [
    Template("ml_strict", buy_prob=0.62, short_prob=0.38, min_hold=45, min_imb=0.0),
    Template("ml_mid", buy_prob=0.58, short_prob=0.42, min_hold=30, min_imb=0.0),
    Template("ml_imb", buy_prob=0.58, short_prob=0.42, min_hold=30, min_imb=0.15),
    Template("ml_imb_strict", buy_prob=0.60, short_prob=0.40, min_hold=40, min_imb=0.20),
]


def _week_id() -> str:
    return datetime.now(IST).strftime("%G-W%V")


def _models() -> dict[str, Any]:
    return {
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
            n_estimators=120,
            max_depth=7,
            min_samples_leaf=25,
            class_weight="balanced_subsample",
            random_state=42,
            n_jobs=-1,
        ),
        "grad_boost": GradientBoostingClassifier(
            n_estimators=100,
            max_depth=3,
            learning_rate=0.05,
            random_state=42,
        ),
    }


def _auc(y_true: np.ndarray, y_prob: np.ndarray) -> float:
    try:
        if len(np.unique(y_true)) < 2:
            return float("nan")
        return float(roc_auc_score(y_true, y_prob))
    except ValueError:
        return float("nan")


def _feature_list(preferred: list[str] | None) -> list[str]:
    cols = [c for c in (preferred or []) if c in FEATURE_COLUMNS]
    if len(cols) < 4:
        return list(FEATURE_COLUMNS)
    return [c for c in FEATURE_COLUMNS if c in cols]


def _xy(df: pd.DataFrame, features: list[str]) -> tuple[pd.DataFrame, pd.Series]:
    need = features + ["y_dir"]
    clean = df.dropna(subset=need).copy()
    return clean[features].astype(float), clean["y_dir"].astype(int)


def recipes_to_templates(behavior_recipes: list[Any]) -> list[Template]:
    out: list[Template] = []
    for r in behavior_recipes:
        out.append(
            Template(
                name=str(r.name),
                buy_prob=float(r.buy_prob),
                short_prob=float(r.short_prob),
                min_hold=int(r.min_hold),
                min_imb=float(r.min_imb),
                every_n=int(r.every_n),
                features=_feature_list(list(r.feature_focus or [])),
                rationale=str(r.rationale or ""),
                family=str(r.family or "generated"),
            )
        )
    for t in TEMPLATES[:2]:
        if all(x.name != t.name for x in out):
            out.append(t)
    return out


def paper_sim(
    *,
    ltp: np.ndarray,
    prob: np.ndarray,
    imb: np.ndarray,
    template: Template,
) -> dict[str, float]:
    cfg = charges_from_env()
    buy_p = template.buy_prob
    short_p = template.short_prob
    min_hold = max(1, int(template.min_hold))
    min_imb = float(template.min_imb)
    every = max(1, int(template.every_n))

    side = 0
    entry_px = 0.0
    entry_i = -10_000
    trades: list[dict[str, float]] = []

    def _close(px: float) -> None:
        nonlocal side, entry_px
        if side == 0:
            return
        pts = (px - entry_px) * side
        bits = apply_charges_and_tax(
            pts,
            cfg,
            side="BUY" if side > 0 else "SHORT",
            entry_price=entry_px,
            exit_price=px,
        )
        trades.append(
            {
                "gross": float(bits["gross_pnl"]),
                "after_tax": float(bits["pnl_after_tax"]),
                "win": 1.0 if float(bits["gross_pnl"]) > 0 else 0.0,
            }
        )
        side = 0

    for i in range(len(ltp)):
        if i % every != 0:
            continue
        p = float(prob[i])
        px = float(ltp[i])
        if not np.isfinite(p) or not np.isfinite(px):
            continue
        held = i - entry_i
        want = 0
        if p >= buy_p:
            want = 1
        elif p <= short_p:
            want = -1
        if min_imb > 0:
            ib = float(imb[i]) if np.isfinite(imb[i]) else 0.0
            if want == 1 and ib < min_imb:
                want = 0
            if want == -1 and ib > -min_imb:
                want = 0

        if side == 0:
            if want != 0:
                side = want
                entry_px = px
                entry_i = i
            continue

        if held < min_hold:
            continue
        if want == 0 or want == -side:
            _close(px)
            if want != 0:
                side = want
                entry_px = px
                entry_i = i

    if side != 0 and len(ltp):
        _close(float(ltp[-1]))

    n = len(trades)
    if n == 0:
        return {
            "n_trades": 0,
            "win_rate": 0.0,
            "gross_pnl": 0.0,
            "after_tax_pnl": 0.0,
        }
    return {
        "n_trades": float(n),
        "win_rate": float(np.mean([t["win"] for t in trades])),
        "gross_pnl": float(np.sum([t["gross"] for t in trades])),
        "after_tax_pnl": float(np.sum([t["after_tax"] for t in trades])),
    }


def _safety(auc: float, sim: dict[str, float]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    ok = True
    if not np.isfinite(auc) or auc < 0.55:
        ok = False
        reasons.append(f"auc={auc:.3f}<0.55")
    else:
        reasons.append(f"auc={auc:.3f} ok")
    if sim["n_trades"] < 5:
        ok = False
        reasons.append(f"n_trades={int(sim['n_trades'])}<5")
    else:
        reasons.append(f"n_trades={int(sim['n_trades'])} ok")
    if sim["after_tax_pnl"] <= 0:
        ok = False
        reasons.append(f"after_tax={sim['after_tax_pnl']:.1f}<=0")
    else:
        reasons.append(f"after_tax={sim['after_tax_pnl']:.1f} ok")
    if sim["win_rate"] < 0.45:
        ok = False
        reasons.append(f"win_rate={sim['win_rate']:.2f}<0.45")
    else:
        reasons.append(f"win_rate={sim['win_rate']:.2f} ok")
    return ok, reasons


def write_pack(
    *,
    out_dir: Path,
    model_name: str,
    model: Any,
    template: Template,
    auc: float,
    sim: dict[str, float],
    safety_ok: bool,
    safety_reasons: list[str],
    features: list[str],
    week_id: str,
    behavior_summary: str = "",
    behavior_path: str = "",
) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(IST).strftime("%Y%m%d_%H%M%S")
    slug = f"{model_name}_{template.name}_{stamp}"
    model_path = out_dir / "models" / f"{slug}.joblib"
    pack_path = out_dir / "packs" / f"{slug}.json"
    model_path.parent.mkdir(parents=True, exist_ok=True)
    pack_path.parent.mkdir(parents=True, exist_ok=True)

    joblib.dump(
        {
            "model": model,
            "features": features,
            "kind": "discover",
            "model_name": model_name,
            "template": asdict(template),
        },
        model_path,
    )
    pack = {
        "id": slug,
        "week_id": week_id,
        "strategy": "S11_DISCOVERED",
        "model_name": model_name,
        "model_path": str(model_path),
        "features": features,
        "buy_prob": template.buy_prob,
        "short_prob": template.short_prob,
        "min_hold_sec": float(template.min_hold),
        "every_n_ticks": template.every_n,
        "min_imb": template.min_imb,
        "family": template.family,
        "rationale": template.rationale,
        "auc": auc,
        "paper": sim,
        "safety_ok": safety_ok,
        "safety_reasons": safety_reasons,
        "behavior_summary": behavior_summary,
        "behavior_report": behavior_path,
        "created_at_ist": datetime.now(IST).isoformat(timespec="seconds"),
    }
    pack_path.write_text(json.dumps(pack, indent=2), encoding="utf-8")
    return pack_path, model_path


def run_discovery(
    *,
    csv_path: Path,
    out_dir: Path = DEFAULT_OUT,
    horizon: int = 0,
    threshold_bps: float = 2.0,
    train_frac: float = 0.7,
    top_k: int = 1,
) -> list[Candidate]:
    raw = load_ticks_csv(csv_path)
    if len(raw) < 400:
        raise SystemExit(f"need ≥400 ticks for discovery (have {len(raw)})")

    behavior = analyze_ticks(
        csv_path, horizon=horizon, threshold_bps=threshold_bps, lots=1.0
    )
    # Train/label on the fee-aware horizon chosen by behavior analysis
    use_horizon = int(behavior.horizon)
    behavior_path = write_behavior_report(behavior, out_dir / "behavior_report.json")
    templates = recipes_to_templates(behavior.recipes)
    print(f"behavior: {behavior.summary}", flush=True)
    print(
        f"generated recipes: {[t.name for t in templates]} "
        f"(predictive={[f.family for f in behavior.families if f.predictive]}) "
        f"horizon={use_horizon} table={behavior.horizon_table}",
        flush=True,
    )

    feat = build_features(raw)
    labeled = add_labels(feat, horizon=use_horizon, threshold_bps=threshold_bps)
    usable = labeled.dropna(subset=FEATURE_COLUMNS + ["y_dir", "y_ret"]).copy()
    train_df, test_df = time_split(usable, train_frac=train_frac)
    if train_df["y_dir"].nunique() < 2:
        raise SystemExit("train labels single-class — collect more varied ticks")

    week = _week_id()
    cands: list[Candidate] = []
    fitted: dict[tuple[str, ...], dict[str, Any]] = {}
    aucs: dict[tuple[str, ...], dict[str, float]] = {}
    probs: dict[tuple[str, ...], dict[str, np.ndarray]] = {}

    for tmpl in templates:
        feats = tuple(_feature_list(tmpl.features))
        if feats not in fitted:
            X_train, y_train = _xy(train_df, list(feats))
            need = list(feats) + ["y_dir", "ltp"]
            if "imb_l1" in test_df.columns:
                need.append("imb_l1")
            clean_test = test_df.dropna(
                subset=[c for c in need if c in test_df.columns]
            ).copy()
            X_test = clean_test[list(feats)].astype(float)
            y_test = clean_test["y_dir"].astype(int)
            ltp_aligned = clean_test["ltp"].to_numpy(dtype=float)
            imb_aligned = (
                clean_test["imb_l1"].to_numpy(dtype=float)
                if "imb_l1" in clean_test.columns
                else np.zeros(len(clean_test))
            )
            fitted[feats] = {}
            aucs[feats] = {}
            probs[feats] = {"__ltp__": ltp_aligned, "__imb__": imb_aligned}
            for mname, model in _models().items():
                m = clone(model)
                m.fit(X_train, y_train.to_numpy())
                prob = m.predict_proba(X_test)[:, 1]
                fitted[feats][mname] = m
                probs[feats][mname] = prob
                aucs[feats][mname] = _auc(y_test.to_numpy(), prob)

        ltp_use = probs[feats]["__ltp__"]
        imb_use = probs[feats]["__imb__"]
        for mname, model in fitted[feats].items():
            prob = probs[feats][mname]
            auc = aucs[feats][mname]
            sim = paper_sim(ltp=ltp_use, prob=prob, imb=imb_use, template=tmpl)
            ok, reasons = _safety(auc, sim)
            if (
                tmpl.family != "baseline"
                and behavior.atr_proxy_pts < behavior.fee_be_pts * 0.5
            ):
                ok = False
                reasons.append(
                    f"behavior_edge atr={behavior.atr_proxy_pts:.1f}<0.5*BE"
                )
            pack_path, model_path = write_pack(
                out_dir=out_dir,
                model_name=mname,
                model=model,
                template=tmpl,
                auc=auc if np.isfinite(auc) else 0.0,
                sim=sim,
                safety_ok=ok,
                safety_reasons=reasons,
                features=list(feats),
                week_id=week,
                behavior_summary=behavior.summary,
                behavior_path=str(behavior_path),
            )
            cands.append(
                Candidate(
                    model_name=mname,
                    template=tmpl,
                    auc=auc if np.isfinite(auc) else 0.0,
                    n_trades=int(sim["n_trades"]),
                    win_rate=float(sim["win_rate"]),
                    gross_pnl=float(sim["gross_pnl"]),
                    after_tax_pnl=float(sim["after_tax_pnl"]),
                    safety_ok=ok,
                    safety_reasons=reasons,
                    pack_path=str(pack_path),
                    model_path=str(model_path),
                    behavior_summary=behavior.summary,
                )
            )

    cands.sort(key=lambda c: (c.safety_ok, c.score()), reverse=True)
    report = {
        "week_id": week,
        "csv": str(csv_path),
        "horizon": use_horizon,
        "horizon_table": behavior.horizon_table,
        "n_train": int(len(train_df)),
        "n_test": int(len(test_df)),
        "behavior_report": str(behavior_path),
        "behavior_summary": behavior.summary,
        "reasoning": behavior.reasoning,
        "predictive_families": [asdict(f) for f in behavior.families if f.predictive],
        "recipes": [asdict(r) for r in behavior.recipes],
        "candidates": [
            {
                "model": c.model_name,
                "template": c.template.name,
                "family": c.template.family,
                "rationale": c.template.rationale,
                "auc": c.auc,
                "n_trades": c.n_trades,
                "win_rate": c.win_rate,
                "gross_pnl": c.gross_pnl,
                "after_tax_pnl": c.after_tax_pnl,
                "safety_ok": c.safety_ok,
                "safety_reasons": c.safety_reasons,
                "pack_path": c.pack_path,
            }
            for c in cands
        ],
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "latest_report.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    return cands[: max(1, top_k)]


def propose_best(cands: list[Candidate], *, note: str = "") -> StrategyProposal | None:
    if not cands:
        return None
    best = cands[0]
    week = _week_id()
    title = (
        f"S11 behavior+ML: {best.model_name}+{best.template.name} "
        f"[{best.template.family}] (AUC {best.auc:.3f})"
    )
    summary = (
        f"Analyzed all tick families (price/book/OI/volume/spread) with math+stats+reasoning; "
        f"generated recipes; trained logreg/rf/gb. "
        f"best={best.model_name}/{best.template.name} "
        f"({best.template.rationale or best.template.family}). "
        f"Paper: trades={best.n_trades} win={best.win_rate:.0%} "
        f"gross={best.gross_pnl:.1f} after_tax={best.after_tax_pnl:.1f}. "
        f"Behavior: {best.behavior_summary[:220]} "
        f"Approve → S11_PACK_PATH + ENABLE_S11 (DRY_RUN). {note}"
    ).strip()
    env_patch = {
        "DRY_RUN": "true",
    }
    if best.safety_ok:
        env_patch["ENABLE_S11"] = "true"
        env_patch["S11_PACK_PATH"] = best.pack_path
    else:
        # Do not tempt operators to enable a failing pack
        env_patch["ENABLE_S11"] = "false"
        env_patch["S11_PACK_PATH"] = ""
        summary = (
            summary
            + " DO NOT enable — safety_ok=False (after-tax/AUC/trades gates failed)."
        )
    prop = StrategyProposal(
        id="",
        week_id=week,
        kind="new",
        strategy="S11_DISCOVERED",
        title=title,
        summary=summary,
        paper=PaperResult(
            n_trades=best.n_trades,
            win_rate=best.win_rate,
            gross_pnl_inr=best.gross_pnl,
            after_tax_pnl_inr=best.after_tax_pnl,
            baseline_after_tax_inr=0.0,
            delta_vs_baseline_inr=best.after_tax_pnl,
            extra={
                "auc": best.auc,
                "model": best.model_name,
                "template": best.template.name,
                "family": best.template.family,
                "rationale": best.template.rationale,
                "behavior_summary": best.behavior_summary,
                "runners_up": [
                    {
                        "model": c.model_name,
                        "template": c.template.name,
                        "family": c.template.family,
                        "after_tax": c.after_tax_pnl,
                        "auc": c.auc,
                        "safety_ok": c.safety_ok,
                    }
                    for c in cands[1:4]
                ],
            },
        ),
        model_path=best.model_path,
        safety_ok=best.safety_ok,
        safety_reasons=best.safety_reasons,
        env_patch=env_patch,
    )
    return add_proposal(prop)


def cmd_run(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.csv:
        csv_path = Path(args.csv)
    else:
        csv_path = out_dir / "ticks_export.csv"
        n = export_full_ticks(csv_path, limit=args.limit)
        print(f"exported {n} ticks → {csv_path}")
    cands = run_discovery(
        csv_path=csv_path,
        out_dir=out_dir,
        horizon=args.horizon,
        threshold_bps=args.threshold_bps,
        train_frac=args.train_frac,
        top_k=args.top_k,
    )
    for i, c in enumerate(cands):
        print(
            f"[{i}] {c.model_name}/{c.template.name} ({c.template.family}) "
            f"auc={c.auc:.3f} trades={c.n_trades} "
            f"after_tax={c.after_tax_pnl:.1f} safety={c.safety_ok} "
            f"pack={c.pack_path}"
        )
    prop = propose_best(cands, note=args.note)
    if prop:
        print(f"proposal id={prop.id} status={prop.status} safety_ok={prop.safety_ok}")
        print("env_patch:", json.dumps(prop.env_patch, indent=2))
    return 0


def cmd_behavior(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.csv:
        csv_path = Path(args.csv)
    else:
        csv_path = out_dir / "ticks_export.csv"
        n = export_full_ticks(csv_path, limit=args.limit)
        print(f"exported {n} ticks → {csv_path}")
    report = analyze_ticks(csv_path, horizon=args.horizon)
    path = write_behavior_report(report, out_dir / "behavior_report.json")
    print(report.summary)
    print(f"wrote {path}")
    print("recipes:", [r.name for r in report.recipes])
    return 0


def main() -> None:
    p = argparse.ArgumentParser(
        description="Behavior-aware multi-model strategy discovery from ticks"
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    run_p = sub.add_parser(
        "run", help="Analyze behavior, generate recipes, train models, propose best"
    )
    run_p.add_argument("--db", default=str(DEFAULT_DB))
    run_p.add_argument("--csv", default="")
    run_p.add_argument("--limit", type=int, default=None)
    run_p.add_argument("--out-dir", default=str(DEFAULT_OUT))
    run_p.add_argument(
        "--horizon",
        type=int,
        default=0,
        help="label horizon in ticks; 0=auto fee-aware",
    )
    run_p.add_argument("--threshold-bps", type=float, default=2.0)
    run_p.add_argument("--train-frac", type=float, default=0.7)
    run_p.add_argument("--top-k", type=int, default=3)
    run_p.add_argument("--note", default="")
    run_p.set_defaults(func=cmd_run)

    beh = sub.add_parser("behavior", help="Math/stats/reasoning over tick families only")
    beh.add_argument("--csv", default="")
    beh.add_argument("--limit", type=int, default=None)
    beh.add_argument("--out-dir", default=str(DEFAULT_OUT))
    beh.add_argument(
        "--horizon",
        type=int,
        default=0,
        help="label horizon in ticks; 0=auto fee-aware",
    )
    beh.set_defaults(func=cmd_behavior)

    args = p.parse_args()
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
