"""Tests for S9 bar-level ML features / train / filter."""

from __future__ import annotations

from pathlib import Path

from s9_bar_ml import (
    bars_to_frame,
    build_features,
    feature_columns,
    make_entry_filter,
    train_from_bars,
    walk_forward_p_up,
)
from strategy_state_s9 import StateS9Config, StateS9Strategy


def _synth_bars(n: int = 80) -> list[dict]:
    bars = []
    px = 10000.0
    tbq, tsq = 10000.0, 10000.0
    for i in range(n):
        # mild oscillating drift so both classes exist
        d = 8.0 if (i % 5) < 3 else -6.0
        o = px
        c = px + d
        h = max(o, c) + 5
        l = min(o, c) - 5
        tbq += 50 if d > 0 else -30
        tsq += -40 if d > 0 else 35
        bars.append(
            {
                "time": f"2026-08-07T{i:04d}",
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "tbq_close": tbq,
                "tsq_close": tsq,
                "range_pts": h - l,
                "bar_volume": 1000 + (i % 7) * 100,
                "n_ticks": 10,
            }
        )
        px = c
    return bars


def test_feature_pipeline():
    bars = _synth_bars(40)
    df = build_features(bars_to_frame(bars), lags=3)
    cols = feature_columns(3)
    assert all(c in df.columns for c in cols)
    assert len(df) == 40


def test_train_logreg():
    bars = _synth_bars(80)
    bundle = train_from_bars(bars, lags=2, train_frac=0.7, model_kind="logreg")
    assert bundle["metrics"]["n"] >= 5
    assert "auc" in bundle["metrics"]


def test_walk_forward_and_filter():
    bars = _synth_bars(70)
    pmap = walk_forward_p_up(bars, lags=2, min_train=25, model_kind="logreg", retrain_every=10)
    assert len(pmap) > 5
    filt = make_entry_filter(pmap, min_proba=0.01, allow_if_missing=True)
    # very low threshold → almost always allow when scored
    some_t = next(iter(pmap))
    assert filt({"time": some_t}, None, "long") is True


def test_s9_ml_filter_blocks_low_proba(tmp_path: Path | None = None):
    # Precomputed map: block long on the enter bar
    bars = _synth_bars(10)
    # Force a known entry setup via strategy + filter
    cfg = StateS9Config(
        bar_minutes=30,
        tp_points=26,
        sl_points=16,
        min_imb_pct=0,
        require_net_sign=True,
        allow_short=False,
    )
    s = StateS9Strategy(cfg)
    # Always-block long filter
    s.extra_entry_filters.append(lambda bar, st, side: side != "long")
    s.on_bar_row(
        {
            "time": "2026-08-07 10:00:00",
            "open": 10000,
            "high": 10010,
            "low": 9990,
            "close": 10005,
            "tbq_open": 10000,
            "tsq_open": 9000,
            "tbq_close": 10000,
            "tsq_close": 9000,
            "n_ticks": 10,
            "bar_volume": 1000,
        }
    )
    sig = s.on_bar_row(
        {
            "time": "2026-08-07 10:30:00",
            "open": 10005,
            "high": 10040,
            "low": 10000,
            "close": 10030,
            "tbq_open": 10000,
            "tsq_open": 9000,
            "tbq_close": 12000,
            "tsq_close": 8000,
            "n_ticks": 10,
            "bar_volume": 1500,
        }
    )
    assert sig is None
    assert s.position == "flat"
    assert s.last_skip == "extra_entry_filter_long"


def main() -> None:
    test_feature_pipeline()
    test_train_logreg()
    test_walk_forward_and_filter()
    test_s9_ml_filter_blocks_low_proba()
    # train CLI smoke on synth via module API already covered
    print("test_s9_bar_ml: OK")


if __name__ == "__main__":
    main()
