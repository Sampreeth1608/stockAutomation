"""FLOW_BRAIN next-move lab. ENABLE stays false. Not live. Not S7_HOURLY."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from control_state import ALL_STRATEGY_NAMES, SLIM_PAPER_STRATEGIES
from flow_brain import BULL_CONT, BOOK, scratchy_then_trend_samples
from flow_brain_next import (
    HORIZONS,
    TRANSFORMER_NOTE,
    format_lab,
    implied_cont,
    label_tape,
    rich_from_quads,
    run_lab,
    surface_cells,
    synthetic_ticks,
    write_learn_db,
)
from live_readiness import PAPER_ONLY_BOOKS
from market_mood import CORE_FIT_BOOKS


def _trend_start_t() -> float:
    t0 = datetime(2026, 8, 17, 10, 0, 0)
    return (t0 + timedelta(minutes=25)).timestamp()


def test_labels_have_horizons_and_sequence() -> None:
    ticks = synthetic_ticks()
    assert len(ticks) == len(scratchy_then_trend_samples())
    labels = label_tape(ticks, session_filter=False, with_mood=True)
    assert len(labels) > 200
    row = labels[len(labels) // 2]
    for hz in HORIZONS:
        assert hz in row.fwd
    assert row.state in {
        "bull_cont",
        "bull_weak",
        "bear_cont",
        "bear_weak",
        "absorb_buy",
        "absorb_sell",
        "mixed",
    }
    assert 0.0 <= row.seq_up_frac <= 1.0
    assert row.mood
    assert implied_cont(BULL_CONT) == "long"


def test_trend_bull_cont_precedes_large_1m() -> None:
    ticks = synthetic_ticks()
    labels = label_tape(ticks, session_filter=False, with_mood=False)
    cut = _trend_start_t()
    fwd = [
        float(x.fwd[60])
        for x in labels
        if x.t >= cut and x.state == BULL_CONT and x.fwd.get(60) is not None
    ]
    assert len(fwd) >= 8
    assert sum(fwd) / len(fwd) > 20.0
    cover = sum(1 for p in fwd if abs(p) >= 5.0) / len(fwd)
    assert cover > 0.8


def test_surface_and_models_on_toy() -> None:
    report = run_lab(
        synthetic_ticks(),
        lots=100.0,
        fees=True,
        session_filter=False,
        with_mood=True,
        with_models=True,
        model_horizon=30,
    )
    assert report["n_labels"] > 200
    assert report["fee_be_pts"] > 0
    bull = report["tables"]["cont"][5]["cells"].get(BULL_CONT)
    assert bull is not None
    assert bull["n"] >= 20
    assert bull["up_pct"] > 50.0
    text = format_lab(report)
    assert "auc_up" in text
    assert "Transformer" in text or "transformer" in text.lower()
    assert "ENABLE_FLOW_BRAIN stays false" in text
    kinds = {m["kind"] for m in report["models"]}
    assert "logreg" in kinds
    assert "random_forest" in kinds
    assert "grad_boost" in kinds
    aucs = [
        m["auc_up"]
        for m in report["models"]
        if isinstance(m.get("auc_up"), float)
    ]
    assert aucs
    assert max(aucs) > 0.55


def test_learn_db_does_not_auto_approve() -> None:
    report = run_lab(
        synthetic_ticks(),
        lots=100.0,
        fees=True,
        session_filter=False,
        with_mood=False,
        with_models=True,
        model_horizon=30,
    )
    path = Path("/tmp/flow_brain_learn_test.db")
    if path.exists():
        path.unlink()
    write_learn_db(
        path,
        report["labels"],
        models=report["models"],
        has_edge=bool(report["has_edge"]),
        edge_states=list(report["edges"]),
    )
    with sqlite3.connect(path) as con:
        n = con.execute("select count(*) from labels").fetchone()[0]
        approved = [
            r[0] for r in con.execute("select approved from models").fetchall()
        ]
    assert n > 50
    assert approved
    assert all(int(a) == 0 for a in approved)


def test_nonoverlap_book_uses_charges() -> None:
    labels = label_tape(synthetic_ticks(), session_filter=False, with_mood=False)
    cells = surface_cells(
        labels,
        horizon=60,
        lots=100.0,
        fees=True,
        side_of=implied_cont,
    )
    bull = cells.get(BULL_CONT)
    assert bull is not None
    if bull["n_side"] >= 1:
        assert abs(bull["gross"] - (bull["after"] + bull["fees"])) < 1.0


def test_wired_mood_and_enable_off() -> None:
    assert BOOK in CORE_FIT_BOOKS
    assert BOOK in ALL_STRATEGY_NAMES
    assert BOOK not in SLIM_PAPER_STRATEGIES
    assert BOOK in PAPER_ONLY_BOOKS
    root = Path(__file__).resolve().parent
    portfolio = (root / "portfolio.py").read_text(encoding="utf-8")
    assert 'on("ENABLE_FLOW_BRAIN", "false")' in portfolio
    env_ex = (root / ".env.example").read_text(encoding="utf-8")
    # VM git-show copy of a few files; .env.example may be stale.
    if "ENABLE_FLOW_BRAIN" in env_ex:
        assert "ENABLE_FLOW_BRAIN=false" in env_ex
    mood = (root / "market_mood.py").read_text(encoding="utf-8")
    assert '"FLOW_BRAIN"' in mood
    next_mod = (root / "flow_brain_next.py").read_text(encoding="utf-8")
    assert "torch" not in next_mod
    assert "Transformer" in TRANSFORMER_NOTE
    js = json.dumps({"book": BOOK})
    assert BOOK in js


if __name__ == "__main__":
    test_labels_have_horizons_and_sequence()
    test_trend_bull_cont_precedes_large_1m()
    test_surface_and_models_on_toy()
    test_learn_db_does_not_auto_approve()
    test_nonoverlap_book_uses_charges()
    test_wired_mood_and_enable_off()
    print("flow brain next tests ok")
