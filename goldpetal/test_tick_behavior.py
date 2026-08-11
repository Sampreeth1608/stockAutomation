"""Tests for tick behavior math/stats/reasoning analyzer."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from tick_behavior import (
    FamilyStat,
    analyze_ticks,
    generate_recipes,
    pick_fee_aware_horizon,
    write_behavior_report,
)


def _synth_csv(path: Path, n: int = 600) -> Path:
    rng = np.random.default_rng(7)
    t0 = pd.Timestamp("2026-08-01 09:00:00")
    rows = []
    px = 15000.0
    for i in range(n):
        px += float(rng.normal(0.2 if (i // 40) % 2 == 0 else -0.15, 1.2))
        bq = float(80 + 40 * np.sin(i / 11))
        sq = float(80 + 40 * np.cos(i / 13))
        rows.append(
            {
                "time": (t0 + pd.Timedelta(seconds=i)).isoformat(),
                "ltp": px,
                "open": px,
                "high": px + 2,
                "low": px - 2,
                "close": px,
                "volume": i * 10,
                "last_traded_quantity": 1,
                "average_traded_price": px,
                "total_buy_quantity": bq * 5,
                "total_sell_quantity": sq * 5,
                "buy1_price": px - 1,
                "buy1_qty": bq,
                "buy2_price": px - 2,
                "buy2_qty": bq,
                "buy3_price": px - 3,
                "buy3_qty": bq,
                "buy4_price": px - 4,
                "buy4_qty": bq,
                "buy5_price": px - 5,
                "buy5_qty": bq,
                "sell1_price": px + 1,
                "sell1_qty": sq,
                "sell2_price": px + 2,
                "sell2_qty": sq,
                "sell3_price": px + 3,
                "sell3_qty": sq,
                "sell4_price": px + 4,
                "sell4_qty": sq,
                "sell5_price": px + 5,
                "sell5_qty": sq,
                "open_interest": 1000 + i,
            }
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def test_analyze_ticks_builds_recipes(tmp_path: Path) -> None:
    csv_path = _synth_csv(tmp_path / "ticks.csv")
    report = analyze_ticks(csv_path)
    assert report.n_ticks > 100
    assert report.fee_be_pts > 0
    assert report.families
    assert report.recipes
    assert report.horizon >= 20
    assert "reasoning" in report.to_dict()
    assert report.reasoning.get("steps")
    path = write_behavior_report(report, tmp_path / "behavior_report.json")
    assert path.exists()
    names = {r.name for r in report.recipes}
    assert "ml_topfeat" in names


def test_pick_fee_aware_horizon() -> None:
    # synthetic: ~0.5pt/tick drift → need ~100 ticks for 50pt
    n = 5000
    ltp = pd.Series(np.arange(n, dtype=float) * 0.5 + 15000.0)
    h, mv, table = pick_fee_aware_horizon(ltp, fee_be=50.0)
    assert table
    assert mv >= 40.0
    assert h >= 60


def test_generate_recipes_chop() -> None:
    families = [
        FamilyStat(
            family="book_l1",
            features=["imb_l1"],
            best_feature="imb_l1",
            best_corr=0.08,
            mean_abs_corr=0.08,
            coverage=1.0,
            predictive=True,
            note="ok",
        )
    ]
    recipes = generate_recipes(
        families=families,
        fee_be=50.0,
        atr_pts=70.0,
        regime_mix={"CHOP": 0.55, "TREND": 0.2, "QUIET": 0.2, "WIDE_SPREAD": 0.05},
        top_features=[{"feature": "imb_l1", "corr_fwd_ret": 0.08}],
    )
    assert any(r.name == "chop_meanrev" for r in recipes)


if __name__ == "__main__":
    from pathlib import Path as P

    t = P("/tmp/tick_behavior_test")
    test_analyze_ticks_builds_recipes(t)
    print("ok analyze")
    test_pick_fee_aware_horizon()
    print("ok horizon")
    test_generate_recipes_chop()
    print("ok recipes")
    print("ALL test_tick_behavior OK")
