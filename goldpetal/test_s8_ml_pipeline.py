"""Tests for full S8 ML pipeline stages."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from s8_ml_pipeline import (
    STAGE_MAP,
    ensure_feedback_template,
    safety_check,
    train_full_pipeline,
)

IST = ZoneInfo("Asia/Kolkata")


def _bars(n: int = 140) -> list[dict]:
    rows = []
    t0 = datetime(2026, 8, 1, 9, 0, tzinfo=IST)
    px, tbq, tsq = 10000.0, 12000.0, 10000.0
    for i in range(n):
        if i % 7 < 4:
            px += 8
            tbq += 180
            tsq += 40
        else:
            px -= 6
            tbq += 40
            tsq += 160
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
                "bar_volume": 220.0,
            }
        )
    return rows


def test_stage_map_complete():
    for key in (
        "instruction_tuning",
        "human_feedback",
        "preference_optimization",
        "reasoning_training",
        "safety_training",
        "tool_use",
        "conversation",
        "optimization",
    ):
        assert key in STAGE_MAP


def test_train_full_pipeline(tmp_path: Path | None = None):
    bundle = train_full_pipeline(_bars(140), lags=2, horizon=4, tp_pts=15, sl_pts=20, feedback=[])
    assert bundle["metrics"]["n_total"] >= 30
    assert "accuracy" in bundle["metrics"]
    assert "heads" in bundle
    assert bundle["version"] >= 2


def test_safety_blocks_low_auc():
    rep = safety_check(
        {"auc": 0.51},
        nn_sum_inr=1000.0,
        baseline_sum_inr=-500.0,
        nn_n=10,
        min_auc=0.55,
    )
    assert rep.ok_to_enable_nn is False


def test_safety_allows_good():
    rep = safety_check(
        {"auc": 0.62},
        nn_sum_inr=5000.0,
        baseline_sum_inr=-1000.0,
        nn_n=8,
        min_auc=0.55,
    )
    assert rep.ok_to_enable_nn is True


def test_feedback_template(tmp_path: Path):
    p = tmp_path / "feedback.csv"
    ensure_feedback_template(p)
    assert p.exists()
    text = p.read_text(encoding="utf-8")
    assert "preference" in text


if __name__ == "__main__":
    test_stage_map_complete()
    test_train_full_pipeline()
    test_safety_blocks_low_auc()
    test_safety_allows_good()
    from tempfile import TemporaryDirectory

    with TemporaryDirectory() as d:
        test_feedback_template(Path(d))
    print("test_s8_ml_pipeline: OK")
