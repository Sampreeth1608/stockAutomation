"""Tests for multi-model strategy discovery."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from discover_strategies import (
    TEMPLATES,
    Candidate,
    paper_sim,
    propose_best,
    run_discovery,
)
from strategy_discovered import DiscoveredStrategy


def _synth_csv(path: Path, n: int = 800) -> Path:
    rng = np.random.default_rng(42)
    t0 = pd.Timestamp("2026-08-01 09:00:00")
    rows = []
    px = 15000.0
    for i in range(n):
        # mild drift + noise so classifiers see both classes
        px += float(rng.normal(0.15 if i % 17 else -0.4, 1.5))
        bq = float(rng.uniform(50, 200))
        sq = float(rng.uniform(50, 200))
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
    df = pd.DataFrame(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    return path


def test_paper_sim_runs() -> None:
    n = 200
    ltp = np.linspace(15000, 15100, n)
    prob = np.clip(np.linspace(0.2, 0.8, n) + np.sin(np.linspace(0, 12, n)) * 0.1, 0, 1)
    imb = np.zeros(n)
    sim = paper_sim(ltp=ltp, prob=prob, imb=imb, template=TEMPLATES[1])
    assert "n_trades" in sim
    assert sim["n_trades"] >= 0
    assert "after_charges_pnl" in sim
    assert "after_tax_pnl" in sim
    if sim["n_trades"]:
        assert sim["after_charges_pnl"] >= sim["after_tax_pnl"]


def _cand(path: str, after_charges: float, *, safety_ok: bool = True) -> Candidate:
    return Candidate(
        model_name="logreg",
        template=TEMPLATES[1],
        auc=0.62,
        n_trades=12,
        win_rate=0.55,
        gross_pnl=after_charges + 80,
        after_tax_pnl=after_charges * 0.7,
        after_charges_pnl=after_charges,
        safety_ok=safety_ok,
        pack_path=path,
        model_path=path.replace(".json", ".joblib"),
    )


def test_propose_first_pack_then_only_if_beats(tmp_path: Path) -> None:
    props = tmp_path / "proposals.json"
    first = propose_best(
        [_cand(str(tmp_path / "data/discover/packs/a.json"), 400.0)],
        current=None,
        proposals_path=props,
    )
    assert first is not None
    assert first.kind == "new"
    assert first.paper.extra["metric"] == "after_charges_ex_tax"
    assert first.paper.extra["after_charges_inr"] == 400.0
    assert first.paper.after_tax_pnl_inr == 400.0
    assert first.env_patch["DRY_RUN"] == "true"
    assert first.env_patch["ENABLE_S11"] == "true"

    worse = propose_best(
        [_cand(str(tmp_path / "data/discover/packs/b.json"), 300.0)],
        current={
            "ok": True,
            "path": str(tmp_path / "data/discover/packs/a.json"),
            "pack_id": "a",
            "after_charges_pnl": 400.0,
        },
        proposals_path=props,
    )
    assert worse is None

    better = propose_best(
        [_cand(str(tmp_path / "data/discover/packs/c.json"), 520.0)],
        current={
            "ok": True,
            "path": str(tmp_path / "data/discover/packs/a.json"),
            "pack_id": "a",
            "after_charges_pnl": 400.0,
        },
        proposals_path=props,
    )
    assert better is not None
    assert better.kind == "improved"
    assert better.paper.delta_vs_baseline_inr == 120.0
    assert "tax excluded" in better.summary

    blocked = propose_best(
        [_cand(str(tmp_path / "data/discover/packs/d.json"), 900.0)],
        current={"ok": False, "path": str(tmp_path / "data/discover/packs/a.json"), "error": "model missing"},
        proposals_path=props,
    )
    assert blocked is None

    unsafe = propose_best(
        [_cand(str(tmp_path / "data/discover/packs/e.json"), 900.0, safety_ok=False)],
        current=None,
        proposals_path=props,
    )
    assert unsafe is None


def test_discovery_writes_pack_and_proposal(tmp_path: Path) -> None:
    csv_path = _synth_csv(tmp_path / "ticks.csv", n=900)
    out = tmp_path / "discover"
    cands = run_discovery(csv_path=csv_path, out_dir=out, top_k=2)
    assert cands
    assert Path(cands[0].pack_path).exists()
    pack = json.loads(Path(cands[0].pack_path).read_text(encoding="utf-8"))
    assert pack["strategy"] == "S11_DISCOVERED"
    assert "buy_prob" in pack
    assert "after_charges_pnl" in (pack.get("paper") or {})
    report = json.loads((out / "latest_report.json").read_text(encoding="utf-8"))
    assert report["metric"] == "after_charges_ex_tax"
    assert "after_charges_pnl" in report["candidates"][0]
    round2 = tmp_path / "discover2"
    run_discovery(
        csv_path=csv_path,
        out_dir=round2,
        top_k=1,
        loaded_pack_path=cands[0].pack_path,
    )
    report2 = json.loads((round2 / "latest_report.json").read_text(encoding="utf-8"))
    assert report2["current_pack"]["ok"] is True
    assert "after_charges_pnl" in report2["current_pack"]
    assert (out / "behavior_report.json").exists()
    behavior = json.loads((out / "behavior_report.json").read_text(encoding="utf-8"))
    assert behavior.get("families")
    assert behavior.get("recipes")
    assert behavior.get("reasoning")
    s = DiscoveredStrategy(
        pack_path=Path(cands[0].pack_path),
        model_path=Path(cands[0].model_path),
        buy_prob=pack["buy_prob"],
        short_prob=pack["short_prob"],
        min_hold_sec=pack["min_hold_sec"],
        min_imb=pack.get("min_imb", 0),
    )
    assert s.name == "S11_DISCOVERED"
    assert s.enabled or s._load_error is not None


if __name__ == "__main__":
    from pathlib import Path as P

    t = P("/tmp/discover_test")
    test_paper_sim_runs()
    print("ok paper_sim")
    test_propose_first_pack_then_only_if_beats(t)
    print("ok propose_best")
    test_discovery_writes_pack_and_proposal(t)
    print("ok discovery")
    print("ALL test_discover_strategies OK")
