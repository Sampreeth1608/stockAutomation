"""AMISE S21, S22, … slots — paper after Lab Approve. Never DRY_RUN=false."""

from __future__ import annotations

from pathlib import Path

from amise_slots import (
    AMISE_SLOT_BOOKS,
    amise_books_now,
    assign_slot,
    load_slot_genome,
    next_free_slot,
    slot_env_patch,
    write_slot,
)
from analytics.env_bridge import write_env_updates
from control_state import ALL_STRATEGY_NAMES, SLIM_PAPER_STRATEGIES, paper_strategy_names
from live_readiness import PAPER_ONLY_BOOKS
from strategy_amise import AmiseSlotStrategy, vol_to_flow
from strategy_genome import StrategyGenome, env_patch_is_safe
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
    other = write_slot(
        "S21_AMISE",
        StrategyGenome(name="mut", entry_long=("bull",), entry_short=("bear",)).normalized(),
        folder=tmp_path,
    )
    assert other["slot"] == "S21_AMISE"
    assert other["overwritten"] is True
    g2 = load_slot_genome("S21_AMISE", tmp_path)
    assert g2 is not None and g2.name.startswith("mut")
    assert load_slot_genome("S22_AMISE", tmp_path) is not None


def test_fifth_assign_is_s25(tmp_path: Path) -> None:
    for i, name in enumerate(("a", "b", "c", "d")):
        assign_slot(
            StrategyGenome(
                name=name, entry_long=("hh",), entry_short=("lh",), params={"rvol": 1.0 + i}
            ).normalized(),
            folder=tmp_path,
        )
    assert next_free_slot(tmp_path) == "S25_AMISE"
    extra = assign_slot(
        StrategyGenome(name="e", entry_long=("vol_up",), entry_short=("vol_up",)).normalized(),
        folder=tmp_path,
    )
    assert extra["ok"] is True
    assert extra["slot"] == "S25_AMISE"
    assert extra["env_patch"] == {"DRY_RUN": "true", "ENABLE_S25": "true"}
    assert extra["env_patch"]["DRY_RUN"] != "false"
    assert "S25_AMISE" in amise_books_now(tmp_path)


def test_enable_s25_is_whitelisted(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("DRY_RUN=true\n", encoding="utf-8")
    assert env_patch_is_safe({"DRY_RUN": "true", "ENABLE_S25": "true"})
    res = write_env_updates({"DRY_RUN": "true", "ENABLE_S25": "true"}, path=env)
    assert res["ok"] is True
    assert res["applied"]["ENABLE_S25"] == "true"
    assert "DRY_RUN=false" not in env.read_text(encoding="utf-8")


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
    assert "S11_DISCOVERED" not in SLIM_PAPER_STRATEGIES
    assert "S11_DISCOVERED" not in paper_strategy_names()
    assert slot_env_patch("S21_AMISE") == {"DRY_RUN": "true", "ENABLE_S21": "true"}
    assert slot_env_patch("S25_AMISE") == {"DRY_RUN": "true", "ENABLE_S25": "true"}


if __name__ == "__main__":
    import tempfile
    from pathlib import Path as P

    td = P(tempfile.mkdtemp())
    (td / "a").mkdir()
    (td / "b").mkdir()
    (td / "c").mkdir()
    test_assign_fills_s21_then_s22(td / "a")
    test_fifth_assign_is_s25(td / "b")
    test_enable_s25_is_whitelisted(td / "c")
    test_slot_strategy_flips_on_hh_hl()
    test_names_are_paper_slots_not_the_engine()
    print("ALL test_amise_slots OK")
