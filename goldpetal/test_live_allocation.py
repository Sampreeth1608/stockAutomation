"""Tests for Streamlit Live Deploy allocation (live_approved + capital)."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import capital
import control_state
import live_orders
from capital import apply_live_capital_allocation, can_open_trade, load_capital, save_capital, default_plan
from control_state import load_state, set_live_approved, set_live_unlocked, set_emergency, set_trading_enabled
from live_orders import LiveBroker, live_lots_for


def _tmp():
    td = tempfile.TemporaryDirectory()
    root = Path(td.name)
    return td, root / "state.json", root / "capital.json", root / "live_orders.jsonl"


def test_set_live_approved_exact_set() -> None:
    td, state, _, _ = _tmp()
    try:
        set_live_approved(["S4_OVERNIGHT", "S5_MINEDGE", "S12_HHHL30"], path=state)
        st = load_state(path=state)
        assert st.live_approved == ["S4_OVERNIGHT", "S5_MINEDGE", "S12_HHHL30"]
        assert "S4_OVERNIGHT" in st.paper_approved
        set_live_approved([], path=state)
        assert load_state(path=state).live_approved == []
    finally:
        td.cleanup()


def test_paper_allowlist_force_disables_s9() -> None:
    td, state, _, _ = _tmp()
    try:
        from control_state import set_paper_allowlist, strategy_entries_allowed

        set_emergency(False, path=state)
        set_trading_enabled(True, path=state)
        set_paper_allowlist(
            ["S4_OVERNIGHT", "S5_MINEDGE", "S8_NET_ZIGZAG", "S11_DISCOVERED", "S12_HHHL30"],
            path=state,
        )
        st = load_state(path=state)
        assert "S9_STATE30" in st.force_disabled
        assert "S1_NETDELTA" in st.force_disabled
        ok, why = strategy_entries_allowed("S9_STATE30", path=state)
        assert not ok and why == "force_disabled"
        ok, why = strategy_entries_allowed("S4_OVERNIGHT", path=state)
        assert ok, why
    finally:
        td.cleanup()


def test_apply_live_capital_and_disable_others() -> None:
    td, _, capital_path, _ = _tmp()
    try:
        save_capital(default_plan(), path=capital_path)
        apply_live_capital_allocation(
            [
                {"strategy": "S4_OVERNIGHT", "budget_inr": 100_000, "max_lots": 2},
                {"strategy": "S12_HHHL30", "budget_inr": 200_000, "max_lots": 3},
            ],
            total_capital_inr=400_000,
            daily_loss_limit_inr=3_000,
            disable_others=True,
            known_strategies=("S4_OVERNIGHT", "S5_MINEDGE", "S12_HHHL30"),
            path=capital_path,
        )
        plan = load_capital(path=capital_path)
        assert plan.total_capital_inr == 400_000
        assert plan.strategies["S4_OVERNIGHT"].budget_inr == 100_000
        assert plan.strategies["S4_OVERNIGHT"].max_lots == 2
        assert plan.strategies["S12_HHHL30"].max_lots == 3
        assert plan.strategies["S5_MINEDGE"].enabled is False
    finally:
        td.cleanup()


def test_save_live_allocation_local() -> None:
    td, state, capital_path, _ = _tmp()
    try:
        control_state.STATE_PATH = state
        control_state.CONTROL_DIR = Path(td.name)
        capital.CAPITAL_PATH = capital_path
        capital.CONTROL_DIR = Path(td.name)
        save_capital(default_plan(), path=capital_path)

        from analytics.local_bridge import save_live_allocation_local

        res = save_live_allocation_local(
            {
                "live_approved": ["S4_OVERNIGHT", "S5_MINEDGE"],
                "allocations": [
                    {
                        "strategy": "S4_OVERNIGHT",
                        "budget_inr": 80_000,
                        "max_lots": 2,
                        "enabled": True,
                    },
                    {
                        "strategy": "S5_MINEDGE",
                        "budget_inr": 40_000,
                        "max_lots": 1,
                        "enabled": True,
                    },
                ],
                "total_capital_inr": 250_000,
                "daily_loss_limit_inr": 2_500,
            }
        )
        assert res["ok"]
        assert res["live_approved"] == ["S4_OVERNIGHT", "S5_MINEDGE"]
        plan = load_capital(path=capital_path)
        assert plan.strategies["S4_OVERNIGHT"].budget_inr == 80_000
        assert plan.strategies["S5_MINEDGE"].max_lots == 1
    finally:
        td.cleanup()
        control_state.STATE_PATH = control_state.CONTROL_DIR / "state.json"
        capital.CAPITAL_PATH = capital.CONTROL_DIR / "capital.json"


def test_live_lots_for_uses_strategy_max_lots() -> None:
    td, state, capital_path, orders = _tmp()
    try:
        control_state.STATE_PATH = state
        live_orders.ORDERS_PATH = orders
        live_orders.CONTROL_DIR = Path(td.name)
        capital.CAPITAL_PATH = capital_path
        save_capital(default_plan(), path=capital_path)
        apply_live_capital_allocation(
            [
                {"strategy": "S4_OVERNIGHT", "budget_inr": 50_000, "max_lots": 3},
                {"strategy": "S16_HHHL_WICK_1H", "budget_inr": 50_000, "max_lots": 3},
            ],
            path=capital_path,
        )
        os.environ["LIVE_LOTS"] = "1"
        os.environ["LIVE_MAX_LOTS"] = "5"
        assert live_lots_for("S4_OVERNIGHT") == 3
        os.environ["LIVE_MAX_LOTS"] = "2"
        assert live_lots_for("S4_OVERNIGHT") == 2

        set_emergency(False, path=state)
        set_trading_enabled(True, path=state)
        set_live_unlocked(True, path=state)
        set_live_approved(["S16_HHHL_WICK_1H"], path=state)
        os.environ["DRY_RUN"] = "false"
        os.environ["LIVE_REQUIRE_APPROVAL"] = "true"
        os.environ["LIVE_MAX_LOTS"] = "5"

        class _Api:
            def __init__(self) -> None:
                self.calls = []

            def placeOrderFullResponse(self, params):
                self.calls.append(params)
                return {"status": True, "data": {"orderid": "1"}}

        api = _Api()
        broker = LiveBroker(api, symbol="GOLDPETAL", token="1")
        res = broker.place_signal(strategy="S16_HHHL_WICK_1H", action="BUY", price=7000.0)
        assert res.ok and res.quantity == 3
        assert api.calls[0]["quantity"] == "3"
    finally:
        td.cleanup()
        os.environ["DRY_RUN"] = "true"
        os.environ["LIVE_MAX_LOTS"] = "1"
        os.environ["LIVE_LOTS"] = "1"
        control_state.STATE_PATH = control_state.CONTROL_DIR / "state.json"
        capital.CAPITAL_PATH = capital.CONTROL_DIR / "capital.json"


def test_lots_mode_save_does_not_zero_budget_or_paper_lots() -> None:
    td, _, capital_path, _ = _tmp()
    try:
        save_capital(default_plan(), path=capital_path)
        before = load_capital(path=capital_path).strategies["S5_MINEDGE"]
        paper_n = before.max_lots
        budget = before.budget_inr
        apply_live_capital_allocation(
            [
                {
                    "strategy": "S5_MINEDGE",
                    "live_size_mode": "lots",
                    "live_lots": 1,
                }
            ],
            path=capital_path,
        )
        plan = load_capital(path=capital_path)
        sb = plan.strategies["S5_MINEDGE"]
        assert sb.budget_inr == budget
        assert sb.max_lots == paper_n
        assert sb.live_lots == 1
        assert sb.live_size_mode == "lots"
        from capital import live_qty_for

        assert live_qty_for("S5_MINEDGE", cap=5, default=1, plan=plan) == 1
    finally:
        td.cleanup()


def test_live_qty_capital_mode_uses_budget_over_ltp() -> None:
    td, _, capital_path, _ = _tmp()
    try:
        save_capital(default_plan(), path=capital_path)
        apply_live_capital_allocation(
            [
                {
                    "strategy": "S5_MINEDGE",
                    "live_size_mode": "capital",
                    "live_budget_inr": 50_000,
                }
            ],
            path=capital_path,
        )
        plan = load_capital(path=capital_path)
        from capital import live_qty_for

        assert live_qty_for(
            "S5_MINEDGE", cap=10, default=1, ltp=15_000, plan=plan
        ) == 3
        assert live_qty_for(
            "S5_MINEDGE", cap=1, default=1, ltp=15_000, plan=plan
        ) == 1
    finally:
        td.cleanup()


def test_can_open_trade_skips_zero_budget_in_lots_mode() -> None:
    td, _, capital_path, _ = _tmp()
    try:
        plan = default_plan()
        plan.strategies["S5_MINEDGE"].budget_inr = 0
        plan.strategies["S5_MINEDGE"].live_size_mode = "lots"
        save_capital(plan, path=capital_path)
        ok, why = can_open_trade("S5_MINEDGE", lots=1, path=capital_path)
        assert ok, why
        plan = load_capital(path=capital_path)
        plan.strategies["S5_MINEDGE"].live_size_mode = "capital"
        save_capital(plan, path=capital_path)
        ok, why = can_open_trade("S5_MINEDGE", lots=1, path=capital_path)
        assert not ok
        assert why == "zero_budget"
    finally:
        td.cleanup()


def test_live_budget_not_paper_split() -> None:
    td, _, capital_path, _ = _tmp()
    try:
        plan = default_plan()
        plan.strategies["S5_MINEDGE"].budget_inr = 4444.44
        plan.strategies["S5_MINEDGE"].live_budget_inr = 0
        plan.strategies["S5_MINEDGE"].live_size_mode = "capital"
        save_capital(plan, path=capital_path)
        from capital import live_qty_for

        assert live_qty_for(
            "S5_MINEDGE", cap=10, default=1, ltp=15_000, plan=plan
        ) == 1
        apply_live_capital_allocation(
            [
                {
                    "strategy": "S5_MINEDGE",
                    "live_size_mode": "capital",
                    "live_budget_inr": 45_000,
                }
            ],
            path=capital_path,
        )
        plan = load_capital(path=capital_path)
        assert plan.strategies["S5_MINEDGE"].budget_inr == 4444.44
        assert plan.strategies["S5_MINEDGE"].live_budget_inr == 45_000
        assert live_qty_for(
            "S5_MINEDGE", cap=10, default=1, ltp=15_000, plan=plan
        ) == 3
    finally:
        td.cleanup()


if __name__ == "__main__":
    test_set_live_approved_exact_set()
    print("ok set_live_approved")
    test_apply_live_capital_and_disable_others()
    print("ok apply_live_capital")
    test_save_live_allocation_local()
    print("ok save_live_allocation_local")
    test_live_lots_for_uses_strategy_max_lots()
    print("ok live_lots_for")
    test_lots_mode_save_does_not_zero_budget_or_paper_lots()
    print("ok lots_mode_save")
    test_live_qty_capital_mode_uses_budget_over_ltp()
    print("ok capital_qty")
    test_can_open_trade_skips_zero_budget_in_lots_mode()
    print("ok zero_budget_lots")
    test_live_budget_not_paper_split()
    print("ok live_budget")
    print("ALL test_live_allocation OK")
