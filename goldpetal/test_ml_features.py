"""Tests for ML feature engineering and labeling."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pandas as pd

from ml_features import (
    FEATURE_COLUMNS,
    add_labels,
    build_features,
    load_ticks_csv,
    model_matrix,
    time_split,
)


def _sample_csv(n: int = 80) -> Path:
    rows = []
    for i in range(n):
        # oscillate so both up/down labels exist
        wave = ((i % 20) - 10) * 1.5
        ltp = 14000 + wave + (i * 0.05)
        buy_q = 20 if (i % 20) < 10 else 5
        sell_q = 5 if (i % 20) < 10 else 20
        rows.append(
            {
                "time": f"2026-08-04 10:{i // 60:02d}:{i % 60:02d}",
                "ltp": ltp,
                "open": 14000,
                "high": max(14010, ltp + 5),
                "low": min(13990, ltp - 5),
                "close": 14000,
                "volume": 1000 + i * 10,
                "last_traded_quantity": 1 + (i % 3),
                "average_traded_price": 14000,
                "total_buy_quantity": 5000 + buy_q * 10,
                "total_sell_quantity": 4000 + sell_q * 10,
                "buy1_price": ltp - 1,
                "buy1_qty": buy_q,
                "buy2_price": ltp - 2,
                "buy2_qty": max(1, buy_q - 2),
                "buy3_price": ltp - 3,
                "buy3_qty": max(1, buy_q - 4),
                "buy4_price": ltp - 4,
                "buy4_qty": 4,
                "buy5_price": ltp - 5,
                "buy5_qty": 2,
                "sell1_price": ltp + 1,
                "sell1_qty": sell_q,
                "sell2_price": ltp + 2,
                "sell2_qty": max(1, sell_q - 2),
                "sell3_price": ltp + 3,
                "sell3_qty": max(1, sell_q - 4),
                "sell4_price": ltp + 4,
                "sell4_qty": 3,
                "sell5_price": ltp + 5,
                "sell5_qty": 1,
                "open_interest": 10000 + (i % 7) - 3,
            }
        )
    path = Path(tempfile.mkdtemp()) / "ticks.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def test_features_and_labels() -> None:
    path = _sample_csv(100)
    raw = load_ticks_csv(path)
    feat = build_features(raw)
    labeled = add_labels(feat, horizon=5, threshold_bps=1.0)
    for col in FEATURE_COLUMNS:
        assert col in labeled.columns
    X, y_dir, y_ret = model_matrix(labeled)
    assert len(X) > 20
    assert set(y_dir.unique()).issubset({0, 1})
    train_df, test_df = time_split(labeled.dropna(subset=FEATURE_COLUMNS + ["y_dir"]))
    assert len(train_df) > len(test_df)
    assert train_df["time"].iloc[-1] <= test_df["time"].iloc[0]


def test_train_smoke() -> None:
    from train_models import train

    path = _sample_csv(200)
    out = Path(tempfile.mkdtemp()) / "models"
    report = train(
        csv_path=path,
        model_dir=out,
        horizon=5,
        threshold_bps=1.0,
        train_frac=0.7,
    )
    assert "logreg" in report["models"]
    assert (out / "logreg.joblib").exists()
    assert (out / "report.json").exists()


if __name__ == "__main__":
    test_features_and_labels()
    test_train_smoke()
    print("ok")
