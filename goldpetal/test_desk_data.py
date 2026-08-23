"""Watch tape: ticks load without rebuilding every trade."""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

from desk_data import (
    history_payload,
    json_safe,
    live_pnl_payload,
    paper_strategy_summaries,
    reset_trade_cache,
    resolve_desk_db,
    tape_freshness,
    tape_payload,
)
from storage import init_db, latest_signals, list_signals, save_signal, save_tick


def _tick(db: Path, day: str = "2026-08-17", ltp: int = 1500000) -> None:
    save_tick(
        {
            "last_traded_price": ltp,
            "total_buy_quantity": 100,
            "total_sell_quantity": 80,
            "volume_trade_for_the_day": 10,
        },
        symbol="GOLDPETAL",
        token="1",
        received_at=f"{day}T10:00:00+05:30",
        db_path=db,
    )


def _round_trip(db: Path) -> None:
    save_signal(
        time_label="2026-08-17T11:00:00+05:30",
        symbol="GOLDPETAL",
        action="BUY",
        position_after="long",
        reason="test",
        price_delta=1.0,
        net=20.0,
        net_delta=2.0,
        dry_run=True,
        strategy="S16_HHHL_WICK_1H",
        cmp=15010.0,
        db_path=db,
    )
    save_signal(
        time_label="2026-08-17T12:00:00+05:30",
        symbol="GOLDPETAL",
        action="CLOSE",
        position_after="flat",
        reason="test_exit",
        price_delta=1.0,
        net=10.0,
        net_delta=-2.0,
        dry_run=True,
        strategy="S16_HHHL_WICK_1H",
        cmp=15025.0,
        db_path=db,
    )


def test_json_safe_strips_nan() -> None:
    payload = json_safe({"x": float("nan"), "y": float("inf"), "z": 1.5})
    assert payload == {"x": None, "y": None, "z": 1.5}
    json.dumps(payload, allow_nan=False)


def test_resolve_desk_db_prefers_fresher_tick_not_mtime() -> None:
    """Desk init_db bumps mtime on a stale file; live bot db must still win."""
    import os

    with tempfile.TemporaryDirectory() as td:
        stale = Path(td) / "stale.db"
        live = Path(td) / "live.db"
        init_db(stale)
        init_db(live)
        _tick(live, day="2026-08-19")
        time.sleep(0.05)
        init_db(stale)
        _tick(stale, day="2026-08-18")
        os.utime(stale, None)
        picked = resolve_desk_db(candidates=[stale, live])
        assert picked == live


def test_resolve_desk_db_prefers_newer_nonempty() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        old = root / "old.db"
        new = root / "new.db"
        old.write_bytes(b"x" * 8000)
        new.write_bytes(b"y" * 8000)
        time.sleep(0.05)
        new.write_bytes(b"z" * 8000)
        picked = resolve_desk_db(candidates=[old, new])
        assert picked == new


def test_quarantine_stale_ticks_db() -> None:
    from desk_data import quarantine_stale_ticks_dbs

    with tempfile.TemporaryDirectory() as td:
        live = Path(td) / "live.db"
        stale = Path(td) / "stale.db"
        init_db(live)
        init_db(stale)
        _tick(live, day="2026-08-19")
        _tick(stale, day="2026-08-18")
        moved = quarantine_stale_ticks_dbs(live, candidates=[live, stale])
        assert live.is_file()
        assert not stale.is_file()
        assert (Path(td) / "stale.db.stale").is_file()
        assert moved


def test_set_db_path_redirects_package_default() -> None:
    from storage import _PACKAGE_DB, _effective_db, set_db_path
    import storage as st

    with tempfile.TemporaryDirectory() as td:
        live = Path(td) / "ticks.db"
        init_db(live)
        old = st.DB_PATH
        try:
            set_db_path(live)
            assert _effective_db(_PACKAGE_DB) == live
        finally:
            set_db_path(old)


def test_tape_payload_shows_ticks_without_trades() -> None:
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "ticks.db"
        init_db(db)
        _tick(db)
        tape = tape_payload(db_path=db)
        assert tape["error"] == ""
        assert tape["ticks"]
        assert tape["tick_count"] >= 1
        assert tape["ltp"] is not None
        assert str(db) in tape["db_path"]
        assert "tape_live" in tape
        assert "live_pnl" in tape
        assert tape["live_pnl"]["summary"]["closed"] == 0


def test_tape_freshness_frozen_quote_is_not_live() -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    ist = ZoneInfo("Asia/Kolkata")
    now = datetime(2026, 8, 19, 11, 23, 3, tzinfo=ist)
    stale = tape_freshness(last_tick_at="2026-08-19T10:23:34+05:30", now=now)
    assert stale["tape_live"] is False
    assert stale["tape_age_sec"] > 3500
    live = tape_freshness(last_tick_at="2026-08-19T11:22:50+05:30", now=now)
    assert live["tape_live"] is True


def test_tick_feed_stale_during_session() -> None:
    from desk_data import tick_feed_stale

    assert tick_feed_stale(idle_sec=3600, market_open=True, got_tick=True) is True
    assert tick_feed_stale(idle_sec=10, market_open=True, got_tick=True) is False
    assert tick_feed_stale(idle_sec=3600, market_open=False, got_tick=True) is False
    assert tick_feed_stale(idle_sec=60, market_open=True, got_tick=False) is False
    assert tick_feed_stale(idle_sec=90, market_open=True, got_tick=False) is True


def test_history_payload_has_closed_trade_and_ticks() -> None:
    reset_trade_cache()
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "ticks.db"
        init_db(db)
        _tick(db)
        _round_trip(db)
        hist = history_payload(db_path=db, limit=80)
        assert hist["ticks"]
        assert hist["total_closed"] >= 1
        assert hist["trades"]
        assert hist["trades"][0]["strategy"] == "S16_HHHL_WICK_1H"
        assert hist["lots"] == 100
        # 15 points at 100 lots → ₹1500 gross (1g contract, ₹1/point/lot)
        closed = hist["trades"][0]
        assert float(closed.get("gross_pnl") or 0) == 1500.0
        assert hist["scoreboard"]


def test_list_signals_limit_keeps_latest() -> None:
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "ticks.db"
        init_db(db)
        for i in range(5):
            save_signal(
                time_label=f"2026-08-17T10:0{i}:00+05:30",
                symbol="GOLDPETAL",
                action="HOLD",
                position_after="flat",
                reason=str(i),
                price_delta=0.0,
                net=0.0,
                net_delta=0.0,
                dry_run=True,
                strategy="S16_HHHL_WICK_1H",
                cmp=15000.0 + i,
                db_path=db,
            )
        rows = list_signals(strategy="S16_HHHL_WICK_1H", db_path=db, limit=2)
        assert len(rows) == 2
        assert rows[0]["reason"] == "3"
        assert rows[1]["reason"] == "4"


def test_live_pnl_excludes_paper_and_scales_live_lots() -> None:
    reset_trade_cache()
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "ticks.db"
        init_db(db)
        _tick(db)
        _round_trip(db)
        save_signal(
            time_label="2026-08-17T13:00:00+05:30",
            symbol="GOLDPETAL",
            action="BUY",
            position_after="long",
            reason="live_in",
            price_delta=1.0,
            net=20.0,
            net_delta=2.0,
            dry_run=False,
            strategy="S5_MINEDGE",
            cmp=15010.0,
            db_path=db,
        )
        save_signal(
            time_label="2026-08-17T14:00:00+05:30",
            symbol="GOLDPETAL",
            action="CLOSE",
            position_after="flat",
            reason="live_out",
            price_delta=1.0,
            net=10.0,
            net_delta=-2.0,
            dry_run=False,
            strategy="S5_MINEDGE",
            cmp=15025.0,
            db_path=db,
        )
        save_signal(
            time_label="2026-08-17T13:00:00+05:30",
            symbol="GOLDPETAL",
            action="BUY",
            position_after="long",
            reason="s20_not_live",
            price_delta=1.0,
            net=20.0,
            net_delta=2.0,
            dry_run=False,
            strategy="S20_FADE_HL",
            cmp=15010.0,
            db_path=db,
        )
        save_signal(
            time_label="2026-08-17T14:00:00+05:30",
            symbol="GOLDPETAL",
            action="CLOSE",
            position_after="flat",
            reason="s20_not_live",
            price_delta=1.0,
            net=10.0,
            net_delta=-2.0,
            dry_run=False,
            strategy="S20_FADE_HL",
            cmp=15025.0,
            db_path=db,
        )
        with patch("desk_data.live_lots_for", return_value=25):
            pnl = live_pnl_payload(db_path=db)
        names = {t["strategy"] for t in pnl["trades"]}
        assert names == {"S5_MINEDGE"}
        assert int(pnl["summary"]["closed"]) == 1
        closed = pnl["trades"][0]
        # No Angel fill log → 1-lot fallback, never today's 25-lot arm.
        assert float(closed.get("gross_pnl") or 0) == 15.0
        assert float(closed.get("lots") or 0) == 1.0
        assert float(closed.get("gross_pnl") or 0) != 375.0
        assert closed.get("tape") == "live"
        hist = history_payload(db_path=db, limit=80)
        paper = [t for t in hist["trades"] if t.get("strategy") == "S16_HHHL_WICK_1H"]
        assert paper
        assert float(paper[0].get("gross_pnl") or 0) == 1500.0
        assert all(t.get("tape") != "live" for t in hist["trades"])


def test_live_pnl_closed_uses_fill_lots_not_armed_size() -> None:
    """S16 closed at 3 lots stays 3 after Live Lots is armed to 25."""
    import live_orders
    from control_state import ControlState

    reset_trade_cache()
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "ticks.db"
        orders = Path(td) / "live_orders.jsonl"
        init_db(db)
        _tick(db, day="2026-08-20")
        save_signal(
            time_label="2026-08-20T14:00:00+05:30",
            symbol="GOLDPETAL",
            action="BUY",
            position_after="long",
            reason="s16_live_in",
            price_delta=1.0,
            net=20.0,
            net_delta=2.0,
            dry_run=False,
            strategy="S16_HHHL_WICK_1H",
            cmp=15010.0,
            db_path=db,
        )
        save_signal(
            time_label="2026-08-20T15:00:00+05:30",
            symbol="GOLDPETAL",
            action="CLOSE",
            position_after="flat",
            reason="s16_live_out",
            price_delta=1.0,
            net=10.0,
            net_delta=-2.0,
            dry_run=False,
            strategy="S16_HHHL_WICK_1H",
            cmp=15025.0,
            db_path=db,
        )
        orders.write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "ok": True,
                            "dry_run": False,
                            "skipped": False,
                            "reason": "placed",
                            "order_id": "1001",
                            "transaction": "BUY",
                            "quantity": 3,
                            "strategy": "S16_HHHL_WICK_1H",
                            "ts_ist": "2026-08-20T14:00:02+05:30",
                        }
                    ),
                    json.dumps(
                        {
                            "ok": True,
                            "dry_run": False,
                            "skipped": False,
                            "reason": "placed",
                            "order_id": "1002",
                            "transaction": "SELL",
                            "quantity": 3,
                            "strategy": "S16_HHHL_WICK_1H",
                            "ts_ist": "2026-08-20T15:00:03+05:30",
                        }
                    ),
                    json.dumps(
                        {
                            "ok": True,
                            "dry_run": False,
                            "skipped": False,
                            "reason": "placed",
                            "order_id": "1003",
                            "transaction": "BUY",
                            "quantity": 25,
                            "strategy": "S16_HHHL_WICK_1H",
                            "ts_ist": "2026-08-21T10:00:00+05:30",
                        }
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        st = ControlState(live_approved=["S16_HHHL_WICK_1H"])
        with (
            patch.object(live_orders, "ORDERS_PATH", orders),
            patch("desk_data.live_lots_for", return_value=25),
            patch("control_state.load_state", return_value=st),
        ):
            pnl = live_pnl_payload(db_path=db)
        closed = [t for t in pnl["closed"] if t.get("strategy") == "S16_HHHL_WICK_1H"]
        assert len(closed) == 1
        assert float(closed[0].get("lots") or 0) == 3.0
        assert float(closed[0].get("gross_pnl") or 0) == 45.0
        assert float(closed[0].get("gross_pnl") or 0) != 375.0
        assert int(pnl["lots"]) == 3
        assert int(pnl["summary"]["closed"]) == 1
        note = str(pnl.get("note") or "")
        assert "fill" in note.lower() or "Live Lots" in note
        by_name = {t["strategy"]: t for t in pnl["positions"]}
        assert by_name["S16_HHHL_WICK_1H"]["status"] == "OPEN"
        assert int(by_name["S16_HHHL_WICK_1H"].get("lots") or 0) == 25
        assert by_name["S16_HHHL_WICK_1H"].get("source") == "angel_fill"
        assert int(pnl["summary"]["open"]) == 1


def test_live_pnl_positions_one_row_per_open_book() -> None:
    """Live tab lists each Angel OPEN book; paper 100-lot opens stay off this list."""
    from control_state import ControlState

    reset_trade_cache()
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "ticks.db"
        init_db(db)
        _tick(db)
        _round_trip(db)
        save_signal(
            time_label="2026-08-17T13:00:00+05:30",
            symbol="GOLDPETAL",
            action="BUY",
            position_after="long",
            reason="live_in",
            price_delta=1.0,
            net=20.0,
            net_delta=2.0,
            dry_run=False,
            strategy="S5_MINEDGE",
            cmp=15010.0,
            db_path=db,
        )
        st = ControlState()
        with (
            patch("control_state.load_state", return_value=st),
            patch("desk_data.live_lots_for", return_value=1),
        ):
            pnl = live_pnl_payload(db_path=db)
        assert int(pnl["summary"]["open"]) == 1
        assert [t["strategy"] for t in pnl["open"]] == ["S5_MINEDGE"]
        by_name = {t["strategy"]: t for t in pnl["positions"]}
        assert "S5_MINEDGE" in by_name
        assert by_name["S5_MINEDGE"]["status"] == "OPEN"
        assert by_name["S5_MINEDGE"]["side"] == "BUY"
        assert float(by_name["S5_MINEDGE"].get("lots") or 0) == 1.0
        assert "S16_HHHL_WICK_1H" not in by_name
        assert not [t for t in pnl["closed"] if t.get("strategy") == "S5_MINEDGE"]


def test_live_pnl_positions_flat_when_live_picked_and_no_open() -> None:
    from control_state import ControlState

    reset_trade_cache()
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "ticks.db"
        init_db(db)
        _tick(db)
        st = ControlState(live_approved=["S16_HHHL_WICK_1H"])
        with (
            patch("control_state.load_state", return_value=st),
            patch("desk_data.live_lots_for", return_value=1),
        ):
            pnl = live_pnl_payload(db_path=db)
        by_name = {t["strategy"]: t for t in pnl["positions"]}
        assert by_name["S16_HHHL_WICK_1H"]["status"] == "FLAT"
        assert by_name["S16_HHHL_WICK_1H"]["side"] == "FLAT"
        assert "S5_MINEDGE" not in by_name
        assert "S8_NET_ZIGZAG" not in by_name
        assert pnl["open"] == []
        board = {r["strategy"]: r for r in pnl["scoreboard"]}
        assert "S16_HHHL_WICK_1H" in board
        assert "S5_MINEDGE" not in board
        assert "S8_NET_ZIGZAG" not in board
        assert board["LIVE"]["strategy"] == "LIVE"
        assert int(board["S16_HHHL_WICK_1H"]["closed"]) == 0


def test_live_pnl_fill_leftover_shows_open_when_tape_flat() -> None:
    """Angel leftover BUY still OPEN on Live even if the signal tape went FLAT."""
    import live_orders
    from control_state import ControlState

    reset_trade_cache()
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "ticks.db"
        orders = Path(td) / "live_orders.jsonl"
        init_db(db)
        _tick(db)
        orders.write_text(
            json.dumps(
                {
                    "ok": True,
                    "dry_run": False,
                    "skipped": False,
                    "reason": "placed",
                    "order_id": "9001",
                    "transaction": "BUY",
                    "quantity": 3,
                    "strategy": "S5_MINEDGE",
                    "ts_ist": "2026-08-21T10:00:02+05:30",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        st = ControlState(live_approved=["S5_MINEDGE"])
        with (
            patch.object(live_orders, "ORDERS_PATH", orders),
            patch("desk_data.live_lots_for", return_value=3),
            patch("control_state.load_state", return_value=st),
            patch(
                "live_readiness.read_live_env",
                return_value={"dry_run": True, "live_max_lots": 3},
            ),
        ):
            pnl = live_pnl_payload(db_path=db)
        by_name = {t["strategy"]: t for t in pnl["positions"]}
        assert by_name["S5_MINEDGE"]["status"] == "OPEN"
        assert by_name["S5_MINEDGE"]["side"] == "BUY"
        assert int(by_name["S5_MINEDGE"].get("lots") or 0) == 3
        assert by_name["S5_MINEDGE"].get("source") == "angel_fill"
        assert int(pnl["summary"]["open"]) >= 1
        note = str(pnl.get("note") or "")
        assert "leftover" in note.lower() or "Angel still has" in note
        assert "squares leftover Angel even in Paper" in note
        assert "Does not Arm live" in note


def test_live_pnl_hides_leftover_when_angel_already_flat() -> None:
    """Fill-log leftover must not stay OPEN after Angel is flat."""
    import live_orders
    from control_state import ControlState

    reset_trade_cache()
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "ticks.db"
        orders = Path(td) / "live_orders.jsonl"
        cache = Path(td) / "angel_net.json"
        init_db(db)
        _tick(db)
        orders.write_text(
            json.dumps(
                {
                    "ok": True,
                    "dry_run": False,
                    "skipped": False,
                    "reason": "placed",
                    "order_id": "9001",
                    "transaction": "SELL",
                    "quantity": 3,
                    "strategy": "S5_MINEDGE",
                    "ts_ist": "2026-08-21T10:00:02+05:30",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        cache.write_text(
            json.dumps(
                {
                    "ok": True,
                    "net": 0,
                    "pnl": -1280.5,
                    "at_unix": time.time(),
                    "symbol": "X",
                    "token": "1",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        st = ControlState(live_approved=["S5_MINEDGE"])
        with (
            patch.object(live_orders, "ORDERS_PATH", orders),
            patch.object(live_orders, "ANGEL_NET_PATH", cache),
            patch("desk_data.live_lots_for", return_value=3),
            patch("control_state.load_state", return_value=st),
            patch(
                "live_readiness.read_live_env",
                return_value={"dry_run": True, "live_max_lots": 3},
            ),
        ):
            pnl = live_pnl_payload(db_path=db)
        by_name = {t["strategy"]: t for t in pnl["positions"]}
        assert "S5_MINEDGE" not in by_name
        assert int(pnl["summary"]["open"] or 0) == 0
        assert pnl["open"] == []
        assert float(pnl["summary"]["pnl_after_charges"]) == -1280.5
        assert float(pnl["summary"]["angel_pnl"]) == -1280.5
        board = {r["strategy"]: r for r in pnl["scoreboard"]}
        if "S5_MINEDGE" in board:
            assert int(board["S5_MINEDGE"].get("open") or 0) == 0


def test_live_pnl_leftover_newer_than_flat_cache_stays_open() -> None:
    """A new Angel fill after a flat snapshot must not be hidden as 0 OPEN."""
    import live_orders
    from control_state import ControlState

    reset_trade_cache()
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "ticks.db"
        orders = Path(td) / "live_orders.jsonl"
        cache = Path(td) / "angel_net.json"
        init_db(db)
        _tick(db)
        orders.write_text(
            json.dumps(
                {
                    "ok": True,
                    "dry_run": False,
                    "skipped": False,
                    "reason": "placed",
                    "order_id": "9100",
                    "transaction": "BUY",
                    "quantity": 3,
                    "strategy": "S5_MINEDGE",
                    "ts_ist": "2026-08-21T13:10:00+05:30",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        cache.write_text(
            json.dumps(
                {
                    "ok": True,
                    "net": 0,
                    "pnl": 0,
                    "at_unix": 1.0,
                    "symbol": "X",
                    "token": "1",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        st = ControlState(live_approved=["S5_MINEDGE"])
        with (
            patch.object(live_orders, "ORDERS_PATH", orders),
            patch.object(live_orders, "ANGEL_NET_PATH", cache),
            patch("desk_data.live_lots_for", return_value=3),
            patch("control_state.load_state", return_value=st),
            patch(
                "live_readiness.read_live_env",
                return_value={"dry_run": True, "live_max_lots": 3},
            ),
        ):
            pnl = live_pnl_payload(db_path=db)
        by_name = {t["strategy"]: t for t in pnl["positions"]}
        assert by_name["S5_MINEDGE"]["status"] == "OPEN"
        assert int(pnl["summary"]["open"] or 0) >= 1
        assert str(pnl["open"][0].get("status") or "") == "OPEN"


def test_live_pnl_wait_false_uses_leftover_without_rebuild() -> None:
    """Desk/tape polls must not rebuild Live P&L or GOLD LTP freezes."""
    import live_orders
    from control_state import ControlState

    reset_trade_cache()
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "ticks.db"
        orders = Path(td) / "live_orders.jsonl"
        init_db(db)
        _tick(db)
        orders.write_text(
            json.dumps(
                {
                    "ok": True,
                    "dry_run": False,
                    "skipped": False,
                    "reason": "placed",
                    "order_id": "9002",
                    "transaction": "SELL",
                    "quantity": 3,
                    "strategy": "S5_MINEDGE",
                    "ts_ist": "2026-08-21T11:40:00+05:30",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        st = ControlState(live_approved=["S5_MINEDGE", "S19_BODY_CLOSE_1H"])
        with (
            patch.object(live_orders, "ORDERS_PATH", orders),
            patch("desk_data.live_lots_for", return_value=3),
            patch("control_state.load_state", return_value=st),
            patch("desk_data._kick_live_pnl"),
            patch("desk_data._build_live_pnl", side_effect=AssertionError("must not rebuild")),
            patch("desk_data.build_trades", side_effect=AssertionError("must not rebuild paper")),
            patch(
                "desk_data.all_trades_cached",
                side_effect=AssertionError("must not wait on paper trades"),
            ),
        ):
            pnl = live_pnl_payload(db_path=db, wait=False)
        by_name = {t["strategy"]: t for t in pnl["positions"]}
        assert by_name["S5_MINEDGE"]["status"] == "OPEN"
        assert by_name["S5_MINEDGE"]["side"] == "SHORT"
        assert int(by_name["S5_MINEDGE"].get("lots") or 0) == 3
        assert "S19_BODY_CLOSE_1H" not in by_name


def test_paper_summaries_wait_false_does_not_rebuild() -> None:
    reset_trade_cache()
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "ticks.db"
        init_db(db)
        with (
            patch("desk_data._kick_paper_trades"),
            patch("desk_data.build_trades", side_effect=AssertionError("must not rebuild")),
        ):
            out = paper_strategy_summaries(db_path=db, wait=False)
        assert isinstance(out, dict)
        assert "S5_MINEDGE" in out


def test_latest_signals_live_only_skips_paper() -> None:
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "ticks.db"
        init_db(db)
        save_signal(
            time_label="2026-08-20T15:00:00+05:30",
            symbol="GOLDPETAL",
            action="BUY",
            position_after="long",
            reason="paper",
            price_delta=1.0,
            net=1.0,
            net_delta=1.0,
            dry_run=True,
            strategy="S5_MINEDGE",
            cmp=15700.0,
            db_path=db,
        )
        save_signal(
            time_label="2026-08-20T15:01:00+05:30",
            symbol="GOLDPETAL",
            action="BUY",
            position_after="long",
            reason="live",
            price_delta=1.0,
            net=1.0,
            net_delta=1.0,
            dry_run=False,
            strategy="S8_NET_ZIGZAG",
            cmp=15710.0,
            db_path=db,
        )
        all_rows = latest_signals(limit=10, db_path=db)
        live_rows = latest_signals(limit=10, db_path=db, live_only=True)
        assert len(all_rows) == 2
        assert len(live_rows) == 1
        assert live_rows[0]["strategy"] == "S8_NET_ZIGZAG"
        assert int(live_rows[0]["dry_run"] or 0) == 0
        listed = list_signals(db_path=db, live_only=True)
        assert len(listed) == 1
        assert listed[0]["strategy"] == "S8_NET_ZIGZAG"


if __name__ == "__main__":
    test_json_safe_strips_nan()
    test_resolve_desk_db_prefers_newer_nonempty()
    test_resolve_desk_db_prefers_fresher_tick_not_mtime()
    test_quarantine_stale_ticks_db()
    test_set_db_path_redirects_package_default()
    test_tape_payload_shows_ticks_without_trades()
    test_tape_freshness_frozen_quote_is_not_live()
    test_tick_feed_stale_during_session()
    test_history_payload_has_closed_trade_and_ticks()
    test_list_signals_limit_keeps_latest()
    test_live_pnl_excludes_paper_and_scales_live_lots()
    test_live_pnl_closed_uses_fill_lots_not_armed_size()
    test_live_pnl_positions_one_row_per_open_book()
    test_live_pnl_positions_flat_when_live_picked_and_no_open()
    test_live_pnl_fill_leftover_shows_open_when_tape_flat()
    test_live_pnl_hides_leftover_when_angel_already_flat()
    test_live_pnl_leftover_newer_than_flat_cache_stays_open()
    test_live_pnl_wait_false_uses_leftover_without_rebuild()
    test_paper_summaries_wait_false_does_not_rebuild()
    test_latest_signals_live_only_skips_paper()
    print("ALL test_desk_data OK")
