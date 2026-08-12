"""Tests for Angel price scaling in export / CSV load."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from export_full_ticks import _price
from ml_features import load_ticks_csv


def test_price_scales_paise() -> None:
    assert _price(1_490_600) == 14_906.0
    assert abs(_price(14_906.0) - 14_906.0) < 1e-9
    assert _price(50) == 0.5


def test_load_ticks_auto_unscales_paise_csv(tmp_path: Path) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "paise.csv"
    pd.DataFrame(
        {
            "time": ["2026-08-11 10:00:00", "2026-08-11 10:00:01"],
            "ltp": [1_490_600, 1_490_700],
            "open": [1_490_000, 1_490_000],
            "high": [1_491_000, 1_491_000],
            "low": [1_489_000, 1_489_000],
            "close": [1_490_600, 1_490_700],
            "buy1_price": [1_490_500, 1_490_600],
            "sell1_price": [1_490_700, 1_490_800],
            "buy1_qty": [10, 10],
            "sell1_qty": [10, 10],
        }
    ).to_csv(path, index=False)
    df = load_ticks_csv(path)
    assert abs(float(df["ltp"].iloc[0]) - 14_906.0) < 1e-6


if __name__ == "__main__":
    test_price_scales_paise()
    print("ok price")
    from pathlib import Path as P

    test_load_ticks_auto_unscales_paise_csv(P("/tmp/price_scale_test"))
    print("ok csv")
    print("ALL test_price_scale OK")
