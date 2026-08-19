"""AI Strategy Research Factory — discover / test / propose. Never deploy.

Closed loop: ticks → relationship atoms → discovery → composer →
deterministic backtest (separate) → walk-forward → robustness →
REJECT or pending proposal. The operator approves on the Lab tab.
Learning ≠ automatic paper or live. The LLM (if any) only explains;
numbers come from the quantitative engines.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, replace
from datetime import datetime
from itertools import combinations
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from amise_timeframes import LAB_TIMEFRAMES, LabTF, parse_tf
from backtest_hhhl_candles import make_charge_cfg
from backtest_ohlcv_lab import MIN_TRADES, _baseline_s16, _days_from_hours, _fill_trade_stats
from charges import ChargeConfig
from flow_lab import (
    EXIT_FLIP,
    RECIPES,
    FlowBar,
    FlowParams,
    Recipe,
    build_flow_features,
    lab_bars_for_tf,
    run_factory_book,
    selected_recipes,
    simulate_flow,
    tape_flags,
    walk_forward_flow,
)
from ohlcv_lab import LabMetrics, after_charges_inr, score_result
from proposals import PaperResult, StrategyProposal, add_proposal
from research_features import (
    ATOM_BY_NAME,
    MIRROR,
    atoms_for_tape,
    compile_genome,
    default_no_trade,
    delayed_decide,
    eval_atom,
)
from s18_ohlc_vol_htf import simulate_s18
from strategy_genome import (
    LAB_NAME,
    RESEARCH_KIND,
    RESEARCH_STRATEGY,
    StrategyGenome,
    env_patch_is_safe,
    genome_from_dict,
    mutate_genome,
    params_from_genome,
    research_env_patch,
)

IST = ZoneInfo("Asia/Kolkata")
ROOT = Path(__file__).resolve().parent
RESEARCH_DIR = ROOT / "data" / "research"
LIBRARY_PATH = RESEARCH_DIR / "library.json"
LAST_RUN_PATH = RESEARCH_DIR / "last_run.json"
MIN_WF_WINS = 2
MIN_DISCOVERY_N = 12
MAX_AND = 4
# Stronger than "barely beat S16/S18". Skipping these does not make better books.
BEAT_MULT = 1.10
MIN_PF = 1.25
MIN_SIDE = 3
HOLD_FRAC = 0.20
MIN_HOLD_BARS = 20
MIN_TRAIN_BARS = 40
# Desk / auto-lab: cheap-screen losers, then run the strong lab on the shortlist.
# 3 folds, recipes, sklearn ranks, 2× and 3× costs, holdout. Never skip gates to "find more".
FAST_LAB_KWARGS: dict[str, Any] = {
    "n_folds": 3,
    "include_recipes": True,
    "max_compose": 24,
    "robustness": True,
    "sklearn": True,
    "strong": True,
    "screen_first": True,
    "max_deep": 4,
}
FULL_LAB_MAX_DEEP = 8


@dataclass
class AtomScore:
    name: str
    n: int
    mean_fwd_atr: float
    tstat: float
    side: str


@dataclass
class Robustness:
    cost_1x: float
    cost_2x: float
    cost_3x: float
    delay_1bar: float
    jitter_lo: float
    jitter_hi: float
    cost_2x_pass: bool
    cost_3x_pass: bool
    delay_pass: bool
    jitter_pass: bool
    regimes: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "cost_1x": self.cost_1x,
            "cost_2x": self.cost_2x,
            "cost_3x": self.cost_3x,
            "delay_1bar": self.delay_1bar,
            "jitter_lo": self.jitter_lo,
            "jitter_hi": self.jitter_hi,
            "cost_2x_pass": self.cost_2x_pass,
            "cost_3x_pass": self.cost_3x_pass,
            "delay_pass": self.delay_pass,
            "jitter_pass": self.jitter_pass,
            "regimes": dict(self.regimes),
        }


def _now_iso() -> str:
    return datetime.now(IST).isoformat(timespec="seconds")


def metrics_public(m: LabMetrics | None) -> dict[str, Any] | None:
    if m is None:
        return None
    return {
        "name": m.name,
        "family": m.family,
        "exit_mode": m.exit_mode,
        "n_trades": m.n_trades,
        "n_long": m.n_long,
        "n_short": m.n_short,
        "after_charges": round(float(m.after_charges), 2),
        "expectancy": round(float(m.expectancy), 2),
        "profit_factor": round(float(m.profit_factor), 3),
        "avg_win": round(float(m.avg_win), 2),
        "avg_loss": round(float(m.avg_loss), 2),
        "max_dd": round(float(m.max_dd), 2),
        "win_rate": round(float(m.win_rate), 4),
        "wf_wins": m.wf_wins,
        "wf_folds": m.wf_folds,
        "stability": m.stability,
        "n_bars": m.n_bars,
    }


def scaled_charge_cfg(*, lots: float, fees: bool, mult: float) -> ChargeConfig:
    cfg = make_charge_cfg(fees=fees, lots=lots)
    if (not fees) or abs(float(mult) - 1.0) < 1e-12:
        return cfg
    m = float(mult)
    return ChargeConfig(
        brokerage_per_order=cfg.brokerage_per_order * m,
        brokerage_promo=cfg.brokerage_promo,
        mcx_txn_rate=cfg.mcx_txn_rate * m,
        ctt_sell_rate=cfg.ctt_sell_rate * m,
        sebi_rate=cfg.sebi_rate * m,
        stamp_buy_rate=cfg.stamp_buy_rate * m,
        gst_rate=cfg.gst_rate,
        tax_rate=cfg.tax_rate,
        lot_size=cfg.lot_size,
        turnover_mult=cfg.turnover_mult,
        ignore_fees=cfg.ignore_fees,
    )


def champion_s16(bars: list[FlowBar], *, lots: float, fees: bool) -> LabMetrics:
    return _fill_trade_stats(_baseline_s16(bars, tf="1h:S16", lots=lots, fees=fees))


def champion_s18(bars: list[FlowBar], *, lots: float, fees: bool) -> LabMetrics | None:
    if not any(float(b.volume) > 0.0 for b in bars):
        return None
    days = _days_from_hours(bars)
    if not days:
        return None
    res = simulate_s18(bars, days, tf="1h:S18", lots=lots, fees=fees, session_filter=True)
    return _fill_trade_stats(
        LabMetrics(
            name="S18",
            family="paper",
            exit_mode="flip_eod",
            n_trades=res.n_trades,
            n_long=res.n_long,
            n_short=res.n_short,
            after_charges=after_charges_inr(res),
            expectancy=0.0,
            profit_factor=0.0,
            avg_win=0.0,
            avg_loss=0.0,
            max_dd=0.0,
            win_rate=0.0,
            result=res,
        )
    )


def champion_s13(bars: list[FlowBar], *, lots: float, fees: bool) -> LabMetrics:
    """S13 daily swing replayed on this tape. Does not change the paper S13 book."""
    from backtest_hhhl_candles import Candle
    from s16_hhhl_wick import s16_bar_decision, simulate_s16

    candles = [Candle(b.time, b.open, b.high, b.low, b.close) for b in bars]
    res = simulate_s16(
        candles,
        tf="1d:S13",
        lots=lots,
        fees=fees,
        session_filter=False,
        min_wick_gap=0.0,
        decide=lambda a, b: s16_bar_decision(a, b, min_wick_gap=0.0),
    )
    return _fill_trade_stats(
        LabMetrics(
            name="S13",
            family="paper",
            exit_mode="flip",
            n_trades=res.n_trades,
            n_long=res.n_long,
            n_short=res.n_short,
            after_charges=after_charges_inr(res),
            expectancy=0.0,
            profit_factor=0.0,
            avg_win=0.0,
            avg_loss=0.0,
            max_dd=0.0,
            win_rate=0.0,
            result=res,
        )
    )


def _tstat(xs: list[float]) -> tuple[float, float, int]:
    n = len(xs)
    if n < 3:
        return 0.0, 0.0, n
    mean = sum(xs) / float(n)
    var = sum((x - mean) ** 2 for x in xs) / float(max(n - 1, 1))
    se = math.sqrt(var) / math.sqrt(n) if var > 1e-18 else 0.0
    t = mean / se if se > 1e-12 else 0.0
    return mean, t, n


def discover_relationships(
    bars: list[FlowBar],
    *,
    params: FlowParams | None = None,
    flags: dict[str, bool] | None = None,
) -> list[AtomScore]:
    """Univariate screen: atom true vs next-bar ATR-normalized return.

    Goal is repeatable state → future return, not "predict tomorrow's price."
    """
    p = params or FlowParams()
    feats = build_flow_features(bars, p)
    use = atoms_for_tape(flags if flags is not None else tape_flags(bars))
    scores: list[AtomScore] = []
    for atom in use:
        if atom.kind == "no_trade":
            continue
        xs: list[float] = []
        for i in range(len(bars) - 1):
            f = feats[i]
            nxt = feats[i + 1]
            if f is None or nxt is None:
                continue
            if not eval_atom(atom.name, bars, feats, i, p):
                continue
            atr = float(f.atr) if f.atr > 1e-12 else 1.0
            xs.append((float(bars[i + 1].close) - float(bars[i].close)) / atr)
        mean, t, n = _tstat(xs)
        scores.append(AtomScore(atom.name, n, mean, t, atom.kind))
    scores.sort(key=lambda s: abs(s.tstat), reverse=True)
    return scores


def sklearn_importances(
    bars: list[FlowBar],
    *,
    params: FlowParams | None = None,
) -> list[dict[str, Any]]:
    """Optional research-only importances. Never a live BUY model."""
    try:
        from sklearn.ensemble import RandomForestClassifier  # type: ignore
    except Exception:
        return []
    from flow_lab import ML_FEATURE_NAMES, feat_vector

    p = params or FlowParams()
    feats = build_flow_features(bars, p)
    x_rows: list[list[float]] = []
    y: list[int] = []
    for i in range(len(bars) - 1):
        f = feats[i]
        if f is None:
            continue
        fwd = float(bars[i + 1].close) - float(bars[i].close)
        if abs(fwd) <= 1e-12:
            continue
        vec = feat_vector(f)
        x_rows.append([float(vec[k]) for k in ML_FEATURE_NAMES])
        y.append(1 if fwd > 0 else 0)
    if len(y) < 40 or len(set(y)) < 2:
        return []
    clf = RandomForestClassifier(n_estimators=80, max_depth=4, random_state=7)
    clf.fit(x_rows, y)
    ranked = sorted(
        zip(ML_FEATURE_NAMES, (float(x) for x in clf.feature_importances_)),
        key=lambda kv: kv[1],
        reverse=True,
    )
    return [{"name": n, "importance": round(v, 4)} for n, v in ranked[:12]]


def recipe_genome(rec: Recipe, *, flags: dict[str, bool] | None = None) -> StrategyGenome:
    no_t = list(default_no_trade(flags))
    if rec.needs_book and (flags or {}).get("book"):
        if "no_depth" not in no_t:
            no_t.append("no_depth")
    return StrategyGenome(
        name=f"recipe:{rec.name}",
        recipe=rec.name,
        source="recipe",
        notes=rec.idea,
        no_trade=tuple(no_t),
        params={},
    ).normalized()


def stamp_genome_tf(genome: StrategyGenome, tf: str) -> StrategyGenome:
    """Pin a genome to a factory rung. 1h keeps its composed name."""
    spec = parse_tf(tf)
    label = spec.label
    src = genome.normalized()
    name = src.name
    if label != "1h":
        base = name.split("@", 1)[0]
        name = f"{base}@{label}"[:80]
    return replace(src, timeframe=label, name=name, genome_id="").normalized()


def cheap_min_trades(min_trades: int) -> int:
    """Cheap-screen floor. 1h stays at 8; daily / 3h rungs are thinner."""
    mt = int(min_trades)
    if mt >= 20:
        return 8
    return max(4, mt // 2)


def compose_from_discovery(
    scores: list[AtomScore],
    *,
    flags: dict[str, bool] | None = None,
    max_and: int = MAX_AND,
) -> list[StrategyGenome]:
    no_t = default_no_trade(flags)
    usable = [
        s
        for s in scores
        if s.n >= MIN_DISCOVERY_N and abs(s.tstat) >= 1.0 and ATOM_BY_NAME.get(s.name)
    ]
    long_atoms = [
        s.name
        for s in usable
        if s.mean_fwd_atr > 0 and ATOM_BY_NAME[s.name].kind in {"long", "both"}
    ]
    short_from_neg = [
        s.name
        for s in usable
        if s.mean_fwd_atr < 0 and ATOM_BY_NAME[s.name].kind in {"short", "both"}
    ]
    out: list[StrategyGenome] = []

    def _add(long_e: tuple[str, ...], short_e: tuple[str, ...], source: str, name: str) -> None:
        if not long_e and not short_e:
            return
        out.append(
            StrategyGenome(
                name=name[:80],
                entry_long=long_e,
                entry_short=short_e,
                no_trade=no_t,
                source=source,
            ).normalized()
        )

    for name in long_atoms[:8]:
        _add((name,), (MIRROR[name],) if name in MIRROR else (), "discovery", f"atom:{name}")
    for name in short_from_neg[:4]:
        if name in long_atoms[:8]:
            continue
        _add((MIRROR[name],) if name in MIRROR else (), (name,), "discovery", f"atom:{name}")

    acc: list[str] = []
    for name in long_atoms[:8]:
        if name in acc:
            continue
        acc.append(name)
        if len(acc) < 2:
            continue
        shorts = tuple(MIRROR[a] for a in acc if a in MIRROR)
        _add(tuple(acc), shorts, "compose", "compose:" + "+".join(acc))
        if len(acc) >= max_and:
            break

    top = long_atoms[:5]
    for a, b in combinations(top, 2):
        shorts = tuple(x for x in (MIRROR.get(a), MIRROR.get(b)) if x)
        _add((a, b), shorts, "compose", f"pair:{a}+{b}")

    # Classic user example: HH + HL + bull + volume expansion
    classic_long = ("hh", "hl", "bull", "vol_up")
    if all(ATOM_BY_NAME.get(n) for n in classic_long):
        classic_short = tuple(MIRROR[n] for n in classic_long if n in MIRROR)
        _add(classic_long, classic_short, "compose", "compose:hh+hl+bull+vol_up")

    seen: set[str] = set()
    unique: list[StrategyGenome] = []
    for g in out:
        if g.genome_id in seen:
            continue
        seen.add(g.genome_id)
        unique.append(g)
    return unique


def run_genome_book(
    bars: list[FlowBar],
    genome: StrategyGenome,
    *,
    lots: float = 100.0,
    fees: bool = True,
    n_folds: int = 3,
    params: FlowParams | None = None,
    charge_cfg: ChargeConfig | None = None,
    decide_fn: Any | None = None,
    tf: str | None = None,
    skip_wf: bool = False,
    flatten_eod: bool = True,
) -> LabMetrics:
    g = genome.normalized()
    p = params or params_from_genome(g)
    decide = decide_fn or compile_genome(g)
    name = g.genome_id or g.name
    sim_kw: dict[str, Any] = dict(
        lots=lots,
        fees=fees,
        flatten_eod=bool(flatten_eod),
        params=p,
        decide_fn=decide,
        charge_cfg=charge_cfg,
    )
    res = simulate_flow(
        bars,
        name,
        exit_mode=g.exit or EXIT_FLIP,
        tf=tf or f"lab:{name}",
        **sim_kw,
    )
    if skip_wf or int(n_folds) <= 0:
        wf_rows, wf_wins, wf_folds = [], 0, 0
    else:
        wf_kw = {k: v for k, v in sim_kw.items() if k not in {"tf", "entry_days"}}
        wf_rows, wf_wins, wf_folds = walk_forward_flow(
            bars,
            name,
            exit_mode=g.exit or EXIT_FLIP,
            n_folds=n_folds,
            **wf_kw,
        )
    metrics = score_result(
        res,
        name=g.name,
        family=g.source,
        exit_mode=g.exit or EXIT_FLIP,
        wf_wins=wf_wins,
        wf_folds=wf_folds,
    )
    metrics.wf_rows = wf_rows
    return metrics


def _regime_label(f: Any) -> list[str]:
    labels: list[str] = []
    if f is None:
        return labels
    if f.atr >= 1.15 * max(f.avg_range, 1e-12):
        labels.append("high_vol")
    else:
        labels.append("low_vol")
    if abs(float(f.mom3)) >= 0.004:
        labels.append("trend")
    else:
        labels.append("range")
    return labels


def _regime_tests(bars: list[FlowBar], metrics: LabMetrics, params: FlowParams) -> dict[str, str]:
    result = metrics.result
    trades = list(getattr(result, "trades", None) or [])
    if not trades:
        return {k: "N/A" for k in ("trend", "range", "high_vol", "low_vol")}
    feats = build_flow_features(bars, params)
    by_t = {b.time: i for i, b in enumerate(bars)}
    buckets: dict[str, list[float]] = {
        "trend": [],
        "range": [],
        "high_vol": [],
        "low_vol": [],
    }
    for t in trades:
        i = by_t.get(getattr(t, "entry_time", ""))
        if i is None or i >= len(feats):
            continue
        ac = float(getattr(t, "gross_pnl_inr", 0.0)) - float(getattr(t, "fees_inr", 0.0))
        for lab in _regime_label(feats[i]):
            buckets[lab].append(ac)
    out: dict[str, str] = {}
    for name, xs in buckets.items():
        if len(xs) < 3:
            out[name] = "N/A"
        elif sum(xs) >= 0:
            out[name] = "PASS"
        else:
            out[name] = "FAIL"
    return out


def run_robustness(
    bars: list[FlowBar],
    genome: StrategyGenome,
    base: LabMetrics,
    *,
    lots: float,
    fees: bool,
    params: FlowParams | None = None,
    flatten_eod: bool = True,
) -> Robustness:
    g = genome.normalized()
    p = params or params_from_genome(g)
    decide = compile_genome(g)
    ac1 = float(base.after_charges)

    def _ac(cfg: ChargeConfig | None = None, decide_fn: Any | None = None, gp: StrategyGenome | None = None) -> float:
        use = gp or g
        m = run_genome_book(
            bars,
            use,
            lots=lots,
            fees=fees,
            n_folds=0,
            params=p if gp is None else params_from_genome(gp, p),
            charge_cfg=cfg,
            decide_fn=decide_fn,
            tf="rob",
            skip_wf=True,
            flatten_eod=flatten_eod,
        )
        return float(m.after_charges)

    ac2 = _ac(scaled_charge_cfg(lots=lots, fees=fees, mult=2.0))
    ac3 = _ac(scaled_charge_cfg(lots=lots, fees=fees, mult=3.0))
    ac_delay = _ac(decide_fn=delayed_decide(decide, bars_delay=1))
    lo = mutate_genome(g, scale=-0.10)
    hi = mutate_genome(g, scale=0.10)
    ac_lo = _ac(gp=lo)
    ac_hi = _ac(gp=hi)
    delay_pass = ac_delay > 0.0 or (ac1 > 0.0 and ac_delay >= 0.40 * ac1)
    jitter_pass = (ac_lo > 0.0 or ac_hi > 0.0) and min(ac_lo, ac_hi) >= min(0.0, ac1) - abs(ac1) * 0.8
    return Robustness(
        cost_1x=ac1,
        cost_2x=ac2,
        cost_3x=ac3,
        delay_1bar=ac_delay,
        jitter_lo=ac_lo,
        jitter_hi=ac_hi,
        cost_2x_pass=ac2 > 0.0,
        cost_3x_pass=ac3 > 0.0,
        delay_pass=bool(delay_pass),
        jitter_pass=bool(jitter_pass),
        regimes=_regime_tests(bars, base, p),
    )


def holdout_split(
    bars: list[FlowBar],
    *,
    frac: float = HOLD_FRAC,
    min_hold: int = MIN_HOLD_BARS,
    min_train: int = MIN_TRAIN_BARS,
) -> tuple[list[FlowBar], list[FlowBar]]:
    """Last ``frac`` of the tape is unseen by discovery. Empty holdout = tape too short."""
    n = len(bars)
    if n < min_train + min_hold:
        return list(bars), []
    cut = int(n * (1.0 - float(frac)))
    cut = max(min_train, min(cut, n - min_hold))
    return list(bars[:cut]), list(bars[cut:])


def _champ_ac(s16: LabMetrics, s18: LabMetrics | None, s13: LabMetrics | None = None) -> float:
    ac = float(s16.after_charges)
    if s18 is not None:
        ac = max(ac, float(s18.after_charges))
    if s13 is not None:
        ac = max(ac, float(s13.after_charges))
    return ac


def gate_failures(
    m: LabMetrics,
    *,
    s16: LabMetrics,
    s18: LabMetrics | None,
    rob: Robustness | None,
    min_trades: int = MIN_TRADES,
    strong: bool = True,
    holdout: LabMetrics | None = None,
    s16_hold: LabMetrics | None = None,
    s18_hold: LabMetrics | None = None,
    s13: LabMetrics | None = None,
    s13_hold: LabMetrics | None = None,
    require_both_sides: bool = True,
) -> list[str]:
    fails: list[str] = []
    if m.n_trades < min_trades:
        fails.append(f"thin sample ({m.n_trades} < {min_trades})")
    if m.after_charges <= 0:
        fails.append("after charges ≤ 0")
    if m.after_charges <= s16.after_charges:
        fails.append(f"does not beat S16 (AC₹={s16.after_charges:.1f})")
    if s18 is not None and m.after_charges <= s18.after_charges:
        fails.append(f"does not beat S18 (AC₹={s18.after_charges:.1f})")
    if s13 is not None and m.after_charges <= s13.after_charges:
        fails.append(f"does not beat S13 (AC₹={s13.after_charges:.1f})")
    if m.wf_folds >= MIN_WF_WINS and m.wf_wins < MIN_WF_WINS:
        fails.append(f"walk-forward {m.stability} < {MIN_WF_WINS}/{m.wf_folds}")
    if rob is not None:
        if not rob.cost_2x_pass:
            fails.append("2× costs failed")
        if strong and not rob.cost_3x_pass:
            fails.append("3× costs failed")
        if not rob.delay_pass:
            fails.append("1-bar delay fragile")
        if not rob.jitter_pass:
            fails.append("threshold jitter fragile")
        regime_fails = sum(1 for v in rob.regimes.values() if v == "FAIL")
        if regime_fails >= 2:
            fails.append("failed ≥2 market regimes")
    if strong:
        champ = _champ_ac(s16, s18, s13)
        if champ > 0 and m.after_charges < champ * BEAT_MULT:
            fails.append(
                f"need {BEAT_MULT:.0%} margin vs champions (AC₹={champ:.1f}, have {m.after_charges:.1f})"
            )
        if m.profit_factor < MIN_PF:
            fails.append(f"PF {m.profit_factor:.2f} < {MIN_PF}")
        if m.after_charges > 0 and m.max_dd > m.after_charges:
            fails.append(f"drawdown ₹{m.max_dd:.0f} > after-charges")
        if require_both_sides and m.n_trades >= MIN_SIDE * 2 and (m.n_long < MIN_SIDE or m.n_short < MIN_SIDE):
            fails.append("one-sided book (need both long and short sample)")
        if holdout is not None:
            if holdout.n_trades < 5:
                fails.append(f"holdout too thin ({holdout.n_trades} < 5)")
            elif holdout.after_charges <= 0:
                fails.append("holdout after charges ≤ 0")
            elif s16_hold is not None and holdout.after_charges <= s16_hold.after_charges:
                fails.append(f"holdout loses to S16 (AC₹={s16_hold.after_charges:.1f})")
            elif (
                s18_hold is not None
                and holdout.after_charges <= s18_hold.after_charges
            ):
                fails.append(f"holdout loses to S18 (AC₹={s18_hold.after_charges:.1f})")
            elif s13_hold is not None and holdout.after_charges <= s13_hold.after_charges:
                fails.append(f"holdout loses to S13 (AC₹={s13_hold.after_charges:.1f})")
    return fails


def supervisor_note(genome: StrategyGenome, m: LabMetrics, fails: list[str]) -> str:
    """Plain-language research supervisor. Not a BUY instruction."""
    cond = " AND ".join(genome.entry_long) if genome.entry_long else genome.recipe or genome.name
    if fails:
        return (
            f"Rejected {genome.name}: {cond}. "
            f"n={m.n_trades} AC₹={m.after_charges:.1f} wf={m.stability}. "
            + "; ".join(fails)
            + ". Not proposed. Learning ≠ deploy."
        )
    return (
        f"I found that {cond} has unusual out-of-sample performance on this tape "
        f"(n={m.n_trades} AC₹={m.after_charges:.1f} PF={m.profit_factor:.2f} wf={m.stability}). "
        "The quantitative engine tested it; I am only proposing. You decide. Not a live signal."
    )


def evaluate_challenger(
    bars: list[FlowBar],
    genome: StrategyGenome,
    *,
    s16: LabMetrics,
    s18: LabMetrics | None,
    lots: float,
    fees: bool,
    n_folds: int,
    params: FlowParams | None = None,
    robustness: bool = True,
    strong: bool = True,
    holdout_bars: list[FlowBar] | None = None,
    flatten_eod: bool = True,
    min_trades: int = MIN_TRADES,
    s13: LabMetrics | None = None,
    require_both_sides: bool = True,
) -> dict[str, Any]:
    g = genome.normalized()
    p = params or params_from_genome(g)
    if g.recipe:
        m = run_factory_book(
            bars,
            g.recipe,
            exit_mode=g.exit or EXIT_FLIP,
            family="recipe",
            n_folds=n_folds,
            lots=lots,
            fees=fees,
            flatten_eod=bool(flatten_eod),
            params=p,
            tf=f"lab:{g.recipe}",
            decide_fn=compile_genome(g),
        )
        m.name = g.name
        m.family = "recipe"
    else:
        m = run_genome_book(
            bars,
            g,
            lots=lots,
            fees=fees,
            n_folds=n_folds,
            params=p,
            flatten_eod=flatten_eod,
        )
    rob = None
    rob_n = min(8, max(4, int(min_trades)))
    if robustness and m.n_trades >= rob_n and m.after_charges > 0:
        rob = run_robustness(
            bars, g, m, lots=lots, fees=fees, params=p, flatten_eod=flatten_eod
        )
    hold_m = None
    s16_h = None
    s18_h = None
    s13_h = None
    hold = list(holdout_bars or [])
    if strong and len(hold) >= MIN_HOLD_BARS:
        hold_m = run_genome_book(
            hold,
            g,
            lots=lots,
            fees=fees,
            n_folds=0,
            params=p,
            tf="hold",
            skip_wf=True,
            flatten_eod=flatten_eod,
        )
        s16_h = champion_s16(hold, lots=lots, fees=fees)
        s18_h = champion_s18(hold, lots=lots, fees=fees) if s18 is not None else None
        if s13 is not None:
            s13_h = champion_s13(hold, lots=lots, fees=fees)
    fails = gate_failures(
        m,
        s16=s16,
        s18=s18,
        rob=rob,
        strong=strong,
        holdout=hold_m,
        s16_hold=s16_h,
        s18_hold=s18_h,
        s13=s13,
        s13_hold=s13_h,
        min_trades=min_trades,
        require_both_sides=require_both_sides,
    )
    pub = metrics_public(m) or {}
    if hold_m is not None:
        pub["holdout_after_charges"] = round(float(hold_m.after_charges), 2)
        pub["holdout_n"] = int(hold_m.n_trades)
    return {
        "genome": g.to_dict(),
        "metrics": pub,
        "robustness": rob.to_dict() if rob else None,
        "fails": fails,
        "proposed": not fails,
        "supervisor": supervisor_note(g, m, fails),
        "_metrics": m,
        "_genome": g,
        "_rob": rob,
    }


def write_library(payload: dict[str, Any], path: Path | None = None) -> Path:
    dest = path or LIBRARY_PATH
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return dest


def load_library(path: Path | None = None) -> dict[str, Any]:
    dest = path or LIBRARY_PATH
    if not dest.exists():
        return {}
    return json.loads(dest.read_text(encoding="utf-8"))


def proposal_from_challenger(
    row: dict[str, Any],
    *,
    week_id: str,
    target_slot: str = "",
    parent_after_charges: float | None = None,
) -> StrategyProposal:
    g = genome_from_dict(row.get("genome") or {})
    m = row.get("metrics") or {}
    slot = str(target_slot or "").strip()
    extra = {
        "metric": "after_charges_ex_tax",
        "after_charges_inr": m.get("after_charges"),
        "genome": g.to_dict(),
        "robustness": row.get("robustness"),
        "supervisor": row.get("supervisor"),
        "walk_forward": m.get("stability"),
        "lab": LAB_NAME,
        "paper_next": True,
        "live": False,
        "enable": False,
        "improve": bool(slot),
        "target_slot": slot,
        "parent_after_charges": parent_after_charges,
    }
    title = g.name
    if slot:
        n = slot.replace("_AMISE", "").replace("S", "")
        title = f"Improve S{n} AMISE: {g.name}"[:80]
    patch = research_env_patch()
    if not env_patch_is_safe(patch):
        raise RuntimeError("research env_patch must be DRY_RUN=true only")
    return StrategyProposal(
        id="",
        week_id=week_id,
        kind=RESEARCH_KIND,
        strategy=RESEARCH_STRATEGY,
        title=title,
        summary=str(row.get("supervisor") or g.name),
        paper=PaperResult(
            n_trades=int(m.get("n_trades") or 0),
            win_rate=float(m.get("win_rate") or 0),
            gross_pnl_inr=float(m.get("after_charges") or 0),
            after_tax_pnl_inr=float(m.get("after_charges") or 0),
            extra=extra,
        ),
        model_path=str(LIBRARY_PATH.relative_to(ROOT)) if LIBRARY_PATH.exists() else "data/research/library.json",
        safety_ok=True,
        safety_reasons=[
            "gates passed: n, after-charges vs S16+S18 with margin, PF, drawdown, "
            "both sides, walk-forward, 2× and 3× costs, holdout"
        ],
        status="pending",
        env_patch=patch,
    )


def run_research_lab(
    bars: list[FlowBar],
    *,
    lots: float = 100.0,
    fees: bool = True,
    n_folds: int = 3,
    params: FlowParams | None = None,
    include_recipes: bool = True,
    max_compose: int = 24,
    robustness: bool = True,
    sklearn: bool = True,
    strong: bool = True,
    screen_first: bool = True,
    propose: bool = False,
    proposals_path: Path | None = None,
    library_path: Path | None = None,
    week_id: str = "",
    timeframe: str = "1h",
    flatten_eod: bool = True,
    min_trades: int | None = None,
    require_both_sides: bool = True,
    use_s13: bool = False,
    beat_s18: bool = True,
    max_deep: int | None = None,
    write: bool = True,
) -> dict[str, Any]:
    """Closed research loop. ``propose=True`` writes pending rows only.

    Cheap-screens thin/losing genomes, then runs 3-fold + recipes + robustness
    + extra strength gates on the shortlist. Skipping those gates does not
    produce stronger books — it only lets weaker ones through.

    Champions are same-timeframe: beat S16-style and S18-style on these bars.
    Daily tapes also beat S13 (S16 replayed on the day). S13/S16 paper formulas
    stay. ``write=False`` lets the multi-TF runner merge libraries.
    """
    spec = parse_tf(timeframe)
    tf = spec.label
    mt = int(min_trades) if min_trades is not None else spec.min_trades
    cheap_n = cheap_min_trades(mt)
    flatten = bool(flatten_eod)
    p = params or FlowParams()
    flags = tape_flags(bars)
    train, hold = holdout_split(bars)
    disc_bars = train or bars
    s16 = champion_s16(bars, lots=lots, fees=fees)
    s18 = champion_s18(bars, lots=lots, fees=fees) if beat_s18 else None
    s13 = champion_s13(bars, lots=lots, fees=fees) if use_s13 else None
    scores = discover_relationships(disc_bars, params=p, flags=flags)
    composed = compose_from_discovery(scores, flags=flags)[:max_compose]
    genomes: list[StrategyGenome] = list(composed)
    if include_recipes:
        try:
            recs = selected_recipes(None, flags=flags)
        except ValueError:
            recs = ()
        for rec in recs:
            genomes.append(recipe_genome(rec, flags=flags))

    seen: set[str] = set()
    uniq: list[StrategyGenome] = []
    for g in genomes:
        g = stamp_genome_tf(g, tf)
        if g.genome_id in seen:
            continue
        seen.add(g.genome_id)
        uniq.append(g)

    champ = _champ_ac(s16, s18, s13)
    screened: list[tuple[float, StrategyGenome]] = []
    rows: list[dict[str, Any]] = []
    for g in uniq:
        if screen_first:
            quick = run_genome_book(
                bars,
                g,
                lots=lots,
                fees=fees,
                n_folds=0,
                params=p,
                tf="pre",
                skip_wf=True,
                flatten_eod=flatten,
            )
            cheap_fail = quick.n_trades < cheap_n or quick.after_charges <= champ
            if cheap_fail:
                fails = gate_failures(
                    quick,
                    s16=s16,
                    s18=s18,
                    rob=None,
                    strong=False,
                    s13=s13,
                    min_trades=cheap_n,
                    require_both_sides=False,
                )
                if not fails:
                    fails = [
                        f"screen: n={quick.n_trades} AC₹={quick.after_charges:.1f} "
                        f"not ahead of same-TF champions (AC₹={champ:.1f})"
                    ]
                rows.append(
                    {
                        "genome": g.to_dict(),
                        "metrics": metrics_public(quick),
                        "robustness": None,
                        "fails": fails,
                        "proposed": False,
                        "supervisor": supervisor_note(g, quick, fails),
                    }
                )
                continue
            screened.append((float(quick.after_charges), g))
        else:
            screened.append((0.0, g))
    screened.sort(key=lambda x: x[0], reverse=True)
    if max_deep is not None:
        screened = screened[: max(0, int(max_deep))]

    for _ac, g in screened:
        rows.append(
            evaluate_challenger(
                bars,
                g,
                s16=s16,
                s18=s18,
                lots=lots,
                fees=fees,
                n_folds=n_folds,
                params=p,
                robustness=robustness,
                strong=strong,
                holdout_bars=hold,
                flatten_eod=flatten,
                min_trades=mt,
                s13=s13,
                require_both_sides=require_both_sides,
            )
        )

    for row in rows:
        row.pop("_metrics", None)
        row.pop("_genome", None)
        row.pop("_rob", None)

    ranked = sorted(
        rows,
        key=lambda r: float((r.get("metrics") or {}).get("after_charges") or -1e18),
        reverse=True,
    )
    proposed = [r for r in ranked if r.get("proposed")]
    rejected = [r for r in ranked if not r.get("proposed")]
    stamp = week_id or datetime.now(IST).strftime("lab-%Y%m%d")
    champs: dict[str, Any] = {
        "S16": metrics_public(s16),
        "S18": metrics_public(s18),
    }
    if s13 is not None:
        champs["S13"] = metrics_public(s13)
    gates = [
        f"after-charges beat same-TF S16{' and S18' if beat_s18 else ''}"
        + (" and S13" if use_s13 else ""),
        f"{BEAT_MULT:.0%} margin vs best champion",
        f"PF ≥ {MIN_PF}",
        "drawdown ≤ after-charges",
        "both long and short sample" if require_both_sides else "both-sides relaxed (daily)",
        f"walk-forward ≥ {MIN_WF_WINS} folds",
        "2× and 3× costs",
        "1-bar delay + threshold jitter",
        "holdout last 20% when the tape is long enough",
        "sklearn feature ranks (research only, not a BUY model)",
        "session-aligned bars from MARKET_OPEN; token split on rollover",
    ]
    payload = {
        "updated_at_ist": _now_iso(),
        "lab": LAB_NAME,
        "n_bars": len(bars),
        "from": bars[0].time if bars else "",
        "to": bars[-1].time if bars else "",
        "tape": flags,
        "timeframe": tf,
        "flatten_eod": flatten,
        "min_trades": mt,
        "champions": champs,
        "discovery": [
            {
                "name": s.name,
                "n": s.n,
                "mean_fwd_atr": round(s.mean_fwd_atr, 4),
                "tstat": round(s.tstat, 3),
                "side": s.side,
            }
            for s in scores[:20]
        ],
        "sklearn_importances": sklearn_importances(disc_bars, params=p) if sklearn else [],
        "holdout_bars": len(hold),
        "train_bars": len(disc_bars),
        "strong": bool(strong),
        "n_deep": len(screened),
        "gates": gates,
        "counts": {
            "found": len(ranked),
            "passed_validation": len(proposed),
            "awaiting_approval": len(proposed),
            "rejected": len(rejected),
        },
        "challengers": proposed,
        "rejected": rejected[:40],
        "proposed_ids": [],
        "note": (
            "AI researches. You decide. Skipping folds/recipes/sklearn/robustness "
            "does not make stronger books — it only lets weaker ones through. "
            "Approve on the Lab tab does not ENABLE a book and never arms live. "
            "Each timeframe beats S16/S18 on that same bar size; daily also beats S13."
        ),
    }
    if write:
        write_library(payload, path=library_path or LIBRARY_PATH)
    pending_ids: list[str] = []
    if propose:
        for row in proposed:
            gid = str((row.get("genome") or {}).get("genome_id") or "g")
            prop = proposal_from_challenger(row, week_id=f"{stamp}:{gid}")
            if proposals_path is not None:
                add_proposal(prop, path=proposals_path)
            else:
                add_proposal(prop)
            pending_ids.append(prop.id)
        payload["proposed_ids"] = pending_ids
        payload["counts"]["awaiting_approval"] = len(pending_ids)
        if write:
            write_library(payload, path=library_path or LIBRARY_PATH)
    last = dict(payload)
    last["rejected"] = [
        {"name": (r.get("genome") or {}).get("name"), "fails": r.get("fails")}
        for r in rejected[:20]
    ]
    if write:
        dest = LAST_RUN_PATH if library_path is None else (library_path.parent / "last_run.json")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(last, indent=2), encoding="utf-8")
    return payload


def run_research_lab_multi(
    tick_rows: list[Any],
    *,
    lots: float = 100.0,
    fees: bool = True,
    n_folds: int = 3,
    params: FlowParams | None = None,
    include_recipes: bool = True,
    max_compose: int = 24,
    robustness: bool = True,
    sklearn: bool = True,
    strong: bool = True,
    screen_first: bool = True,
    propose: bool = False,
    proposals_path: Path | None = None,
    library_path: Path | None = None,
    week_id: str = "",
    max_deep: int | None = None,
    timeframes: list[Any] | tuple[Any, ...] | None = None,
) -> dict[str, Any]:
    """Run the strong lab on every LAB_TIMEFRAME. Same-TF champions; daily vs S13."""
    if timeframes:
        specs = [t if isinstance(t, LabTF) else parse_tf(t) for t in timeframes]
    else:
        specs = list(LAB_TIMEFRAMES)
    by_tf: dict[str, Any] = {}
    challengers: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    proposed_ids: list[str] = []
    champions_by_tf: dict[str, Any] = {}
    n_found = 0
    n_pass = 0
    n_skip = 0
    anchor: dict[str, Any] | None = None
    stamp = week_id or datetime.now(IST).strftime("lab-%Y%m%d")
    shared: dict[str, Any] = dict(
        lots=lots,
        fees=fees,
        n_folds=n_folds,
        params=params,
        include_recipes=include_recipes,
        max_compose=max_compose,
        robustness=robustness,
        sklearn=sklearn,
        strong=strong,
        screen_first=screen_first,
        propose=propose,
        proposals_path=proposals_path,
        library_path=library_path,
        max_deep=max_deep,
        write=False,
    )
    for spec in specs:
        bars = lab_bars_for_tf(tick_rows, spec)
        if len(bars) < spec.min_bars:
            n_skip += 1
            by_tf[spec.label] = {
                "ok": False,
                "skipped": True,
                "n_bars": len(bars),
                "min_bars": spec.min_bars,
                "error": f"need {spec.min_bars} {spec.label} bars, have {len(bars)}",
            }
            continue
        lab = run_research_lab(
            bars,
            week_id=f"{stamp}:{spec.label}",
            timeframe=spec.label,
            flatten_eod=spec.flatten_eod,
            min_trades=spec.min_trades,
            require_both_sides=not spec.daily,
            use_s13=spec.daily,
            beat_s18=not spec.daily,
            **shared,
        )
        by_tf[spec.label] = {
            "ok": True,
            "skipped": False,
            "n_bars": len(bars),
            "n_deep": lab.get("n_deep"),
            "counts": lab.get("counts") or {},
            "champions": lab.get("champions") or {},
            "kept": [
                {
                    "name": (r.get("genome") or {}).get("name"),
                    "genome_id": (r.get("genome") or {}).get("genome_id"),
                    "after_charges": (r.get("metrics") or {}).get("after_charges"),
                }
                for r in (lab.get("challengers") or [])
            ],
        }
        champions_by_tf[spec.label] = lab.get("champions") or {}
        n_found += int((lab.get("counts") or {}).get("found") or 0)
        n_pass += int((lab.get("counts") or {}).get("passed_validation") or 0)
        challengers.extend(lab.get("challengers") or [])
        rejected.extend(lab.get("rejected") or [])
        proposed_ids.extend(lab.get("proposed_ids") or [])
        if spec.label == "1h" or anchor is None:
            anchor = lab
    if anchor is None:
        anchor = {
            "updated_at_ist": _now_iso(),
            "lab": LAB_NAME,
            "n_bars": 0,
            "from": "",
            "to": "",
            "tape": {},
            "discovery": [],
            "sklearn_importances": [],
            "holdout_bars": 0,
            "train_bars": 0,
            "strong": bool(strong),
            "gates": [],
            "note": "no timeframe had enough bars",
        }
    ids: list[str] = []
    seen_id: set[str] = set()
    for gid in proposed_ids:
        if gid and gid not in seen_id:
            seen_id.add(gid)
            ids.append(gid)
    challengers.sort(
        key=lambda r: float((r.get("metrics") or {}).get("after_charges") or -1e18),
        reverse=True,
    )
    payload = {
        "updated_at_ist": _now_iso(),
        "lab": LAB_NAME,
        "multi_tf": True,
        "n_bars": anchor.get("n_bars") or 0,
        "from": anchor.get("from") or "",
        "to": anchor.get("to") or "",
        "tape": anchor.get("tape") or {},
        "timeframes": [s.label for s in specs],
        "n_timeframes": len(by_tf),
        "n_skipped": n_skip,
        "champions": (champions_by_tf.get("1h") or anchor.get("champions") or {}),
        "champions_by_tf": champions_by_tf,
        "by_tf": by_tf,
        "discovery": anchor.get("discovery") or [],
        "sklearn_importances": anchor.get("sklearn_importances") or [],
        "holdout_bars": anchor.get("holdout_bars") or 0,
        "train_bars": anchor.get("train_bars") or 0,
        "strong": bool(strong),
        "gates": list(anchor.get("gates") or [])
        or [
            "same-TF S16/S18 after charges (daily also vs S13)",
            f"{BEAT_MULT:.0%} margin, PF, drawdown, walk-forward, 2×/3×, holdout",
        ],
        "counts": {
            "found": n_found,
            "passed_validation": n_pass,
            "awaiting_approval": len(ids) if propose else n_pass,
            "rejected": max(0, n_found - n_pass),
        },
        "challengers": challengers,
        "rejected": rejected[:40],
        "proposed_ids": ids,
        "note": (
            "Factory screened 3m…3h45 plus the daily bar. Each rung beats S16/S18 "
            "on that same bar size (daily also beats S13). Session bars start at "
            "MARKET_OPEN; a contract roll starts a new candle. Skipping gates does "
            "not make stronger books. Approve on Lab does not ENABLE live. "
            "Keep DRY_RUN=true."
        ),
    }
    write_library(payload, path=library_path or LIBRARY_PATH)
    dest = LAST_RUN_PATH if library_path is None else (library_path.parent / "last_run.json")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return payload
