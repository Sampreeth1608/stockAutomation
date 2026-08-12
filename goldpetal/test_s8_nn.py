"""Tests for S8 weekly MLP neural-net helpers + entry gate."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from s8_nn import (
    add_edge_labels,
    features_from_hist,
    make_mlp,
    predict_edge_proba,
    save_bundle,
    time_split_train,
)
from s9_bar_ml import bars_to_frame, build_features
from strategy_s8_align import AlignS8Config, AlignS8Strategy

IST = ZoneInfo("Asia/Kolkata")


def _synthetic_bars(n: int = 120) -> list[dict]:
    rows = []
    t0 = datetime(2026, 8, 1, 9, 0, tzinfo=IST)
    px = 10000.0
    tbq, tsq = 12000.0, 10000.0
    for i in range(n):
        # alternating mild trend so labels are mixed
        if i % 7 < 4:
            px += 8
            tbq += 180
            tsq += 40
        else:
            px -= 6
            tbq += 40
            tsq += 160
        net = tbq - tsq
        imb = abs(net) / max(tbq, tsq) * 100
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
                "imb_pct": imb,
                "range_pts": 6.0,
                "bar_volume": 220.0,
            }
        )
    return rows


def test_time_split_train_and_predict(tmp_path: Path | None = None):
    bars = _synthetic_bars(140)
    bundle = time_split_train(bars, lags=2, horizon=4, tp_pts=15, sl_pts=20)
    assert bundle["metrics"]["n_total"] >= 30
    assert "accuracy" in bundle["metrics"]
    out = Path("data/models") / "_test_s8_nn_mlp.joblib"
    out.parent.mkdir(parents=True, exist_ok=True)
    save_bundle(bundle, out)
    assert out.exists()
    assert out.with_suffix(".json").exists()

    hist = [
        {
            "px": float(b["close"]),
            "tbq": float(b["tbq"]),
            "tsq": float(b["tsq"]),
            "imb": float(b["imb_pct"]),
            "net": float(b["net"]),
        }
        for b in bars[-40:]
    ]
    feats = features_from_hist(hist, lags=2)
    assert feats is not None
    p = predict_edge_proba(bundle, feats)
    assert 0.0 <= p <= 1.0
    out.unlink(missing_ok=True)
    out.with_suffix(".json").unlink(missing_ok=True)


def test_nn_gate_blocks_when_required_and_missing_model():
    s = AlignS8Strategy(
        AlignS8Config(
            require_nn_filter=True,
            nn_model_path="data/models/does_not_exist_s8.joblib",
            min_imb_pct=0,
            require_rising_imb=False,
            cooldown_ticks=0,
        )
    )
    assert s._nn_allows_entry() is False
    assert s.last_skip and "nn_missing" in s.last_skip


def test_nn_gate_off_by_default():
    s = AlignS8Strategy(AlignS8Config(require_nn_filter=False))
    assert s._nn_allows_entry() is True


def test_add_edge_labels_has_both_classes():
    bars = _synthetic_bars(100)
    df = build_features(bars_to_frame(bars), lags=2)
    lab = add_edge_labels(df, horizon=5, tp_pts=12, sl_pts=18)
    vals = set(lab["y_edge"].dropna().unique().tolist())
    assert vals & {0.0, 1.0}


def test_make_mlp_pipeline():
    pipe = make_mlp(hidden=(8, 4), max_iter=20)
    assert "mlp" in pipe.named_steps


if __name__ == "__main__":
    test_time_split_train_and_predict()
    test_nn_gate_blocks_when_required_and_missing_model()
    test_nn_gate_off_by_default()
    test_add_edge_labels_has_both_classes()
    test_make_mlp_pipeline()
    print("test_s8_nn: OK")
