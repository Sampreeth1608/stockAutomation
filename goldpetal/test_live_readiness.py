"""Tests for live readiness checklist and panel .env writes (does not arm live)."""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from live_readiness import (
    apply_panel_enables,
    apply_panel_live_env,
    bot_age_seconds,
    desk_snapshot,
    live_readiness,
    panel_restart_allowed,
    read_live_env,
)

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
    assert "S19_BODY_CLOSE_1H" in names
    assert "S20_FADE_HL" in names
    assert "S14_WICK30_STRICT" not in names
    assert "S15_WICK30_NOWICK" not in names
    assert "S12_HHHL30" not in names
    s16 = next(b for b in r["books"] if b["strategy"] == "S16_HHHL_WICK_1H")
    assert s16["live_approved"] is False
    assert s16["live_qty"] == 0
    assert "enables" in r
    assert "S16_HHHL_WICK_1H" in r["enables"]
    assert "S18_OHLC_VOL_HTF" in r["enables"]
    assert "S19_BODY_CLOSE_1H" in r["enables"]
    assert "S20_FADE_HL" in r["enables"]


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
        assert "1–10" in res["error"] or "1-10" in res["error"]
        assert "LIVE_MAX_LOTS=1" in env.read_text(encoding="utf-8")
        res0 = apply_panel_live_env(
            dry_run=True,
            live_max_lots=0,
            confirm="",
            path=env,
            sync_environ=False,
        )
        assert res0["ok"] is False
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


def test_apply_desk_books_s18_stays_paper_only() -> None:
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
        )
        assert res["ok"] is True
        assert "S18_OHLC_VOL_HTF" in res["enabled"]
        assert "S18_OHLC_VOL_HTF" not in res["live_approved"]
        assert "S13_HHHL_DAY" in res["live_approved"]
        assert "S18_OHLC_VOL_HTF" in res["skipped_live_not_in_bot"]
        text = env.read_text(encoding="utf-8")
        assert "ENABLE_S18=true" in text
        assert "DRY_RUN=true" in text
        assert "SECRET=keep" in text
    finally:
        td.cleanup()


def test_apply_desk_books_s19_stays_paper_only() -> None:
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
        assert "S19_BODY_CLOSE_1H" in res["enabled"]
        assert "S19_BODY_CLOSE_1H" not in res["live_approved"]
        assert "S13_HHHL_DAY" in res["live_approved"]
        assert "S19_BODY_CLOSE_1H" in res["skipped_live_not_in_bot"]
        text = env.read_text(encoding="utf-8")
        assert "ENABLE_S19=true" in text
        assert "DRY_RUN=true" in text
        assert "SECRET=keep" in text
    finally:
        td.cleanup()


def test_apply_desk_books_s20_stays_paper_only() -> None:
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
        assert "S20_FADE_HL" in res["enabled"]
        assert "S20_FADE_HL" not in res["live_approved"]
        assert "S13_HHHL_DAY" in res["live_approved"]
        assert "S20_FADE_HL" in res["skipped_live_not_in_bot"]
        text = env.read_text(encoding="utf-8")
        assert "ENABLE_S20=true" in text
        assert "DRY_RUN=true" in text
        assert "SECRET=keep" in text
    finally:
        td.cleanup()


def test_desk_snapshot_skips_checklist() -> None:
    os.environ["DRY_RUN"] = "true"
    snap = desk_snapshot()
    assert "steps" not in snap
    assert "S16_HHHL_WICK_1H" in snap["enables"]
    assert "S18_OHLC_VOL_HTF" in snap["enables"]
    assert "S19_BODY_CLOSE_1H" in snap["enables"]
    assert "S20_FADE_HL" in snap["enables"]
    assert any(b["strategy"] == "S16_HHHL_WICK_1H" for b in snap["books"])
    assert any(b["strategy"] == "S18_OHLC_VOL_HTF" for b in snap["books"])
    assert any(b["strategy"] == "S19_BODY_CLOSE_1H" for b in snap["books"])
    assert any(b["strategy"] == "S20_FADE_HL" for b in snap["books"])
    s18 = next(b for b in snap["books"] if b["strategy"] == "S18_OHLC_VOL_HTF")
    s16 = next(b for b in snap["books"] if b["strategy"] == "S16_HHHL_WICK_1H")
    assert s18["live_eligible"] is False
    assert s16["live_eligible"] is True
    assert "S4_OVERNIGHT" not in snap["enables"]
    assert not any(b["strategy"] == "S4_OVERNIGHT" for b in snap["books"])
    assert "S14_WICK30_STRICT" not in snap["enables"]
    assert snap["would_place_real_orders"] is False


def test_apply_desk_books_amise_not_live() -> None:
    td = tempfile.TemporaryDirectory()
    try:
        env = Path(td.name) / ".env"
        env.write_text(
            "ENABLE_S13=true\nENABLE_S21=true\nDRY_RUN=true\nSECRET=keep\n",
            encoding="utf-8",
        )
        state = Path(td.name) / "state.json"
        from live_readiness import apply_desk_books

        res = apply_desk_books(
            ["S13_HHHL_DAY", "S21_AMISE"],
            ["S13_HHHL_DAY", "S21_AMISE"],
            path=env,
            state_path=state,
        )
        assert res["ok"] is True
        assert "S21_AMISE" in res["enabled"]
        assert "S21_AMISE" not in res["live_approved"]
        assert "S13_HHHL_DAY" in res["live_approved"]
        assert "S21_AMISE" in res["skipped_live_not_in_bot"]
        assert "DRY_RUN=true" in env.read_text(encoding="utf-8")
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
                {"strategy": "S5_MINEDGE", "budget_inr": 50000, "max_lots": 1}
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
    test_apply_desk_books_s18_stays_paper_only()
    print("ok s18 paper only")
    test_apply_desk_books_s19_stays_paper_only()
    print("ok s19 paper only")
    test_apply_desk_books_s20_stays_paper_only()
    print("ok s20 paper only")
    test_desk_snapshot_skips_checklist()
    print("ok desk snapshot")
    test_apply_desk_books_amise_not_live()
    print("ok amise not live")
    test_apply_desk_arm_rejects_live_without_word()
    print("ok arm needs LIVE")
    test_apply_desk_arm_paper_locks()
    print("ok arm paper")
    test_apply_desk_arm_empty_in_bot_keeps_paper_enables()
    print("ok arm keeps paper In")
    print("ALL test_live_readiness OK")
