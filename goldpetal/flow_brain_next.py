"""FLOW_BRAIN 'what happens next' lab. Research only. Not live.

Hand-written pressure states on LTP+TBQ+TSQ are not an AI. This module
asks the tape whether those states precede small vs large moves *after
Angel charges* (tax excluded) at 5s / 30s / 1m / 5m.

If they do not, stacking GBDT / random forest / a sequence model /
Transformer on the same labels will not save it. ENABLE_FLOW_BRAIN
stays false. Stay DRY_RUN. Not S7_HOURLY. Not S16 paper.

What this is:
  - probability surface (up%, large-move%, fee-cover%)
  - training labels for the next move
  - rolling 100-tick sequence *features* (tabular, not a Transformer)
  - sqlite learn log of decision + outcome
  - walk-forward of the surface, then optional logreg / RF / GB
  - bid/ask, depth, OI, VWAP, 1m candle-relation, market-mood as
    columns on each label (when the tick carries them)
  - expected-net ranking + Profit Guardian status per cell

What this is not:
  - a live model, a Profit Guardian UI, strategy discovery replacing
    research_factory, or 'trade every profitable situation'
  - auto-ENABLE / auto-approve
"""

from __future__ import annotations

import json
import sqlite3
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Literal

import numpy as np

from backtest_hhhl_candles import make_charge_cfg
from charges import ChargeConfig, apply_charges_and_tax
from edge import fee_break_even_points
from flow_brain import (
    ABSORB_BUY,
    ABSORB_SELL,
    BEAR_CONT,
    BEAR_WEAK,
    BOOK,
    BULL_CONT,
    BULL_WEAK,
    MIXED,
    SCALE_RANK,
    FlowBrain,
    FlowState,
    move_scale,
    scratchy_then_trend_samples,
)
from market_mood import MoodDetector, MoodState
from profit_guardian import classify_status, expectancy, max_drawdown, profit_factor
from s9_bar_ml import ModelKind, make_estimator

LAB_NAME = "FLOW_BRAIN_NEXT"
HORIZONS: tuple[int, ...] = (5, 30, 60, 300)
HORIZON_NAME: dict[int, str] = {5: "5s", 30: "30s", 60: "1m", 300: "5m"}
STATES: tuple[str, ...] = (
    BULL_CONT,
    BULL_WEAK,
    BEAR_CONT,
    BEAR_WEAK,
    ABSORB_BUY,
    ABSORB_SELL,
    MIXED,
)
MOODS: tuple[str, ...] = (
    "UNKNOWN",
    "QUIET",
    "HEAT",
    "COOL",
    "BURST",
    "FALL_START",
    "RISE_START",
)
REGIMES: tuple[str, ...] = (
    "UNKNOWN",
    "CALM",
    "RANGE",
    "ACCUMULATION",
    "BREAKOUT",
    "BREAKDOWN",
    "TRENDING",
    "EXHAUSTION",
    "COOLDOWN",
    "BURST",
)
SEQ_TICKS = 100
MIN_CELL_N = 40
MIN_EDGE_N = 80
MAX_FIT_ROWS = 40_000
TRANSFORMER_NOTE = (
    "No Transformer. Tabular logreg/RF/GB on these labels first. "
    "If the surface is flat after charges, a Transformer on the same "
    "tape is the same coin flip."
)
NOT_EVERY_SITUATION = (
    "Do not 'trade every profitable situation'. A green cell is a "
    "hypothesis: sized n + walk-forward after-charges, then you approve. "
    "ENABLE_FLOW_BRAIN stays false."
)

Side = Literal["long", "short"]


@dataclass
class RichTick:
    dt: datetime
    t: float
    ltp: float
    tbq: float
    tsq: float
    buy5: float = 0.0
    sell5: float = 0.0
    buy1_px: float = 0.0
    sell1_px: float = 0.0
    oi: float = 0.0
    ltq: float = 0.0
    volume: float = 0.0
    vwap_px: float = 0.0


@dataclass
class NextLabel:
    t: float
    ts: str
    state: str
    expanding: bool
    decaying: bool
    want: str
    scale: str
    ltp: float
    atr: float
    imb: float
    d_ltp_5: float
    net: float
    imb_pct: float
    s19: int
    s16: int
    s20: int
    mood: str
    regime: str
    mood_dir: str
    heat: float
    depth_imb: float
    spread: float
    vwap_gap: float
    oi: float
    oi_chg: float
    seq_ret_100: float
    seq_up_frac: float
    seq_bull_frac: float
    fwd: dict[int, float | None] = field(default_factory=dict)


def implied_cont(state: str | NextLabel) -> Side | None:
    name = state.state if isinstance(state, NextLabel) else state
    if name == BULL_CONT:
        return "long"
    if name == BEAR_CONT:
        return "short"
    return None


def implied_fade(state: str | NextLabel) -> Side | None:
    name = state.state if isinstance(state, NextLabel) else state
    if name in {BULL_WEAK, ABSORB_BUY}:
        return "short"
    if name in {BEAR_WEAK, ABSORB_SELL}:
        return "long"
    return None


def _side_i(want: str | None) -> int:
    if want == "long":
        return 1
    if want == "short":
        return -1
    return 0


def _onehot(name: str, universe: tuple[str, ...]) -> list[float]:
    return [1.0 if name == u else 0.0 for u in universe]


def _depth_imb(buy5: float, sell5: float) -> float:
    den = abs(buy5) + abs(sell5)
    if den <= 1e-9:
        return 0.0
    return (buy5 - sell5) / den


def rich_from_quads(
    samples: Iterable[tuple[datetime, float, float, float]],
) -> list[RichTick]:
    out: list[RichTick] = []
    for dt, ltp, tbq, tsq in samples:
        out.append(
            RichTick(
                dt=dt,
                t=dt.timestamp(),
                ltp=float(ltp),
                tbq=float(tbq),
                tsq=float(tsq),
            )
        )
    return out


def rich_from_tick_rows(rows: list[Any]) -> list[RichTick]:
    from export_full_ticks import _depth_side
    from mtf_bars import _msg, parse_ts, tick_metrics

    out: list[RichTick] = []
    cum_pv = 0.0
    cum_v = 0.0
    last_day: str | None = None
    for row in rows:
        m = tick_metrics(row)
        ltp = m.get("ltp")
        if ltp is None:
            continue
        dt = parse_ts(row["received_at"])
        day = dt.strftime("%Y-%m-%d")
        if last_day is not None and day != last_day:
            cum_pv = 0.0
            cum_v = 0.0
        last_day = day
        msg = _msg(row)
        buy = _depth_side(msg, "buy")
        sell = _depth_side(msg, "sell")
        buy1 = float(buy[0][0] or 0.0) if buy else 0.0
        sell1 = float(sell[0][0] or 0.0) if sell else 0.0
        ltq = float(m.get("ltq") or 0.0)
        qty = ltq if ltq > 0 else 1.0
        cum_pv += float(ltp) * qty
        cum_v += qty
        vwap = (cum_pv / cum_v) if cum_v > 0 else float(ltp)
        out.append(
            RichTick(
                dt=dt,
                t=dt.timestamp(),
                ltp=float(ltp),
                tbq=float(m.get("tbq") or 0.0),
                tsq=float(m.get("tsq") or 0.0),
                buy5=float(m.get("buy5_sum") or 0.0),
                sell5=float(m.get("sell5_sum") or 0.0),
                buy1_px=buy1,
                sell1_px=sell1,
                oi=float(m.get("oi") or 0.0),
                ltq=ltq,
                volume=float(m.get("volume") or 0.0),
                vwap_px=float(vwap),
            )
        )
    return out


def _fwd_index(
    times: np.ndarray, t0: float, horizon: int
) -> int | None:
    j = int(np.searchsorted(times, t0 + float(horizon), side="left"))
    if j >= len(times):
        return None
    late = float(times[j]) - (t0 + float(horizon))
    if late > max(3.0, min(30.0, float(horizon) * 0.5)):
        return None
    if float(times[j]) - t0 > float(horizon) + 180.0:
        return None
    return j


def _after_charges(
    *,
    side: Side,
    entry: float,
    exit_px: float,
    cfg: ChargeConfig,
) -> tuple[float, float, float]:
    if side == "long":
        pts = exit_px - entry
        order = "BUY"
    else:
        pts = entry - exit_px
        order = "SELL"
    settled = apply_charges_and_tax(
        pts, cfg, side=order, entry_price=entry, exit_price=exit_px
    )
    return (
        float(pts),
        float(settled["gross_pnl"]),
        float(settled["pnl_after_charges"]),
    )


class _Seq:
    def __init__(self, n: int = SEQ_TICKS) -> None:
        self.px: deque[float] = deque(maxlen=n)
        self.up: deque[int] = deque(maxlen=n)
        self.states: deque[str] = deque(maxlen=n)

    def push_tick(self, ltp: float) -> None:
        if self.px:
            self.up.append(1 if ltp > self.px[-1] else 0)
        self.px.append(float(ltp))

    def push_state(self, state: str) -> None:
        self.states.append(state)

    def feats(self) -> tuple[float, float, float]:
        if len(self.px) < 8:
            return 0.0, 0.5, 0.0
        ret = float(self.px[-1] - self.px[0])
        upf = (sum(self.up) / len(self.up)) if self.up else 0.5
        if self.states:
            bull = sum(1 for s in self.states if s == BULL_CONT) / len(self.states)
        else:
            bull = 0.0
        return ret, float(upf), float(bull)


def label_tape(
    ticks: list[RichTick],
    *,
    session_filter: bool = True,
    market_open: str = "09:00",
    market_close: str = "23:30",
    decide_every_s: float = 1.0,
    with_mood: bool = True,
) -> list[NextLabel]:
    """Walk ticks → FLOW_BRAIN state + forward pts at each horizon."""
    if not ticks:
        return []
    from flow_brain import _in_session

    times = np.array([x.t for x in ticks], dtype=float)
    ltps = np.array([x.ltp for x in ticks], dtype=float)
    eng = FlowBrain(decide_every_s=decide_every_s)
    mood = MoodDetector(window=80) if with_mood else None
    seq = _Seq()
    last_oi = 0.0
    labels: list[NextLabel] = []
    for tick in ticks:
        seq.push_tick(tick.ltp)
        if mood is not None:
            mood.update(tick.ltp, tick.tbq, tick.tsq)
        if session_filter and not _in_session(tick.dt, market_open, market_close):
            eng.buf.clear()
            continue
        snap = eng.push(tick.t, tick.ltp, tick.tbq, tick.tsq)
        if snap is None:
            continue
        seq.push_state(snap.state)
        seq_ret, seq_up, seq_bull = seq.feats()
        st_mood: MoodState | None = mood.last if mood is not None else None
        oi_chg = (tick.oi - last_oi) if tick.oi and last_oi else 0.0
        if tick.oi:
            last_oi = tick.oi
        spread = 0.0
        if tick.buy1_px > 0 and tick.sell1_px > 0:
            spread = max(0.0, tick.sell1_px - tick.buy1_px)
        vwap = tick.vwap_px if tick.vwap_px > 0 else tick.ltp
        fwd: dict[int, float | None] = {}
        for hz in HORIZONS:
            j = _fwd_index(times, tick.t, hz)
            if j is None:
                fwd[hz] = None
            else:
                fwd[hz] = float(ltps[j] - tick.ltp)
        labels.append(
            NextLabel(
                t=tick.t,
                ts=tick.dt.strftime("%Y-%m-%d %H:%M:%S"),
                state=snap.state,
                expanding=bool(snap.expanding),
                decaying=bool(snap.decaying),
                want=str(snap.want or ""),
                scale=str(snap.scale),
                ltp=float(snap.ltp),
                atr=float(snap.atr or 0.0),
                imb=float(snap.flow_imb_5s),
                d_ltp_5=float(snap.d_ltp_5s),
                net=float(snap.net),
                imb_pct=float(snap.imb_pct),
                s19=_side_i(snap.s19_1m),
                s16=_side_i(snap.s16_1m),
                s20=_side_i(snap.s20_1m),
                mood=str(st_mood.mood if st_mood else "UNKNOWN"),
                regime=str(st_mood.regime if st_mood else "UNKNOWN"),
                mood_dir=str(st_mood.direction if st_mood else "flat"),
                heat=float(st_mood.heat if st_mood else 0.0),
                depth_imb=_depth_imb(tick.buy5, tick.sell5),
                spread=spread,
                vwap_gap=float(tick.ltp - vwap),
                oi=float(tick.oi or 0.0),
                oi_chg=float(oi_chg),
                seq_ret_100=seq_ret,
                seq_up_frac=seq_up,
                seq_bull_frac=seq_bull,
                fwd=fwd,
            )
        )
    return labels


def feature_names() -> list[str]:
    names = [f"st_{s}" for s in STATES]
    names += ["expanding", "decaying", "want", "scale", "atr", "imb", "d_ltp_5"]
    names += ["net_sign", "imb_pct", "s19", "s16", "s20"]
    names += [f"mood_{m}" for m in MOODS]
    names += [f"rg_{r}" for r in REGIMES]
    names += [
        "heat",
        "depth_imb",
        "spread",
        "vwap_gap",
        "oi_chg",
        "seq_ret_100",
        "seq_up_frac",
        "seq_bull_frac",
    ]
    return names


def label_features(row: NextLabel) -> list[float]:
    x = _onehot(row.state, STATES)
    x += [
        1.0 if row.expanding else 0.0,
        1.0 if row.decaying else 0.0,
        float(_side_i(row.want or None)),
        float(SCALE_RANK.get(row.scale, 0)),
        float(row.atr),
        float(row.imb),
        float(row.d_ltp_5),
        1.0 if row.net > 0 else (-1.0 if row.net < 0 else 0.0),
        float(row.imb_pct),
        float(row.s19),
        float(row.s16),
        float(row.s20),
    ]
    x += _onehot(row.mood, MOODS)
    x += _onehot(row.regime, REGIMES)
    x += [
        float(row.heat),
        float(row.depth_imb),
        float(row.spread),
        float(row.vwap_gap),
        float(row.oi_chg),
        float(row.seq_ret_100),
        float(row.seq_up_frac),
        float(row.seq_bull_frac),
    ]
    return x


def _empty_cell() -> dict[str, Any]:
    return {
        "n": 0,
        "n_up": 0,
        "n_large": 0,
        "n_cover": 0,
        "sum_fwd": 0.0,
        "sum_abs": 0.0,
        "n_side": 0,
        "n_side_win": 0,
        "sum_signed": 0.0,
        "after": 0.0,
        "gross": 0.0,
        "fees": 0.0,
        "pnls": [],
    }


def _bump_pred(
    cell: dict[str, Any],
    *,
    fwd: float,
    atr: float,
    fee_be: float,
) -> None:
    cell["n"] += 1
    cell["sum_fwd"] += fwd
    cell["sum_abs"] += abs(fwd)
    if fwd > 0:
        cell["n_up"] += 1
    sc = move_scale(fwd, atr if atr > 1e-9 else 10.0)
    if sc in {"large", "extreme"}:
        cell["n_large"] += 1
    if abs(fwd) >= fee_be:
        cell["n_cover"] += 1


def surface_cells(
    labels: list[NextLabel],
    *,
    horizon: int,
    lots: float,
    fees: bool,
    side_of: Callable[[NextLabel], Side | None],
    expanding_only: bool = False,
) -> dict[str, dict[str, Any]]:
    """Predictive counts for every snapshot + non-overlap after-charges book."""
    cfg = make_charge_cfg(fees=fees, lots=lots)
    fee_be = 0.0
    if labels:
        fee_be = fee_break_even_points(
            labels[0].ltp, force_fees=bool(fees), lots=lots
        )
    cells: dict[str, dict[str, Any]] = {}
    last_exit: dict[str, float] = {}
    for row in labels:
        if expanding_only and not row.expanding:
            continue
        pts = row.fwd.get(horizon)
        if pts is None:
            continue
        cell = cells.setdefault(row.state, _empty_cell())
        _bump_pred(cell, fwd=float(pts), atr=row.atr, fee_be=fee_be)
        side = side_of(row)
        if side is None:
            continue
        until = last_exit.get(row.state, -1e18)
        if row.t < until:
            continue
        exit_px = row.ltp + float(pts)
        signed, gross, after = _after_charges(
            side=side, entry=row.ltp, exit_px=exit_px, cfg=cfg
        )
        cell["n_side"] += 1
        cell["sum_signed"] += signed
        cell["gross"] += gross
        cell["after"] += after
        cell["fees"] += gross - after
        cell["pnls"].append(after)
        if after > 0:
            cell["n_side_win"] += 1
        last_exit[row.state] = row.t + float(horizon)
    for cell in cells.values():
        n = max(1, int(cell["n"]))
        ns = int(cell["n_side"])
        cell["up_pct"] = 100.0 * cell["n_up"] / n
        cell["large_pct"] = 100.0 * cell["n_large"] / n
        cell["cover_pct"] = 100.0 * cell["n_cover"] / n
        cell["mean_fwd"] = cell["sum_fwd"] / n
        cell["mean_abs"] = cell["sum_abs"] / n
        cell["win_pct"] = (100.0 * cell["n_side_win"] / ns) if ns else 0.0
        cell["fee_be"] = fee_be
        pnls = list(cell["pnls"])
        cell["expectancy"] = expectancy(pnls)
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        cell["pf"] = profit_factor(wins, losses)
        dd, peak = max_drawdown(pnls)
        cell["status"] = classify_status(
            n=ns,
            pf=cell["pf"],
            recent_pf=cell["pf"],
            hist_pf=cell["pf"],
            dd=dd,
            peak=peak,
        )
    return cells


def walk_forward_agree(
    labels: list[NextLabel],
    *,
    horizon: int,
    lots: float,
    fees: bool,
    side_of: Callable[[NextLabel], Side | None],
    n_folds: int = 3,
) -> dict[str, dict[str, Any]]:
    """Expanding-window walk-forward: train after>0 and test after>0 per state."""
    if len(labels) < 30:
        return {}
    n = len(labels)
    n_folds = max(2, int(n_folds))
    step = max(8, n // n_folds)
    bounds = [0]
    for i in range(1, n_folds):
        bounds.append(min(n, step * i))
    bounds.append(n)
    tally: dict[str, dict[str, Any]] = {}
    for i in range(1, len(bounds) - 1):
        train = labels[: bounds[i]]
        test = labels[bounds[i] : bounds[i + 1]]
        if len(train) < 12 or len(test) < 8:
            continue
        tr = surface_cells(
            train, horizon=horizon, lots=lots, fees=fees, side_of=side_of
        )
        te = surface_cells(
            test, horizon=horizon, lots=lots, fees=fees, side_of=side_of
        )
        for state in STATES:
            row = tally.setdefault(
                state,
                {"folds": 0, "wins": 0, "train_after": 0.0, "test_after": 0.0},
            )
            a = tr.get(state)
            b = te.get(state)
            if not a or not b:
                continue
            if a["n_side"] < 4 or b["n_side"] < 3:
                continue
            row["folds"] += 1
            row["train_after"] += float(a["after"])
            row["test_after"] += float(b["after"])
            if a["after"] > 0 and b["after"] > 0:
                row["wins"] += 1
    for row in tally.values():
        f = max(1, int(row["folds"]))
        row["stability"] = f"{row['wins']}/{row['folds']}"
        row["train_after"] /= f
        row["test_after"] /= f
        row["agree"] = int(row["wins"]) >= 1 and int(row["folds"]) >= 1
    return tally


def _usable(
    labels: list[NextLabel], horizon: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[NextLabel]]:
    xs: list[list[float]] = []
    y_up: list[int] = []
    y_large: list[int] = []
    kept: list[NextLabel] = []
    for row in labels:
        pts = row.fwd.get(horizon)
        if pts is None:
            continue
        xs.append(label_features(row))
        y_up.append(1 if float(pts) > 0 else 0)
        sc = move_scale(float(pts), row.atr if row.atr > 1e-9 else 10.0)
        y_large.append(1 if sc in {"large", "extreme"} else 0)
        kept.append(row)
    if not xs:
        return (
            np.zeros((0, len(feature_names()))),
            np.zeros((0,), dtype=int),
            np.zeros((0,), dtype=int),
            [],
        )
    return np.asarray(xs, dtype=float), np.asarray(y_up), np.asarray(y_large), kept


def _fit_cap(n: int) -> np.ndarray:
    if n <= MAX_FIT_ROWS:
        return np.arange(n)
    step = n / float(MAX_FIT_ROWS)
    return np.unique(np.clip((np.arange(MAX_FIT_ROWS) * step).astype(int), 0, n - 1))


def train_models(
    labels: list[NextLabel],
    *,
    horizon: int = 60,
    lots: float = 100.0,
    fees: bool = True,
    kinds: tuple[ModelKind, ...] = ("logreg", "random_forest", "grad_boost"),
    train_frac: float = 0.7,
    p_long: float = 0.58,
    p_short: float = 0.42,
) -> list[dict[str, Any]]:
    """Chronological train → OOS direction AUC + non-overlap after-charges book."""
    from sklearn.metrics import roc_auc_score

    X, y_up, y_large, kept = _usable(labels, horizon)
    out: list[dict[str, Any]] = []
    if len(kept) < 40 or int(np.unique(y_up).size) < 2:
        return [
            {
                "kind": "none",
                "horizon": horizon,
                "note": f"need ≥40 mixed labels, have {len(kept)}",
                "auc_up": None,
                "auc_large": None,
                "after": 0.0,
                "trades": 0,
                "win_pct": 0.0,
            }
        ]
    split = max(20, int(len(kept) * train_frac))
    split = min(split, len(kept) - 10)
    cfg = make_charge_cfg(fees=fees, lots=lots)
    for kind in kinds:
        try:
            est_up = make_estimator(kind)
            if hasattr(est_up, "n_estimators") and int(est_up.n_estimators) > 80:
                est_up.n_estimators = 80
            if hasattr(est_up, "n_jobs"):
                est_up.n_jobs = 1
            idx = _fit_cap(split)
            if int(np.unique(y_up[idx]).size) < 2:
                out.append(
                    {
                        "kind": kind,
                        "horizon": horizon,
                        "n_train": int(split),
                        "n_test": int(len(kept) - split),
                        "auc_up": None,
                        "auc_large": None,
                        "after": 0.0,
                        "trades": 0,
                        "win_pct": 0.0,
                        "note": "train fold one-class — no model",
                    }
                )
                continue
            est_up.fit(X[idx], y_up[idx])
            proba = est_up.predict_proba(X[split:])
            classes = list(getattr(est_up, "classes_", [0, 1]))
            if hasattr(est_up, "named_steps"):
                classes = list(
                    getattr(est_up.named_steps[list(est_up.named_steps)[-1]], "classes_", [0, 1])
                )
            if proba.shape[1] < 2 or 1 not in {int(c) for c in classes}:
                out.append(
                    {
                        "kind": kind,
                        "horizon": horizon,
                        "note": "predict_proba has one class",
                        "auc_up": None,
                        "auc_large": None,
                        "after": 0.0,
                        "trades": 0,
                        "win_pct": 0.0,
                    }
                )
                continue
            pos = [int(c) for c in classes].index(1)
            p = np.asarray(proba[:, pos], dtype=float)
            yte = y_up[split:]
            try:
                auc_up = float(roc_auc_score(yte, p))
            except ValueError:
                auc_up = None
            auc_large = None
            if int(np.unique(y_large[:split]).size) >= 2 and int(
                np.unique(y_large[idx]).size
            ) >= 2:
                est_lg = make_estimator(kind)
                if hasattr(est_lg, "n_jobs"):
                    est_lg.n_jobs = 1
                est_lg.fit(X[idx], y_large[idx])
                pl = np.asarray(est_lg.predict_proba(X[split:])[:, 1], dtype=float)
                try:
                    auc_large = float(roc_auc_score(y_large[split:], pl))
                except ValueError:
                    auc_large = None
            last_exit = -1e18
            after = 0.0
            n_tr = 0
            n_win = 0
            for row, prob in zip(kept[split:], p):
                pts = row.fwd.get(horizon)
                if pts is None or row.t < last_exit:
                    continue
                if prob >= p_long:
                    side: Side | None = "long"
                elif prob <= p_short:
                    side = "short"
                else:
                    continue
                _signed, _g, ac = _after_charges(
                    side=side,
                    entry=row.ltp,
                    exit_px=row.ltp + float(pts),
                    cfg=cfg,
                )
                after += ac
                n_tr += 1
                if ac > 0:
                    n_win += 1
                last_exit = row.t + float(horizon)
            out.append(
                {
                    "kind": kind,
                    "horizon": horizon,
                    "n_train": int(split),
                    "n_test": int(len(kept) - split),
                    "auc_up": auc_up,
                    "auc_large": auc_large,
                    "after": after,
                    "trades": n_tr,
                    "win_pct": (100.0 * n_win / n_tr) if n_tr else 0.0,
                    "p_long": p_long,
                    "p_short": p_short,
                }
            )
        except Exception as exc:  # noqa: BLE001 — diagnostic, keep other models
            out.append(
                {
                    "kind": kind,
                    "horizon": horizon,
                    "note": f"fit failed: {exc}",
                    "auc_up": None,
                    "auc_large": None,
                    "after": 0.0,
                    "trades": 0,
                    "win_pct": 0.0,
                }
            )
    return out


def surface_has_edge(
    cells: dict[str, dict[str, Any]],
    wf: dict[str, dict[str, Any]],
    *,
    min_n: int = MIN_EDGE_N,
) -> list[str]:
    good: list[str] = []
    for state, cell in cells.items():
        if int(cell.get("n_side") or 0) < min_n:
            continue
        if float(cell.get("after") or 0.0) <= 0:
            continue
        w = wf.get(state) or {}
        folds = int(w.get("folds") or 0)
        wins = int(w.get("wins") or 0)
        if folds < 1 or wins < folds:
            continue
        if float(w.get("test_after") or 0.0) <= 0:
            continue
        good.append(state)
    return good


def init_learn_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS labels (
                t REAL, ts TEXT, state TEXT, expanding INT, decaying INT,
                want TEXT, mood TEXT, regime TEXT, ltp REAL, atr REAL,
                imb REAL, d_ltp_5 REAL, depth_imb REAL, vwap_gap REAL,
                oi REAL, seq_ret_100 REAL, seq_up_frac REAL,
                fwd_5s REAL, fwd_30s REAL, fwd_60s REAL, fwd_300s REAL
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS models (
                trained_at TEXT, kind TEXT, horizon_s INT,
                auc_up REAL, auc_large REAL, oos_after REAL,
                trades INT, approved INT DEFAULT 0, note TEXT
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS verdict (
                at TEXT, has_edge INT, states TEXT, note TEXT
            )
            """
        )
        con.commit()


def write_learn_db(
    path: Path,
    labels: list[NextLabel],
    *,
    models: list[dict[str, Any]] | None = None,
    has_edge: bool = False,
    edge_states: list[str] | None = None,
    max_rows: int = 20_000,
) -> None:
    init_learn_db(path)
    step = 1
    if len(labels) > max_rows:
        step = max(1, len(labels) // max_rows)
    rows = []
    for i, lab in enumerate(labels):
        if i % step:
            continue
        rows.append(
            (
                lab.t,
                lab.ts,
                lab.state,
                int(lab.expanding),
                int(lab.decaying),
                lab.want,
                lab.mood,
                lab.regime,
                lab.ltp,
                lab.atr,
                lab.imb,
                lab.d_ltp_5,
                lab.depth_imb,
                lab.vwap_gap,
                lab.oi,
                lab.seq_ret_100,
                lab.seq_up_frac,
                lab.fwd.get(5),
                lab.fwd.get(30),
                lab.fwd.get(60),
                lab.fwd.get(300),
            )
        )
    with sqlite3.connect(path) as con:
        con.execute("DELETE FROM labels")
        con.executemany(
            """
            INSERT INTO labels VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            rows,
        )
        if models:
            con.execute("DELETE FROM models")
            now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            for m in models:
                con.execute(
                    """
                    INSERT INTO models(trained_at, kind, horizon_s, auc_up,
                        auc_large, oos_after, trades, approved, note)
                    VALUES (?,?,?,?,?,?,?,0,?)
                    """,
                    (
                        now,
                        str(m.get("kind")),
                        int(m.get("horizon") or 60),
                        m.get("auc_up"),
                        m.get("auc_large"),
                        m.get("after"),
                        m.get("trades"),
                        str(m.get("note") or ""),
                    ),
                )
        con.execute("DELETE FROM verdict")
        con.execute(
            "INSERT INTO verdict VALUES (?,?,?,?)",
            (
                datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
                int(has_edge),
                ",".join(edge_states or []),
                TRANSFORMER_NOTE,
            ),
        )
        con.commit()


def mood_cross(
    labels: list[NextLabel],
    *,
    horizon: int = 60,
    lots: float = 100.0,
    fees: bool = True,
) -> list[dict[str, Any]]:
    """State × mood at one horizon. Continuation side only."""
    cfg = make_charge_cfg(fees=fees, lots=lots)
    last_exit: dict[tuple[str, str], float] = {}
    acc: dict[tuple[str, str], dict[str, Any]] = {}
    for row in labels:
        pts = row.fwd.get(horizon)
        if pts is None:
            continue
        key = (row.state, row.mood)
        cell = acc.setdefault(key, _empty_cell())
        fee_be = fee_break_even_points(row.ltp, force_fees=bool(fees), lots=lots)
        _bump_pred(cell, fwd=float(pts), atr=row.atr, fee_be=fee_be)
        side = implied_cont(row.state)
        if side is None:
            continue
        until = last_exit.get(key, -1e18)
        if row.t < until:
            continue
        _s, g, ac = _after_charges(
            side=side, entry=row.ltp, exit_px=row.ltp + float(pts), cfg=cfg
        )
        cell["n_side"] += 1
        cell["after"] += ac
        cell["gross"] += g
        last_exit[key] = row.t + float(horizon)
    out: list[dict[str, Any]] = []
    for (state, mood), cell in sorted(acc.items()):
        n = max(1, int(cell["n"]))
        out.append(
            {
                "state": state,
                "mood": mood,
                "n": cell["n"],
                "up_pct": 100.0 * cell["n_up"] / n,
                "large_pct": 100.0 * cell["n_large"] / n,
                "trades": cell["n_side"],
                "after": cell["after"],
            }
        )
    return out


def run_lab(
    ticks: list[RichTick],
    *,
    lots: float = 100.0,
    fees: bool = True,
    session_filter: bool = True,
    with_mood: bool = True,
    with_models: bool = True,
    model_horizon: int = 60,
) -> dict[str, Any]:
    labels = label_tape(
        ticks, session_filter=session_filter, with_mood=with_mood
    )
    fee_be = 0.0
    if labels:
        fee_be = fee_break_even_points(
            labels[len(labels) // 2].ltp, force_fees=bool(fees), lots=lots
        )
    packs = {
        "cont": implied_cont,
        "fade": implied_fade,
    }
    tables: dict[str, Any] = {}
    edges: list[str] = []
    for pack, side_of in packs.items():
        tables[pack] = {}
        for hz in HORIZONS:
            cells = surface_cells(
                labels,
                horizon=hz,
                lots=lots,
                fees=fees,
                side_of=side_of,
            )
            wf = walk_forward_agree(
                labels,
                horizon=hz,
                lots=lots,
                fees=fees,
                side_of=side_of,
            )
            tables[pack][hz] = {"cells": cells, "wf": wf}
            if pack == "cont":
                for st in surface_has_edge(cells, wf):
                    edges.append(f"{st}@{HORIZON_NAME[hz]}")
    models: list[dict[str, Any]] = []
    if with_models:
        for hz in (30, int(model_horizon)):
            hz = int(hz)
            if models and hz == models[-1].get("horizon"):
                continue
            models.extend(
                train_models(labels, horizon=hz, lots=lots, fees=fees)
            )
    has_edge = bool(edges)
    note = (
        "States precede after-charges moves on walk-forward. Models are "
        "still research. You approve. ENABLE_FLOW_BRAIN stays false."
        if has_edge
        else (
            "States do not precede after-charges moves on this tape. "
            "Stacking GBDT/RF/Transformer will not save it. "
            "ENABLE_FLOW_BRAIN stays false."
        )
    )
    return {
        "book": BOOK,
        "lab": LAB_NAME,
        "n_ticks": len(ticks),
        "n_labels": len(labels),
        "lots": lots,
        "fees": fees,
        "fee_be_pts": fee_be,
        "labels": labels,
        "tables": tables,
        "mood_1m": mood_cross(labels, horizon=60, lots=lots, fees=fees),
        "models": models,
        "edges": edges,
        "has_edge": has_edge,
        "note": note,
        "transformer": TRANSFORMER_NOTE,
        "not_every": NOT_EVERY_SITUATION,
    }


def format_lab(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(str(report["book"]))
    lines.append(
        "What happens next: do FLOW_BRAIN states precede small vs large "
        "moves after Angel charges (tax excluded)?"
    )
    lines.append(
        f"ticks={report['n_ticks']}  labels={report['n_labels']}  "
        f"lots={report['lots']}  fees={report['fees']}  "
        f"fee_BE≈{report['fee_be_pts']:.2f} pts (this tape's LTP; VM gold is ~3pt at 100 lots)"
    )
    lines.append(
        "P(up)/P(large)/P(cover) are overlapping diagnostics. "
        "after₹ is a non-overlap horizon book (one trade at a time)."
    )
    lines.append(str(report["transformer"]))
    lines.append(str(report["not_every"]))
    lines.append("Not S7_HOURLY. Not S16. Stay DRY_RUN.")
    lines.append("")
    for pack in ("cont", "fade"):
        title = (
            "continuation (bull_cont→LONG, bear_cont→SHORT)"
            if pack == "cont"
            else "fade (weak/absorb against the flow)"
        )
        lines.append(f"=== {pack}: {title} ===")
        hdr = (
            f"{'hz':<4} {'state':<12} {'n':>7} {'up%':>7} {'large%':>8} "
            f"{'cover%':>8} {'mean_pt':>8} {'t':>5} {'win%':>7} "
            f"{'after₹':>12} {'guard':<14} {'WF'}"
        )
        lines.append(hdr)
        lines.append("-" * len(hdr))
        for hz in HORIZONS:
            block = report["tables"][pack][hz]
            cells: dict[str, dict[str, Any]] = block["cells"]
            wf: dict[str, dict[str, Any]] = block["wf"]
            for state in STATES:
                cell = cells.get(state)
                if not cell or cell["n"] < 1:
                    continue
                w = wf.get(state) or {}
                lines.append(
                    f"{HORIZON_NAME[hz]:<4} {state:<12} {cell['n']:7d} "
                    f"{cell['up_pct']:6.1f}% {cell['large_pct']:7.1f}% "
                    f"{cell['cover_pct']:7.1f}% {cell['mean_fwd']:8.2f} "
                    f"{cell['n_side']:5d} {cell['win_pct']:6.1f}% "
                    f"{cell['after']:12.1f} {str(cell['status']):<14} "
                    f"{w.get('stability', '-')}"
                )
            lines.append("")
    lines.append("=== state × mood at 1m (continuation side) ===")
    lines.append(
        f"{'state':<12} {'mood':<12} {'n':>7} {'up%':>7} {'large%':>8} "
        f"{'t':>5} {'after₹':>12}"
    )
    for row in report["mood_1m"]:
        if row["n"] < 8:
            continue
        lines.append(
            f"{row['state']:<12} {row['mood']:<12} {row['n']:7d} "
            f"{row['up_pct']:6.1f}% {row['large_pct']:7.1f}% "
            f"{row['trades']:5d} {row['after']:12.1f}"
        )
    lines.append("")
    lines.append("=== models (chrono 70/30, non-overlap OOS book) ===")
    for m in report["models"]:
        auc = m.get("auc_up")
        auc_l = m.get("auc_large")
        auc_s = f"{auc:.3f}" if isinstance(auc, float) else str(auc)
        auc_ls = f"{auc_l:.3f}" if isinstance(auc_l, float) else str(auc_l)
        extra = m.get("note") or ""
        lines.append(
            f"  hz={m.get('horizon')}s {str(m.get('kind')):<16} "
            f"auc_up={auc_s}  auc_large={auc_ls}  "
            f"OOS trades={m.get('trades')} win%={m.get('win_pct'):.1f} "
            f"after₹={float(m.get('after') or 0):.1f} {extra}"
        )
    lines.append("")
    if report["has_edge"]:
        lines.append("EDGE CELLS: " + ", ".join(report["edges"]))
    else:
        lines.append("EDGE CELLS: none")
    lines.append(str(report["note"]))
    lines.append(
        "Retrain → backtest → walk-forward → you approve a new model. "
        "approved=0 in the learn db until you say so. Do not ENABLE."
    )
    lines.append(
        "Strategy discovery / expected-net factory / Profit Guardian UI "
        "stay in research_factory.py + profit_guardian.py. This lab only "
        "ranks FLOW_BRAIN states."
    )
    return "\n".join(lines)


def synthetic_ticks() -> list[RichTick]:
    return rich_from_quads(scratchy_then_trend_samples())
