"""Full-session bullish / bearish day bias for delivery (overnight) positions.

Analyses the entire IST trading day from ticks, then scores whether the
session is structurally bullish or bearish. Used near MARKET_CLOSE to
open a paper delivery / carry position into the next session.

Score components (each roughly in [-1, +1], then combined):
- Day log-return vs open
- Close Location Value (where close sits in the day's range)
- Late-session return (last N minutes)
- Late depth imbalance (buy1-5 vs sell1-5)
- Time spent above session VWAP / open
- HH vs LL pressure (upthrust vs distribution)
- OI confirmation (price↑+OI↑ bullish; price↓+OI↑ bearish short covering less)
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from ml_features import build_features
from overnight_features import close_location_value


def _sigmoid(z: float) -> float:
    z = max(-20.0, min(20.0, z))
    return 1.0 / (1.0 + math.exp(-z))


def _tanh_scale(x: float, scale: float) -> float:
    if scale == 0:
        return 0.0
    return math.tanh(x / scale)


@dataclass
class DayBiasResult:
    bias: str  # "BULLISH" | "BEARISH" | "NEUTRAL"
    prob_bullish: float
    day_return: float
    clv: float
    late_ret: float
    late_imb: float
    above_vwap_frac: float
    hh_ll_score: float
    oi_confirm: float
    reason: str
    open: float
    high: float
    low: float
    close: float
    n_ticks: int


def analyze_day(
    tick_rows: list[dict[str, Any]] | pd.DataFrame,
    *,
    late_minutes: int = 45,
    bullish_prob: float = 0.58,
    bearish_prob: float = 0.42,
) -> DayBiasResult | None:
    """Score full-day bullish/bearish structure from today's ticks."""
    if isinstance(tick_rows, list):
        if not tick_rows:
            return None
        df = pd.DataFrame(tick_rows)
    else:
        df = tick_rows.copy()
    if df.empty or "ltp" not in df.columns:
        return None

    df["time"] = pd.to_datetime(df["time"], errors="coerce")
    df = df.dropna(subset=["time", "ltp"]).sort_values("time")
    if len(df) < 30:
        return None

    feat = build_features(df)
    o = float(feat["ltp"].iloc[0])
    h = float(feat["ltp"].max())
    l = float(feat["ltp"].min())
    c = float(feat["ltp"].iloc[-1])
    if o <= 0 or c <= 0:
        return None

    day_ret = math.log(c / o)
    clv = close_location_value(h, l, c)

    t_end = feat["time"].iloc[-1]
    late = feat[feat["time"] >= t_end - pd.Timedelta(minutes=late_minutes)]
    if late.empty:
        late = feat.tail(max(10, len(feat) // 8))
    late_open = float(late["ltp"].iloc[0])
    late_ret = math.log(c / late_open) if late_open > 0 else 0.0
    late_imb = float(late["imb_l5"].fillna(0).mean()) if "imb_l5" in late else 0.0

    # VWAP proxy: average traded price if present, else expanding mean of ltp
    if "average_traded_price" in feat.columns and feat["average_traded_price"].notna().any():
        vwap = feat["average_traded_price"].ffill().fillna(o)
    else:
        vwap = feat["ltp"].expanding().mean()
    above_vwap_frac = float((feat["ltp"] > vwap).mean())

    # Higher-high / lower-low pressure on a coarse grid
    step = max(1, len(feat) // 40)
    sample = feat["ltp"].iloc[::step].to_numpy(dtype=float)
    hh = ll = 0
    for i in range(2, len(sample)):
        if sample[i] > sample[i - 1] > sample[i - 2]:
            hh += 1
        if sample[i] < sample[i - 1] < sample[i - 2]:
            ll += 1
    hh_ll = (hh - ll) / max(1, hh + ll)

    # OI confirmation
    oi = feat["open_interest"].dropna() if "open_interest" in feat.columns else pd.Series(dtype=float)
    oi_confirm = 0.0
    if len(oi) >= 2 and abs(float(oi.iloc[0])) > 0:
        oi_chg = (float(oi.iloc[-1]) - float(oi.iloc[0])) / abs(float(oi.iloc[0]))
        if day_ret > 0 and oi_chg > 0:
            oi_confirm = _tanh_scale(oi_chg, 0.02)  # bullish confirmation
        elif day_ret < 0 and oi_chg > 0:
            oi_confirm = -_tanh_scale(oi_chg, 0.02)  # bearish / short build
        elif day_ret > 0 and oi_chg < 0:
            oi_confirm = -0.3  # short covering / weak rally
        elif day_ret < 0 and oi_chg < 0:
            oi_confirm = 0.2  # long liquidation / possible bounce setup dampener

    # Weighted logit for P(bullish day → delivery long)
    z = (
        6.0 * _tanh_scale(day_ret, 0.004)
        + 1.2 * clv
        + 4.0 * _tanh_scale(late_ret, 0.002)
        + 0.9 * _tanh_scale(late_imb, 0.35)
        + 1.0 * (above_vwap_frac - 0.5) * 2.0
        + 0.8 * hh_ll
        + 0.7 * oi_confirm
    )
    prob = _sigmoid(z)

    if prob >= bullish_prob:
        bias = "BULLISH"
    elif prob <= bearish_prob:
        bias = "BEARISH"
    else:
        bias = "NEUTRAL"

    reason = (
        f"day_bias={bias} P(bull)={prob:.3f} "
        f"ret={day_ret*1e4:.1f}bps clv={clv:.2f} late_ret={late_ret*1e4:.1f}bps "
        f"late_imb={late_imb:.2f} above_vwap={above_vwap_frac:.2f} "
        f"hh_ll={hh_ll:.2f} oi_c={oi_confirm:.2f}"
    )
    return DayBiasResult(
        bias=bias,
        prob_bullish=prob,
        day_return=day_ret,
        clv=clv,
        late_ret=late_ret,
        late_imb=late_imb,
        above_vwap_frac=above_vwap_frac,
        hh_ll_score=hh_ll,
        oi_confirm=oi_confirm,
        reason=reason,
        open=o,
        high=h,
        low=l,
        close=c,
        n_ticks=int(len(feat)),
    )
