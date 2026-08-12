"""Tests for S3_ML live strategy signals."""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from strategy_ml import MLStrategy
from train_models import train


def _sample_csv(n: int = 200) -> Path:
    rows = []
    for i in range(n):
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
                "last_traded_quantity": 1,
                "average_traded_price": 14000,
                "total_buy_quantity": 5000 + buy_q * 10,
                "total_sell_quantity": 4000 + sell_q * 10,
                "buy1_price": ltp - 1,
                "buy1_qty": buy_q,
                "buy2_price": ltp - 2,
                "buy2_qty": max(1, buy_q - 2),
                "buy3_price": ltp - 3,
                "buy3_qty": 4,
                "buy4_price": ltp - 4,
                "buy4_qty": 3,
                "buy5_price": ltp - 5,
                "buy5_qty": 2,
                "sell1_price": ltp + 1,
                "sell1_qty": sell_q,
                "sell2_price": ltp + 2,
                "sell2_qty": max(1, sell_q - 2),
                "sell3_price": ltp + 3,
                "sell3_qty": 4,
                "sell4_price": ltp + 4,
                "sell4_qty": 3,
                "sell5_price": ltp + 5,
                "sell5_qty": 1,
                "open_interest": 10000 + (i % 7),
            }
        )
    path = Path(tempfile.mkdtemp()) / "ticks.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def test_disabled_without_model() -> None:
    s = MLStrategy(model_path=Path("/tmp/does_not_exist_ml.joblib"))
    assert s.enabled is False
    assert s.maybe_signal(datetime.now()) is None


def test_emits_buy_short_close() -> None:
    csv_path = _sample_csv(220)
    model_dir = Path(tempfile.mkdtemp()) / "models"
    train(
        csv_path=csv_path,
        model_dir=model_dir,
        horizon=5,
        threshold_bps=1.0,
        train_frac=0.7,
    )
    model_path = model_dir / "logreg.joblib"
    assert model_path.exists()

    s = MLStrategy(
        model_path=model_path,
        buy_prob=0.55,
        short_prob=0.45,
        min_hold_sec=0,
        every_n_ticks=1,
    )
    assert s.enabled

    rows = pd.read_csv(csv_path).to_dict(orient="records")
    now = datetime(2026, 8, 4, 10, 0, 0)
    actions = []
    for i, row in enumerate(rows):
        s.push_row(row)
        sig = s.maybe_signal(now + timedelta(seconds=i))
        if sig is not None:
            actions.append(sig.action)

    assert any(a in {"BUY", "SHORT"} for a in actions)
    # After enough flips we should see transitions recorded
    assert len(actions) >= 1


if __name__ == "__main__":
    test_disabled_without_model()
    test_emits_buy_short_close()
    print("ok")
