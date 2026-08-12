"""Tests for control panel state, capital, proposals, and entry gates."""

from __future__ import annotations

import tempfile
from pathlib import Path

from capital import (
    can_open_trade,
    capital_snapshot,
    default_plan,
    load_capital,
    record_realized_pnl,
    save_capital,
    update_strategy_budget,
)
from control_state import (
    approve_strategy_live,
    entries_blocked,
    load_state,
    set_emergency,
    set_trading_enabled,
    strategy_entries_allowed,
)
from proposals import (
    PaperResult,
    StrategyProposal,
    add_proposal,
    decide_proposal,
    proposal_from_weekly_s8,
    proposals_snapshot,
)


def _tmp_paths():
    td = tempfile.TemporaryDirectory()
    root = Path(td.name)
    state = root / "state.json"
    capital = root / "capital.json"
    props = root / "proposals.json"
    return td, state, capital, props


def test_emergency_blocks_entries() -> None:
    td, state, capital, props = _tmp_paths()
    try:
        set_emergency(False, path=state)
        blocked, reason = entries_blocked(path=state)
        assert not blocked
        set_emergency(True, path=state)
        blocked, reason = entries_blocked(path=state)
        assert blocked and reason == "emergency_off"
        ok, why = strategy_entries_allowed("S10_LEGACY30", path=state)
        assert not ok and why == "emergency_off"
    finally:
        td.cleanup()


def test_trading_master_switch() -> None:
    td, state, _, _ = _tmp_paths()
    try:
        set_trading_enabled(False, path=state)
        blocked, reason = entries_blocked(path=state)
        assert blocked and reason == "trading_disabled"
        set_trading_enabled(True, path=state)
        blocked, _ = entries_blocked(path=state)
        assert not blocked
    finally:
        td.cleanup()


def test_capital_daily_loss_and_lots() -> None:
    td, _, capital, _ = _tmp_paths()
    try:
        plan = default_plan()
        plan.daily_loss_limit_inr = 1000.0
        plan.strategies["S5_MINEDGE"].max_lots = 2
        save_capital(plan, path=capital)
        ok, why = can_open_trade("S5_MINEDGE", lots=1, path=capital)
        assert ok, why
        record_realized_pnl(-1500.0, path=capital)
        ok, why = can_open_trade("S5_MINEDGE", lots=1, path=capital)
        assert not ok
        assert "daily_loss_limit" in why

        # reset pnl book for lot test
        plan = load_capital(path=capital)
        plan.day_pnl_inr = {}
        plan.open_lots = {"S5_MINEDGE": 2}
        save_capital(plan, path=capital)
        ok, why = can_open_trade("S5_MINEDGE", lots=1, path=capital)
        assert not ok
        assert "strategy_max_lots" in why
    finally:
        td.cleanup()


def test_proposal_approve_reject_flow() -> None:
    td, state, capital, props = _tmp_paths()
    try:
        prop = StrategyProposal(
            id="abc123",
            week_id="2026-08-10",
            kind="improved",
            strategy="S8_NET_ZIGZAG",
            title="test improve",
            summary="paper +1000",
            paper=PaperResult(n_trades=5, after_tax_pnl_inr=1000.0),
            safety_ok=True,
            status="pending",
        )
        add_proposal(prop, path=props)
        snap = proposals_snapshot(path=props)
        assert snap["counts"]["pending"] == 1

        decided = decide_proposal("abc123", "approved_paper", path=props, state_path=state)
        assert decided.status == "approved_paper"
        st = load_state(path=state)
        assert "S8_NET_ZIGZAG" in st.paper_approved

        prop2 = StrategyProposal(
            id="def456",
            week_id="2026-08-10",
            kind="new",
            strategy="S9_STATE30",
            title="new S9",
            summary="paper weak",
            paper=PaperResult(n_trades=2, after_tax_pnl_inr=-200.0),
            status="pending",
        )
        add_proposal(prop2, path=props)
        decide_proposal("def456", "rejected", path=props, state_path=state)
        st = load_state(path=state)
        assert "S9_STATE30" in st.force_disabled
    finally:
        td.cleanup()


def test_weekly_s8_proposal_builder() -> None:
    prop = proposal_from_weekly_s8(
        week_id="2026-08-10",
        summary={
            "nn_sum_inr": 4200,
            "baseline_sum_inr": 1000,
            "nn_n": 8,
            "nn_dir_pct": 62.5,
            "nn_auc": 0.61,
        },
        safety_ok=True,
        safety_reasons=["ok"],
        model_path="data/models/s8_nn_mlp.joblib",
    )
    assert prop.strategy == "S8_NET_ZIGZAG"
    assert prop.kind == "improved"
    assert prop.status == "pending"
    assert prop.paper.delta_vs_baseline_inr == 3200.0
    assert prop.env_patch.get("DRY_RUN") == "true"


def test_entry_gates_compose() -> None:
    td, state, capital, _ = _tmp_paths()
    try:
        set_emergency(False, path=state)
        blocked, reason = entries_blocked(path=state)
        assert not blocked
        set_emergency(True, path=state)
        blocked, reason = entries_blocked(path=state)
        assert blocked and reason == "emergency_off"
        # Capital path-aware smoke
        save_capital(default_plan(), path=capital)
        ok, why = can_open_trade("S10_LEGACY30", path=capital)
        assert ok, why
    finally:
        td.cleanup()


def test_capital_budget_edit() -> None:
    td, _, capital, _ = _tmp_paths()
    try:
        save_capital(default_plan(), path=capital)
        update_strategy_budget("S10_LEGACY30", budget_inr=75_000, max_lots=5, path=capital)
        plan = load_capital(path=capital)
        assert plan.strategies["S10_LEGACY30"].budget_inr == 75_000
        assert plan.strategies["S10_LEGACY30"].max_lots == 5
        snap = capital_snapshot(path=capital)
        assert "deployable_inr" in snap
        assert snap["total_capital_inr"] == 500_000
    finally:
        td.cleanup()


def test_approve_live_still_dry_run_default() -> None:
    td, state, _, _ = _tmp_paths()
    try:
        approve_strategy_live("S10_LEGACY30", path=state)
        st = load_state(path=state)
        assert "S10_LEGACY30" in st.live_approved
        assert "S10_LEGACY30" in st.paper_approved
        # live_unlocked still false by default
        assert st.live_unlocked is False
    finally:
        td.cleanup()


def test_control_panel_dashboard_payload() -> None:
    from control_panel import dashboard_payload
    from storage import init_db

    # Uses real goldpetal/data — ok if empty.
    init_db()
    payload = dashboard_payload(tick_limit=5, trade_limit=5)
    assert "state" in payload
    assert "ticks" in payload
    assert "trades" in payload
    assert "capital" in payload
    assert "proposals" in payload
    assert "scoreboard" in payload
    assert isinstance(payload["tick_count"], int)


if __name__ == "__main__":
    test_emergency_blocks_entries()
    test_trading_master_switch()
    test_capital_daily_loss_and_lots()
    test_proposal_approve_reject_flow()
    test_weekly_s8_proposal_builder()
    test_entry_gates_compose()
    test_capital_budget_edit()
    test_approve_live_still_dry_run_default()
    test_control_panel_dashboard_payload()
    print("all control/capital/proposal tests passed")
