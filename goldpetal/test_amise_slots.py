"""AMISE S21–S24 slots — paper after Lab Approve. Never DRY_RUN=false."""

from __future__ import annotations

from pathlib import Path

from amise_slots import (
    AMISE_SLOT_BOOKS,
    assign_slot,
    load_slot_genome,
    next_free_slot,
    slot_env_patch,
)
from control_state import ALL_STRATEGY_NAMES, SLIM_PAPER_STRATEGIES
from live_readiness import PAPER_ONLY_BOOKS
from strategy_amise import AmiseSlotStrategy, vol_to_flow
from strategy_genome import StrategyGenome
from s18_ohlc_vol_htf import VolBar


def _g() -> StrategyGenome:
    return StrategyGenome(
        name="hh-hl",
        entry_long=("hh", "hl"),
        entry_short=("lh", "ll"),
        no_trade=(),
        direction="both",
        params={"lookback": 1, "atr_n": 1},
    ).normalized()


def test_assign_fills_s21_then_s22(tmp_path: Path) -> None:
    a = assign_slot(_g(), proposal_id="p1", folder=tmp_path)
    assert a["ok"] is True
    assert a["slot"] == "S21_AMISE"
    assert a["env_patch"]["DRY_RUN"] == "true"
    assert a["env_patch"]["ENABLE_S21"] == "true"
    g = load_slot_genome("S21_AMISE", tmp_path)
    assert g is not None and g.genome_id == a["genome_id"]
    b = assign_slot(
        StrategyGenome(name="other", entry_long=("bull",), entry_short=("bear",)).normalized(),
        proposal_id="p2",
        folder=tmp_path,
    )
    assert b["slot"] == "S22_AMISE"
    same = assign_slot(_g(), proposal_id="p1b", folder=tmp_path)
    assert same["slot"] == "S21_AMISE"


def test_slots_full(tmp_path: Path) -> None:
    for i, name in enumerate(("a", "b", "c", "d")):
        assign_slot(
            StrategyGenome(name=name, entry_long=("hh",), entry_short=("lh",), params={"rvol": 1.0 + i}).normalized(),
            folder=tmp_path,
        )
    assert next_free_slot(tmp_path) is None
    extra = assign_slot(
        StrategyGenome(name="e", entry_long=("vol_up",), entry_short=("vol_up",)).normalized(),
        folder=tmp_path,
    )
    assert extra["ok"] is False


def test_slot_strategy_flips_on_hh_hl() -> None:
    g = _g()
    s = AmiseSlotStrategy("S21_AMISE", g, seed=False)
    b0 = VolBar("2026-08-18 10:00:00", 100, 101, 99, 100.5, 100, 10, 80, 40)
    b1 = VolBar("2026-08-18 11:00:00", 100.5, 103, 100.6, 102.5, 150, 12, 90, 30)
    s._flow_bars = [vol_to_flow(b0)]
    out = s._decide_closed(b1)
    assert out is not None
    assert out.action == "BUY"
    assert s.position == "long"


def test_names_are_paper_slots_not_the_engine() -> None:
    for name in AMISE_SLOT_BOOKS:
        assert name in ALL_STRATEGY_NAMES
        assert name in SLIM_PAPER_STRATEGIES
        assert name not in PAPER_ONLY_BOOKS
    assert "AMISE" not in ALL_STRATEGY_NAMES
    assert slot_env_patch("S21_AMISE") == {"DRY_RUN": "true", "ENABLE_S21": "true"}


if __name__ == "__main__":
    import tempfile
    from pathlib import Path as P

    td = P(tempfile.mkdtemp())
    (td / "a").mkdir()
    (td / "b").mkdir()
    test_assign_fills_s21_then_s22(td / "a")
    test_slots_full(td / "b")
    test_slot_strategy_flips_on_hh_hl()
    test_names_are_paper_slots_not_the_engine()
    print("ALL test_amise_slots OK")
