"""Tests for live readiness checklist and panel .env writes (does not arm live)."""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from live_readiness import (
    HARD_LIVE_MAX_LOTS,
    PANEL_LIVE_MAX_LOTS,
    apply_panel_enables,
    apply_panel_live_env,
    bot_age_seconds,
    desk_snapshot,
    live_readiness,
    panel_restart_allowed,
    read_live_env,
)


def test_hard_live_max_lots_imported() -> None:
    assert HARD_LIVE_MAX_LOTS == 1000
    assert PANEL_LIVE_MAX_LOTS == 1000


def test_hard_live_max_lots_survives_old_live_orders(monkeypatch) -> None:
    import importlib

    import live_orders
    import live_readiness as lr

    monkeypatch.delattr(live_orders, "HARD_LIVE_MAX_LOTS", raising=True)
    reloaded = importlib.reload(lr)
    assert reloaded.HARD_LIVE_MAX_LOTS == 1000
    assert reloaded.PANEL_LIVE_MAX_LOTS == 1000

IST = ZoneInfo("Asia/Kolkata")


def test_bot_age() -> None:
    now = datetime(2026, 8, 17, 12, 30, tzinfo=IST)
    health = {"ts_ist": (now - timedelta(seconds=20)).isoformat(timespec="seconds")}
    assert bot_age_seconds(health, now=now) == 20.0
    assert bot_age_seconds({}, now=now) is None


def test_readiness_paper_by_default() -> None:
    os.environ["DRY_RUN"] = "true"
    os.environ["LIVE_MAX_LOTS"] = "1"
    r = live_readiness()
    assert r["dry_run"] is True
    assert r["would_place_real_orders"] is False
    dry_step = next(s for s in r["steps"] if s["id"] == "dry_run")
    assert dry_step["ok"] is False
    names = [b["strategy"] for b in r["books"]]
    assert "S16_HHHL_WICK_1H" in names
    assert "S18_OHLC_VOL_HTF" in names
    assert "S19_BODY_CLOSE_1H" not in names
    assert "S20_FADE_HL" not in names
    assert "OVERNIGHT_GAP" in names
    assert "S14_WICK30_STRICT" not in names
    assert "S15_WICK30_NOWICK" not in names
    assert "S12_HHHL30" not in names
    s16 = next(b for b in r["books"] if b["strategy"] == "S16_HHHL_WICK_1H")
    assert s16["live_approved"] is False
    assert s16["live_qty"] == 0
    assert s16["intraday"] is True
    s18 = next(b for b in r["books"] if b["strategy"] == "S18_OHLC_VOL_HTF")
    assert s18["intraday"] is False
    gap = next(b for b in r["books"] if b["strategy"] == "OVERNIGHT_GAP")
    assert gap["intraday"] is False
    assert "enables" in r
    assert "S16_HHHL_WICK_1H" in r["enables"]
    assert "S18_OHLC_VOL_HTF" in r["enables"]
    assert "S19_BODY_CLOSE_1H" not in r["enables"]
    assert "S20_FADE_HL" not in r["enables"]
    assert "OVERNIGHT_GAP" in r["enables"]


def test_apply_panel_live_env_paper_ok() -> None:
    td = tempfile.TemporaryDirectory()
    try:
        env = Path(td.name) / ".env"
        env.write_text("DRY_RUN=true\nLIVE_MAX_LOTS=1\nSECRET=keep\n", encoding="utf-8")
        res = apply_panel_live_env(
            dry_run=True,
            live_max_lots=1,
            confirm="",
            path=env,
            sync_environ=False,
        )
        assert res["ok"] is True
        assert res["applied"]["DRY_RUN"] == "true"
        assert res["applied"]["LIVE_MAX_LOTS"] == "1"
        text = env.read_text(encoding="utf-8")
        assert "SECRET=keep" in text
        assert "DRY_RUN=true" in text
        snap = read_live_env(path=env)
        assert snap["dry_run"] is True
        assert snap["live_max_lots"] == 1
    finally:
        td.cleanup()


def test_apply_panel_live_env_rejects_without_live_word() -> None:
    td = tempfile.TemporaryDirectory()
    try:
        env = Path(td.name) / ".env"
        env.write_text("DRY_RUN=true\nLIVE_MAX_LOTS=1\n", encoding="utf-8")
        res = apply_panel_live_env(
            dry_run=False,
            live_max_lots=1,
            confirm="",
            path=env,
            sync_environ=False,
        )
        assert res["ok"] is False
        assert "LIVE" in res["error"]
        assert "DRY_RUN=true" in env.read_text(encoding="utf-8")
        res2 = apply_panel_live_env(
            dry_run=False,
            live_max_lots=1,
            confirm="live",
            path=env,
            sync_environ=False,
        )
        assert res2["ok"] is False
        assert "DRY_RUN=true" in env.read_text(encoding="utf-8")
    finally:
        td.cleanup()


def test_apply_panel_live_env_caps_lots() -> None:
    td = tempfile.TemporaryDirectory()
    try:
        env = Path(td.name) / ".env"
        env.write_text("DRY_RUN=true\nLIVE_MAX_LOTS=1\n", encoding="utf-8")
        res = apply_panel_live_env(
            dry_run=True,
            live_max_lots=100,
            confirm="",
            path=env,
            sync_environ=False,
        )
        assert res["ok"] is False
        assert "SIZE" in res["error"]
        assert "LIVE_MAX_LOTS=1" in env.read_text(encoding="utf-8")
        too_big = apply_panel_live_env(
            dry_run=True,
            live_max_lots=1001,
            confirm="",
            size_confirm="SIZE",
            path=env,
            sync_environ=False,
        )
        assert too_big["ok"] is False
        assert "1–1000" in too_big["error"] or "1-1000" in too_big["error"]
        res0 = apply_panel_live_env(
            dry_run=True,
            live_max_lots=0,
            confirm="",
            path=env,
            sync_environ=False,
        )
        assert res0["ok"] is False
        ok_size = apply_panel_live_env(
            dry_run=True,
            live_max_lots=1000,
            confirm="",
            size_confirm="SIZE",
            path=env,
            sync_environ=False,
        )
        assert ok_size["ok"] is True
        assert ok_size["applied"]["LIVE_MAX_LOTS"] == "1000"
    finally:
        td.cleanup()


def test_apply_panel_live_env_live_with_word() -> None:
    td = tempfile.TemporaryDirectory()
    try:
        env = Path(td.name) / ".env"
        env.write_text("DRY_RUN=true\nLIVE_MAX_LOTS=1\n", encoding="utf-8")
        res = apply_panel_live_env(
            dry_run=False,
            live_max_lots=1,
            confirm="LIVE",
            path=env,
            sync_environ=False,
        )
        assert res["ok"] is True
        assert res["applied"]["DRY_RUN"] == "false"
        assert res["applied"]["LIVE_MAX_LOTS"] == "1"
        assert "DRY_RUN=false" in env.read_text(encoding="utf-8")
        snap = read_live_env(path=env)
        assert snap["dry_run"] is False
    finally:
        td.cleanup()


def test_panel_restart_requires_word() -> None:
    ok, why = panel_restart_allowed("")
    assert ok is False
    assert "RESTART" in why
    ok2, _ = panel_restart_allowed("restart")
    assert ok2 is False
    ok3, why3 = panel_restart_allowed("RESTART")
    assert ok3 is True
    assert why3 == "ok"


def test_apply_panel_enables_slim() -> None:
    td = tempfile.TemporaryDirectory()
    try:
        env = Path(td.name) / ".env"
        env.write_text("ENABLE_S4=true\nENABLE_S9=true\nSECRET=keep\n", encoding="utf-8")
        res = apply_panel_enables(["S4_OVERNIGHT", "S13_HHHL_DAY", "S16_HHHL_WICK_1H"], path=env)
        assert res["ok"] is True
        text = env.read_text(encoding="utf-8")
        assert "ENABLE_S4=false" in text
        assert "ENABLE_S13=true" in text
        assert "ENABLE_S16=true" in text
        assert "ENABLE_S9=false" in text
        assert "ENABLE_S5=false" in text
        assert "SECRET=keep" in text
        assert "S16_HHHL_WICK_1H" in res["enabled"]
        assert "S13_HHHL_DAY" in res["enabled"]
        assert "S4_OVERNIGHT" not in res["enabled"]
    finally:
        td.cleanup()


def test_apply_desk_books_live_requires_in_bot() -> None:
    td = tempfile.TemporaryDirectory()
    try:
        env = Path(td.name) / ".env"
        env.write_text(
            "ENABLE_S13=true\nENABLE_S16=true\nDRY_RUN=true\nSECRET=keep\n",
            encoding="utf-8",
        )
        state = Path(td.name) / "state.json"
        from live_readiness import apply_desk_books

        res = apply_desk_books(
            ["S13_HHHL_DAY"],
            ["S13_HHHL_DAY", "S16_HHHL_WICK_1H"],
            path=env,
            state_path=state,
        )
        assert res["ok"] is True
        assert res["live_approved"] == ["S13_HHHL_DAY"]
        assert "S16_HHHL_WICK_1H" in res["skipped_live_not_in_bot"]
        text = env.read_text(encoding="utf-8")
        assert "ENABLE_S13=true" in text
        assert "ENABLE_S16=false" in text
        assert "DRY_RUN=true" in text
        assert "SECRET=keep" in text
        assert res["restart_needed"] is True
    finally:
        td.cleanup()


def test_apply_desk_books_s18_live_eligible_without_40() -> None:
    td = tempfile.TemporaryDirectory()
    try:
        env = Path(td.name) / ".env"
        env.write_text(
            "ENABLE_S13=true\nENABLE_S18=true\nDRY_RUN=true\nSECRET=keep\n",
            encoding="utf-8",
        )
        state = Path(td.name) / "state.json"
        from live_readiness import apply_desk_books

        res = apply_desk_books(
            ["S13_HHHL_DAY", "S18_OHLC_VOL_HTF"],
            ["S13_HHHL_DAY", "S18_OHLC_VOL_HTF"],
            path=env,
            state_path=state,
            qualified=[],
        )
        assert res["ok"] is True
        assert "S18_OHLC_VOL_HTF" in res["enabled"]
        assert "S18_OHLC_VOL_HTF" in res["live_approved"]
        assert "S13_HHHL_DAY" in res["live_approved"]
        assert "S18_OHLC_VOL_HTF" not in res["skipped_live_not_in_bot"]
        text = env.read_text(encoding="utf-8")
        assert "ENABLE_S18=true" in text
        assert "DRY_RUN=true" in text
        assert "SECRET=keep" in text
    finally:
        td.cleanup()


def test_apply_desk_books_s19_stays_off() -> None:
    td = tempfile.TemporaryDirectory()
    try:
        env = Path(td.name) / ".env"
        env.write_text(
            "ENABLE_S13=true\nENABLE_S19=true\nDRY_RUN=true\nSECRET=keep\n",
            encoding="utf-8",
        )
        state = Path(td.name) / "state.json"
        from live_readiness import apply_desk_books

        res = apply_desk_books(
            ["S13_HHHL_DAY", "S19_BODY_CLOSE_1H"],
            ["S13_HHHL_DAY", "S19_BODY_CLOSE_1H"],
            path=env,
            state_path=state,
        )
        assert res["ok"] is True
        assert "S19_BODY_CLOSE_1H" not in res["enabled"]
        assert "S19_BODY_CLOSE_1H" not in res["live_approved"]
        assert "S13_HHHL_DAY" in res["live_approved"]
        text = env.read_text(encoding="utf-8")
        assert "ENABLE_S19=false" in text
        assert "DRY_RUN=true" in text
        assert "SECRET=keep" in text
    finally:
        td.cleanup()


def test_apply_desk_books_s20_stays_off() -> None:
    td = tempfile.TemporaryDirectory()
    try:
        env = Path(td.name) / ".env"
        env.write_text(
            "ENABLE_S13=true\nENABLE_S20=true\nDRY_RUN=true\nSECRET=keep\n",
            encoding="utf-8",
        )
        state = Path(td.name) / "state.json"
        from live_readiness import apply_desk_books

        res = apply_desk_books(
            ["S13_HHHL_DAY", "S20_FADE_HL"],
            ["S13_HHHL_DAY", "S20_FADE_HL"],
            path=env,
            state_path=state,
        )
        assert res["ok"] is True
        assert "S20_FADE_HL" not in res["enabled"]
        assert "S20_FADE_HL" not in res["live_approved"]
        assert "S13_HHHL_DAY" in res["live_approved"]
        text = env.read_text(encoding="utf-8")
        assert "ENABLE_S20=false" in text
        assert "DRY_RUN=true" in text
        assert "SECRET=keep" in text
    finally:
        td.cleanup()


def test_apply_desk_books_overnight_gap_live_eligible_without_40() -> None:
    td = tempfile.TemporaryDirectory()
    try:
        env = Path(td.name) / ".env"
        env.write_text(
            "ENABLE_S13=true\nENABLE_OVERNIGHT_GAP=true\nDRY_RUN=true\nSECRET=keep\n",
            encoding="utf-8",
        )
        state = Path(td.name) / "state.json"
        from live_readiness import apply_desk_books

        res = apply_desk_books(
            ["S13_HHHL_DAY", "OVERNIGHT_GAP"],
            ["S13_HHHL_DAY", "OVERNIGHT_GAP"],
            path=env,
            state_path=state,
            qualified=[],
        )
        assert res["ok"] is True
        assert "OVERNIGHT_GAP" in res["enabled"]
        assert "OVERNIGHT_GAP" in res["live_approved"]
        assert "S13_HHHL_DAY" in res["live_approved"]
        assert "OVERNIGHT_GAP" not in res["skipped_live_not_in_bot"]
        text = env.read_text(encoding="utf-8")
        assert "ENABLE_OVERNIGHT_GAP=true" in text
        assert "DRY_RUN=true" in text
    finally:
        td.cleanup()


def test_apply_desk_books_overnight_gap_live_when_wr_40() -> None:
    td = tempfile.TemporaryDirectory()
    try:
        env = Path(td.name) / ".env"
        env.write_text(
            "ENABLE_S13=true\nENABLE_OVERNIGHT_GAP=true\nDRY_RUN=true\nSECRET=keep\n",
            encoding="utf-8",
        )
        state = Path(td.name) / "state.json"
        from live_readiness import apply_desk_books

        res = apply_desk_books(
            ["S13_HHHL_DAY", "OVERNIGHT_GAP"],
            ["S13_HHHL_DAY", "OVERNIGHT_GAP"],
            path=env,
            state_path=state,
            qualified=["OVERNIGHT_GAP"],
        )
        assert res["ok"] is True
        assert "OVERNIGHT_GAP" in res["enabled"]
        assert "OVERNIGHT_GAP" in res["live_approved"]
        assert "S13_HHHL_DAY" in res["live_approved"]
        text = env.read_text(encoding="utf-8")
        assert "ENABLE_OVERNIGHT_GAP=true" in text
        assert "DRY_RUN=true" in text
    finally:
        td.cleanup()


def test_ensure_overnight_gap_enable_writes_env() -> None:
    td = tempfile.TemporaryDirectory()
    try:
        env = Path(td.name) / ".env"
        env.write_text("ENABLE_OVERNIGHT_GAP=false\nDRY_RUN=true\n", encoding="utf-8")
        os.environ["ENABLE_OVERNIGHT_GAP"] = "false"
        from live_readiness import ensure_overnight_gap_enable

        res = ensure_overnight_gap_enable(path=env)
        assert res.get("ok") is True
        assert "ENABLE_OVERNIGHT_GAP=true" in env.read_text(encoding="utf-8")
        assert os.environ.get("ENABLE_OVERNIGHT_GAP") == "true"
        assert "Arm" in str(res.get("note") or "")
        assert "40" not in str(res.get("note") or "")
    finally:
        os.environ.pop("ENABLE_OVERNIGHT_GAP", None)
        td.cleanup()


def test_desk_snapshot_skips_checklist() -> None:
    os.environ["DRY_RUN"] = "true"
    snap = desk_snapshot(summaries={})
    assert "steps" not in snap
    assert "S16_HHHL_WICK_1H" in snap["enables"]
    assert "S18_OHLC_VOL_HTF" in snap["enables"]
    assert "S19_BODY_CLOSE_1H" not in snap["enables"]
    assert "S20_FADE_HL" not in snap["enables"]
    assert "OVERNIGHT_GAP" in snap["enables"]
    assert any(b["strategy"] == "S16_HHHL_WICK_1H" for b in snap["books"])
    assert any(b["strategy"] == "S18_OHLC_VOL_HTF" for b in snap["books"])
    assert not any(b["strategy"] == "S19_BODY_CLOSE_1H" for b in snap["books"])
    assert not any(b["strategy"] == "S20_FADE_HL" for b in snap["books"])
    gap = next(b for b in snap["books"] if b["strategy"] == "OVERNIGHT_GAP")
    s18 = next(b for b in snap["books"] if b["strategy"] == "S18_OHLC_VOL_HTF")
    s16 = next(b for b in snap["books"] if b["strategy"] == "S16_HHHL_WICK_1H")
    assert s18["live_eligible"] is True
    assert s16["live_eligible"] is True
    assert gap["live_eligible"] is True
    assert "S4_OVERNIGHT" not in snap["enables"]
    assert not any(b["strategy"] == "S4_OVERNIGHT" for b in snap["books"])
    assert "S14_WICK30_STRICT" not in snap["enables"]
    assert snap["would_place_real_orders"] is False


def test_summary_qualifies_live_needs_closed_and_40pct() -> None:
    from live_readiness import summary_qualifies_live

    assert summary_qualifies_live(None) is False
    assert summary_qualifies_live({"closed": 0, "win_rate_after_charges": 100}) is False
    assert summary_qualifies_live({"closed": 5, "win_rate_after_charges": 39.9}) is False
    assert summary_qualifies_live({"closed": 5, "win_rate_after_charges": 40.0}) is True
    assert summary_qualifies_live({"closed": 1, "win_rate": 100}) is True


def test_desk_snapshot_lists_40pct_paper_books() -> None:
    os.environ["DRY_RUN"] = "true"
    snap = desk_snapshot(
        summaries={
            "S18_OHLC_VOL_HTF": {
                "closed": 10,
                "win_rate_after_charges": 40.0,
                "pnl_after_charges": 250.0,
            },
            "S16_HHHL_WICK_1H": {"closed": 8, "win_rate_after_charges": 25.0},
            "OVERNIGHT_GAP": {
                "closed": 6,
                "win_rate_after_charges": 40.0,
                "pnl_after_charges": 120.0,
            },
        }
    )
    s18 = next(b for b in snap["books"] if b["strategy"] == "S18_OHLC_VOL_HTF")
    s16 = next(b for b in snap["books"] if b["strategy"] == "S16_HHHL_WICK_1H")
    gap = next(b for b in snap["books"] if b["strategy"] == "OVERNIGHT_GAP")
    s5 = next(b for b in snap["books"] if b["strategy"] == "S5_MINEDGE")
    assert s18["live_eligible"] is True
    assert s18["qualifies_live"] is True
    assert s18["closed"] == 10
    assert s16["live_eligible"] is True
    assert gap["live_eligible"] is True
    assert gap["qualifies_live"] is True
    assert s5["live_eligible"] is True
    assert snap["live_wr_min_pct"] == 40.0


def test_apply_desk_books_s18_live_when_wr_40() -> None:
    td = tempfile.TemporaryDirectory()
    try:
        env = Path(td.name) / ".env"
        env.write_text(
            "ENABLE_S13=true\nENABLE_S18=true\nDRY_RUN=true\nSECRET=keep\n",
            encoding="utf-8",
        )
        state = Path(td.name) / "state.json"
        from live_readiness import apply_desk_books

        res = apply_desk_books(
            ["S13_HHHL_DAY", "S18_OHLC_VOL_HTF"],
            ["S13_HHHL_DAY", "S18_OHLC_VOL_HTF"],
            path=env,
            state_path=state,
            qualified=["S18_OHLC_VOL_HTF"],
        )
        assert res["ok"] is True
        assert "S18_OHLC_VOL_HTF" in res["live_approved"]
        assert "S13_HHHL_DAY" in res["live_approved"]
    finally:
        td.cleanup()


def test_apply_desk_arm_rejects_live_without_word() -> None:
    from live_readiness import apply_desk_arm

    res = apply_desk_arm(mode="live", confirm="")
    assert res["ok"] is False
    assert "LIVE" in res["error"]


def test_apply_desk_arm_paper_locks() -> None:
    td = tempfile.TemporaryDirectory()
    try:
        import capital
        import control_state
        from live_readiness import apply_desk_arm

        env = Path(td.name) / ".env"
        env.write_text("ENABLE_S5=true\nDRY_RUN=true\nLIVE_MAX_LOTS=1\n", encoding="utf-8")
        state = Path(td.name) / "state.json"
        cap = Path(td.name) / "capital.json"
        control_state.STATE_PATH = state
        capital.CAPITAL_PATH = cap
        res = apply_desk_arm(
            mode="paper",
            confirm="",
            live_max_lots=1,
            in_bot=["S5_MINEDGE"],
            live=["S5_MINEDGE"],
            total_capital_inr=200000,
            daily_loss_limit_inr=2000,
            allocations=[
                {
                    "strategy": "S5_MINEDGE",
                    "live_size_mode": "lots",
                    "live_lots": 1,
                    "budget_inr": 50000,
                }
            ],
            path=env,
            state_path=state,
            capital_path=cap,
        )
        assert res["ok"] is True
        assert res["mode"] == "paper"
        assert control_state.load_state(path=state).live_unlocked is False
        assert "DRY_RUN=true" in env.read_text(encoding="utf-8")
        plan = capital.load_capital(path=cap)
        assert plan.strategies["S5_MINEDGE"].budget_inr == 50000
        assert plan.strategies["S5_MINEDGE"].live_lots == 1
        assert plan.strategies["S5_MINEDGE"].live_size_mode == "lots"
    finally:
        td.cleanup()
        import capital as capital_mod
        import control_state as cs

        cs.STATE_PATH = cs.CONTROL_DIR / "state.json"
        capital_mod.CAPITAL_PATH = capital_mod.CONTROL_DIR / "capital.json"


def test_apply_desk_arm_empty_in_bot_keeps_paper_enables() -> None:
    td = tempfile.TemporaryDirectory()
    try:
        import capital
        import control_state
        from live_readiness import apply_desk_arm

        env = Path(td.name) / ".env"
        env.write_text(
            "ENABLE_S5=true\nENABLE_S16=true\nENABLE_S18=true\n"
            "DRY_RUN=true\nLIVE_MAX_LOTS=1\n",
            encoding="utf-8",
        )
        state = Path(td.name) / "state.json"
        cap = Path(td.name) / "capital.json"
        control_state.STATE_PATH = state
        capital.CAPITAL_PATH = cap
        res = apply_desk_arm(
            mode="paper",
            confirm="",
            live_max_lots=1,
            in_bot=[],
            live=["S5_MINEDGE"],
            path=env,
            state_path=state,
            capital_path=cap,
        )
        assert res["ok"] is True
        text = env.read_text(encoding="utf-8")
        assert "ENABLE_S5=true" in text
        assert "ENABLE_S16=true" in text
        assert "ENABLE_S18=true" in text
        assert res["books"]["live_approved"] == ["S5_MINEDGE"]
        assert "S16_HHHL_WICK_1H" in res["books"]["enabled"]
        assert "S18_OHLC_VOL_HTF" in res["books"]["enabled"]
    finally:
        td.cleanup()
        import capital as capital_mod
        import control_state as cs

        cs.STATE_PATH = cs.CONTROL_DIR / "state.json"
        capital_mod.CAPITAL_PATH = capital_mod.CONTROL_DIR / "capital.json"


def test_load_state_intraday_default_vs_empty() -> None:
    import json

    from control_state import DEFAULT_INTRADAY_BOOKS, load_state

    td = tempfile.TemporaryDirectory()
    try:
        p = Path(td.name) / "state.json"
        p.write_text(
            json.dumps(
                {
                    "emergency_off": False,
                    "trading_enabled": True,
                    "live_unlocked": False,
                    "paper_approved": [],
                    "live_approved": [],
                    "force_disabled": [],
                }
            ),
            encoding="utf-8",
        )
        st = load_state(path=p)
        assert st.intraday_books == list(DEFAULT_INTRADAY_BOOKS)

        p.write_text(json.dumps({"intraday_books": []}), encoding="utf-8")
        st = load_state(path=p)
        assert st.intraday_books == []
    finally:
        td.cleanup()


def test_apply_desk_books_intraday_ticks() -> None:
    td = tempfile.TemporaryDirectory()
    try:
        env = Path(td.name) / ".env"
        env.write_text(
            "ENABLE_S5=true\nENABLE_S16=true\nDRY_RUN=true\nSECRET=keep\n",
            encoding="utf-8",
        )
        state = Path(td.name) / "state.json"
        from control_state import load_state
        from live_readiness import apply_desk_books

        res = apply_desk_books(
            ["S5_MINEDGE", "S16_HHHL_WICK_1H"],
            ["S5_MINEDGE"],
            path=env,
            state_path=state,
            intraday=[
                "S16_HHHL_WICK_1H",
                "S5_MINEDGE",
                "OVERNIGHT_GAP",
                "S25_AMISE",
            ],
        )
        assert res["ok"] is True
        books = list(load_state(path=state).intraday_books)
        assert "S16_HHHL_WICK_1H" in books
        assert "S5_MINEDGE" in books
        assert "S25_AMISE" in books
        assert "OVERNIGHT_GAP" not in books
        assert books == res["intraday_books"]

        kept = apply_desk_books(
            ["S5_MINEDGE", "S16_HHHL_WICK_1H"],
            ["S5_MINEDGE"],
            path=env,
            state_path=state,
        )
        assert kept["ok"] is True
        assert load_state(path=state).intraday_books == books

        cleared = apply_desk_books(
            ["S5_MINEDGE", "S16_HHHL_WICK_1H"],
            ["S5_MINEDGE"],
            path=env,
            state_path=state,
            intraday=[],
        )
        assert cleared["ok"] is True
        assert load_state(path=state).intraday_books == []
        assert cleared["intraday_books"] == []
    finally:
        td.cleanup()


def test_apply_desk_arm_intraday_ticks() -> None:
    td = tempfile.TemporaryDirectory()
    try:
        import capital
        import control_state
        from live_readiness import apply_desk_arm

        env = Path(td.name) / ".env"
        env.write_text(
            "ENABLE_S5=true\nENABLE_S16=true\nDRY_RUN=true\nLIVE_MAX_LOTS=1\n",
            encoding="utf-8",
        )
        state = Path(td.name) / "state.json"
        cap = Path(td.name) / "capital.json"
        control_state.STATE_PATH = state
        capital.CAPITAL_PATH = cap
        res = apply_desk_arm(
            mode="paper",
            confirm="",
            live_max_lots=1,
            in_bot=["S5_MINEDGE", "S16_HHHL_WICK_1H"],
            live=["S5_MINEDGE"],
            intraday=["S5_MINEDGE", "S16_HHHL_WICK_1H", "OVERNIGHT_GAP"],
            path=env,
            state_path=state,
            capital_path=cap,
        )
        assert res["ok"] is True
        st = control_state.load_state(path=state)
        assert "S5_MINEDGE" in st.intraday_books
        assert "S16_HHHL_WICK_1H" in st.intraday_books
        assert "OVERNIGHT_GAP" not in st.intraday_books
        assert st.intraday_books == res["books"]["intraday_books"]
    finally:
        td.cleanup()
        import capital as capital_mod
        import control_state as cs

        cs.STATE_PATH = cs.CONTROL_DIR / "state.json"
        capital_mod.CAPITAL_PATH = capital_mod.CONTROL_DIR / "capital.json"


def test_apply_desk_arm_keep_does_not_lock_live() -> None:
    td = tempfile.TemporaryDirectory()
    try:
        import capital
        import control_state
        from live_readiness import apply_desk_arm

        env = Path(td.name) / ".env"
        env.write_text(
            "ENABLE_S5=true\nDRY_RUN=false\nLIVE_MAX_LOTS=25\n",
            encoding="utf-8",
        )
        state = Path(td.name) / "state.json"
        cap = Path(td.name) / "capital.json"
        control_state.STATE_PATH = state
        capital.CAPITAL_PATH = cap
        control_state.set_live_unlocked(True, path=state, note="armed")
        control_state.set_live_approved(["S5_MINEDGE"], path=state)
        res = apply_desk_arm(
            mode="keep",
            confirm="",
            live_max_lots=25,
            in_bot=["S5_MINEDGE"],
            live=[],
            path=env,
            state_path=state,
            capital_path=cap,
        )
        assert res["ok"] is True
        assert res["mode"] == "live"
        st = control_state.load_state(path=state)
        assert st.live_unlocked is True
        assert "S5_MINEDGE" in st.live_approved
        text = env.read_text(encoding="utf-8")
        assert "DRY_RUN=false" in text
        assert "LIVE_MAX_LOTS=25" in text
        assert "unchanged" in str(res.get("note") or "").lower() or "RESTART" in str(res.get("note") or "")
    finally:
        td.cleanup()
        import capital as capital_mod
        import control_state as cs

        cs.STATE_PATH = cs.CONTROL_DIR / "state.json"
        capital_mod.CAPITAL_PATH = capital_mod.CONTROL_DIR / "capital.json"


if __name__ == "__main__":
    test_bot_age()
    print("ok age")
    test_readiness_paper_by_default()
    print("ok paper default")
    test_apply_panel_live_env_paper_ok()
    print("ok paper env")
    test_apply_panel_live_env_rejects_without_live_word()
    print("ok reject without LIVE")
    test_apply_panel_live_env_caps_lots()
    print("ok cap lots")
    test_apply_panel_live_env_live_with_word()
    print("ok LIVE word")
    test_panel_restart_requires_word()
    print("ok restart word")
    test_apply_panel_enables_slim()
    print("ok enables")
    test_apply_desk_books_live_requires_in_bot()
    print("ok desk books")
    test_apply_desk_books_s18_live_eligible_without_40()
    print("ok s18 live eligible")
    test_apply_desk_books_s19_stays_off()
    print("ok s19 off")
    test_apply_desk_books_s20_stays_off()
    print("ok s20 off")
    test_apply_desk_books_overnight_gap_live_eligible_without_40()
    print("ok overnight gap live eligible")
    test_apply_desk_books_overnight_gap_live_when_wr_40()
    print("ok overnight gap live at 40")
    test_ensure_overnight_gap_enable_writes_env()
    print("ok overnight gap enable write")
    test_desk_snapshot_skips_checklist()
    print("ok desk snapshot")
    test_summary_qualifies_live_needs_closed_and_40pct()
    print("ok wr 40 gate")
    test_desk_snapshot_lists_40pct_paper_books()
    print("ok snapshot 40pct")
    test_apply_desk_books_s18_live_when_wr_40()
    print("ok s18 live at 40")
    test_apply_desk_arm_rejects_live_without_word()
    print("ok arm needs LIVE")
    test_apply_desk_arm_paper_locks()
    print("ok arm paper")
    test_apply_desk_arm_empty_in_bot_keeps_paper_enables()
    print("ok arm keeps paper In")
    test_load_state_intraday_default_vs_empty()
    print("ok intraday default vs empty")
    test_apply_desk_books_intraday_ticks()
    print("ok desk books intraday")
    test_apply_desk_arm_intraday_ticks()
    print("ok arm intraday")
    test_apply_desk_arm_keep_does_not_lock_live()
    print("ok arm keep")
    print("ALL test_live_readiness OK")
