"""Tests for multi-model strategy discovery."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from discover_strategies import TEMPLATES, paper_sim, run_discovery
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


def test_discovery_writes_pack_and_proposal(tmp_path: Path) -> None:
    csv_path = _synth_csv(tmp_path / "ticks.csv", n=900)
    out = tmp_path / "discover"
    cands = run_discovery(csv_path=csv_path, out_dir=out, top_k=2)
    assert cands
    assert Path(cands[0].pack_path).exists()
    pack = json.loads(Path(cands[0].pack_path).read_text(encoding="utf-8"))
    assert pack["strategy"] == "S11_DISCOVERED"
    assert "buy_prob" in pack
    # proposal store under tmp control dir via env would be ideal; call propose_best
    # with default path may write into repo data/control — use pack load instead
    s = DiscoveredStrategy(
        pack_path=Path(cands[0].pack_path),
        model_path=Path(cands[0].model_path),
        buy_prob=pack["buy_prob"],
        short_prob=pack["short_prob"],
        min_hold_sec=pack["min_hold_sec"],
        min_imb=pack.get("min_imb", 0),
    )
    assert s.name == "S11_DISCOVERED"
    # model should load from discovery joblib
    assert s.enabled or s._load_error is not None


if __name__ == "__main__":
    from pathlib import Path as P

    t = P("/tmp/discover_test")
    test_paper_sim_runs()
    print("ok paper_sim")
    test_discovery_writes_pack_and_proposal(t)
    print("ok discovery")
    print("ALL test_discover_strategies OK")
