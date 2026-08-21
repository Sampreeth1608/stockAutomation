"""Tests for Angel live order bridge + safety gates."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

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
from live_orders import LiveBroker, NullBroker, broker_from_session, live_lots, mirror_positions_from_signals, square_fill_leftover


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


def test_live_lots_hard_cap() -> None:
    os.environ["LIVE_LOTS"] = "5000"
    os.environ["LIVE_MAX_LOTS"] = "5000"
    from live_orders import HARD_LIVE_MAX_LOTS

    assert live_lots() == HARD_LIVE_MAX_LOTS
    os.environ["LIVE_LOTS"] = "1"
    os.environ["LIVE_MAX_LOTS"] = "1"


def test_null_broker_when_dry_run() -> None:
    td, state, orders = _tmp_state()
    try:
        control_state.STATE_PATH = state
        live_orders.ORDERS_PATH = orders
        os.environ["DRY_RUN"] = "true"
        session = MagicMock()
        session.api = _FakeApi()
        b = broker_from_session(session, {"symbol": "GOLDPETAL26APRFUT", "token": "1"})
        assert isinstance(b, NullBroker)
        res = b.place_signal(strategy="S10_LEGACY30", action="BUY", price=100.0)
        assert res.dry_run and res.skipped
        assert not session.api.calls
        assert not orders.exists()
    finally:
        td.cleanup()
        control_state.STATE_PATH = control_state.CONTROL_DIR / "state.json"
        live_orders.ORDERS_PATH = control_state.CONTROL_DIR / "live_orders.jsonl"


def test_null_broker_logs_when_armed_but_bot_still_paper() -> None:
    """Arm live writes DRY_RUN=false; RAM NullBroker must show Why on the Live tab."""
    td, state, orders = _tmp_state()
    try:
        control_state.STATE_PATH = state
        live_orders.ORDERS_PATH = orders
        set_emergency(False, path=state)
        set_trading_enabled(True, path=state)
        set_live_unlocked(True, path=state)
        os.environ["DRY_RUN"] = "true"
        with patch("analytics.env_bridge.read_env", return_value={"DRY_RUN": "false"}):
            b = NullBroker(symbol="GOLDPETAL", token="1")
            res = b.place_signal(strategy="S5_MINEDGE", action="BUY", price=100.0)
        assert res.dry_run and res.skipped
        assert res.reason == "bot_still_paper_restart_required"
        row = json.loads(orders.read_text(encoding="utf-8").strip().splitlines()[-1])
        assert row["reason"] == "bot_still_paper_restart_required"
        assert row["strategy"] == "S5_MINEDGE"
        assert row["transaction"] == "BUY"
    finally:
        td.cleanup()
        control_state.STATE_PATH = control_state.CONTROL_DIR / "state.json"
        live_orders.ORDERS_PATH = control_state.CONTROL_DIR / "live_orders.jsonl"


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


def test_mirror_positions_skips_paper_when_live_only() -> None:
    rows = [
        {"strategy": "S5_MINEDGE", "position_after": "long", "dry_run": 1},
        {"strategy": "S8_NET_ZIGZAG", "position_after": "short", "dry_run": 1},
        {"strategy": "S5_MINEDGE", "position_after": "flat", "dry_run": 0},
    ]
    paper = mirror_positions_from_signals(rows, live_only=False)
    assert paper["S5_MINEDGE"] == "long"
    assert paper["S8_NET_ZIGZAG"] == "short"
    live = mirror_positions_from_signals(rows, live_only=True)
    assert live == {"S5_MINEDGE": "flat"}


def test_lots_on_fill_uses_traded_qty_not_later_arm() -> None:
    from live_orders import lots_on_fill

    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "live_orders.jsonl"
        rows = [
            {
                "ok": False,
                "dry_run": False,
                "skipped": True,
                "reason": "dry_run_or_paper",
                "quantity": 25,
                "strategy": "S16_HHHL_WICK_1H",
                "transaction": "BUY",
                "ts_ist": "2026-08-20T14:00:00+05:30",
            },
            {
                "ok": True,
                "dry_run": False,
                "skipped": False,
                "reason": "placed",
                "order_id": "1",
                "quantity": 3,
                "strategy": "S16_HHHL_WICK_1H",
                "transaction": "BUY",
                "ts_ist": "2026-08-20T14:00:02+05:30",
            },
            {
                "ok": True,
                "dry_run": False,
                "skipped": False,
                "reason": "placed",
                "order_id": "2",
                "quantity": 25,
                "strategy": "S16_HHHL_WICK_1H",
                "transaction": "SELL",
                "ts_ist": "2026-08-20T15:00:03+05:30",
            },
            {
                "ok": True,
                "dry_run": False,
                "skipped": False,
                "reason": "placed",
                "order_id": "3",
                "quantity": 25,
                "strategy": "S16_HHHL_WICK_1H",
                "transaction": "BUY",
                "ts_ist": "2026-08-21T10:00:00+05:30",
            },
        ]
        path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        got = lots_on_fill(
            "S16_HHHL_WICK_1H",
            "2026-08-20T14:00:00+05:30",
            "BUY",
            exit_ts="2026-08-20T15:00:00+05:30",
            path=path,
        )
        assert got == 3
        later = lots_on_fill(
            "S16_HHHL_WICK_1H",
            "2026-08-21T10:00:00+05:30",
            "BUY",
            path=path,
        )
        assert later == 25
        missing = lots_on_fill("S5_MINEDGE", "2026-08-20T14:00:00+05:30", "BUY", path=path)
        assert missing is None


def test_net_open_from_fills_leftover_and_reverse() -> None:
    from live_orders import net_open_from_fills

    def _row(tx: str, qty: int, ts: str = "2026-08-21T10:00:00+05:30") -> str:
        return json.dumps(
            {
                "ok": True,
                "dry_run": False,
                "skipped": False,
                "reason": "placed",
                "order_id": "1",
                "transaction": tx,
                "quantity": qty,
                "strategy": "S5_MINEDGE",
                "ts_ist": ts,
            }
        )

    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "live_orders.jsonl"
        path.write_text(_row("BUY", 3) + "\n", encoding="utf-8")
        leftover = net_open_from_fills(path=path)
        assert leftover["S5_MINEDGE"]["status"] == "OPEN"
        assert leftover["S5_MINEDGE"]["side"] == "BUY"
        assert leftover["S5_MINEDGE"]["lots"] == 3
        assert leftover["S5_MINEDGE"]["source"] == "angel_fill"

        path.write_text(_row("BUY", 3) + "\n" + _row("SELL", 3, "2026-08-21T11:00:00+05:30") + "\n", encoding="utf-8")
        assert net_open_from_fills(path=path) == {}

        path.write_text(
            _row("BUY", 3) + "\n" + _row("SELL", 6, "2026-08-21T11:00:00+05:30") + "\n",
            encoding="utf-8",
        )
        rev = net_open_from_fills(path=path)
        assert rev["S5_MINEDGE"]["status"] == "OPEN"
        assert rev["S5_MINEDGE"]["side"] == "SHORT"
        assert rev["S5_MINEDGE"]["lots"] == 3


def test_leftover_square_sends_buy_in_paper() -> None:
    """Exit leftover SHORT 3 must BUY 3 even when DRY_RUN and live locked."""
    td, state, orders = _tmp_state()
    try:
        control_state.STATE_PATH = state
        live_orders.ORDERS_PATH = orders
        live_orders.CONTROL_DIR = Path(td.name)
        os.environ["DRY_RUN"] = "true"
        set_emergency(False, path=state)
        set_trading_enabled(True, path=state)
        set_live_unlocked(False, path=state)
        api = _FakeApi()
        broker = LiveBroker(api, symbol="GOLDPETAL26APRFUT", token="99")
        leftover = {"strategy": "S5_MINEDGE", "side": "SHORT", "lots": 3}
        res = square_fill_leftover("S5_MINEDGE", leftover=leftover, broker=broker)
        assert res.ok and not res.skipped and res.dry_run is False
        assert res.transaction == "BUY"
        assert res.quantity == 3
        assert broker.positions["S5_MINEDGE"] == "flat"
        assert api.calls[0]["transactiontype"] == "BUY"
        assert api.calls[0]["quantity"] == "3"
        logged = json.loads(orders.read_text(encoding="utf-8").strip().splitlines()[-1])
        assert logged.get("leftover_square") is True
    finally:
        td.cleanup()
        control_state.STATE_PATH = control_state.CONTROL_DIR / "state.json"


class _FakeBookApi(_FakeApi):
    def __init__(self, payload: dict) -> None:
        super().__init__()
        self.payload = payload

    def positionBook(self):
        return self.payload


def test_angel_net_empty_book_is_zero() -> None:
    from live_orders import angel_net_lots_from_book

    kind, net = angel_net_lots_from_book(
        {"status": True, "data": None},
        symbol="GOLDPETAL26APRFUT",
        token="99",
    )
    assert kind == "ok" and net == 0
    kind, net = angel_net_lots_from_book(
        {
            "status": True,
            "data": [
                {
                    "tradingsymbol": "GOLDPETAL26APRFUT",
                    "symboltoken": "99",
                    "netqty": "-3",
                }
            ],
        },
        symbol="GOLDPETAL26APRFUT",
        token="99",
    )
    assert kind == "ok" and net == -3
    kind, net = angel_net_lots_from_book(
        {"status": False, "message": "fail"},
        symbol="GOLDPETAL26APRFUT",
        token="99",
    )
    assert kind == "error" and net is None
    kind, net = angel_net_lots_from_book(
        {
            "status": False,
            "errorcode": "AB1019",
            "message": "AB1019 No Data",
        },
        symbol="GOLDPETAL26APRFUT",
        token="99",
    )
    assert kind == "ok" and net == 0
    kind, net = angel_net_lots_from_book(
        {
            "status": True,
            "data": {
                "net": [
                    {
                        "tradingsymbol": "GOLDPETAL26APRFUT",
                        "symboltoken": "99",
                        "netqty": "-3",
                    }
                ],
                "day": [
                    {
                        "tradingsymbol": "GOLDPETAL26APRFUT",
                        "symboltoken": "99",
                        "netqty": "-3",
                    }
                ],
            },
        },
        symbol="GOLDPETAL26APRFUT",
        token="99",
    )
    assert kind == "ok" and net == -3
    from live_orders import angel_pnl_from_book

    pnl = angel_pnl_from_book(
        {
            "status": True,
            "data": {
                "net": [],
                "day": [
                    {
                        "tradingsymbol": "GOLDPETAL26APRFUT",
                        "symboltoken": "99",
                        "netqty": "0",
                        "realised": "-1280.50",
                        "unrealised": "0",
                    }
                ],
            },
        },
        symbol="GOLDPETAL26APRFUT",
        token="99",
    )
    assert pnl == -1280.50


class _FakePositionApi(_FakeApi):
    def __init__(self, payload: dict) -> None:
        super().__init__()
        self.payload = payload

    def position(self):
        return self.payload


def test_leftover_square_clears_when_angel_already_flat() -> None:
    """You already squared in the Angel app — Exit must not send a new BUY."""
    td, state, orders = _tmp_state()
    try:
        control_state.STATE_PATH = state
        live_orders.ORDERS_PATH = orders
        live_orders.ANGEL_NET_PATH = Path(td.name) / "angel_net.json"
        live_orders._angel_fetch_at = 0.0
        os.environ["DRY_RUN"] = "true"
        set_emergency(False, path=state)
        set_trading_enabled(True, path=state)
        set_live_unlocked(False, path=state)
        orders.write_text(
            json.dumps(
                {
                    "ok": True,
                    "dry_run": False,
                    "skipped": False,
                    "reason": "placed",
                    "order_id": "1",
                    "transaction": "SELL",
                    "quantity": 3,
                    "strategy": "S5_MINEDGE",
                    "ts_ist": "2026-08-20T23:00:00+05:30",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        api = _FakeBookApi({"status": True, "data": []})
        broker = LiveBroker(api, symbol="GOLDPETAL26APRFUT", token="99")
        leftover = {"strategy": "S5_MINEDGE", "side": "SHORT", "lots": 3}
        res = square_fill_leftover("S5_MINEDGE", leftover=leftover, broker=broker)
        assert res.ok and not res.skipped
        assert res.reason == "angel_already_flat"
        assert not api.calls
        from live_orders import net_open_from_fills

        assert net_open_from_fills(path=orders) == {}
    finally:
        td.cleanup()
        control_state.STATE_PATH = control_state.CONTROL_DIR / "state.json"


def test_leftover_square_sends_when_angel_still_short() -> None:
    td, state, orders = _tmp_state()
    try:
        control_state.STATE_PATH = state
        live_orders.ORDERS_PATH = orders
        live_orders.ANGEL_NET_PATH = Path(td.name) / "angel_net.json"
        live_orders._angel_fetch_at = 0.0
        os.environ["DRY_RUN"] = "true"
        set_emergency(False, path=state)
        set_trading_enabled(True, path=state)
        set_live_unlocked(False, path=state)
        api = _FakeBookApi(
            {
                "status": True,
                "data": [
                    {
                        "tradingsymbol": "GOLDPETAL26APRFUT",
                        "symboltoken": "99",
                        "netqty": "-3",
                    }
                ],
            }
        )
        broker = LiveBroker(api, symbol="GOLDPETAL26APRFUT", token="99")
        leftover = {"strategy": "S5_MINEDGE", "side": "SHORT", "lots": 3}
        res = square_fill_leftover("S5_MINEDGE", leftover=leftover, broker=broker)
        assert res.ok and not res.skipped
        assert api.calls[0]["transactiontype"] == "BUY"
        assert api.calls[0]["quantity"] == "3"
    finally:
        td.cleanup()
        control_state.STATE_PATH = control_state.CONTROL_DIR / "state.json"


def test_reconcile_fill_leftovers_when_angel_flat() -> None:
    from live_orders import net_open_from_fills, reconcile_fill_leftovers_with_angel

    td, state, orders = _tmp_state()
    try:
        live_orders.ORDERS_PATH = orders
        live_orders.ANGEL_NET_PATH = Path(td.name) / "angel_net.json"
        live_orders._angel_fetch_at = 0.0
        orders.write_text(
            json.dumps(
                {
                    "ok": True,
                    "dry_run": False,
                    "skipped": False,
                    "reason": "placed",
                    "order_id": "1",
                    "transaction": "SELL",
                    "quantity": 3,
                    "strategy": "S5_MINEDGE",
                    "ts_ist": "2026-08-20T23:00:00+05:30",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        assert net_open_from_fills(path=orders)["S5_MINEDGE"]["status"] == "OPEN"
        api = _FakeBookApi({"status": True, "data": None})
        cleared = reconcile_fill_leftovers_with_angel(
            api,
            symbol="GOLDPETAL26APRFUT",
            token="99",
            path=orders,
            min_interval_sec=0,
        )
        assert cleared == ["S5_MINEDGE"]
        assert net_open_from_fills(path=orders) == {}
        assert not api.calls
    finally:
        td.cleanup()


def test_reconcile_uses_position_method_and_ab1019() -> None:
    """SmartConnect.position() + AB1019 must clear leftover, not keep 1 OPEN."""
    from live_orders import net_open_from_fills, reconcile_fill_leftovers_with_angel

    td, state, orders = _tmp_state()
    try:
        live_orders.ORDERS_PATH = orders
        live_orders.ANGEL_NET_PATH = Path(td.name) / "angel_net.json"
        live_orders._angel_fetch_at = 0.0
        orders.write_text(
            json.dumps(
                {
                    "ok": True,
                    "dry_run": False,
                    "skipped": False,
                    "reason": "placed",
                    "order_id": "1",
                    "transaction": "SELL",
                    "quantity": 3,
                    "strategy": "S5_MINEDGE",
                    "ts_ist": "2026-08-20T23:00:00+05:30",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        api = _FakePositionApi(
            {
                "status": False,
                "errorcode": "AB1019",
                "message": "AB1019 No Data",
            }
        )
        cleared = reconcile_fill_leftovers_with_angel(
            api,
            symbol="GOLDPETAL26APRFUT",
            token="99",
            path=orders,
            min_interval_sec=0,
        )
        assert cleared == ["S5_MINEDGE"]
        assert net_open_from_fills(path=orders) == {}
        snap = json.loads((Path(td.name) / "angel_net.json").read_text(encoding="utf-8"))
        assert int(snap["net"]) == 0
        assert snap.get("ok") is True
        assert not api.calls
    finally:
        td.cleanup()


def test_leftover_square_blocked_by_emergency() -> None:
    td, state, orders = _tmp_state()
    try:
        control_state.STATE_PATH = state
        live_orders.ORDERS_PATH = orders
        live_orders.CONTROL_DIR = Path(td.name)
        os.environ["DRY_RUN"] = "true"
        set_emergency(True, path=state)
        api = _FakeApi()
        broker = LiveBroker(api, symbol="X", token="1")
        res = square_fill_leftover(
            "S5_MINEDGE",
            leftover={"side": "SHORT", "lots": 3},
            broker=broker,
        )
        assert res.skipped and res.reason == "emergency_off"
        assert not api.calls
    finally:
        td.cleanup()
        control_state.STATE_PATH = control_state.CONTROL_DIR / "state.json"


def test_broker_from_session_force_live_in_paper() -> None:
    os.environ["DRY_RUN"] = "true"
    session = MagicMock()
    session.api = _FakeApi()
    contract = {"symbol": "GOLDPETAL26APRFUT", "token": "1", "exchange": "MCX", "lotsize": 1}
    assert isinstance(broker_from_session(session, contract), NullBroker)
    b = broker_from_session(session, contract, force_live=True)
    assert isinstance(b, LiveBroker)
    os.environ["DRY_RUN"] = "true"


if __name__ == "__main__":
    test_live_lots_capped()
    print("ok live_lots")
    test_live_lots_hard_cap()
    print("ok live_lots_hard_cap")
    test_null_broker_when_dry_run()
    print("ok null_broker")
    test_null_broker_logs_when_armed_but_bot_still_paper()
    print("ok null_broker_logs_restart")
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
    test_mirror_positions_skips_paper_when_live_only()
    print("ok mirror_skips_paper")
    test_lots_on_fill_uses_traded_qty_not_later_arm()
    print("ok lots_on_fill")
    test_net_open_from_fills_leftover_and_reverse()
    print("ok net_open_from_fills")
    test_leftover_square_sends_buy_in_paper()
    print("ok leftover_square_paper")
    test_angel_net_empty_book_is_zero()
    print("ok angel_net_parse")
    test_leftover_square_clears_when_angel_already_flat()
    print("ok leftover_already_flat")
    test_leftover_square_sends_when_angel_still_short()
    print("ok leftover_still_short")
    test_reconcile_fill_leftovers_when_angel_flat()
    print("ok reconcile_flat")
    test_reconcile_uses_position_method_and_ab1019()
    print("ok reconcile_position_ab1019")
    test_leftover_square_blocked_by_emergency()
    print("ok leftover_square_emergency")
    test_broker_from_session_force_live_in_paper()
    print("ok force_live_in_paper")
    print("ALL test_live_orders OK")
