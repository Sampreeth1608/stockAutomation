"""Tests for Angel live order bridge + safety gates."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import control_state
import live_orders
from control_state import (
    approve_strategy_live,
    load_state,
    save_state,
    set_emergency,
    set_live_unlocked,
    set_trading_enabled,
)
from live_orders import LiveBroker, NullBroker, broker_from_session, live_lots


class _FakeApi:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.fail = False
        self.next_id = 1000

    def placeOrderFullResponse(self, params: dict):
        self.calls.append(dict(params))
        if self.fail:
            return {"status": False, "message": "rejected", "data": {}}
        self.next_id += 1
        return {"status": True, "data": {"orderid": str(self.next_id)}}


def _tmp_state():
    td = tempfile.TemporaryDirectory()
    root = Path(td.name)
    state = root / "state.json"
    orders = root / "live_orders.jsonl"
    return td, state, orders


def _arm_live(state: Path, strategy: str = "S10_LEGACY30") -> None:
    set_emergency(False, path=state)
    set_trading_enabled(True, path=state)
    set_live_unlocked(True, path=state)
    approve_strategy_live(strategy, path=state)


def test_live_lots_capped() -> None:
    os.environ["LIVE_LOTS"] = "5"
    os.environ["LIVE_MAX_LOTS"] = "2"
    assert live_lots() == 2
    os.environ["LIVE_LOTS"] = "1"
    os.environ["LIVE_MAX_LOTS"] = "1"
    assert live_lots() == 1


def test_null_broker_when_dry_run() -> None:
    os.environ["DRY_RUN"] = "true"
    session = MagicMock()
    session.api = _FakeApi()
    b = broker_from_session(session, {"symbol": "GOLDPETAL26APRFUT", "token": "1"})
    assert isinstance(b, NullBroker)
    res = b.place_signal(strategy="S10_LEGACY30", action="BUY", price=100.0)
    assert res.dry_run and res.skipped
    assert not session.api.calls


def test_gates_block_without_approval(monkeypatch_paths=None) -> None:
    td, state, orders = _tmp_state()
    try:
        control_state.STATE_PATH = state
        live_orders.ORDERS_PATH = orders
        live_orders.CONTROL_DIR = Path(td.name)
        os.environ["DRY_RUN"] = "false"
        os.environ["LIVE_REQUIRE_APPROVAL"] = "true"
        os.environ["LIVE_LOTS"] = "1"
        os.environ["LIVE_MAX_LOTS"] = "1"
        set_emergency(False, path=state)
        set_trading_enabled(True, path=state)
        set_live_unlocked(True, path=state)
        # not approved
        api = _FakeApi()
        broker = LiveBroker(api, symbol="GOLDPETAL26APRFUT", token="99")
        res = broker.place_signal(strategy="S13_HHHL_DAY", action="BUY", price=7200.0)
        assert res.skipped and res.reason == "strategy_not_live_approved"
        assert not api.calls
    finally:
        td.cleanup()
        control_state.STATE_PATH = control_state.CONTROL_DIR / "state.json"


def test_you_manual_live_without_book_approval() -> None:
    td, state, orders = _tmp_state()
    try:
        control_state.STATE_PATH = state
        live_orders.ORDERS_PATH = orders
        live_orders.CONTROL_DIR = Path(td.name)
        os.environ["DRY_RUN"] = "false"
        os.environ["LIVE_REQUIRE_APPROVAL"] = "true"
        os.environ["LIVE_LOTS"] = "1"
        os.environ["LIVE_MAX_LOTS"] = "1"
        set_emergency(False, path=state)
        set_trading_enabled(True, path=state)
        set_live_unlocked(True, path=state)
        api = _FakeApi()
        broker = LiveBroker(api, symbol="GOLDPETAL26APRFUT", token="99")
        you = broker.place_signal(strategy="YOU_MANUAL", action="BUY", price=7200.0)
        assert you.ok and you.transaction == "BUY"
        assert api.calls
    finally:
        td.cleanup()
        control_state.STATE_PATH = control_state.CONTROL_DIR / "state.json"


def test_place_buy_short_close_reverse() -> None:
    td, state, orders = _tmp_state()
    try:
        control_state.STATE_PATH = state
        live_orders.ORDERS_PATH = orders
        live_orders.CONTROL_DIR = Path(td.name)
        os.environ["DRY_RUN"] = "false"
        os.environ["LIVE_REQUIRE_APPROVAL"] = "true"
        os.environ["LIVE_LOTS"] = "1"
        os.environ["LIVE_MAX_LOTS"] = "1"
        _arm_live(state, "S13_HHHL_DAY")

        api = _FakeApi()
        broker = LiveBroker(api, symbol="GOLDPETAL26APRFUT", token="99")

        r1 = broker.place_signal(strategy="S13_HHHL_DAY", action="BUY", price=7200.0)
        assert r1.ok and r1.transaction == "BUY" and r1.quantity == 1
        assert broker.positions["S13_HHHL_DAY"] == "long"

        # BUY again while long → no-op
        r_noop = broker.place_signal(strategy="S13_HHHL_DAY", action="BUY")
        assert r_noop.skipped

        r2 = broker.place_signal(strategy="S13_HHHL_DAY", action="REVERSE_SHORT")
        assert r2.ok and r2.transaction == "SELL" and r2.quantity == 2
        assert broker.positions["S13_HHHL_DAY"] == "short"

        r3 = broker.place_signal(strategy="S13_HHHL_DAY", action="CLOSE")
        assert r3.ok and r3.transaction == "BUY" and r3.quantity == 1
        assert broker.positions["S13_HHHL_DAY"] == "flat"

        r4 = broker.place_signal(strategy="S13_HHHL_DAY", action="SHORT")
        assert r4.ok and r4.transaction == "SELL"
        r5 = broker.place_signal(strategy="S13_HHHL_DAY", action="REVERSE_LONG")
        assert r5.ok and r5.transaction == "BUY" and r5.quantity == 2

        assert orders.exists()
        lines = orders.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) >= 4
        last = json.loads(lines[-1])
        assert "params" in last or last.get("ok") is True
    finally:
        td.cleanup()
        control_state.STATE_PATH = control_state.CONTROL_DIR / "state.json"


def test_seed_positions_and_emergency() -> None:
    td, state, orders = _tmp_state()
    try:
        control_state.STATE_PATH = state
        live_orders.ORDERS_PATH = orders
        live_orders.CONTROL_DIR = Path(td.name)
        os.environ["DRY_RUN"] = "false"
        _arm_live(state, "S8_NET_ZIGZAG")
        api = _FakeApi()
        broker = LiveBroker(api, symbol="X", token="1")
        broker.seed_positions({"S8_NET_ZIGZAG": "long"})
        assert broker.positions["S8_NET_ZIGZAG"] == "long"

        set_emergency(True, path=state)
        res = broker.place_signal(strategy="S8_NET_ZIGZAG", action="CLOSE")
        assert res.skipped and res.reason == "emergency_off"
        assert not api.calls
    finally:
        td.cleanup()
        control_state.STATE_PATH = control_state.CONTROL_DIR / "state.json"


def test_s18_cannot_trade_live_even_if_approved() -> None:
    td, state, orders = _tmp_state()
    try:
        control_state.STATE_PATH = state
        live_orders.ORDERS_PATH = orders
        live_orders.CONTROL_DIR = Path(td.name)
        os.environ["DRY_RUN"] = "false"
        os.environ["LIVE_REQUIRE_APPROVAL"] = "true"
        os.environ["LIVE_LOTS"] = "1"
        os.environ["LIVE_MAX_LOTS"] = "1"
        _arm_live(state, "S18_OHLC_VOL_HTF")
        api = _FakeApi()
        broker = LiveBroker(api, symbol="GOLDPETAL26APRFUT", token="99")
        from unittest.mock import patch

        with patch("live_readiness.qualified_live_names", return_value=frozenset()):
            res = broker.place_signal(strategy="S18_OHLC_VOL_HTF", action="BUY", price=7200.0)
        assert res.skipped and res.reason == "not_live_eligible"
        assert not api.calls
    finally:
        td.cleanup()
        control_state.STATE_PATH = control_state.CONTROL_DIR / "state.json"


def test_s18_can_trade_live_when_wr_40() -> None:
    from unittest.mock import patch

    td, state, orders = _tmp_state()
    try:
        control_state.STATE_PATH = state
        live_orders.ORDERS_PATH = orders
        live_orders.CONTROL_DIR = Path(td.name)
        os.environ["DRY_RUN"] = "false"
        os.environ["LIVE_REQUIRE_APPROVAL"] = "true"
        os.environ["LIVE_LOTS"] = "1"
        os.environ["LIVE_MAX_LOTS"] = "1"
        _arm_live(state, "S18_OHLC_VOL_HTF")
        api = _FakeApi()
        broker = LiveBroker(api, symbol="GOLDPETAL26APRFUT", token="99")
        with patch(
            "live_readiness.qualified_live_names",
            return_value=frozenset({"S18_OHLC_VOL_HTF"}),
        ):
            res = broker.place_signal(
                strategy="S18_OHLC_VOL_HTF", action="BUY", price=7200.0
            )
        assert res.ok and res.transaction == "BUY"
        assert api.calls
    finally:
        td.cleanup()
        control_state.STATE_PATH = control_state.CONTROL_DIR / "state.json"


def test_broker_from_session_live_when_not_dry() -> None:
    os.environ["DRY_RUN"] = "false"
    session = MagicMock()
    session.api = _FakeApi()
    b = broker_from_session(
        session,
        {"symbol": "GOLDPETAL26APRFUT", "token": "1", "exchange": "MCX", "lotsize": 1},
    )
    assert isinstance(b, LiveBroker)
    os.environ["DRY_RUN"] = "true"


if __name__ == "__main__":
    test_live_lots_capped()
    print("ok live_lots")
    test_null_broker_when_dry_run()
    print("ok null_broker")
    test_gates_block_without_approval()
    print("ok gates_block")
    test_you_manual_live_without_book_approval()
    print("ok you_manual")
    test_place_buy_short_close_reverse()
    print("ok place_flow")
    test_seed_positions_and_emergency()
    print("ok seed_emergency")
    test_s18_cannot_trade_live_even_if_approved()
    print("ok s18 not live")
    test_s18_can_trade_live_when_wr_40()
    print("ok s18 live at 40")
    test_broker_from_session_live_when_not_dry()
    print("ok broker_from_session")
    print("ALL test_live_orders OK")
