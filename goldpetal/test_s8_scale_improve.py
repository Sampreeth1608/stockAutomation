"""Tests for at-scale S8 improvement (walk-forward + reasoner features)."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from s8_scale_improve import (
    REASON_FEATURE_COLS,
    add_multistep_path_labels,
    add_reasoner_features,
    build_scale_frame,
    plan_curriculum,
    walk_forward_train,
)
from s9_bar_ml import bars_to_frame, build_features

IST = ZoneInfo("Asia/Kolkata")


def _bars(n: int = 200) -> list[dict]:
    rows = []
    t0 = datetime(2026, 7, 1, 9, 0, tzinfo=IST)
    px, tbq, tsq = 10000.0, 12000.0, 10000.0
    for i in range(n):
        if i % 8 < 5:
            px += 7
            tbq += 160
            tsq += 50
        else:
            px -= 5
            tbq += 50
            tsq += 140
        net = tbq - tsq
        rows.append(
            {
                "time": (t0 + timedelta(minutes=10 * i)).isoformat(),
                "open": px - 2,
                "high": px + 3,
                "low": px - 3,
                "close": px,
                "tbq": tbq,
                "tsq": tsq,
                "net": net,
                "imb_pct": abs(net) / max(tbq, tsq) * 100,
                "range_pts": 6.0,
                "bar_volume": 200.0,
            }
        )
    return rows


def test_reasoner_features_present():
    df = build_features(bars_to_frame(_bars(80)), lags=2)
    rz = add_reasoner_features(df, tp=20, sl=25)
    for c in REASON_FEATURE_COLS:
        assert c in rz.columns


def test_multistep_labels():
    df = build_features(bars_to_frame(_bars(100)), lags=2)
    df = add_reasoner_features(df)
    lab = add_multistep_path_labels(df, horizon=6, tp_pts=12, sl_pts=18)
    assert "y_path" in lab.columns
    assert lab["y_path"].dropna().isin([0.0, 1.0]).all()


def test_walk_forward():
    frame = build_scale_frame(_bars(220), lags=2, horizon=6, tp_pts=12, sl_pts=18)
    bundle, folds = walk_forward_train(frame, lags=2, folds=4, hidden=(32, 16))
    assert bundle["version"] >= 3
    assert bundle["metrics"]["feature_count"] >= len(REASON_FEATURE_COLS)
    assert "auc" in bundle["metrics"]
    cur = plan_curriculum(bundle["metrics"], folds)
    assert any(r["domain"] == "planning" for r in cur)


if __name__ == "__main__":
    test_reasoner_features_present()
    test_multistep_labels()
    test_walk_forward()
    print("test_s8_scale_improve: OK")
