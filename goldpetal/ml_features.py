"""Feature engineering from full-depth Gold Petal tick columns."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

# Exact sheet / export columns the user collects.
RAW_COLUMNS = [
    "time",
    "ltp",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "last_traded_quantity",
    "average_traded_price",
    "total_buy_quantity",
    "total_sell_quantity",
    "buy1_price",
    "buy1_qty",
    "buy2_price",
    "buy2_qty",
    "buy3_price",
    "buy3_qty",
    "buy4_price",
    "buy4_qty",
    "buy5_price",
    "buy5_qty",
    "sell1_price",
    "sell1_qty",
    "sell2_price",
    "sell2_qty",
    "sell3_price",
    "sell3_qty",
    "sell4_price",
    "sell4_qty",
    "sell5_price",
    "sell5_qty",
    "open_interest",
]

FEATURE_COLUMNS = [
    "ret_1",
    "ret_5",
    "ret_20",
    "ltp_vs_vwap",
    "ltp_vs_mid",
    "spread",
    "spread_bps",
    "microprice_gap",
    "imb_l1",
    "imb_l5",
    "imb_total",
    "buy_depth_sum",
    "sell_depth_sum",
    "depth_ratio",
    "oi_chg",
    "oi_chg_5",
    "vol_chg",
    "vol_chg_5",
    "ltq",
    "range_pos",
]


def _num(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def load_ticks_csv(path: str | Any) -> pd.DataFrame:
    df = pd.read_csv(path)
    # tolerate missing optional cols
    for col in RAW_COLUMNS:
        if col not in df.columns:
            df[col] = np.nan
    df["time"] = pd.to_datetime(df["time"], errors="coerce")
    for col in RAW_COLUMNS:
        if col == "time":
            continue
        df[col] = _num(df[col])
    df = df.dropna(subset=["ltp"]).sort_values("time").reset_index(drop=True)
    return df


def _safe_div(a: pd.Series, b: pd.Series) -> pd.Series:
    out = a / b.replace(0, np.nan)
    return out.replace([np.inf, -np.inf], np.nan)


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """Build model features from raw depth/LTP columns."""
    out = df.copy()
    ltp = out["ltp"]

    buy_qty = sum(out[f"buy{i}_qty"].fillna(0.0) for i in range(1, 6))
    sell_qty = sum(out[f"sell{i}_qty"].fillna(0.0) for i in range(1, 6))
    b1q = out["buy1_qty"].fillna(0.0)
    s1q = out["sell1_qty"].fillna(0.0)
    b1p = out["buy1_price"]
    s1p = out["sell1_price"]

    mid = (b1p + s1p) / 2.0
    spread = s1p - b1p
    micro = _safe_div(b1p * s1q + s1p * b1q, b1q + s1q)

    out["ret_1"] = ltp.pct_change(1)
    out["ret_5"] = ltp.pct_change(5)
    out["ret_20"] = ltp.pct_change(20)
    out["ltp_vs_vwap"] = _safe_div(ltp - out["average_traded_price"], out["average_traded_price"])
    out["ltp_vs_mid"] = _safe_div(ltp - mid, mid)
    out["spread"] = spread
    out["spread_bps"] = _safe_div(spread, mid) * 1e4
    out["microprice_gap"] = _safe_div(micro - ltp, ltp)
    out["imb_l1"] = _safe_div(b1q - s1q, b1q + s1q)
    out["imb_l5"] = _safe_div(buy_qty - sell_qty, buy_qty + sell_qty)
    tb = out["total_buy_quantity"].fillna(0.0)
    ts = out["total_sell_quantity"].fillna(0.0)
    out["imb_total"] = _safe_div(tb - ts, tb + ts)
    out["buy_depth_sum"] = buy_qty
    out["sell_depth_sum"] = sell_qty
    out["depth_ratio"] = _safe_div(buy_qty, sell_qty)
    out["oi_chg"] = out["open_interest"].diff(1)
    out["oi_chg_5"] = out["open_interest"].diff(5)
    out["vol_chg"] = out["volume"].diff(1)
    out["vol_chg_5"] = out["volume"].diff(5)
    out["ltq"] = out["last_traded_quantity"].fillna(0.0)
    # where LTP sits in day range
    day_high = out["high"]
    day_low = out["low"]
    out["range_pos"] = _safe_div(ltp - day_low, day_high - day_low)

    return out


def add_labels(
    df: pd.DataFrame,
    horizon: int = 20,
    threshold_bps: float = 2.0,
) -> pd.DataFrame:
    """Label future LTP direction over `horizon` rows.

    y_dir: 1 = up, 0 = down/flat (binary for classifiers)
    y_ret: future return
    y_side: 1 up / 0 flat / -1 down using threshold_bps
    """
    out = df.copy()
    future = out["ltp"].shift(-horizon)
    out["y_ret"] = _safe_div(future - out["ltp"], out["ltp"])
    thr = threshold_bps / 1e4
    out["y_side"] = 0
    out.loc[out["y_ret"] > thr, "y_side"] = 1
    out.loc[out["y_ret"] < -thr, "y_side"] = -1
    out["y_dir"] = (out["y_ret"] > 0).astype("float")
    # drop rows without future label
    out.loc[out["y_ret"].isna(), ["y_dir", "y_side"]] = np.nan
    return out


def model_matrix(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    """Return X, y_dir, y_ret with NaNs dropped."""
    need = FEATURE_COLUMNS + ["y_dir", "y_ret", "time"]
    clean = df.dropna(subset=need).copy()
    X = clean[FEATURE_COLUMNS].astype(float)
    y_dir = clean["y_dir"].astype(int)
    y_ret = clean["y_ret"].astype(float)
    return X, y_dir, y_ret


def time_split(
    df: pd.DataFrame,
    train_frac: float = 0.7,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Chronological split — never shuffle trading data."""
    n = len(df)
    cut = max(1, int(n * train_frac))
    return df.iloc[:cut].copy(), df.iloc[cut:].copy()
