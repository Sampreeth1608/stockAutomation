"""Relationship atoms for the research factory.

OHLC / volume / OI / wick / VWAP / depth / imbalance / microprice.
Discovery searches these; the composer ANDs them; no-trade atoms are
HOLD / skip, not BUY.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from flow_lab import DECIDERS, FlowBar, FlowFeat, FlowParams
from strategy_genome import StrategyGenome

Predicate = Callable[
    [list[FlowBar], list[FlowFeat | None], int, FlowParams], bool
]


@dataclass(frozen=True)
class Atom:
    name: str
    family: str
    idea: str
    needs_book: bool = False
    needs_l1: bool = False
    needs_oi: bool = False
    kind: str = "filter"  # long | short | both | no_trade


def _prev(bars: list[FlowBar], i: int) -> FlowBar | None:
    if i < 1:
        return None
    return bars[i - 1]


def _feat(
    feats: list[FlowFeat | None], i: int
) -> FlowFeat | None:
    if i < 0 or i >= len(feats):
        return None
    return feats[i]


def _wicks(b: FlowBar) -> tuple[float, float, float]:
    rng = float(b.high) - float(b.low)
    upper = float(b.high) - max(float(b.open), float(b.close))
    lower = min(float(b.open), float(b.close)) - float(b.low)
    return upper, lower, rng


def atom_hh(bars: list[FlowBar], feats: list[FlowFeat | None], i: int, p: FlowParams) -> bool:
    del feats, p
    prev = _prev(bars, i)
    return bool(prev is not None and bars[i].high > prev.high)


def atom_hl(bars: list[FlowBar], feats: list[FlowFeat | None], i: int, p: FlowParams) -> bool:
    del feats, p
    prev = _prev(bars, i)
    return bool(prev is not None and bars[i].low > prev.low)


def atom_hc(bars: list[FlowBar], feats: list[FlowFeat | None], i: int, p: FlowParams) -> bool:
    del feats, p
    prev = _prev(bars, i)
    return bool(prev is not None and bars[i].close > prev.close)


def atom_ho_gt_pc(bars: list[FlowBar], feats: list[FlowFeat | None], i: int, p: FlowParams) -> bool:
    del feats, p
    prev = _prev(bars, i)
    return bool(prev is not None and bars[i].open > prev.close)


def atom_lh(bars: list[FlowBar], feats: list[FlowFeat | None], i: int, p: FlowParams) -> bool:
    del feats, p
    prev = _prev(bars, i)
    return bool(prev is not None and bars[i].high < prev.high)


def atom_ll(bars: list[FlowBar], feats: list[FlowFeat | None], i: int, p: FlowParams) -> bool:
    del feats, p
    prev = _prev(bars, i)
    return bool(prev is not None and bars[i].low < prev.low)


def atom_lc(bars: list[FlowBar], feats: list[FlowFeat | None], i: int, p: FlowParams) -> bool:
    del feats, p
    prev = _prev(bars, i)
    return bool(prev is not None and bars[i].close < prev.close)


def atom_bull(bars: list[FlowBar], feats: list[FlowFeat | None], i: int, p: FlowParams) -> bool:
    del bars, p
    f = _feat(feats, i)
    return bool(f is not None and f.green)


def atom_bear(bars: list[FlowBar], feats: list[FlowFeat | None], i: int, p: FlowParams) -> bool:
    del bars, p
    f = _feat(feats, i)
    return bool(f is not None and f.red)


def atom_vol_up(bars: list[FlowBar], feats: list[FlowFeat | None], i: int, p: FlowParams) -> bool:
    del p
    prev = _prev(bars, i)
    f = _feat(feats, i)
    if prev is None or f is None:
        return False
    return float(bars[i].volume) > float(prev.volume) > 0.0


def atom_vol_dn(bars: list[FlowBar], feats: list[FlowFeat | None], i: int, p: FlowParams) -> bool:
    del p
    prev = _prev(bars, i)
    if prev is None:
        return False
    return 0.0 < float(bars[i].volume) < float(prev.volume)


def atom_rvol_hi(bars: list[FlowBar], feats: list[FlowFeat | None], i: int, p: FlowParams) -> bool:
    del bars
    f = _feat(feats, i)
    return bool(f is not None and f.rvol >= float(p.rvol if p.rvol > 1e-12 else 1.0))


def atom_rvol_1p5(bars: list[FlowBar], feats: list[FlowFeat | None], i: int, p: FlowParams) -> bool:
    del bars, p
    f = _feat(feats, i)
    return bool(f is not None and f.rvol >= 1.5)


def atom_oi_up(bars: list[FlowBar], feats: list[FlowFeat | None], i: int, p: FlowParams) -> bool:
    del bars, p
    f = _feat(feats, i)
    return bool(f is not None and f.oi > 0.0 and f.oi_chg > 0.0)


def atom_oi_dn(bars: list[FlowBar], feats: list[FlowFeat | None], i: int, p: FlowParams) -> bool:
    del bars, p
    f = _feat(feats, i)
    return bool(f is not None and f.oi > 0.0 and f.oi_chg < 0.0)


def atom_body_strong(bars: list[FlowBar], feats: list[FlowFeat | None], i: int, p: FlowParams) -> bool:
    del feats, p
    _u, _l, rng = _wicks(bars[i])
    if rng <= 1e-12:
        return False
    body = abs(float(bars[i].close) - float(bars[i].open))
    return body / rng >= 0.60


def atom_upper_wick(bars: list[FlowBar], feats: list[FlowFeat | None], i: int, p: FlowParams) -> bool:
    del feats, p
    upper, _l, rng = _wicks(bars[i])
    return rng > 1e-12 and upper / rng >= 0.40


def atom_lower_wick(bars: list[FlowBar], feats: list[FlowFeat | None], i: int, p: FlowParams) -> bool:
    del feats, p
    _u, lower, rng = _wicks(bars[i])
    return rng > 1e-12 and lower / rng >= 0.40


def atom_close_high(bars: list[FlowBar], feats: list[FlowFeat | None], i: int, p: FlowParams) -> bool:
    del bars
    f = _feat(feats, i)
    return bool(f is not None and f.close_loc >= float(p.close_loc))


def atom_close_low(bars: list[FlowBar], feats: list[FlowFeat | None], i: int, p: FlowParams) -> bool:
    del bars
    f = _feat(feats, i)
    return bool(f is not None and f.close_loc <= (1.0 - float(p.close_loc)))


def atom_px_gt_vwap(bars: list[FlowBar], feats: list[FlowFeat | None], i: int, p: FlowParams) -> bool:
    del bars, p
    f = _feat(feats, i)
    return bool(f is not None and f.vwap_gap > 0.0)


def atom_px_lt_vwap(bars: list[FlowBar], feats: list[FlowFeat | None], i: int, p: FlowParams) -> bool:
    del bars, p
    f = _feat(feats, i)
    return bool(f is not None and f.vwap_gap < 0.0)


def atom_brk20(bars: list[FlowBar], feats: list[FlowFeat | None], i: int, p: FlowParams) -> bool:
    del p
    f = _feat(feats, i)
    return bool(f is not None and bars[i].high > f.prev_high_n and f.prev_high_n > 0.0)


def atom_brk20_dn(bars: list[FlowBar], feats: list[FlowFeat | None], i: int, p: FlowParams) -> bool:
    del p
    f = _feat(feats, i)
    return bool(f is not None and bars[i].low < f.prev_low_n and f.prev_low_n > 0.0)


def atom_imb_buy(bars: list[FlowBar], feats: list[FlowFeat | None], i: int, p: FlowParams) -> bool:
    del bars
    f = _feat(feats, i)
    return bool(f is not None and f.has_book and f.imb >= float(p.imb_th))


def atom_imb_sell(bars: list[FlowBar], feats: list[FlowFeat | None], i: int, p: FlowParams) -> bool:
    del bars
    f = _feat(feats, i)
    return bool(f is not None and f.has_book and f.imb <= -float(p.imb_th))


def atom_depth_buy(bars: list[FlowBar], feats: list[FlowFeat | None], i: int, p: FlowParams) -> bool:
    del bars
    f = _feat(feats, i)
    return bool(f is not None and f.has_book and f.depth_imb >= float(p.depth_th))


def atom_depth_sell(bars: list[FlowBar], feats: list[FlowFeat | None], i: int, p: FlowParams) -> bool:
    del bars
    f = _feat(feats, i)
    return bool(f is not None and f.has_book and f.depth_imb <= -float(p.depth_th))


def atom_micro_buy(bars: list[FlowBar], feats: list[FlowFeat | None], i: int, p: FlowParams) -> bool:
    del bars
    f = _feat(feats, i)
    th = float(p.micro_bps) * 1e-4
    return bool(f is not None and f.has_l1 and f.micro_gap <= -th)


def atom_micro_sell(bars: list[FlowBar], feats: list[FlowFeat | None], i: int, p: FlowParams) -> bool:
    del bars
    f = _feat(feats, i)
    th = float(p.micro_bps) * 1e-4
    return bool(f is not None and f.has_l1 and f.micro_gap >= th)


def atom_spread_ok(bars: list[FlowBar], feats: list[FlowFeat | None], i: int, p: FlowParams) -> bool:
    del bars, p
    f = _feat(feats, i)
    return bool(f is not None and f.has_l1 and not f.spread_wide)


def atom_spread_wide(bars: list[FlowBar], feats: list[FlowFeat | None], i: int, p: FlowParams) -> bool:
    del bars, p
    f = _feat(feats, i)
    return bool(f is not None and f.spread_wide)


def atom_rvol_extreme(bars: list[FlowBar], feats: list[FlowFeat | None], i: int, p: FlowParams) -> bool:
    del bars, p
    f = _feat(feats, i)
    return bool(f is not None and f.rvol >= 4.0)


def atom_no_depth(bars: list[FlowBar], feats: list[FlowFeat | None], i: int, p: FlowParams) -> bool:
    del bars, p
    f = _feat(feats, i)
    return bool(f is not None and not f.has_book)


def atom_oi_missing(bars: list[FlowBar], feats: list[FlowFeat | None], i: int, p: FlowParams) -> bool:
    del p
    f = _feat(feats, i)
    return bool(f is not None and float(bars[i].oi or 0.0) <= 0.0)


def atom_mom_up(bars: list[FlowBar], feats: list[FlowFeat | None], i: int, p: FlowParams) -> bool:
    del bars, p
    f = _feat(feats, i)
    return bool(f is not None and f.mom > 0.0)


def atom_mom_dn(bars: list[FlowBar], feats: list[FlowFeat | None], i: int, p: FlowParams) -> bool:
    del bars, p
    f = _feat(feats, i)
    return bool(f is not None and f.mom < 0.0)


ATOMS: tuple[Atom, ...] = (
    Atom("hh", "ohlc", "C.high > P.high", kind="long"),
    Atom("hl", "ohlc", "C.low > P.low", kind="long"),
    Atom("hc", "ohlc", "C.close > P.close", kind="long"),
    Atom("ho_gt_pc", "ohlc", "C.open > P.close", kind="long"),
    Atom("lh", "ohlc", "C.high < P.high", kind="short"),
    Atom("ll", "ohlc", "C.low < P.low", kind="short"),
    Atom("lc", "ohlc", "C.close < P.close", kind="short"),
    Atom("bull", "candle", "bullish close (C > O)", kind="long"),
    Atom("bear", "candle", "bearish close (C < O)", kind="short"),
    Atom("vol_up", "volume", "volume up vs previous bar", kind="both"),
    Atom("vol_dn", "volume", "volume down vs previous bar", kind="both"),
    Atom("rvol_hi", "volume", "RVOL >= params.rvol", kind="both"),
    Atom("rvol_1p5", "volume", "RVOL >= 1.5×", kind="both"),
    Atom("oi_up", "oi", "open interest increasing", needs_oi=True, kind="both"),
    Atom("oi_dn", "oi", "open interest decreasing", needs_oi=True, kind="both"),
    Atom("body_strong", "candle", "body / range >= 0.60", kind="both"),
    Atom("upper_wick", "candle", "upper wick / range >= 0.40", kind="short"),
    Atom("lower_wick", "candle", "lower wick / range >= 0.40", kind="long"),
    Atom("close_high", "candle", "close in the top of the range", kind="long"),
    Atom("close_low", "candle", "close in the bottom of the range", kind="short"),
    Atom("px_gt_vwap", "vwap", "price above session VWAP", kind="long"),
    Atom("px_lt_vwap", "vwap", "price below session VWAP", kind="short"),
    Atom("brk20", "structure", "20-bar high break", kind="long"),
    Atom("brk20_dn", "structure", "20-bar low break", kind="short"),
    Atom("imb_buy", "flow", "TBQ/TSQ imbalance long", needs_book=True, kind="long"),
    Atom("imb_sell", "flow", "TBQ/TSQ imbalance short", needs_book=True, kind="short"),
    Atom("depth_buy", "depth", "5-level depth imbalance long", needs_book=True, kind="long"),
    Atom("depth_sell", "depth", "5-level depth imbalance short", needs_book=True, kind="short"),
    Atom("micro_buy", "micro", "LTP below microprice", needs_l1=True, kind="long"),
    Atom("micro_sell", "micro", "LTP above microprice", needs_l1=True, kind="short"),
    Atom("spread_ok", "micro", "spread not abnormally wide", needs_l1=True, kind="both"),
    Atom("mom_up", "ohlc", "close momentum positive", kind="long"),
    Atom("mom_dn", "ohlc", "close momentum negative", kind="short"),
    Atom("spread_wide", "no_trade", "spread abnormally wide → NO TRADE", needs_l1=True, kind="no_trade"),
    Atom("rvol_extreme", "no_trade", "RVOL >= 4 → NO TRADE", kind="no_trade"),
    Atom("no_depth", "no_trade", "book missing on this bar → NO TRADE", needs_book=True, kind="no_trade"),
    Atom("oi_missing", "no_trade", "OI missing on this bar → NO TRADE", needs_oi=True, kind="no_trade"),
)

FUNCS: dict[str, Predicate] = {
    "hh": atom_hh,
    "hl": atom_hl,
    "hc": atom_hc,
    "ho_gt_pc": atom_ho_gt_pc,
    "lh": atom_lh,
    "ll": atom_ll,
    "lc": atom_lc,
    "bull": atom_bull,
    "bear": atom_bear,
    "vol_up": atom_vol_up,
    "vol_dn": atom_vol_dn,
    "rvol_hi": atom_rvol_hi,
    "rvol_1p5": atom_rvol_1p5,
    "oi_up": atom_oi_up,
    "oi_dn": atom_oi_dn,
    "body_strong": atom_body_strong,
    "upper_wick": atom_upper_wick,
    "lower_wick": atom_lower_wick,
    "close_high": atom_close_high,
    "close_low": atom_close_low,
    "px_gt_vwap": atom_px_gt_vwap,
    "px_lt_vwap": atom_px_lt_vwap,
    "brk20": atom_brk20,
    "brk20_dn": atom_brk20_dn,
    "imb_buy": atom_imb_buy,
    "imb_sell": atom_imb_sell,
    "depth_buy": atom_depth_buy,
    "depth_sell": atom_depth_sell,
    "micro_buy": atom_micro_buy,
    "micro_sell": atom_micro_sell,
    "spread_ok": atom_spread_ok,
    "mom_up": atom_mom_up,
    "mom_dn": atom_mom_dn,
    "spread_wide": atom_spread_wide,
    "rvol_extreme": atom_rvol_extreme,
    "no_depth": atom_no_depth,
    "oi_missing": atom_oi_missing,
}

MIRROR: dict[str, str] = {
    "hh": "lh",
    "hl": "ll",
    "hc": "lc",
    "ho_gt_pc": "lc",
    "lh": "hh",
    "ll": "hl",
    "lc": "hc",
    "bull": "bear",
    "bear": "bull",
    "close_high": "close_low",
    "close_low": "close_high",
    "px_gt_vwap": "px_lt_vwap",
    "px_lt_vwap": "px_gt_vwap",
    "brk20": "brk20_dn",
    "brk20_dn": "brk20",
    "imb_buy": "imb_sell",
    "imb_sell": "imb_buy",
    "depth_buy": "depth_sell",
    "depth_sell": "depth_buy",
    "micro_buy": "micro_sell",
    "micro_sell": "micro_buy",
    "mom_up": "mom_dn",
    "mom_dn": "mom_up",
    "lower_wick": "upper_wick",
    "upper_wick": "lower_wick",
}

ATOM_BY_NAME: dict[str, Atom] = {a.name: a for a in ATOMS}


def eval_atom(
    name: str,
    bars: list[FlowBar],
    feats: list[FlowFeat | None],
    i: int,
    p: FlowParams,
) -> bool:
    fn = FUNCS.get(name)
    if fn is None:
        raise KeyError(f"unknown relationship atom {name!r}")
    return bool(fn(bars, feats, i, p))


def atoms_for_tape(flags: dict[str, bool] | None) -> tuple[Atom, ...]:
    flags = flags or {}
    out: list[Atom] = []
    for a in ATOMS:
        if a.needs_book and not flags.get("book"):
            continue
        if a.needs_l1 and not flags.get("l1"):
            continue
        if a.needs_oi and not flags.get("oi"):
            continue
        out.append(a)
    return tuple(out)


def default_no_trade(flags: dict[str, bool] | None) -> tuple[str, ...]:
    flags = flags or {}
    names = ["rvol_extreme"]
    if flags.get("l1"):
        names.append("spread_wide")
    return tuple(names)


def compile_genome(
    genome: StrategyGenome,
) -> Callable[..., tuple[str | None, str]]:
    """Turn a genome into a flow_lab decide_fn. HOLD / NO-TRADE is None."""
    g = genome.normalized()
    recipe_fn = DECIDERS.get(g.recipe) if g.recipe else None
    if g.recipe and recipe_fn is None:
        raise ValueError(f"unknown recipe {g.recipe!r}")

    def decide(
        bars: list[FlowBar],
        feats: list[FlowFeat | None],
        i: int,
        p: FlowParams,
        st: dict[str, Any],
    ) -> tuple[str | None, str]:
        f = feats[i] if 0 <= i < len(feats) else None
        if f is None:
            return None, "warmup"
        for name in g.no_trade:
            if eval_atom(name, bars, feats, i, p):
                return None, f"no-trade:{name}"
        if recipe_fn is not None:
            return recipe_fn(bars, feats, i, p, st)
        long_ok = bool(g.entry_long) and all(
            eval_atom(n, bars, feats, i, p) for n in g.entry_long
        )
        short_ok = bool(g.entry_short) and all(
            eval_atom(n, bars, feats, i, p) for n in g.entry_short
        )
        if long_ok and short_ok:
            return None, "no-trade:disagree"
        if long_ok and g.direction in {"long", "both"}:
            return "long", " AND ".join(g.entry_long)
        if short_ok and g.direction in {"short", "both"}:
            return "short", " AND ".join(g.entry_short)
        return None, "hold"

    return decide


def delayed_decide(
    inner: Callable[..., tuple[str | None, str]],
    *,
    bars_delay: int = 1,
) -> Callable[..., tuple[str | None, str]]:
    """1-bar entry delay (bar analogue of a late fill / 1-tick lag)."""
    hist: list[tuple[str | None, str]] = []

    def decide(
        bars: list[FlowBar],
        feats: list[FlowFeat | None],
        i: int,
        p: FlowParams,
        st: dict[str, Any],
    ) -> tuple[str | None, str]:
        want, why = inner(bars, feats, i, p, st)
        hist.append((want, why))
        n = max(1, int(bars_delay))
        if len(hist) <= n:
            return None, "delay"
        delayed, prev_why = hist[-1 - n]
        return delayed, f"delayed:{prev_why}"

    return decide
