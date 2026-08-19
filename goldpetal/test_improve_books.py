"""Closed-trade / same-slot improve. Never ENABLE. Never DRY_RUN=false."""

from __future__ import annotations

from pathlib import Path

from amise_slots import assign_slot, load_slot_genome, next_free_slot, write_slot
from control_state import load_state
from improve_books import FORMULA_LOCKED, book_improve_plan, improve_roster, run_improve
from proposals import add_proposal, get_proposal
from research_desk import decide_research
from research_factory import proposal_from_challenger
from strategy_genome import RESEARCH_KIND, StrategyGenome


def _g(name: str = "hh-hl", **kw) -> StrategyGenome:
    raw = dict(
        name=name,
        entry_long=("hh", "hl"),
        entry_short=("lh", "ll"),
        no_trade=(),
        direction="both",
        params={"rvol": 1.2, "lookback": 1, "atr_n": 1},
    )
    raw.update(kw)
    return StrategyGenome(**raw).normalized()


def test_formula_locked_and_roster() -> None:
    s13 = book_improve_plan("S13_HHHL_DAY")
    assert s13["mode"] == "hour_gate"
    assert "Formulas stay" in s13["note"] or "formulas stay" in s13["note"].lower()
    s16 = book_improve_plan("S16_HHHL_WICK_1H")
    assert s16["mode"] == "hour_gate"
    s18 = book_improve_plan("S18_OHLC_VOL_HTF")
    assert s18["mode"] == "s18_pack"
    assert s18["approve"] == "ML tab"
    empty = book_improve_plan("S21_AMISE", filled=False)
    assert empty["mode"] == "empty"
    filled = book_improve_plan("S21_AMISE", filled=True)
    assert filled["mode"] == "amise_mutate"
    assert filled["approve"] == "Lab"
    assert "S13_HHHL_DAY" in FORMULA_LOCKED
    roster = {r["book"] for r in improve_roster()}
    assert "S5_MINEDGE" in roster
    assert "S8_NET_ZIGZAG" in roster
    assert "S13_HHHL_DAY" in roster
    assert "S16_HHHL_WICK_1H" in roster
    assert "S18_OHLC_VOL_HTF" in roster
    assert "S21_AMISE" in roster
    from research_desk import research_desk_payload

    desk = research_desk_payload()
    assert "improve" in desk
    books = {r["book"] for r in (desk.get("improve") or {}).get("roster") or []}
    assert "S16_HHHL_WICK_1H" in books
    assert "S21_AMISE" in books


def test_improve_proposal_targets_same_slot() -> None:
    row = {
        "genome": _g("mut-tight", params={"rvol": 1.4, "lookback": 1, "atr_n": 1}).to_dict(),
        "metrics": {
            "n_trades": 40,
            "after_charges": 1800.0,
            "win_rate": 0.62,
            "profit_factor": 1.8,
            "stability": "3/3",
        },
        "robustness": {"cost_2x_pass": True, "cost_3x_pass": True},
        "supervisor": "tighter rvol beat the chair",
        "fails": [],
    }
    prop = proposal_from_challenger(
        row,
        week_id="improve-test:S21",
        target_slot="S21_AMISE",
        parent_after_charges=1200.0,
    )
    assert prop.kind == RESEARCH_KIND
    assert prop.env_patch == {"DRY_RUN": "true"}
    assert "ENABLE" not in "".join(prop.env_patch.keys())
    extra = prop.paper.extra
    assert extra["improve"] is True
    assert extra["target_slot"] == "S21_AMISE"
    assert extra["live"] is False
    assert extra["enable"] is False
    assert "Improve" in prop.title
    assert extra["parent_after_charges"] == 1200.0


def test_lab_approve_improve_overwrites_s21_not_s22(tmp_path: Path) -> None:
    props = tmp_path / "proposals.json"
    env = tmp_path / ".env"
    state = tmp_path / "state.json"
    slots = tmp_path / "slots"
    env.write_text("DRY_RUN=true\nENABLE_S21=true\nENABLE_S22=false\n", encoding="utf-8")
    parent = _g("orig")
    assigned = assign_slot(parent, proposal_id="first", folder=slots)
    assert assigned["slot"] == "S21_AMISE"
    assert next_free_slot(slots) == "S22_AMISE"
    better = _g("better", params={"rvol": 1.6, "lookback": 1, "atr_n": 1})
    assert better.genome_id != parent.genome_id
    prop = proposal_from_challenger(
        {
            "genome": better.to_dict(),
            "metrics": {"n_trades": 30, "after_charges": 2000.0, "win_rate": 0.6},
            "supervisor": "same chair",
            "fails": [],
        },
        week_id="improve-s21",
        target_slot="S21_AMISE",
        parent_after_charges=100.0,
    )
    add_proposal(prop, path=props)
    live = decide_research(prop.id, "approved_live", proposals_path=props)
    assert live["ok"] is False
    assert live.get("live_blocked") is True
    still = get_proposal(prop.id, path=props)
    assert still is not None and still.status == "pending"
    res = decide_research(
        prop.id,
        "approved_paper",
        note="keep S21",
        proposals_path=props,
        env_path=env,
        slots_dir=slots,
        state_path=state,
        apply_env=True,
        queue_live=True,
        sync_environ=False,
    )
    assert res["ok"] is True
    assert res["slot"] == "S21_AMISE"
    assert res["live_unlocked"] is False
    assert res["dry_run_forced"] is True
    assert "Updated" in (res.get("reminder") or "")
    g = load_slot_genome("S21_AMISE", slots)
    assert g is not None
    assert g.genome_id == better.genome_id
    assert load_slot_genome("S22_AMISE", slots) is None
    assert next_free_slot(slots) == "S22_AMISE"
    text = env.read_text(encoding="utf-8")
    assert "DRY_RUN=true" in text
    assert "DRY_RUN=false" not in text
    assert "ENABLE_S21=true" in text
    st = load_state(state)
    assert "S21_AMISE" in st.paper_approved


def test_write_slot_overwrites_chair(tmp_path: Path) -> None:
    a = assign_slot(_g("a"), folder=tmp_path)
    b = write_slot(a["slot"], _g("b", params={"rvol": 2.0}), folder=tmp_path)
    assert b["ok"] is True
    assert b["slot"] == "S21_AMISE"
    assert b["overwritten"] is True
    assert b["env_patch"]["DRY_RUN"] == "true"
    g = load_slot_genome("S21_AMISE", tmp_path)
    assert g is not None and g.name.startswith("b")


def test_run_improve_empty_slots_does_not_enable(tmp_path: Path) -> None:
    slots = tmp_path / "slots"
    slots.mkdir()
    out = tmp_path / "improve.json"
    props = tmp_path / "proposals.json"
    payload = run_improve(
        [],
        db=tmp_path / "ticks.db",
        propose=True,
        proposals_path=props,
        slots_folder=slots,
        skip_s18=True,
        skip_amise=False,
        out_path=out,
    )
    assert payload["live"] is False
    assert payload["enable"] is False
    assert payload["dry_run_required"] is True
    assert payload["proposed_ids"] == []
    assert "S13_HHHL_DAY" in payload["formula_locked"]
    assert not props.exists() or get_proposal("nope", path=props) is None
    assert out.is_file()


if __name__ == "__main__":
    from pathlib import Path as P
    import tempfile

    test_formula_locked_and_roster()
    test_improve_proposal_targets_same_slot()
    td = P(tempfile.mkdtemp())
    for name in ("a", "b", "c"):
        (td / name).mkdir()
    test_lab_approve_improve_overwrites_s21_not_s22(td / "a")
    test_write_slot_overwrites_chair(td / "b")
    test_run_improve_empty_slots_does_not_enable(td / "c")
    print("ALL test_improve_books OK")
