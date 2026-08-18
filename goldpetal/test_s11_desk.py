"""S11 ML approvals on the HTML desk: paper-safe env apply + pack inspector."""

from __future__ import annotations

import json
from pathlib import Path

from analytics.env_bridge import read_env
from control_state import load_state
from proposals import PaperResult, StrategyProposal, add_proposal
from s11_desk import (
    LIVE_BLOCKED_REASON,
    activate_s11_pack,
    decide_proposal_for_desk,
    list_s11_packs,
    ml_desk_payload,
    pack_summary,
    paper_safe_env_patch,
    to_env_pack_path,
)


def _write_pack(path: Path, *, safety_ok: bool = True, auc: float = 0.62) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "id": path.stem,
        "week_id": "2026-W33",
        "strategy": "S11_DISCOVERED",
        "model_name": "logreg",
        "model_path": "data/discover/models/demo.joblib",
        "features": ["ret_1", "imb_l1", "spread"],
        "buy_prob": 0.58,
        "short_prob": 0.42,
        "min_hold_sec": 30,
        "every_n_ticks": 5,
        "min_imb": 0.0,
        "family": "book",
        "rationale": "imbalance follows",
        "auc": auc,
        "paper": {
            "n_trades": 12,
            "win_rate": 0.58,
            "gross_pnl": 410.0,
            "after_tax_pnl": 280.0,
        },
        "safety_ok": safety_ok,
        "safety_reasons": ["auc=0.62 ok"] if safety_ok else ["auc=0.40<0.55"],
        "behavior_summary": "book imbalance leads short-horizon ticks.",
        "created_at_ist": "2026-08-17T19:00:00+05:30",
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def test_paper_safe_patch_keeps_dry_run_and_blocks_s4() -> None:
    clean, skipped = paper_safe_env_patch(
        {
            "ENABLE_S11": "true",
            "S11_PACK_PATH": "data/discover/packs/demo.json",
            "DRY_RUN": "false",
            "ENABLE_S4": "true",
            "LIVE_UNLOCK": "true",
        }
    )
    assert clean["DRY_RUN"] == "true"
    assert clean["ENABLE_S11"] == "true"
    assert clean["S11_PACK_PATH"] == "data/discover/packs/demo.json"
    assert "ENABLE_S4" not in clean
    assert "DRY_RUN" in skipped
    assert skipped["ENABLE_S4"]
    assert skipped["LIVE_UNLOCK"]


def test_pack_summary_and_list(tmp_path: Path) -> None:
    packs = tmp_path / "data" / "discover" / "packs"
    one = _write_pack(packs / "logreg_book_20260817.json")
    (tmp_path / "data" / "discover" / "models").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "discover" / "models" / "demo.joblib").write_bytes(b"joblib")
    row = pack_summary(one, root=tmp_path)
    assert row["exists"] is True
    assert row["model_name"] == "logreg"
    assert row["auc"] == 0.62
    assert row["n_features"] == 3
    assert row["path"] == "data/discover/packs/logreg_book_20260817.json"
    listed = list_s11_packs(root=tmp_path)
    assert len(listed) == 1
    assert listed[0]["id"] == "logreg_book_20260817"


def test_approve_paper_writes_s11_pack_and_keeps_dry_run(tmp_path: Path) -> None:
    packs = tmp_path / "data" / "discover" / "packs"
    pack = _write_pack(packs / "week.json")
    env_key = to_env_pack_path(pack, root=tmp_path)
    env = tmp_path / ".env"
    env.write_text("DRY_RUN=true\nENABLE_S11=false\nENABLE_S4=false\n", encoding="utf-8")
    props = tmp_path / "control" / "proposals.json"
    state = tmp_path / "control" / "state.json"
    add_proposal(
        StrategyProposal(
            id="s11ok",
            week_id="2026-W33",
            kind="new",
            strategy="S11_DISCOVERED",
            title="S11 behavior+ML demo",
            summary="pack ready",
            paper=PaperResult(n_trades=12, win_rate=0.58, after_tax_pnl_inr=280.0),
            safety_ok=True,
            env_patch={
                "ENABLE_S11": "true",
                "S11_PACK_PATH": env_key,
                "DRY_RUN": "false",
                "ENABLE_S4": "true",
            },
        ),
        path=props,
    )
    res = decide_proposal_for_desk(
        "s11ok",
        "approved_paper",
        note="paper it",
        proposals_path=props,
        state_path=state,
        env_path=env,
        root=tmp_path,
    )
    assert res["ok"] is True
    assert res["restart_needed"] is True
    applied = (res["env_applied"] or {}).get("applied") or {}
    assert applied["S11_PACK_PATH"] == env_key
    assert applied["ENABLE_S11"] == "true"
    assert applied["DRY_RUN"] == "true"
    skipped = (res["env_applied"] or {}).get("skipped") or {}
    assert "ENABLE_S4" in skipped
    snap = read_env(env)
    assert snap["S11_PACK_PATH"] == env_key
    assert snap["DRY_RUN"] == "true"
    assert snap["ENABLE_S4"] == "false"
    st = load_state(path=state)
    assert "S11_DISCOVERED" in st.paper_approved


def test_unsafe_approve_requires_accept(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("DRY_RUN=true\n", encoding="utf-8")
    props = tmp_path / "control" / "proposals.json"
    state = tmp_path / "control" / "state.json"
    add_proposal(
        StrategyProposal(
            id="s11bad",
            week_id="2026-W33",
            kind="new",
            strategy="S11_DISCOVERED",
            title="unsafe",
            summary="gates failed",
            paper=PaperResult(n_trades=2, after_tax_pnl_inr=-20.0),
            safety_ok=False,
            env_patch={"ENABLE_S11": "false", "S11_PACK_PATH": "", "DRY_RUN": "true"},
        ),
        path=props,
    )
    blocked = decide_proposal_for_desk(
        "s11bad",
        "approved_paper",
        proposals_path=props,
        state_path=state,
        env_path=env,
        root=tmp_path,
    )
    assert blocked["ok"] is False
    assert "safety_ok=False" in blocked["error"]
    ok = decide_proposal_for_desk(
        "s11bad",
        "approved_paper",
        accept_unsafe=True,
        proposals_path=props,
        state_path=state,
        env_path=env,
        root=tmp_path,
    )
    assert ok["ok"] is True
    assert ok["status"] == "approved_paper"


def test_approve_live_is_blocked(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("DRY_RUN=true\n", encoding="utf-8")
    props = tmp_path / "control" / "proposals.json"
    state = tmp_path / "control" / "state.json"
    add_proposal(
        StrategyProposal(
            id="s11live",
            week_id="2026-W33",
            kind="new",
            strategy="S11_DISCOVERED",
            title="live no",
            summary="no",
            paper=PaperResult(),
            safety_ok=True,
            env_patch={"DRY_RUN": "false", "ENABLE_S11": "true"},
        ),
        path=props,
    )
    res = decide_proposal_for_desk(
        "s11live",
        "approved_live",
        proposals_path=props,
        state_path=state,
        env_path=env,
        root=tmp_path,
    )
    assert res["ok"] is False
    assert "DRY_RUN=false" in res["error"] or "Live approval" in res["error"]
    from proposals import get_proposal

    still = get_proposal("s11live", path=props)
    assert still is not None
    assert still.status == "pending"
    assert read_env(env)["DRY_RUN"] == "true"
    st = load_state(path=state)
    assert "S11_DISCOVERED" not in (st.live_approved or [])


def test_activate_pack_and_ml_payload(tmp_path: Path) -> None:
    packs = tmp_path / "data" / "discover" / "packs"
    pack = _write_pack(packs / "loadme.json")
    (tmp_path / "data" / "discover" / "models").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "discover" / "models" / "demo.joblib").write_bytes(b"x")
    env = tmp_path / ".env"
    env.write_text("DRY_RUN=true\nENABLE_S11=false\n", encoding="utf-8")
    res = activate_s11_pack(str(pack), env_path=env, root=tmp_path)
    assert res["ok"] is True
    assert res["restart_needed"] is True
    assert read_env(env)["S11_PACK_PATH"] == "data/discover/packs/loadme.json"
    assert read_env(env)["ENABLE_S11"] == "true"
    payload = ml_desk_payload(root=tmp_path, env_path=env, proposals_path=tmp_path / "none.json")
    # none.json missing → proposals_snapshot creates empty store
    assert payload["s11"]["enable_s11"] is True
    assert payload["s11"]["pack"]["id"] == "loadme"
    assert "s18" in payload
    assert payload["s18"]["metric"] == "after_charges_ex_tax"
    assert payload["live_blocked"] is True
    assert LIVE_BLOCKED_REASON in payload["live_blocked_reason"]
    assert any(p["is_loaded"] for p in payload["packs"])


def test_activate_unsafe_pack_requires_accept(tmp_path: Path) -> None:
    packs = tmp_path / "data" / "discover" / "packs"
    pack = _write_pack(packs / "unsafe.json", safety_ok=False, auc=0.41)
    env = tmp_path / ".env"
    env.write_text("DRY_RUN=true\n", encoding="utf-8")
    blocked = activate_s11_pack(str(pack), env_path=env, root=tmp_path)
    assert blocked["ok"] is False
    ok = activate_s11_pack(str(pack), accept_unsafe=True, env_path=env, root=tmp_path)
    assert ok["ok"] is True


def test_reject_does_not_write_env(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("DRY_RUN=true\nENABLE_S11=false\n", encoding="utf-8")
    props = tmp_path / "control" / "proposals.json"
    state = tmp_path / "control" / "state.json"
    add_proposal(
        StrategyProposal(
            id="nope",
            week_id="2026-W33",
            kind="new",
            strategy="S11_DISCOVERED",
            title="no",
            summary="no",
            paper=PaperResult(),
            safety_ok=True,
            env_patch={"ENABLE_S11": "true", "S11_PACK_PATH": "data/discover/packs/x.json"},
        ),
        path=props,
    )
    res = decide_proposal_for_desk(
        "nope",
        "rejected",
        proposals_path=props,
        state_path=state,
        env_path=env,
        root=tmp_path,
    )
    assert res["ok"] is True
    assert res["env_applied"] is None
    assert "S11_PACK_PATH" not in read_env(env)
    st = load_state(path=state)
    assert "S11_DISCOVERED" in st.force_disabled


def test_joblib_model_path_does_not_crash_ml_tab(tmp_path: Path) -> None:
    """Weekend proposals store the .joblib on model_path; pickle starts with 0x80."""
    models = tmp_path / "data" / "discover" / "models"
    models.mkdir(parents=True)
    joblib = models / "logreg_book.joblib"
    joblib.write_bytes(b"\x80\x04\x95joblib")
    row = pack_summary(str(joblib), root=tmp_path)
    assert row["error"]
    assert "utf-8" not in row["error"]
    fake_json = tmp_path / "data" / "discover" / "packs" / "notjson.json"
    fake_json.parent.mkdir(parents=True, exist_ok=True)
    fake_json.write_bytes(b"\x80\x04\x95joblib")
    row_json = pack_summary(fake_json, root=tmp_path)
    assert row_json["exists"] is True
    assert "utf-8" not in (row_json.get("error") or "")
    assert "pickle" in (row_json.get("error") or "") or "binary" in (row_json.get("error") or "")

    env = tmp_path / ".env"
    env.write_text("DRY_RUN=true\nENABLE_S11=true\nS11_PACK_PATH=\n", encoding="utf-8")
    props = tmp_path / "control" / "proposals.json"
    add_proposal(
        StrategyProposal(
            id="s11job",
            week_id="2026-W33",
            kind="new",
            strategy="S11_DISCOVERED",
            title="S11 joblib fallback",
            summary="safety failed so pack path empty",
            paper=PaperResult(),
            safety_ok=False,
            model_path=str(joblib),
            env_patch={"ENABLE_S11": "false", "S11_PACK_PATH": "", "DRY_RUN": "true"},
        ),
        path=props,
    )
    payload = ml_desk_payload(root=tmp_path, env_path=env, proposals_path=props)
    assert payload["ok"] is True
    pending = payload["proposals"]["pending"]
    assert len(pending) == 1
    assert pending[0]["proposed_pack"].get("error") == "empty"


def test_s11_pending_uses_after_charges_label(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("DRY_RUN=true\nENABLE_S11=true\nS11_PACK_PATH=\n", encoding="utf-8")
    props = tmp_path / "control" / "proposals.json"
    add_proposal(
        StrategyProposal(
            id="s11ch",
            week_id="2026-W34",
            kind="new",
            strategy="S11_DISCOVERED",
            title="S11 after charges",
            summary="first pack",
            paper=PaperResult(
                n_trades=8,
                win_rate=0.5,
                gross_pnl_inr=500.0,
                after_tax_pnl_inr=410.0,
                extra={
                    "metric": "after_charges_ex_tax",
                    "after_charges_inr": 410.0,
                    "after_tax_inr": 300.0,
                },
            ),
            safety_ok=True,
            env_patch={
                "ENABLE_S11": "true",
                "S11_PACK_PATH": "data/discover/packs/week.json",
                "DRY_RUN": "true",
            },
        ),
        path=props,
    )
    payload = ml_desk_payload(root=tmp_path, env_path=env, proposals_path=props)
    row = payload["proposals"]["pending"][0]
    assert row["pnl_label"] == "After charges ₹"
    assert row["pnl_value"] == 410.0


if __name__ == "__main__":
    import tempfile

    test_paper_safe_patch_keeps_dry_run_and_blocks_s4()
    with tempfile.TemporaryDirectory() as td:
        p = Path(td)
        test_pack_summary_and_list(p)
    with tempfile.TemporaryDirectory() as td:
        test_approve_paper_writes_s11_pack_and_keeps_dry_run(Path(td))
    with tempfile.TemporaryDirectory() as td:
        test_unsafe_approve_requires_accept(Path(td))
    with tempfile.TemporaryDirectory() as td:
        test_approve_live_is_blocked(Path(td))
    with tempfile.TemporaryDirectory() as td:
        test_activate_pack_and_ml_payload(Path(td))
    with tempfile.TemporaryDirectory() as td:
        test_activate_unsafe_pack_requires_accept(Path(td))
    with tempfile.TemporaryDirectory() as td:
        test_reject_does_not_write_env(Path(td))
    with tempfile.TemporaryDirectory() as td:
        test_joblib_model_path_does_not_crash_ml_tab(Path(td))
    with tempfile.TemporaryDirectory() as td:
        test_s11_pending_uses_after_charges_label(Path(td))
    print("all s11 desk tests passed")
