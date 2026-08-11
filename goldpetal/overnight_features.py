"""Quantitative day-level features for overnight / next-open prediction.

Mathematics used
----------------
- Log returns: r = ln(P_t / P_{t-1})
- Parkinson volatility (range-based):
    σ_p = sqrt( 1/(4 ln 2) * (ln(H/L))^2 )
- Garman–Klass OHLC estimator:
    σ_gk^2 = 0.5 (ln(H/L))^2 − (2 ln 2 − 1) (ln(C/O))^2
- Close Location Value:
    CLV = ((C−L) − (H−C)) / (H−L)
- Overnight gap (label): g_{t→t+1} = ln(O_{t+1} / C_t)
- Late-session order-flow imbalance (mean of depth imb over last window)
- EWMA volatility and z-scored returns
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ml_features import build_features, load_ticks_csv

LN2 = math.log(2.0)

DAY_FEATURE_COLS = [
    "log_ret",
    "parkinson",
    "garman_klass",
    "clv",
    "range_bps",
    "late_ret",
    "late_imb",
    "late_spread_bps",
    "oi_chg_pct",
    "vol_chg_pct",
    "gap_lag1",
    "gap_lag2",
    "mom_3",
    "ewma_vol",
    "ret_z",
]


def _safe_ln(a: float, b: float) -> float:
    if a is None or b is None or b == 0 or a <= 0 or b <= 0:
        return float("nan")
    return math.log(a / b)


def parkinson_vol(high: float, low: float) -> float:
    if high is None or low is None or low <= 0 or high <= 0 or high < low:
        return float("nan")
    return math.sqrt((1.0 / (4.0 * LN2)) * (math.log(high / low) ** 2))


def garman_klass(open_: float, high: float, low: float, close: float) -> float:
    try:
        if min(open_, high, low, close) <= 0:
            return float("nan")
        term_hl = 0.5 * (math.log(high / low) ** 2)
        term_co = (2.0 * LN2 - 1.0) * (math.log(close / open_) ** 2)
        var = term_hl - term_co
        return math.sqrt(max(var, 0.0))
    except (TypeError, ValueError, ZeroDivisionError):
        return float("nan")


def close_location_value(high: float, low: float, close: float) -> float:
    if high is None or low is None or close is None or high == low:
        return 0.0
    return ((close - low) - (high - close)) / (high - low)


def day_bars_from_ticks(ticks: pd.DataFrame, late_minutes: int = 30) -> pd.DataFrame:
    """Aggregate tick CSV into one row per IST calendar day."""
    df = ticks.copy()
    if "time" not in df.columns:
        raise ValueError("ticks need a time column")
    df["time"] = pd.to_datetime(df["time"], errors="coerce")
    df = df.dropna(subset=["time", "ltp"]).sort_values("time")
    if df.empty:
        return pd.DataFrame()

    # enrich with imbalance features when depth present
    feat = build_features(df)
    feat["date"] = feat["time"].dt.tz_localize(None).dt.date.astype(str)

    rows: list[dict[str, Any]] = []
    for day, g in feat.groupby("date", sort=True):
        g = g.sort_values("time")
        o = float(g["ltp"].iloc[0])
        h = float(g["ltp"].max())
        l = float(g["ltp"].min())
        c = float(g["ltp"].iloc[-1])
        t_end = g["time"].iloc[-1]
        late_cut = t_end - pd.Timedelta(minutes=late_minutes)
        late = g[g["time"] >= late_cut]
        if late.empty:
            late = g.tail(max(5, len(g) // 10))

        late_open = float(late["ltp"].iloc[0])
        oi0 = g["open_interest"].dropna()
        vol0 = g["volume"].dropna()
        oi_chg = float("nan")
        vol_chg = float("nan")
        if len(oi0) >= 2 and oi0.iloc[0]:
            oi_chg = (float(oi0.iloc[-1]) - float(oi0.iloc[0])) / abs(float(oi0.iloc[0]))
        if len(vol0) >= 2 and vol0.iloc[0]:
            vol_chg = (float(vol0.iloc[-1]) - float(vol0.iloc[0])) / abs(float(vol0.iloc[0]))

        rows.append(
            {
                "date": day,
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "log_ret": _safe_ln(c, o),
                "parkinson": parkinson_vol(h, l),
                "garman_klass": garman_klass(o, h, l, c),
                "clv": close_location_value(h, l, c),
                "range_bps": (h - l) / c * 1e4 if c else float("nan"),
                "late_ret": _safe_ln(c, late_open),
                "late_imb": float(late["imb_l5"].fillna(0).mean()) if "imb_l5" in late else 0.0,
                "late_spread_bps": float(late["spread_bps"].fillna(0).mean())
                if "spread_bps" in late
                else 0.0,
                "oi_chg_pct": oi_chg,
                "vol_chg_pct": vol_chg,
                "n_ticks": int(len(g)),
            }
        )

    out = pd.DataFrame(rows)
    if out.empty:
        return out

    # overnight gap realized next morning (label uses shift)
    out["next_open"] = out["open"].shift(-1)
    out["gap_next"] = [
        _safe_ln(no, c) if pd.notna(no) else float("nan")
        for no, c in zip(out["next_open"], out["close"])
    ]
    out["y_gap_up"] = (out["gap_next"] > 0).astype(float)
    out.loc[out["gap_next"].isna(), "y_gap_up"] = np.nan

    # lags of prior realized overnight gaps (today open vs yesterday close)
    prior_gap = []
    for i in range(len(out)):
        if i == 0:
            prior_gap.append(float("nan"))
        else:
            prior_gap.append(_safe_ln(float(out.loc[i, "open"]), float(out.loc[i - 1, "close"])))
    out["gap_lag1"] = prior_gap
    out["gap_lag2"] = out["gap_lag1"].shift(1)

    out["mom_3"] = np.log(out["close"] / out["close"].shift(3))
    # EWMA vol of log_ret (span=5 days)
    out["ewma_vol"] = out["log_ret"].ewm(span=5, min_periods=2).std()
    roll_mean = out["log_ret"].rolling(5, min_periods=2).mean()
    roll_std = out["log_ret"].rolling(5, min_periods=2).std()
    out["ret_z"] = (out["log_ret"] - roll_mean) / roll_std.replace(0, np.nan)

    return out


def load_archive_days(archive_root: Path) -> pd.DataFrame:
    """Load and concat archived full_ticks.csv files."""
    frames = []
    if not archive_root.exists():
        return pd.DataFrame()
    for folder in sorted(p for p in archive_root.iterdir() if p.is_dir()):
        csv_path = folder / "full_ticks.csv"
        if csv_path.exists():
            frames.append(load_ticks_csv(csv_path))
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True).drop_duplicates(subset=["time"]).sort_values("time")


def model_matrix(days: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    need_y = days.dropna(subset=["y_gap_up"]).copy()
    for c in DAY_FEATURE_COLS:
        if c not in need_y.columns:
            need_y[c] = 0.0
        need_y[c] = need_y[c].replace([np.inf, -np.inf], np.nan).fillna(0.0)
    X = need_y[DAY_FEATURE_COLS].astype(float)
    y = need_y["y_gap_up"].astype(int)
    gaps = need_y["gap_next"].astype(float)
    return X, y, gaps
