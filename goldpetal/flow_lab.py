"""Order-flow + OI Strategy Factory — research only. Not a paper book. Not live.

GoldPetal ticks carry more than OHLC + volume: LTP, TBQ/TSQ, 5-level
bid/ask prices and quantities, and OI (from ``raw_json`` / full CSV /
Upstox candles). Total buy/sell *price* is not a stored column; we
rebuild book-weighted prices as Σ(level price × qty) / Σ qty from L1–L5.

This laboratory does not pick one "best" guess. It builds a factory of
named recipes (order-book confirmation, absorption, depth acceleration,
microprice, VWAP+flow, liquidity shock, failed breakout, OI overlays,
and a multi-factor score) and ranks them after Angel charges, tax
excluded, with walk-forward folds. Win rate is secondary.

Fill at the signal bar's close. ATR targets also exit at close.
Intraday tapes flatten at the last bar of each calendar day.

Do not add these names to ALL_STRATEGY_NAMES / desk / ENABLE_*. Paper
them only if a later *real* tick tape beats S16 and S18 after charges
with enough trades and the operator asks.

Cloud ``ticks.db`` is often empty. Depth recipes need a VM ``--db`` or
a full-depth ticks CSV. OI + OHLCV inventions run on Upstox candles
that include the 7th field (OI).
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from export_full_ticks import _depth_side
from mtf_bars import floor_bar, load_tick_rows, parse_ts, tick_metrics
from ohlcv_lab import (
    EXIT_ATR,
    EXIT_FLIP,
    LabMetrics,
    day_folds,
    score_result,
    session_bars,
)
from s18_ohlc_vol_htf import VolBar, _close_leg
from backtest_hhhl_candles import make_charge_cfg
from backtest_wick_candles import _tf_result_from_trades
from charges import ChargeConfig

LAB_NAME = "FLOW_LAB"

FORMULA = (
    "Research factory, not a paper book. Finished bars with LTP/OHLC/volume "
    "+ TBQ/TSQ + 5-level depth + OI → signed imbalance, depth acceleration, "
    "microprice, book-weighted fair, spread, VWAP, RVOL, OI change → named "
    "recipes (depth+LTP, flow divergence, breakout+depth, absorption, "
    "microprice, VWAP+flow, liquidity shock, failed breakout, OI overlays, "
    "multi-factor score) → flip+EOD or 2ATR/1.5ATR trail. Fill at close. "
    "Rank after charges (expectancy, profit factor, drawdown, walk-forward); "
    "win% secondary. Discover which *combination* has out-of-sample expectancy."
)

ML_FEATURE_NAMES: tuple[str, ...] = (
    "rvol",
    "mom",
    "mom3",
    "atr",
    "close_loc",
    "vwap_gap",
    "imb",
    "depth_imb",
    "depth_accel",
    "imb_accel",
    "micro_gap",
    "spread_bps",
    "l1_imb",
    "ba_ratio",
    "oi_chg",
    "oi_pct",
    "tbq_chg",
    "tsq_chg",
    "bid_depth_chg",
    "ask_depth_chg",
    "book_fair_gap",
)


@dataclass(frozen=True)
class FlowParams:
    lookback: int = 20
    atr_n: int = 14
    imb_th: float = 0.25
    depth_th: float = 0.25
    rvol: float = 1.0
    breakout_rvol: float = 1.5
    absorb_qty: float = 0.30
    absorb_mom: float = 0.0015
    accel_th: float = 0.08
    shock: float = 0.40
    spread_wide_mult: float = 2.5
    score_th: float = 40.0
    tp_atr: float = 2.0
    trail_atr: float = 1.5
    close_loc: float = 0.75
    oi_rvol: float = 1.2
    climax_rvol: float = 2.5
    climax_range_mult: float = 1.5
    ba_ratio_th: float = 1.25
    micro_bps: float = 2.0


@dataclass
class FlowBar(VolBar):
    """OHLCV bar plus close-of-bar order-flow / depth / OI snapshot."""

    oi: float = 0.0
    buy5: float = 0.0
    sell5: float = 0.0
    buy_px: tuple[float, ...] = ()
    buy_qty: tuple[float, ...] = ()
    sell_px: tuple[float, ...] = ()
    sell_qty: tuple[float, ...] = ()

    @property
    def has_book(self) -> bool:
        return (float(self.tbq) + float(self.tsq)) > 1e-9 or (
            float(self.buy5) + float(self.sell5)
        ) > 1e-9

    @property
    def has_l1(self) -> bool:
        return self.buy1_px > 1e-12 and self.sell1_px > 1e-12

    @property
    def buy1_px(self) -> float:
        return float(self.buy_px[0]) if self.buy_px else 0.0

    @property
    def sell1_px(self) -> float:
        return float(self.sell_px[0]) if self.sell_px else 0.0

    @property
    def buy1_qty(self) -> float:
        return float(self.buy_qty[0]) if self.buy_qty else 0.0

    @property
    def sell1_qty(self) -> float:
        return float(self.sell_qty[0]) if self.sell_qty else 0.0


@dataclass(frozen=True)
class FlowFeat:
    rng: float
    body: float
    close_loc: float
    green: bool
    red: bool
    avg_vol: float
    rvol: float
    avg_range: float
    prev_high_n: float
    prev_low_n: float
    atr: float
    sma: float
    vwap: float
    prev_close: float
    prev_vol: float
    mom: float
    mom3: float
    oi: float
    oi_chg: float
    oi_pct: float
    oi_chg3: float
    has_book: bool
    has_l1: bool
    imb: float
    depth_imb: float
    depth_accel: float
    imb_accel: float
    tbq_chg: float
    tsq_chg: float
    ba_ratio: float
    l1_imb: float
    spread: float
    spread_bps: float
    spread_wide: bool
    micro: float
    micro_gap: float
    book_fair: float
    book_fair_gap: float
    bid_depth: float
    ask_depth: float
    bid_depth_chg: float
    ask_depth_chg: float
    vwap_gap: float
    score: float


@dataclass(frozen=True)
class Recipe:
    name: str
    family: str
    idea: str
    needs_book: bool = False
    needs_l1: bool = False
    needs_oi: bool = False
    priority: int = 99


# Priority matches the operator list (1 = test first). Inventions after 10.
RECIPES: tuple[Recipe, ...] = (
    Recipe(
        "depth_ltp",
        "depth",
        "5-level depth imbalance + LTP momentum + bullish/bearish close + RVOL",
        needs_book=True,
        priority=1,
    ),
    Recipe(
        "flow_div",
        "divergence",
        "LTP vs order-flow divergence (price up, bid/depth down → SHORT)",
        needs_book=True,
        priority=2,
    ),
    Recipe(
        "brk_flow",
        "breakout",
        "20-bar breakout + 1.5× volume + TBQ/TSQ and 5-level imbalance",
        needs_book=True,
        priority=3,
    ),
    Recipe(
        "absorb",
        "absorption",
        "Buy qty spikes while LTP stays flat, then sell pressure → SHORT (mirror LONG)",
        needs_book=True,
        priority=4,
    ),
    Recipe(
        "micro",
        "microprice",
        "LTP vs microprice short-term pressure, confirmed by candle + RVOL",
        needs_l1=True,
        priority=5,
    ),
    Recipe(
        "vwap_flow",
        "vwap",
        "VWAP pullback + bid/ask support + signed imbalance + candle",
        needs_book=True,
        priority=6,
    ),
    Recipe(
        "liq_shock",
        "liquidity",
        "Sudden bid (ask) depth disappearance with LTP and volume confirmation",
        needs_book=True,
        priority=7,
    ),
    Recipe(
        "fail_brk",
        "reversal",
        "Failed 20-bar breakout plus book reversal (bids vanish, asks grow)",
        needs_book=True,
        priority=8,
    ),
    Recipe(
        "climax_vol",
        "reversal",
        "Volume climax fade (OHLCV baseline + optional OI extreme)",
        priority=9,
    ),
    Recipe(
        "candle",
        "baseline",
        "Pure candlestick baseline: close location + momentum (no book/OI)",
        priority=10,
    ),
    Recipe(
        "imb_confirm",
        "depth",
        "Signed TBQ/TSQ imbalance > ±25% with price confirmation",
        needs_book=True,
        priority=11,
    ),
    Recipe(
        "ba_spread",
        "depth",
        "Bid/ask quantity ratio with a stable spread and LTP + volume",
        needs_l1=True,
        priority=12,
    ),
    Recipe(
        "depth_accel",
        "depth",
        "Depth imbalance *acceleration* (change in imbalance, not the level)",
        needs_book=True,
        priority=13,
    ),
    Recipe(
        "tbq_div",
        "flow",
        "TBQ vs TSQ divergence: one side accelerates, the other does not",
        needs_book=True,
        priority=14,
    ),
    Recipe(
        "book_fair",
        "microprice",
        "L5 book-weighted buy/sell prices vs LTP (reconstructed total buy/sell price)",
        needs_l1=True,
        priority=15,
    ),
    Recipe(
        "score",
        "multi",
        "Signed multi-factor score (momentum, candle, volume, book, micro, VWAP, OI)",
        priority=16,
    ),
    Recipe(
        "rvol_oi",
        "oi",
        "RVOL + OI rising with candle/momentum (new OI confirming the move)",
        needs_oi=True,
        priority=17,
    ),
    Recipe(
        "brk_oi",
        "oi",
        "Volume breakout only when OI expands in the breakout direction",
        needs_oi=True,
        priority=18,
    ),
    Recipe(
        "vwap_oi",
        "oi",
        "VWAP pullback with OI change aligned to the bounce",
        needs_oi=True,
        priority=19,
    ),
    Recipe(
        "oi_div",
        "oi",
        "Price vs OI divergence (LTP up / OI down → SHORT)",
        needs_oi=True,
        priority=20,
    ),
    Recipe(
        "climax_oi",
        "oi",
        "Volume climax plus large OI change, then fade the recovery bar",
        needs_oi=True,
        priority=21,
    ),
    Recipe(
        "vwap_rvol_oi",
        "oi",
        "Invention: above/below VWAP + RVOL + OI confirmation",
        needs_oi=True,
        priority=22,
    ),
    Recipe(
        "absorb_oi",
        "oi",
        "Invention: high volume, small body, OI up (absorbed) then fade with candle",
        needs_oi=True,
        priority=23,
    ),
)


def signed_imb(buy: float, sell: float) -> float:
    """(buy − sell) / (buy + sell). Signed. 0 if the book is empty."""
    tot = float(buy) + float(sell)
    if tot <= 1e-12:
        return 0.0
    return (float(buy) - float(sell)) / tot


def microprice(bid: float, bid_qty: float, ask: float, ask_qty: float) -> float:
    """Ask×BidQty + Bid×AskQty over BidQty+AskQty."""
    den = float(bid_qty) + float(ask_qty)
    if den <= 1e-12:
        return 0.0
    return (float(ask) * float(bid_qty) + float(bid) * float(ask_qty)) / den


def weighted_px(prices: tuple[float, ...], qtys: tuple[float, ...]) -> float:
    """Σ(price × qty) / Σ qty — reconstructed total buy/sell price from L1–L5."""
    if not prices or not qtys:
        return 0.0
    num = 0.0
    den = 0.0
    for px, qty in zip(prices, qtys):
        q = float(qty)
        if q <= 0.0 or float(px) <= 0.0:
            continue
        num += float(px) * q
        den += q
    if den <= 1e-12:
        return 0.0
    return num / den


def _clip(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, float(x)))


def _pct_chg(cur: float, prev: float) -> float:
    if abs(float(prev)) <= 1e-12:
        return 0.0
    return (float(cur) - float(prev)) / float(prev)


def _close_loc(high: float, low: float, close: float) -> float:
    rng = float(high) - float(low)
    if rng <= 1e-12:
        return 0.5
    return (float(close) - float(low)) / rng


def _f(row: dict[str, Any], *keys: str, default: float = 0.0) -> float:
    for k in keys:
        if k in row and row[k] not in (None, ""):
            try:
                return float(row[k])
            except (TypeError, ValueError):
                continue
    return default


def tape_flags(bars: list[FlowBar]) -> dict[str, bool]:
    return {
        "book": any(b.has_book for b in bars),
        "l1": any(b.has_l1 for b in bars),
        "oi": any(float(b.oi) > 0.0 for b in bars),
        "volume": any(float(b.volume) > 0.0 for b in bars),
    }


def selected_recipes(
    names: list[str] | None,
    *,
    flags: dict[str, bool] | None = None,
) -> tuple[Recipe, ...]:
    """Filter RECIPES (factory order). Drop recipes the tape cannot support."""
    use = RECIPES
    if names:
        want: list[str] = []
        for item in names:
            want.extend(x.strip() for x in str(item).split(",") if x.strip())
        known = {r.name for r in RECIPES}
        unknown = [n for n in want if n not in known]
        if unknown:
            raise ValueError(
                f"unknown factory recipe {unknown!r}; choose from "
                f"{[r.name for r in RECIPES]}"
            )
        pick = set(want)
        use = tuple(r for r in RECIPES if r.name in pick)
    flags = flags or {}
    out: list[Recipe] = []
    for r in use:
        if r.needs_book and not flags.get("book", True):
            continue
        if r.needs_l1 and not flags.get("l1", True):
            continue
        if r.needs_oi and not flags.get("oi", True):
            continue
        out.append(r)
    return tuple(out)


def _book_levels(msg: dict[str, Any]) -> tuple[tuple[float, ...], tuple[float, ...], tuple[float, ...], tuple[float, ...]]:
    buy = _depth_side(msg, "buy")
    sell = _depth_side(msg, "sell")
    buy_px = tuple(float(p or 0.0) for p, _q in buy)
    buy_qty = tuple(float(q or 0.0) for _p, q in buy)
    sell_px = tuple(float(p or 0.0) for p, _q in sell)
    sell_qty = tuple(float(q or 0.0) for _p, q in sell)
    return buy_px, buy_qty, sell_px, sell_qty


def _msg_from_row(row: Any) -> dict[str, Any]:
    raw = ""
    if isinstance(row, dict):
        raw = str(row.get("raw_json") or "")
    else:
        try:
            raw = str(row["raw_json"] or "")
        except (KeyError, TypeError, IndexError):
            raw = ""
    if not raw:
        return {}
    try:
        msg = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return msg if isinstance(msg, dict) else {}


def flow_bars_from_tick_rows(rows: Any, minutes: int) -> list[FlowBar]:
    """Aggregate websocket / sqlite tick rows into FlowBars (last-tick book snapshot)."""
    bars: list[FlowBar] = []
    cur_key: datetime | None = None
    o = h = l = c = None
    tbq = tsq = buy5 = sell5 = 0.0
    oi = 0.0
    vol_close: float | None = None
    n = 0
    buy_px: tuple[float, ...] = ()
    buy_qty: tuple[float, ...] = ()
    sell_px: tuple[float, ...] = ()
    sell_qty: tuple[float, ...] = ()

    def flush(key: datetime) -> None:
        nonlocal o, h, l, c, tbq, tsq, buy5, sell5, oi, vol_close, n
        nonlocal buy_px, buy_qty, sell_px, sell_qty
        if o is None or c is None or h is None or l is None:
            return
        prev_vol = bars[-1]._vol_close if bars else None  # type: ignore[attr-defined]
        bar_vol = 0.0
        if vol_close is not None and prev_vol is not None:
            bar_vol = max(0.0, float(vol_close) - float(prev_vol))
        elif vol_close is not None and not bars:
            bar_vol = float(vol_close)
        b = FlowBar(
            time=key.strftime("%Y-%m-%d %H:%M:%S"),
            open=float(o),
            high=float(h),
            low=float(l),
            close=float(c),
            volume=float(bar_vol),
            n_ticks=float(n),
            tbq=float(tbq),
            tsq=float(tsq),
            oi=float(oi or 0.0),
            buy5=float(buy5),
            sell5=float(sell5),
            buy_px=buy_px,
            buy_qty=buy_qty,
            sell_px=sell_px,
            sell_qty=sell_qty,
        )
        b._vol_close = vol_close  # type: ignore[attr-defined]
        bars.append(b)
        o = h = l = c = None
        n = 0

    for row in rows:
        m = tick_metrics(row)
        if m["ltp"] is None:
            continue
        ts = parse_ts(row["received_at"] if not isinstance(row, dict) else row.get("received_at") or row.get("time"))
        key = floor_bar(ts, minutes)
        ltp = float(m["ltp"])
        msg = _msg_from_row(row)
        bp, bq, sp, sq = _book_levels(msg)
        if cur_key is None:
            cur_key = key
        if key != cur_key:
            flush(cur_key)
            cur_key = key
        if o is None:
            o = h = l = c = ltp
            n = 0
        h = max(h, ltp)
        l = min(l, ltp)
        c = ltp
        tbq = float(m["tbq"] or 0.0)
        tsq = float(m["tsq"] or 0.0)
        buy5 = float(sum(bq)) or float(m["buy5_sum"] or 0.0)
        sell5 = float(sum(sq)) or float(m["sell5_sum"] or 0.0)
        buy_px, buy_qty, sell_px, sell_qty = bp, bq, sp, sq
        oi = float(m["oi"] or 0.0)
        vol_close = m["volume"]
        n += 1
    if cur_key is not None:
        flush(cur_key)
    for b in bars:
        if hasattr(b, "_vol_close"):
            delattr(b, "_vol_close")
    return bars


def flow_bars_from_db(db: Path, minutes: int = 60) -> list[FlowBar]:
    return flow_bars_from_tick_rows(load_tick_rows(db), minutes)


def flow_bars_from_full_csv(path: Path, minutes: int = 1) -> list[FlowBar]:
    """Full 32-col tick CSV (export_full_ticks headers) → FlowBars."""
    rows: list[dict[str, Any]] = []
    with path.open(newline="", encoding="utf-8") as f:
        for raw in csv.DictReader(f):
            t = str(raw.get("time") or "").replace("T", " ")
            rows.append(
                {
                    "received_at": t,
                    "ltp": _f(raw, "ltp"),
                    "bp": _f(raw, "total_buy_quantity"),
                    "sp": _f(raw, "total_sell_quantity"),
                    "volume": _f(raw, "volume"),
                    "raw_json": json.dumps(
                        {
                            "last_traded_price": _f(raw, "ltp"),
                            "total_buy_quantity": _f(raw, "total_buy_quantity"),
                            "total_sell_quantity": _f(raw, "total_sell_quantity"),
                            "volume_trade_for_the_day": _f(raw, "volume"),
                            "open_interest": _f(raw, "open_interest"),
                            "last_traded_quantity": _f(raw, "last_traded_quantity"),
                            "best_5_buy_data": [
                                {
                                    "price": _f(raw, f"buy{i}_price"),
                                    "quantity": _f(raw, f"buy{i}_qty"),
                                }
                                for i in range(1, 6)
                            ],
                            "best_5_sell_data": [
                                {
                                    "price": _f(raw, f"sell{i}_price"),
                                    "quantity": _f(raw, f"sell{i}_qty"),
                                }
                                for i in range(1, 6)
                            ],
                        }
                    ),
                }
            )
    return flow_bars_from_tick_rows(rows, minutes)


def flow_bars_from_ohlcv_csv(path: Path) -> list[FlowBar]:
    """OHLCV (+ optional oi) candles — depth fields stay empty."""
    out: list[FlowBar] = []
    with path.open(newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            t = str(r["time"]).replace("T", " ")[:19]
            out.append(
                FlowBar(
                    time=t,
                    open=float(r["open"]),
                    high=float(r["high"]),
                    low=float(r["low"]),
                    close=float(r["close"]),
                    volume=_f(r, "volume", "vol"),
                    oi=_f(r, "oi", "open_interest"),
                )
            )
    out.sort(key=lambda b: b.time)
    return out


def flow_bars_from_upstox_json(path: Path) -> list[FlowBar]:
    """Upstox v3 historical candles: [time, o, h, l, c, volume, oi]."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    candles = raw.get("data", raw).get("candles") if isinstance(raw, dict) else raw
    if not isinstance(candles, list):
        raise ValueError(f"no candles in {path}")
    out: list[FlowBar] = []
    for c in candles:
        t = str(c[0]).replace("T", " ")[:19]
        oi = float(c[6]) if len(c) > 6 and c[6] is not None else 0.0
        out.append(
            FlowBar(
                time=t,
                open=float(c[1]),
                high=float(c[2]),
                low=float(c[3]),
                close=float(c[4]),
                volume=float(c[5] or 0.0),
                oi=oi,
            )
        )
    out.sort(key=lambda b: b.time)
    return out


def _multi_score(f_like: dict[str, float], *, green: bool, red: bool) -> float:
    """Operator-style signed stack. Roughly ±119 at full clips."""
    s = 0.0
    s += 18.0 * _clip(f_like["mom"] / 0.005)
    s += 12.0 if green else (-12.0 if red else 0.0)
    s += 14.0 * _clip((f_like["rvol"] - 1.0) / 1.5)
    s += 16.0 * _clip(f_like["imb"] / 0.40)
    s += 19.0 * _clip(f_like["depth_imb"] / 0.40)
    s += 10.0 * _clip(f_like["l1_imb"] / 0.40)
    s += 8.0 * _clip(f_like["micro_gap"] / 0.001)
    s += 7.0 * _clip(f_like["vwap_gap"] / 0.003)
    s += 11.0 * _clip(f_like["bid_depth_chg"])
    s += 4.0 * _clip(f_like["oi_pct"] / 0.01)
    return s


def build_flow_features(
    bars: list[FlowBar], params: FlowParams | None = None
) -> list[FlowFeat | None]:
    p = params or FlowParams()
    n = len(bars)
    n_lb = max(1, int(p.lookback))
    n_atr = max(1, int(p.atr_n))
    out: list[FlowFeat | None] = [None] * n
    if n < 2:
        return out

    trs = [0.0] * n
    for i in range(1, n):
        h, l, pc = bars[i].high, bars[i].low, bars[i - 1].close
        trs[i] = max(h - l, abs(h - pc), abs(l - pc))
    atrs = [0.0] * n
    atr_ready = [False] * n
    if n > n_atr:
        atrs[n_atr] = sum(trs[1 : n_atr + 1]) / float(n_atr)
        atr_ready[n_atr] = True
        for i in range(n_atr + 1, n):
            atrs[i] = (atrs[i - 1] * (n_atr - 1) + trs[i]) / float(n_atr)
            atr_ready[i] = True

    imb = [signed_imb(b.tbq, b.tsq) for b in bars]
    dimb = [signed_imb(b.buy5, b.sell5) for b in bars]
    spreads: list[float] = []
    for b in bars:
        if b.has_l1:
            spreads.append(max(0.0, b.sell1_px - b.buy1_px))
        else:
            spreads.append(0.0)

    cum_tpv = 0.0
    cum_vol = 0.0
    prev_day = ""
    for i, b in enumerate(bars):
        if b.day != prev_day:
            cum_tpv = 0.0
            cum_vol = 0.0
            prev_day = b.day
        typical = (b.high + b.low + b.close) / 3.0
        cum_tpv += typical * float(b.volume)
        cum_vol += float(b.volume)
        vwap = (cum_tpv / cum_vol) if cum_vol > 1e-12 else b.close
        if i < n_lb or not atr_ready[i]:
            continue
        window = bars[i - n_lb : i]
        avg_vol = sum(float(x.volume) for x in window) / float(n_lb)
        avg_range = sum((x.high - x.low) for x in window) / float(n_lb)
        rvol = (float(b.volume) / avg_vol) if avg_vol > 1e-12 else 0.0
        sma = sum(x.close for x in bars[i - n_lb + 1 : i + 1]) / float(n_lb)
        rng = float(b.high) - float(b.low)
        prev = bars[i - 1]
        mom = _pct_chg(b.close, prev.close)
        mom3 = _pct_chg(b.close, bars[i - 3].close) if i >= 3 else mom
        oi = float(b.oi or 0.0)
        poi = float(prev.oi or 0.0)
        oi_chg = oi - poi
        oi_pct = _pct_chg(oi, poi)
        oi_chg3 = oi - float(bars[i - 3].oi or 0.0) if i >= 3 else oi_chg
        tbq_chg = _pct_chg(b.tbq, prev.tbq)
        tsq_chg = _pct_chg(b.tsq, prev.tsq)
        bid_d = float(b.buy5)
        ask_d = float(b.sell5)
        bid_chg = _pct_chg(bid_d, prev.buy5)
        ask_chg = _pct_chg(ask_d, prev.sell5)
        l1_imb = signed_imb(b.buy1_qty, b.sell1_qty) if b.has_l1 else 0.0
        spr = spreads[i]
        avg_spr = 0.0
        spr_n = 0
        for j in range(i - n_lb, i):
            if spreads[j] > 1e-12:
                avg_spr += spreads[j]
                spr_n += 1
        if spr_n:
            avg_spr /= float(spr_n)
        spread_wide = bool(b.has_l1 and avg_spr > 1e-12 and spr > p.spread_wide_mult * avg_spr)
        mid = (b.buy1_px + b.sell1_px) / 2.0 if b.has_l1 else b.close
        spr_bps = (spr / mid * 1e4) if mid > 1e-12 and b.has_l1 else 0.0
        micro = (
            microprice(b.buy1_px, b.buy1_qty, b.sell1_px, b.sell1_qty)
            if b.has_l1
            else 0.0
        )
        micro_gap = ((micro - b.close) / b.close) if (b.has_l1 and b.close > 1e-12) else 0.0
        buy_w = weighted_px(b.buy_px, b.buy_qty)
        sell_w = weighted_px(b.sell_px, b.sell_qty)
        if buy_w > 1e-12 and sell_w > 1e-12:
            fair = (buy_w + sell_w) / 2.0
        else:
            fair = 0.0
        fair_gap = ((fair - b.close) / b.close) if (fair > 1e-12 and b.close > 1e-12) else 0.0
        ba_ratio = (bid_d / ask_d) if ask_d > 1e-12 else (2.0 if bid_d > 1e-12 else 1.0)
        vwap_gap = ((b.close - vwap) / vwap) if vwap > 1e-12 else 0.0
        green = b.close > b.open
        red = b.close < b.open
        sc = _multi_score(
            {
                "mom": mom,
                "rvol": rvol,
                "imb": imb[i],
                "depth_imb": dimb[i],
                "l1_imb": l1_imb,
                "micro_gap": micro_gap,
                "vwap_gap": vwap_gap,
                "bid_depth_chg": bid_chg,
                "oi_pct": oi_pct,
            },
            green=green,
            red=red,
        )
        out[i] = FlowFeat(
            rng=rng,
            body=abs(float(b.close) - float(b.open)),
            close_loc=_close_loc(b.high, b.low, b.close),
            green=green,
            red=red,
            avg_vol=avg_vol,
            rvol=rvol,
            avg_range=avg_range,
            prev_high_n=max(x.high for x in window),
            prev_low_n=min(x.low for x in window),
            atr=atrs[i],
            sma=sma,
            vwap=vwap,
            prev_close=prev.close,
            prev_vol=float(prev.volume),
            mom=mom,
            mom3=mom3,
            oi=oi,
            oi_chg=oi_chg,
            oi_pct=oi_pct,
            oi_chg3=oi_chg3,
            has_book=b.has_book,
            has_l1=b.has_l1,
            imb=imb[i],
            depth_imb=dimb[i],
            depth_accel=dimb[i] - dimb[i - 1],
            imb_accel=imb[i] - imb[i - 1],
            tbq_chg=tbq_chg,
            tsq_chg=tsq_chg,
            ba_ratio=ba_ratio,
            l1_imb=l1_imb,
            spread=spr,
            spread_bps=spr_bps,
            spread_wide=spread_wide,
            micro=micro,
            micro_gap=micro_gap,
            book_fair=fair,
            book_fair_gap=fair_gap,
            bid_depth=bid_d,
            ask_depth=ask_d,
            bid_depth_chg=bid_chg,
            ask_depth_chg=ask_chg,
            vwap_gap=vwap_gap,
            score=sc,
        )
    return out


def feat_vector(f: FlowFeat) -> dict[str, float]:
    """Named features for later logreg / RF / XGB (not trained in this lab)."""
    return {k: float(getattr(f, k)) for k in ML_FEATURE_NAMES}


def _vol_ok(f: FlowFeat, p: FlowParams, *, rvol: float | None = None) -> bool:
    return f.rvol >= (rvol if rvol is not None else p.rvol)


# --- recipes -----------------------------------------------------------------


def decide_depth_ltp(
    bars: list[FlowBar], feats: list[FlowFeat | None], i: int, p: FlowParams, st: dict[str, Any]
) -> tuple[str | None, str]:
    del bars, st
    f = feats[i]
    if f is None:
        return None, "warmup"
    if not f.has_book:
        return None, "no book"
    if f.depth_imb > p.depth_th and f.mom > 0 and f.green and _vol_ok(f, p):
        return "long", f"depth_ltp:long dimb={f.depth_imb:.2f}"
    if f.depth_imb < -p.depth_th and f.mom < 0 and f.red and _vol_ok(f, p):
        return "short", f"depth_ltp:short dimb={f.depth_imb:.2f}"
    return None, "depth_ltp:no"


def decide_flow_div(
    bars: list[FlowBar], feats: list[FlowFeat | None], i: int, p: FlowParams, st: dict[str, Any]
) -> tuple[str | None, str]:
    del bars, st
    if i < 3:
        return None, "warmup"
    f, f1, f2 = feats[i], feats[i - 1], feats[i - 2]
    if f is None or f1 is None or f2 is None or not f.has_book:
        return None, "warmup"
    if not _vol_ok(f, p):
        return None, "flow_div:low vol"
    # Bearish: 3-bar LTP up, imbalance and depth rolling over
    if f.mom3 > 0 and f.imb < f1.imb < f2.imb and f.depth_imb < f1.depth_imb:
        return "short", "flow_div:bearish price up / book down"
    if f.mom3 < 0 and f.imb > f1.imb > f2.imb and f.depth_imb > f1.depth_imb:
        return "long", "flow_div:bullish price down / book up"
    return None, "flow_div:no"


def decide_brk_flow(
    bars: list[FlowBar], feats: list[FlowFeat | None], i: int, p: FlowParams, st: dict[str, Any]
) -> tuple[str | None, str]:
    del st
    f = feats[i]
    if f is None:
        return None, "warmup"
    if not f.has_book:
        return None, "no book"
    b = bars[i]
    if (
        b.close > f.prev_high_n
        and f.rvol >= p.breakout_rvol
        and f.imb > p.imb_th
        and f.depth_imb > p.depth_th
        and f.mom > 0
    ):
        return "long", "brk_flow:long break + book"
    if (
        b.close < f.prev_low_n
        and f.rvol >= p.breakout_rvol
        and f.imb < -p.imb_th
        and f.depth_imb < -p.depth_th
        and f.mom < 0
    ):
        return "short", "brk_flow:short break + book"
    return None, "brk_flow:no"


def decide_absorb(
    bars: list[FlowBar], feats: list[FlowFeat | None], i: int, p: FlowParams, st: dict[str, Any]
) -> tuple[str | None, str]:
    del bars
    f = feats[i]
    if f is None or not f.has_book:
        st["arm"] = None
        return None, "warmup"
    arm = st.get("arm")
    if arm == "short_wait" and f.red and (f.tsq_chg >= 0.05 or f.depth_imb < 0):
        st["arm"] = None
        return "short", "absorb:buy absorbed then sell"
    if arm == "long_wait" and f.green and (f.tbq_chg >= 0.05 or f.depth_imb > 0):
        st["arm"] = None
        return "long", "absorb:sell absorbed then buy"
    if f.tbq_chg >= p.absorb_qty and abs(f.mom) <= p.absorb_mom:
        st["arm"] = "short_wait"
        return None, "absorb:arm short (buy qty up, LTP flat)"
    if f.tsq_chg >= p.absorb_qty and abs(f.mom) <= p.absorb_mom:
        st["arm"] = "long_wait"
        return None, "absorb:arm long (sell qty up, LTP flat)"
    return None, "absorb:no"


def decide_micro(
    bars: list[FlowBar], feats: list[FlowFeat | None], i: int, p: FlowParams, st: dict[str, Any]
) -> tuple[str | None, str]:
    del bars, st
    f = feats[i]
    if f is None:
        return None, "warmup"
    if not f.has_l1:
        return None, "no l1"
    if f.spread_wide:
        return None, "spread wide"
    th = p.micro_bps / 1e4
    if f.micro_gap > th and f.green and _vol_ok(f, p):
        return "long", f"micro:long LTP<micro gap={f.micro_gap:.5f}"
    if f.micro_gap < -th and f.red and _vol_ok(f, p):
        return "short", f"micro:short LTP>micro gap={f.micro_gap:.5f}"
    return None, "micro:no"


def decide_vwap_flow(
    bars: list[FlowBar], feats: list[FlowFeat | None], i: int, p: FlowParams, st: dict[str, Any]
) -> tuple[str | None, str]:
    del p, st
    f = feats[i]
    if f is None:
        return None, "warmup"
    b = bars[i]
    if i == 0 or bars[i - 1].day != b.day:
        return None, "vwap_flow:first bar"
    if not f.has_book:
        return None, "no book"
    if b.close > f.vwap and b.low <= f.vwap and f.green and f.imb > 0 and f.bid_depth_chg > 0:
        return "long", "vwap_flow:long pullback + bid support"
    if b.close < f.vwap and b.high >= f.vwap and f.red and f.imb < 0 and f.ask_depth_chg > 0:
        return "short", "vwap_flow:short pullback + ask pressure"
    return None, "vwap_flow:no"


def decide_liq_shock(
    bars: list[FlowBar], feats: list[FlowFeat | None], i: int, p: FlowParams, st: dict[str, Any]
) -> tuple[str | None, str]:
    del bars, st
    f = feats[i]
    if f is None or not f.has_book:
        return None, "warmup"
    if f.bid_depth_chg <= -p.shock and f.mom < 0 and _vol_ok(f, p) and f.ask_depth_chg >= 0:
        return "short", f"liq_shock:bid collapsed {f.bid_depth_chg:.2f}"
    if f.ask_depth_chg <= -p.shock and f.mom > 0 and _vol_ok(f, p) and f.bid_depth_chg >= 0:
        return "long", f"liq_shock:ask collapsed {f.ask_depth_chg:.2f}"
    return None, "liq_shock:no"


def decide_fail_brk(
    bars: list[FlowBar], feats: list[FlowFeat | None], i: int, p: FlowParams, st: dict[str, Any]
) -> tuple[str | None, str]:
    f = feats[i]
    if f is None:
        return None, "warmup"
    b = bars[i]
    broke = st.get("broke")
    level = float(st.get("level") or 0.0)
    if broke == "high" and b.close < level and f.has_book:
        if f.bid_depth_chg < 0 and f.ask_depth_chg > 0 and _vol_ok(f, p):
            st["broke"] = None
            return "short", "fail_brk:failed high + book flip"
        st["broke"] = None
    if broke == "low" and b.close > level and f.has_book:
        if f.ask_depth_chg < 0 and f.bid_depth_chg > 0 and _vol_ok(f, p):
            st["broke"] = None
            return "long", "fail_brk:failed low + book flip"
        st["broke"] = None
    if b.close > f.prev_high_n and f.rvol >= p.breakout_rvol:
        st["broke"] = "high"
        st["level"] = float(f.prev_high_n)
        return None, "fail_brk:armed high"
    if b.close < f.prev_low_n and f.rvol >= p.breakout_rvol:
        st["broke"] = "low"
        st["level"] = float(f.prev_low_n)
        return None, "fail_brk:armed low"
    return None, "fail_brk:no"


def decide_climax_vol(
    bars: list[FlowBar], feats: list[FlowFeat | None], i: int, p: FlowParams, st: dict[str, Any]
) -> tuple[str | None, str]:
    f = feats[i]
    if f is None:
        st["pending"] = None
        return None, "warmup"
    b = bars[i]
    pending = st.get("pending")
    if pending is not None:
        side, mid = pending
        st["pending"] = None
        if side == "long" and b.close > mid:
            return "long", "climax_vol:recover above midpoint"
        if side == "short" and b.close < mid:
            return "short", "climax_vol:reject below midpoint"
        return None, "climax_vol:no recover"
    large = f.rng >= p.climax_range_mult * f.avg_range and f.avg_range > 1e-12
    if large and f.rvol >= p.climax_rvol and f.red and f.close_loc <= (1.0 - p.close_loc):
        st["pending"] = ("long", (b.high + b.low) / 2.0)
        return None, "climax_vol:bearish setup"
    if large and f.rvol >= p.climax_rvol and f.green and f.close_loc >= p.close_loc:
        st["pending"] = ("short", (b.high + b.low) / 2.0)
        return None, "climax_vol:bullish setup"
    return None, "climax_vol:no"


def decide_candle(
    bars: list[FlowBar], feats: list[FlowFeat | None], i: int, p: FlowParams, st: dict[str, Any]
) -> tuple[str | None, str]:
    del bars, st
    f = feats[i]
    if f is None:
        return None, "warmup"
    if f.green and f.mom > 0 and f.close_loc >= p.close_loc:
        return "long", "candle:bullish close-high"
    if f.red and f.mom < 0 and f.close_loc <= (1.0 - p.close_loc):
        return "short", "candle:bearish close-low"
    return None, "candle:no"


def decide_imb_confirm(
    bars: list[FlowBar], feats: list[FlowFeat | None], i: int, p: FlowParams, st: dict[str, Any]
) -> tuple[str | None, str]:
    del bars, st
    f = feats[i]
    if f is None or not f.has_book:
        return None, "warmup"
    if f.imb > p.imb_th and f.mom > 0 and f.green and _vol_ok(f, p):
        return "long", f"imb_confirm:long imb={f.imb:.2f}"
    if f.imb < -p.imb_th and f.mom < 0 and f.red and _vol_ok(f, p):
        return "short", f"imb_confirm:short imb={f.imb:.2f}"
    return None, "imb_confirm:no"


def decide_ba_spread(
    bars: list[FlowBar], feats: list[FlowFeat | None], i: int, p: FlowParams, st: dict[str, Any]
) -> tuple[str | None, str]:
    del bars, st
    f = feats[i]
    if f is None or not f.has_l1:
        return None, "warmup"
    if f.spread_wide:
        return None, "spread wide"
    if f.ba_ratio >= p.ba_ratio_th and f.mom > 0 and f.green and _vol_ok(f, p):
        return "long", f"ba_spread:long ratio={f.ba_ratio:.2f}"
    if f.ba_ratio <= 1.0 / p.ba_ratio_th and f.mom < 0 and f.red and _vol_ok(f, p):
        return "short", f"ba_spread:short ratio={f.ba_ratio:.2f}"
    return None, "ba_spread:no"


def decide_depth_accel(
    bars: list[FlowBar], feats: list[FlowFeat | None], i: int, p: FlowParams, st: dict[str, Any]
) -> tuple[str | None, str]:
    del bars, st
    f = feats[i]
    if f is None or not f.has_book:
        return None, "warmup"
    if f.depth_accel >= p.accel_th and f.depth_imb > 0.10 and f.green and _vol_ok(f, p):
        return "long", f"depth_accel:long d={f.depth_accel:.2f}"
    if f.depth_accel <= -p.accel_th and f.depth_imb < -0.10 and f.red and _vol_ok(f, p):
        return "short", f"depth_accel:short d={f.depth_accel:.2f}"
    return None, "depth_accel:no"


def decide_tbq_div(
    bars: list[FlowBar], feats: list[FlowFeat | None], i: int, p: FlowParams, st: dict[str, Any]
) -> tuple[str | None, str]:
    del bars, st
    f = feats[i]
    if f is None or not f.has_book:
        return None, "warmup"
    # One-sided expansion with price follow-through
    if f.tbq_chg >= p.absorb_qty and f.tsq_chg <= 0.05 and f.mom > 0 and _vol_ok(f, p):
        return "long", "tbq_div:buy qty accelerates, sell does not"
    if f.tsq_chg >= p.absorb_qty and f.tbq_chg <= 0.05 and f.mom < 0 and _vol_ok(f, p):
        return "short", "tbq_div:sell qty accelerates, buy does not"
    return None, "tbq_div:no"


def decide_book_fair(
    bars: list[FlowBar], feats: list[FlowFeat | None], i: int, p: FlowParams, st: dict[str, Any]
) -> tuple[str | None, str]:
    del bars, st
    f = feats[i]
    if f is None or not f.has_l1:
        return None, "warmup"
    if f.spread_wide or abs(f.book_fair) <= 1e-12:
        return None, "book_fair:skip"
    if f.book_fair_gap > 0 and f.imb > 0 and f.green and _vol_ok(f, p):
        return "long", "book_fair:LTP below weighted book"
    if f.book_fair_gap < 0 and f.imb < 0 and f.red and _vol_ok(f, p):
        return "short", "book_fair:LTP above weighted book"
    return None, "book_fair:no"


def decide_score(
    bars: list[FlowBar], feats: list[FlowFeat | None], i: int, p: FlowParams, st: dict[str, Any]
) -> tuple[str | None, str]:
    del bars, st
    f = feats[i]
    if f is None:
        return None, "warmup"
    if f.score >= p.score_th:
        return "long", f"score:{f.score:.1f}"
    if f.score <= -p.score_th:
        return "short", f"score:{f.score:.1f}"
    return None, "score:no"


def decide_rvol_oi(
    bars: list[FlowBar], feats: list[FlowFeat | None], i: int, p: FlowParams, st: dict[str, Any]
) -> tuple[str | None, str]:
    del bars, st
    f = feats[i]
    if f is None:
        return None, "warmup"
    if f.rvol >= p.oi_rvol and f.oi_chg > 0 and f.green and f.mom > 0:
        return "long", "rvol_oi:long new OI + RVOL"
    if f.rvol >= p.oi_rvol and f.oi_chg > 0 and f.red and f.mom < 0:
        return "short", "rvol_oi:short new OI + RVOL"
    return None, "rvol_oi:no"


def decide_brk_oi(
    bars: list[FlowBar], feats: list[FlowFeat | None], i: int, p: FlowParams, st: dict[str, Any]
) -> tuple[str | None, str]:
    del st
    f = feats[i]
    if f is None:
        return None, "warmup"
    b = bars[i]
    if b.close > f.prev_high_n and f.rvol >= p.breakout_rvol and f.oi_chg > 0 and f.close_loc >= p.close_loc:
        return "long", "brk_oi:long break + OI up"
    if b.close < f.prev_low_n and f.rvol >= p.breakout_rvol and f.oi_chg > 0 and f.close_loc <= (1.0 - p.close_loc):
        return "short", "brk_oi:short break + OI up"
    return None, "brk_oi:no"


def decide_vwap_oi(
    bars: list[FlowBar], feats: list[FlowFeat | None], i: int, p: FlowParams, st: dict[str, Any]
) -> tuple[str | None, str]:
    del p, st
    f = feats[i]
    if f is None:
        return None, "warmup"
    b = bars[i]
    if i == 0 or bars[i - 1].day != b.day:
        return None, "vwap_oi:first bar"
    if b.close > f.vwap and b.low <= f.vwap and f.green and f.oi_chg > 0:
        return "long", "vwap_oi:long VWAP + OI up"
    if b.close < f.vwap and b.high >= f.vwap and f.red and f.oi_chg > 0:
        return "short", "vwap_oi:short VWAP + OI up"
    return None, "vwap_oi:no"


def decide_oi_div(
    bars: list[FlowBar], feats: list[FlowFeat | None], i: int, p: FlowParams, st: dict[str, Any]
) -> tuple[str | None, str]:
    del bars, st
    if i < 3:
        return None, "warmup"
    f = feats[i]
    if f is None:
        return None, "warmup"
    if f.mom3 > 0 and f.oi_chg3 < 0 and _vol_ok(f, p):
        return "short", "oi_div:price up OI down"
    if f.mom3 < 0 and f.oi_chg3 < 0 and _vol_ok(f, p):
        # falling price *and* falling OI = longs covering / shorts covering mixed;
        # require bid support invention: treat as long only if candle turns green
        if f.green:
            return "long", "oi_div:price down OI down, bounce candle"
        return None, "oi_div:wait bounce"
    return None, "oi_div:no"


def decide_climax_oi(
    bars: list[FlowBar], feats: list[FlowFeat | None], i: int, p: FlowParams, st: dict[str, Any]
) -> tuple[str | None, str]:
    f = feats[i]
    if f is None:
        st["pending"] = None
        return None, "warmup"
    b = bars[i]
    pending = st.get("pending")
    if pending is not None:
        side, mid = pending
        st["pending"] = None
        if side == "long" and b.close > mid:
            return "long", "climax_oi:recover"
        if side == "short" and b.close < mid:
            return "short", "climax_oi:reject"
        return None, "climax_oi:no recover"
    large = f.rng >= p.climax_range_mult * f.avg_range and f.avg_range > 1e-12
    oi_big = abs(f.oi_pct) >= 0.005 or abs(f.oi_chg) > 0
    if large and f.rvol >= p.climax_rvol and oi_big and f.red and f.close_loc <= (1.0 - p.close_loc):
        st["pending"] = ("long", (b.high + b.low) / 2.0)
        return None, "climax_oi:bearish setup"
    if large and f.rvol >= p.climax_rvol and oi_big and f.green and f.close_loc >= p.close_loc:
        st["pending"] = ("short", (b.high + b.low) / 2.0)
        return None, "climax_oi:bullish setup"
    return None, "climax_oi:no"


def decide_vwap_rvol_oi(
    bars: list[FlowBar], feats: list[FlowFeat | None], i: int, p: FlowParams, st: dict[str, Any]
) -> tuple[str | None, str]:
    del st
    f = feats[i]
    if f is None:
        return None, "warmup"
    b = bars[i]
    if (
        b.close > f.vwap
        and f.rvol >= p.oi_rvol
        and f.oi_chg > 0
        and f.green
        and f.mom > 0
    ):
        return "long", "vwap_rvol_oi:long stack"
    if (
        b.close < f.vwap
        and f.rvol >= p.oi_rvol
        and f.oi_chg > 0
        and f.red
        and f.mom < 0
    ):
        return "short", "vwap_rvol_oi:short stack"
    return None, "vwap_rvol_oi:no"


def decide_absorb_oi(
    bars: list[FlowBar], feats: list[FlowFeat | None], i: int, p: FlowParams, st: dict[str, Any]
) -> tuple[str | None, str]:
    f = feats[i]
    if f is None:
        st["arm"] = None
        return None, "warmup"
    b = bars[i]
    small = f.rng > 1e-12 and f.body <= 0.35 * f.rng
    arm = st.get("arm")
    if arm == "short_wait" and f.red:
        st["arm"] = None
        return "short", "absorb_oi:fade after absorbed high-vol doji"
    if arm == "long_wait" and f.green:
        st["arm"] = None
        return "long", "absorb_oi:fade after absorbed high-vol doji"
    if f.rvol >= 1.8 and small and f.oi_chg > 0:
        st["arm"] = "short_wait" if b.close >= b.open else "long_wait"
        return None, "absorb_oi:armed"
    return None, "absorb_oi:no"


DECIDERS: dict[str, Callable[..., tuple[str | None, str]]] = {
    "depth_ltp": decide_depth_ltp,
    "flow_div": decide_flow_div,
    "brk_flow": decide_brk_flow,
    "absorb": decide_absorb,
    "micro": decide_micro,
    "vwap_flow": decide_vwap_flow,
    "liq_shock": decide_liq_shock,
    "fail_brk": decide_fail_brk,
    "climax_vol": decide_climax_vol,
    "candle": decide_candle,
    "imb_confirm": decide_imb_confirm,
    "ba_spread": decide_ba_spread,
    "depth_accel": decide_depth_accel,
    "tbq_div": decide_tbq_div,
    "book_fair": decide_book_fair,
    "score": decide_score,
    "rvol_oi": decide_rvol_oi,
    "brk_oi": decide_brk_oi,
    "vwap_oi": decide_vwap_oi,
    "oi_div": decide_oi_div,
    "climax_oi": decide_climax_oi,
    "vwap_rvol_oi": decide_vwap_rvol_oi,
    "absorb_oi": decide_absorb_oi,
}


def _is_last_of_day(bars: list[FlowBar], i: int) -> bool:
    if i >= len(bars) - 1:
        return True
    return bars[i + 1].day != bars[i].day


def simulate_flow(
    bars: list[FlowBar],
    strategy: str,
    *,
    exit_mode: str = EXIT_FLIP,
    tf: str | None = None,
    lots: float = 100.0,
    fees: bool = True,
    flatten_eod: bool = True,
    params: FlowParams | None = None,
    entry_days: set[str] | None = None,
    charge_cfg: ChargeConfig | None = None,
) -> Any:
    if strategy not in DECIDERS:
        raise ValueError(f"unknown factory recipe {strategy!r}")
    if exit_mode not in {EXIT_FLIP, EXIT_ATR}:
        raise ValueError(f"unknown exit_mode {exit_mode!r}")
    p = params or FlowParams()
    feats = build_flow_features(bars, p)
    decide = DECIDERS[strategy]
    st: dict[str, Any] = {}
    cfg = charge_cfg or make_charge_cfg(fees=fees, lots=lots)
    book = tf or f"{strategy}:{exit_mode}"
    trades: list[Any] = []
    side: str | None = None
    entry_px = 0.0
    entry_time = ""
    entry_i = -1
    entry_atr = 0.0
    ext_px = 0.0

    def close_trade(exit_c: FlowBar) -> None:
        nonlocal side, entry_px, entry_time
        assert side is not None
        trades.append(
            _close_leg(
                tf=book,
                side=side,
                entry_time=entry_time,
                entry_px=entry_px,
                exit_c=exit_c,
                cfg=cfg,
            )
        )
        side = None

    def open_trade(cur: FlowBar, want: str, atr: float, i: int) -> None:
        nonlocal side, entry_px, entry_time, entry_i, entry_atr, ext_px
        side = "LONG" if want == "long" else "SHORT"
        entry_px = cur.close
        entry_time = cur.time
        entry_i = i
        entry_atr = float(atr)
        ext_px = cur.high if side == "LONG" else cur.low

    for i, cur in enumerate(bars):
        f = feats[i]
        want, _why = decide(bars, feats, i, p, st) if f is not None else (None, "warmup")
        scoring = entry_days is None or cur.day in entry_days
        if not scoring:
            if side is not None:
                close_trade(cur)
            continue
        last = bool(flatten_eod) and _is_last_of_day(bars, i)
        if last:
            if side is not None:
                close_trade(cur)
            continue
        if side is not None and exit_mode == EXIT_ATR and i > entry_i and f is not None:
            ext_px = max(ext_px, cur.high) if side == "LONG" else min(ext_px, cur.low)
            atr = entry_atr
            if atr > 1e-12:
                if side == "LONG":
                    tp = entry_px + p.tp_atr * atr
                    trail = ext_px - p.trail_atr * atr
                    if cur.close >= tp or cur.close <= trail:
                        close_trade(cur)
                else:
                    tp = entry_px - p.tp_atr * atr
                    trail = ext_px + p.trail_atr * atr
                    if cur.close <= tp or cur.close >= trail:
                        close_trade(cur)
        atr_now = float(f.atr) if f is not None else 0.0
        if side == "LONG":
            if want == "short":
                close_trade(cur)
                open_trade(cur, "short", atr_now, i)
            continue
        if side == "SHORT":
            if want == "long":
                close_trade(cur)
                open_trade(cur, "long", atr_now, i)
            continue
        if want == "long":
            open_trade(cur, "long", atr_now, i)
        elif want == "short":
            open_trade(cur, "short", atr_now, i)

    if side is not None and bars:
        close_trade(bars[-1])
    if entry_days is not None:
        trades = [t for t in trades if t.entry_time[:10] in entry_days]
    return _tf_result_from_trades(book, len(bars), trades)


def walk_forward_flow(
    bars: list[FlowBar],
    strategy: str,
    *,
    exit_mode: str = EXIT_FLIP,
    n_folds: int = 3,
    **kwargs: Any,
) -> tuple[list[LabMetrics], int, int]:
    kwargs = {k: v for k, v in kwargs.items() if k not in {"tf", "entry_days"}}
    folds = day_folds(bars, n_folds=n_folds)
    rows: list[LabMetrics] = []
    for i, days in enumerate(folds):
        if not days:
            continue
        last = max(days)
        window = [b for b in bars if b.day <= last]
        res = simulate_flow(
            window,
            strategy,
            exit_mode=exit_mode,
            entry_days=days,
            tf=f"{strategy}:{exit_mode}:wf{i + 1}",
            **kwargs,
        )
        rows.append(
            score_result(
                res,
                name=f"{strategy}:{exit_mode}:wf{i + 1}",
                exit_mode=exit_mode,
            )
        )
    wins = sum(1 for r in rows if r.after_charges > 0)
    return rows, wins, len(rows)


def run_factory_book(
    bars: list[FlowBar],
    strategy: str,
    *,
    exit_mode: str = EXIT_FLIP,
    family: str = "",
    n_folds: int = 3,
    **kwargs: Any,
) -> LabMetrics:
    sim_kwargs = {k: v for k, v in kwargs.items() if k != "n_folds"}
    res = simulate_flow(bars, strategy, exit_mode=exit_mode, **sim_kwargs)
    wf_kwargs = {k: v for k, v in sim_kwargs.items() if k not in {"tf", "entry_days"}}
    wf_rows, wf_wins, wf_folds = walk_forward_flow(
        bars, strategy, exit_mode=exit_mode, n_folds=n_folds, **wf_kwargs
    )
    fam = family or next((r.family for r in RECIPES if r.name == strategy), "")
    metrics = score_result(
        res,
        name=strategy,
        family=fam,
        exit_mode=exit_mode,
        wf_wins=wf_wins,
        wf_folds=wf_folds,
    )
    metrics.wf_rows = wf_rows
    return metrics


def write_ohlcv_oi_csv(bars: list[FlowBar], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["time", "open", "high", "low", "close", "volume", "oi"])
        for b in bars:
            w.writerow([b.time, b.open, b.high, b.low, b.close, b.volume, b.oi])
