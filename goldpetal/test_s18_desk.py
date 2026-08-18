"""S18 ML tab: propose after charges (tax excluded), Approve → paper, never live."""

from __future__ import annotations

import json
from pathlib import Path

from analytics.env_bridge import read_env
from control_state import load_state
from proposals import add_proposal, proposal_from_weekly_s18
from s11_desk import decide_proposal_for_desk, ml_desk_payload
from s18_ohlc_vol_htf import BASE_PACK


def _winner(*, name: str = "plus_tbq", after_charges: float = 4200.0) -> dict:
    pack = BASE_PACK.as_dict()
    pack["name"] = name
    pack["require_tbq_lead"] = True
    return {
        "pack": pack,
        "test": {
            "n_trades": 4,
            "win_rate": 50.0,
            "gross_pnl_inr": 5000.0,
            "after_charges_inr": after_charges,
            "after_tax_inr": 3000.0,
            "fees_inr": 800.0,
        },
    }


def _current(*, after_charges: float = 1700.0) -> dict:
    return {
        "pack": BASE_PACK.as_dict(),
        "test": {
            "n_trades": 3,
            "win_rate": 33.3,
            "gross_pnl_inr": 2000.0,
            "after_charges_inr": after_charges,
            "after_tax_inr": 1200.0,
            "fees_inr": 300.0,
        },
    }


def test_proposal_ranks_after_charges_not_tax() -> None:
    p = proposal_from_weekly_s18(
        week_id="2026-W33",
        winner=_winner(),
        current=_current(),
        model_path="data/learn/s18/proposed.json",
        safety_ok=True,
        safety_reasons=["ranked on after-charges PnL (tax excluded)"],
    )
    assert p.strategy == "S18_OHLC_VOL_HTF"
    assert p.paper.after_tax_pnl_inr == 4200.0
    assert p.paper.extra["after_charges_inr"] == 4200.0
    assert p.paper.extra["after_tax_inr"] == 3000.0
    assert p.paper.extra["metric"] == "after_charges_ex_tax"
    assert p.paper.delta_vs_baseline_inr == 2500.0
    assert p.env_patch["ENABLE_S18"] == "true"
    assert p.env_patch["DRY_RUN"] == "true"
    assert "tax excluded" in p.summary
    assert p.paper.extra["pack"]["name"] == "plus_tbq"


def test_approve_paper_writes_active_and_keeps_dry_run(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("DRY_RUN=true\nENABLE_S18=false\n", encoding="utf-8")
    props = tmp_path / "control" / "proposals.json"
    state = tmp_path / "control" / "state.json"
    pack_path = tmp_path / "data" / "learn" / "s18" / "active.json"
    prop = proposal_from_weekly_s18(
        week_id="2026-W33",
        winner=_winner(),
        current=_current(),
        model_path="data/learn/s18/proposed.json",
        safety_ok=True,
        safety_reasons=["ok"],
    )
    prop.id = "s18ok"
    add_proposal(prop, path=props)
    payload = ml_desk_payload(root=tmp_path, env_path=env, proposals_path=props)
    assert payload["s18"]["pack_name"] == "base"
    pending = payload["proposals"]["pending"]
    assert len(pending) == 1
    assert pending[0]["pnl_label"] == "After charges ₹"
    assert pending[0]["pnl_value"] == 4200.0
    assert pending[0]["same_as_loaded"] is False

    res = decide_proposal_for_desk(
        "s18ok",
        "approved_paper",
        note="paper it",
        proposals_path=props,
        state_path=state,
        env_path=env,
        root=tmp_path,
        s18_pack_path=pack_path,
    )
    assert res["ok"] is True
    assert res["restart_needed"] is True
    assert res["s18_applied"]["ok"] is True
    assert pack_path.is_file()
    blob = json.loads(pack_path.read_text(encoding="utf-8"))
    assert blob["pack"]["name"] == "plus_tbq"
    assert blob["live"] is False
    snap = read_env(env)
    assert snap["ENABLE_S18"] == "true"
    assert snap["DRY_RUN"] == "true"
    st = load_state(path=state)
    assert "S18_OHLC_VOL_HTF" in st.paper_approved
    assert "S18_OHLC_VOL_HTF" not in (st.live_approved or [])
    after = ml_desk_payload(root=tmp_path, env_path=env, proposals_path=props)
    assert after["s18"]["pack_name"] == "plus_tbq"


def test_reject_leaves_active_pack_running(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("DRY_RUN=true\nENABLE_S18=true\n", encoding="utf-8")
    props = tmp_path / "control" / "proposals.json"
    state = tmp_path / "control" / "state.json"
    pack_path = tmp_path / "data" / "learn" / "s18" / "active.json"
    pack_path.parent.mkdir(parents=True, exist_ok=True)
    pack_path.write_text(
        json.dumps({"pack": BASE_PACK.as_dict(), "paper": True, "live": False}, indent=2),
        encoding="utf-8",
    )
    prop = proposal_from_weekly_s18(
        week_id="2026-W33",
        winner=_winner(name="plus_wick"),
        current=_current(),
        model_path="data/learn/s18/proposed.json",
        safety_ok=True,
        safety_reasons=["ok"],
    )
    prop.id = "s18no"
    add_proposal(prop, path=props)
    res = decide_proposal_for_desk(
        "s18no",
        "rejected",
        note="keep base",
        proposals_path=props,
        state_path=state,
        env_path=env,
        root=tmp_path,
        s18_pack_path=pack_path,
    )
    assert res["ok"] is True
    assert res["env_applied"] is None
    assert json.loads(pack_path.read_text(encoding="utf-8"))["pack"]["name"] == "base"
    assert read_env(env)["ENABLE_S18"] == "true"
    st = load_state(path=state)
    assert "S18_OHLC_VOL_HTF" not in (st.force_disabled or [])


def test_approve_live_blocked_for_s18(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("DRY_RUN=true\n", encoding="utf-8")
    props = tmp_path / "control" / "proposals.json"
    state = tmp_path / "control" / "state.json"
    pack_path = tmp_path / "data" / "learn" / "s18" / "active.json"
    prop = proposal_from_weekly_s18(
        week_id="2026-W33",
        winner=_winner(),
        current=_current(),
        model_path="data/learn/s18/proposed.json",
        safety_ok=True,
        safety_reasons=["ok"],
    )
    prop.id = "s18live"
    add_proposal(prop, path=props)
    res = decide_proposal_for_desk(
        "s18live",
        "approved_live",
        proposals_path=props,
        state_path=state,
        env_path=env,
        root=tmp_path,
        s18_pack_path=pack_path,
    )
    assert res["ok"] is False
    assert res.get("live_blocked") is True
    assert not pack_path.exists()
    from proposals import get_proposal

    still = get_proposal("s18live", path=props)
    assert still is not None
    assert still.status == "pending"


if __name__ == "__main__":
    import tempfile

    test_proposal_ranks_after_charges_not_tax()
    with tempfile.TemporaryDirectory() as td:
        test_approve_paper_writes_active_and_keeps_dry_run(Path(td))
    with tempfile.TemporaryDirectory() as td:
        test_reject_leaves_active_pack_running(Path(td))
    with tempfile.TemporaryDirectory() as td:
        test_approve_live_blocked_for_s18(Path(td))
    print("all s18 desk tests passed")
