"""Full-tick behavior analysis: mathematics → statistics → science → reasoning → plans.

Understands every major tick-data family present in Gold Petal exports:

  price / OHLC     ltp, open, high, low, close, range_pos
  trade flow       volume, ltq, vwap (average_traded_price)
  book L1–L5       buy1-5 / sell1-5 price+qty, spread, microprice
  aggregate book   total_buy_quantity, total_sell_quantity, imb_total
  open interest    oi_chg, oi_chg_5

Outputs a BehaviorReport used by discover_strategies to *generate* strategy
recipes (not only score fixed templates).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ml_features import FEATURE_COLUMNS, RAW_COLUMNS, add_labels, build_features, load_ticks_csv
from s8_reasoner import ReasonStep, ReasoningTrace, fee_be_points


# Families of tick columns / derived features for behavior reports
DATA_FAMILIES: dict[str, list[str]] = {
    "price": ["ret_1", "ret_5", "ret_20", "range_pos", "ltp_vs_vwap", "ltp_vs_mid"],
    "spread_micro": ["spread", "spread_bps", "microprice_gap"],
    "book_l1": ["imb_l1"],
    "book_l5": ["imb_l5", "buy_depth_sum", "sell_depth_sum", "depth_ratio"],
    "book_total": ["imb_total"],
    "oi": ["oi_chg", "oi_chg_5"],
    "volume": ["vol_chg", "vol_chg_5", "ltq"],
}


@dataclass
class FamilyStat:
    family: str
    features: list[str]
    best_feature: str
    best_corr: float
    mean_abs_corr: float
    coverage: float  # fraction non-null
    predictive: bool
    note: str


@dataclass
class StrategyRecipe:
    """A generated strategy skeleton from behavior understanding."""

    name: str
    family: str
    buy_prob: float
    short_prob: float
    min_hold: int
    min_imb: float
    every_n: int
    feature_focus: list[str]
    tp_pts: float | None
    sl_pts: float | None
    rationale: str
    math_edge_ok: bool


@dataclass
class BehaviorReport:
    n_ticks: int
    mid_ltp: float
    fee_be_pts: float
    ret_vol: float
    atr_proxy_pts: float
    regime_mix: dict[str, float]
    families: list[FamilyStat]
    top_features: list[dict[str, float | str]]
    recipes: list[StrategyRecipe]
    reasoning: dict[str, Any]
    summary: str
    horizon: int = 20
    horizon_table: list[dict[str, float | int]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_ticks": self.n_ticks,
            "mid_ltp": self.mid_ltp,
            "fee_be_pts": self.fee_be_pts,
            "ret_vol": self.ret_vol,
            "atr_proxy_pts": self.atr_proxy_pts,
            "regime_mix": self.regime_mix,
            "families": [asdict(f) for f in self.families],
            "top_features": self.top_features,
            "recipes": [asdict(r) for r in self.recipes],
            "reasoning": self.reasoning,
            "summary": self.summary,
            "horizon": self.horizon,
            "horizon_table": self.horizon_table,
        }


def _safe_corr(a: pd.Series, b: pd.Series) -> float:
    x = pd.concat([a, b], axis=1).dropna()
    if len(x) < 40:
        return float("nan")
    if x.iloc[:, 0].std() < 1e-12 or x.iloc[:, 1].std() < 1e-12:
        return float("nan")
    return float(x.iloc[:, 0].corr(x.iloc[:, 1]))


def _regime_mix(df: pd.DataFrame, window: int = 60) -> dict[str, float]:
    """Cheap offline regime mix from LTP + spread_bps (mirrors RegimeDetector ideas)."""
    if "ltp" not in df.columns or len(df) < window:
        return {"UNKNOWN": 1.0}
    ltp = df["ltp"].astype(float).to_numpy()
    spread = (
        df["spread_bps"].astype(float).to_numpy()
        if "spread_bps" in df.columns
        else np.zeros(len(df))
    )
    counts = {"TREND": 0, "CHOP": 0, "QUIET": 0, "WIDE_SPREAD": 0, "UNKNOWN": 0}
    for i in range(window, len(ltp)):
        prices = ltp[i - window : i]
        rets = np.diff(prices) / np.maximum(prices[:-1], 1e-9) * 1e4
        if len(rets) < 10:
            counts["UNKNOWN"] += 1
            continue
        vol = float(np.std(rets))
        direction = float(np.sum(rets))
        flips = int(np.sum(rets[1:] * rets[:-1] < 0))
        window_sp = spread[i - window : i]
        finite = window_sp[np.isfinite(window_sp)]
        avg_spread = float(np.mean(finite)) if len(finite) else 0.0
        if avg_spread >= 8.0:
            counts["WIDE_SPREAD"] += 1
        elif abs(direction) >= 15.0 and flips < len(rets) * 0.35:
            counts["TREND"] += 1
        elif vol < 1.2 and abs(direction) < 8.0:
            counts["QUIET"] += 1
        elif flips >= len(rets) * 0.45 and abs(direction) < vol * 3:
            counts["CHOP"] += 1
        else:
            counts["TREND"] += 1
    total = sum(counts.values()) or 1
    return {k: round(v / total, 3) for k, v in counts.items()}


def analyze_families(feat: pd.DataFrame, y_ret: pd.Series) -> list[FamilyStat]:
    out: list[FamilyStat] = []
    for fam, cols in DATA_FAMILIES.items():
        present = [c for c in cols if c in feat.columns]
        if not present:
            out.append(
                FamilyStat(
                    family=fam,
                    features=[],
                    best_feature="",
                    best_corr=0.0,
                    mean_abs_corr=0.0,
                    coverage=0.0,
                    predictive=False,
                    note="missing columns",
                )
            )
            continue
        corrs: dict[str, float] = {}
        coverages: list[float] = []
        for c in present:
            corrs[c] = _safe_corr(feat[c], y_ret)
            coverages.append(float(feat[c].notna().mean()))
        finite = {k: v for k, v in corrs.items() if np.isfinite(v)}
        if not finite:
            best_f, best_c, mean_abs = present[0], 0.0, 0.0
            predictive = False
            note = "no finite corr"
        else:
            best_f = max(finite, key=lambda k: abs(finite[k]))
            best_c = finite[best_f]
            mean_abs = float(np.mean([abs(v) for v in finite.values()]))
            predictive = abs(best_c) >= 0.03
            note = f"|corr| max={abs(best_c):.3f} mean={mean_abs:.3f}"
        out.append(
            FamilyStat(
                family=fam,
                features=present,
                best_feature=best_f,
                best_corr=float(best_c),
                mean_abs_corr=float(mean_abs),
                coverage=float(np.mean(coverages)) if coverages else 0.0,
                predictive=predictive,
                note=note,
            )
        )
    out.sort(key=lambda f: abs(f.best_corr), reverse=True)
    return out


def top_feature_corrs(feat: pd.DataFrame, y_ret: pd.Series, k: int = 12) -> list[dict[str, float | str]]:
    rows: list[dict[str, float | str]] = []
    for c in FEATURE_COLUMNS:
        if c not in feat.columns:
            continue
        r = _safe_corr(feat[c], y_ret)
        if not np.isfinite(r):
            continue
        rows.append({"feature": c, "corr_fwd_ret": round(float(r), 4)})
    rows.sort(key=lambda d: abs(float(d["corr_fwd_ret"])), reverse=True)
    return rows[:k]


def reason_over_behavior(
    *,
    mid_ltp: float,
    fee_be: float,
    atr_pts: float,
    families: list[FamilyStat],
    regime_mix: dict[str, float],
) -> ReasoningTrace:
    steps: list[ReasonStep] = []
    # Mathematics
    steps.append(
        ReasonStep(
            "mathematics",
            "fee_be",
            fee_be > 0,
            f"RT fee BE≈{fee_be:.1f}pt @ LTP≈{mid_ltp:.0f}",
            fee_be,
        )
    )
    edge_ok = atr_pts >= fee_be * 0.8
    steps.append(
        ReasonStep(
            "mathematics",
            "move_vs_fee",
            edge_ok,
            f"ATR-proxy≈{atr_pts:.1f}pt vs BE={fee_be:.1f}",
            atr_pts,
        )
    )
    # Statistics / science — which data families matter
    predictive = [f for f in families if f.predictive]
    steps.append(
        ReasonStep(
            "science",
            "predictive_families",
            len(predictive) >= 1,
            "predictive: " + (", ".join(f.family for f in predictive) or "none"),
            float(len(predictive)),
        )
    )
    if families:
        top = families[0]
        steps.append(
            ReasonStep(
                "statistics",
                "top_family",
                abs(top.best_corr) >= 0.03,
                f"{top.family}.{top.best_feature} corr={top.best_corr:+.3f}",
                top.best_corr,
            )
        )
    # Logic — regime suitability
    trend = float(regime_mix.get("TREND", 0))
    chop = float(regime_mix.get("CHOP", 0))
    quiet = float(regime_mix.get("QUIET", 0))
    wide = float(regime_mix.get("WIDE_SPREAD", 0))
    steps.append(
        ReasonStep(
            "logic",
            "regime_tradeable",
            wide < 0.35,
            f"mix trend={trend:.0%} chop={chop:.0%} quiet={quiet:.0%} wide={wide:.0%}",
            wide,
        )
    )
    # Planning
    if wide >= 0.5:
        action = "SKIP"
    elif len(predictive) == 0:
        action = "WAIT"
    elif trend >= 0.35 and any(f.family.startswith("book") or f.family == "price" for f in predictive):
        action = "ENTER_LONG"  # directional bias placeholder; recipes decide side
    else:
        action = "WAIT"
    ok_n = sum(1 for s in steps if s.ok)
    score = ok_n / max(len(steps), 1)
    steps.append(
        ReasonStep("planning", "plan", True, f"action={action} score={score:.2f}", score)
    )
    return ReasoningTrace(
        action=action,  # type: ignore[arg-type]
        score=score,
        steps=steps,
        summary=(
            f"tick-behavior plan={action} score={score:.2f} "
            f"families={[f.family for f in predictive]}"
        ),
    )


def generate_recipes(
    *,
    families: list[FamilyStat],
    fee_be: float,
    atr_pts: float,
    regime_mix: dict[str, float],
    top_features: list[dict[str, float | str]],
) -> list[StrategyRecipe]:
    """Generate strategy recipes from which tick behaviors actually predict."""
    recipes: list[StrategyRecipe] = []
    focus = [str(r["feature"]) for r in top_features[:8]]
    trend = float(regime_mix.get("TREND", 0))
    chop = float(regime_mix.get("CHOP", 0))
    predictive = [f for f in families if f.predictive]
    fam_names = {f.family for f in predictive}
    edge_ok = atr_pts >= fee_be * 0.8

    # Always include a mid ML baseline using top features
    recipes.append(
        StrategyRecipe(
            name="ml_topfeat",
            family="ensemble",
            buy_prob=0.58,
            short_prob=0.42,
            min_hold=30,
            min_imb=0.0,
            every_n=5,
            feature_focus=focus,
            tp_pts=None,
            sl_pts=None,
            rationale="Multi-model on top predictive features across all tick families",
            math_edge_ok=edge_ok,
        )
    )

    if "book_l1" in fam_names or "book_l5" in fam_names or "book_total" in fam_names:
        recipes.append(
            StrategyRecipe(
                name="book_imb_ml",
                family="book",
                buy_prob=0.57,
                short_prob=0.43,
                min_hold=35,
                min_imb=0.12 if trend >= 0.25 else 0.18,
                every_n=5,
                feature_focus=[
                    c
                    for c in focus
                    if "imb" in c or "depth" in c or "micro" in c or "spread" in c
                ]
                or focus[:4],
                tp_pts=max(fee_be * 1.2, atr_pts * 0.8),
                sl_pts=max(fee_be * 0.6, atr_pts * 0.45),
                rationale="Book imbalance + depth predict forward ret → gate ML with min_imb",
                math_edge_ok=edge_ok,
            )
        )

    if "price" in fam_names:
        recipes.append(
            StrategyRecipe(
                name="momentum_ml",
                family="price",
                buy_prob=0.60 if trend >= 0.3 else 0.62,
                short_prob=0.40 if trend >= 0.3 else 0.38,
                min_hold=40,
                min_imb=0.0,
                every_n=5,
                feature_focus=[c for c in focus if c.startswith("ret_") or "vwap" in c or "range" in c]
                or focus[:4],
                tp_pts=max(fee_be * 1.4, atr_pts),
                sl_pts=max(fee_be * 0.7, atr_pts * 0.5),
                rationale="Price momentum / VWAP / range_pos predictive → stricter prob, longer hold",
                math_edge_ok=edge_ok,
            )
        )

    if "oi" in fam_names or "volume" in fam_names:
        recipes.append(
            StrategyRecipe(
                name="flow_oi_ml",
                family="flow",
                buy_prob=0.58,
                short_prob=0.42,
                min_hold=45,
                min_imb=0.08,
                every_n=7,
                feature_focus=[
                    c
                    for c in focus
                    if c.startswith("oi_") or c.startswith("vol_") or c == "ltq"
                ]
                or focus[:4],
                tp_pts=max(fee_be * 1.3, atr_pts * 0.9),
                sl_pts=max(fee_be * 0.65, atr_pts * 0.5),
                rationale="OI/volume flow predictive → slower every_n, mild imb confirm",
                math_edge_ok=edge_ok,
            )
        )

    if chop >= 0.4:
        recipes.append(
            StrategyRecipe(
                name="chop_meanrev",
                family="spread_micro",
                buy_prob=0.63,
                short_prob=0.37,
                min_hold=25,
                min_imb=0.20,
                every_n=3,
                feature_focus=[c for c in focus if "micro" in c or "spread" in c or "imb" in c]
                or focus[:4],
                tp_pts=max(fee_be, atr_pts * 0.6),
                sl_pts=max(fee_be * 0.5, atr_pts * 0.35),
                rationale="CHOP-heavy regime → mean-revert style: high imb gate, faster cadence",
                math_edge_ok=edge_ok,
            )
        )

    # Deduplicate by name
    seen: set[str] = set()
    uniq: list[StrategyRecipe] = []
    for r in recipes:
        if r.name in seen:
            continue
        seen.add(r.name)
        uniq.append(r)
    return uniq


def median_horizon_move(ltp: pd.Series, horizon: int) -> float:
    move = (ltp.shift(-horizon) - ltp).abs().median()
    v = float(move) if move == move else 0.0
    return v if np.isfinite(v) else 0.0


def pick_fee_aware_horizon(
    ltp: pd.Series,
    fee_be: float,
    *,
    candidates: tuple[int, ...] = (20, 60, 120, 300, 600, 1200, 2400, 4800),
) -> tuple[int, float, list[dict[str, float | int]]]:
    """Choose the shortest horizon whose median |move| can cover ~80% of fee BE."""
    table: list[dict[str, float | int]] = []
    best_h = candidates[0]
    best_move = 0.0
    chosen: tuple[int, float] | None = None
    for h in candidates:
        if h >= len(ltp):
            continue
        mv = median_horizon_move(ltp, h)
        table.append({"horizon": h, "median_abs_move": round(mv, 2)})
        if mv > best_move:
            best_move = mv
            best_h = h
        if chosen is None and mv >= fee_be * 0.8:
            chosen = (h, mv)
    if chosen is not None:
        return chosen[0], chosen[1], table
    return best_h, best_move, table


def analyze_ticks(
    csv_path: Path,
    *,
    horizon: int = 0,
    threshold_bps: float = 2.0,
    lots: float = 1.0,
) -> BehaviorReport:
    """Analyze tick families. horizon=0 → auto-pick fee-aware horizon."""
    raw = load_ticks_csv(csv_path)
    for col in RAW_COLUMNS:
        if col not in raw.columns:
            raw[col] = np.nan
    feat = build_features(raw)
    mid_ltp = float(feat["ltp"].median()) if len(feat) else 0.0
    fee_be = float(fee_be_points(mid_ltp, lots=lots))

    horizon_table: list[dict[str, float | int]] = []
    if horizon <= 0:
        horizon, atr_pts, horizon_table = pick_fee_aware_horizon(feat["ltp"], fee_be)
    else:
        atr_pts = median_horizon_move(feat["ltp"], horizon)
        horizon_table = [{"horizon": horizon, "median_abs_move": round(atr_pts, 2)}]

    labeled = add_labels(feat, horizon=horizon, threshold_bps=threshold_bps)
    usable = labeled.dropna(subset=["y_ret"]).copy()
    if usable.empty:
        raise SystemExit("no labeled ticks for behavior analysis")

    ret1 = usable["ltp"].pct_change().dropna()
    ret_vol = float(ret1.std() * 1e4) if len(ret1) else 0.0
    # refresh move on labeled rows
    atr_pts = median_horizon_move(usable["ltp"], horizon) or atr_pts

    families = analyze_families(usable, usable["y_ret"])
    tops = top_feature_corrs(usable, usable["y_ret"])
    regimes = _regime_mix(usable)
    recipes = generate_recipes(
        families=families,
        fee_be=fee_be,
        atr_pts=max(atr_pts, 1.0),
        regime_mix=regimes,
        top_features=tops,
    )
    trace = reason_over_behavior(
        mid_ltp=mid_ltp,
        fee_be=fee_be,
        atr_pts=max(atr_pts, 1.0),
        families=families,
        regime_mix=regimes,
    )
    predictive = [f.family for f in families if f.predictive]
    summary = (
        f"n={len(usable)} LTP≈{mid_ltp:.0f} BE≈{fee_be:.0f}pt "
        f"horizon={horizon}ticks move≈{atr_pts:.1f}pt | "
        f"predictive_families={predictive or ['none']} | "
        f"regimes={regimes} | recipes={[r.name for r in recipes]} | {trace.line()}"
    )
    return BehaviorReport(
        n_ticks=int(len(usable)),
        mid_ltp=mid_ltp,
        fee_be_pts=fee_be,
        ret_vol=ret_vol,
        atr_proxy_pts=float(atr_pts),
        regime_mix=regimes,
        families=families,
        top_features=tops,
        recipes=recipes,
        reasoning=trace.to_dict(),
        summary=summary,
        horizon=int(horizon),
        horizon_table=horizon_table,
    )


def write_behavior_report(report: BehaviorReport, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
    return path
