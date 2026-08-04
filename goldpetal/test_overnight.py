"""Tests for overnight quant features and S4 entry/exit windows."""

from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from overnight_features import (
    DAY_FEATURE_COLS,
    close_location_value,
    day_bars_from_ticks,
    garman_klass,
    parkinson_vol,
)
from strategy_overnight import OvernightStrategy

IST = ZoneInfo("Asia/Kolkata")


def test_vol_math() -> None:
    p = parkinson_vol(101.0, 99.0)
    assert p > 0
    gk = garman_klass(100.0, 101.0, 99.0, 100.5)
    assert gk >= 0
    assert abs(close_location_value(110, 90, 100)) < 1e-9  # mid


def _synthetic_ticks(n_days: int = 5, rows_per_day: int = 40) -> pd.DataFrame:
    rows = []
    px = 14000.0
    for d in range(n_days):
        day = f"2026-08-{d+1:02d}"
        for i in range(rows_per_day):
            # mild drift + noise
            px += (1 if d % 2 == 0 else -1) * 0.5 + ((i % 7) - 3) * 0.05
            hh = 10 + i // 60
            mm = i % 60
            rows.append(
                {
                    "time": f"{day} {hh:02d}:{mm:02d}:00",
                    "ltp": px,
                    "open": 14000,
                    "high": px + 5,
                    "low": px - 5,
                    "close": 14000,
                    "volume": 1000 + i + d * 100,
                    "last_traded_quantity": 1,
                    "average_traded_price": px,
                    "total_buy_quantity": 5000,
                    "total_sell_quantity": 4000,
                    "buy1_price": px - 1,
                    "buy1_qty": 10,
                    "buy2_price": px - 2,
                    "buy2_qty": 8,
                    "buy3_price": px - 3,
                    "buy3_qty": 6,
                    "buy4_price": px - 4,
                    "buy4_qty": 4,
                    "buy5_price": px - 5,
                    "buy5_qty": 2,
                    "sell1_price": px + 1,
                    "sell1_qty": 9,
                    "sell2_price": px + 2,
                    "sell2_qty": 7,
                    "sell3_price": px + 3,
                    "sell3_qty": 5,
                    "sell4_price": px + 4,
                    "sell4_qty": 3,
                    "sell5_price": px + 5,
                    "sell5_qty": 1,
                    "open_interest": 10000 + d * 10 + i,
                }
            )
    return pd.DataFrame(rows)


def test_day_bars_and_labels() -> None:
    ticks = _synthetic_ticks(6)
    days = day_bars_from_ticks(ticks)
    assert len(days) == 6
    labeled = days.dropna(subset=["y_gap_up"])
    assert len(labeled) == 5
    for c in ("parkinson", "garman_klass", "clv", "late_imb", "gap_next"):
        assert c in days.columns


def test_s4_entry_exit_windows() -> None:
    # minimal fake model via training
    from train_overnight import train

    # build fake archive
    root = Path(tempfile.mkdtemp())
    ticks = _synthetic_ticks(6)
    for day, g in ticks.groupby(ticks["time"].str.slice(0, 10)):
        folder = root / day
        folder.mkdir(parents=True, exist_ok=True)
        g.to_csv(folder / "full_ticks.csv", index=False)

    # monkeypatch archive root by training from days directly
    days = day_bars_from_ticks(ticks)
    model_dir = Path(tempfile.mkdtemp())
    # train using matrix path inside train() needs archive — call features path
    import overnight_features as of

    of.ARCHIVE_ROOT = root  # type: ignore[attr-defined]
    # train_overnight uses archive_ticks.ARCHIVE_ROOT
    import archive_ticks as at
    import train_overnight as to

    at.ARCHIVE_ROOT = root
    to.ARCHIVE_ROOT = root
    to.train(model_dir=model_dir, late_minutes=30, skip_archive=True)

    state = Path(tempfile.mkdtemp()) / "s4.json"
    s = OvernightStrategy(
        model_path=model_dir / "overnight_logreg.joblib",
        buy_prob=0.55,
        short_prob=0.45,
        entry_minutes_before_close=15,
        exit_minutes_after_open=10,
        market_open="09:00",
        market_close="23:30",
        state_path=state,
    )
    assert s.enabled
    # strong up-day ticks for delivery long
    px = 14000.0
    for i in range(100):
        px += 1.5
        s.push_tick_row(
            {
                "time": f"2026-08-06 10:00:{i % 60:02d}",
                "ltp": px,
                "open": 14000,
                "high": px + 2,
                "low": 13990,
                "close": 14000,
                "volume": 1000 + i,
                "last_traded_quantity": 1,
                "average_traded_price": (14000 + px) / 2,
                "total_buy_quantity": 8000,
                "total_sell_quantity": 2000,
                "buy1_price": px - 1,
                "buy1_qty": 30,
                "buy2_price": px - 2,
                "buy2_qty": 20,
                "buy3_price": px - 3,
                "buy3_qty": 10,
                "buy4_price": px - 4,
                "buy4_qty": 8,
                "buy5_price": px - 5,
                "buy5_qty": 5,
                "sell1_price": px + 1,
                "sell1_qty": 5,
                "sell2_price": px + 2,
                "sell2_qty": 4,
                "sell3_price": px + 3,
                "sell3_qty": 3,
                "sell4_price": px + 4,
                "sell4_qty": 2,
                "sell5_price": px + 5,
                "sell5_qty": 1,
                "open_interest": 10000 + i * 3,
            }
        )

    now = datetime(2026, 8, 6, 23, 20, tzinfo=IST)
    sig = s.maybe_signal(now, cmp=px)
    assert sig is not None and sig.action == "BUY", (sig, s.last_prob)
    assert s.position == "long"
    assert "DELIVERY" in (sig.reason or "")

    s._exited_today = False
    now2 = datetime(2026, 8, 7, 9, 3, tzinfo=IST)
    sig2 = s.maybe_signal(now2, cmp=px + 10)
    assert sig2 is not None and sig2.action == "CLOSE"
    assert s.position == "flat"


if __name__ == "__main__":
    test_vol_math()
    test_day_bars_and_labels()
    test_s4_entry_exit_windows()
    print("ok")
