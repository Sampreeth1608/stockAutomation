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
    assert "S14_WICK30_STRICT" in names
    assert "S15_WICK30_NOWICK" in names
    s14 = next(b for b in r["books"] if b["strategy"] == "S14_WICK30_STRICT")
    assert s14["live_approved"] is False
    assert s14["live_qty"] == 0
    assert "enables" in r
    assert "S14_WICK30_STRICT" in r["enables"]


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
        res = apply_panel_enables(["S4_OVERNIGHT", "S14_WICK30_STRICT"], path=env)
        assert res["ok"] is True
        text = env.read_text(encoding="utf-8")
        assert "ENABLE_S4=true" in text
        assert "ENABLE_S14=true" in text
        assert "ENABLE_S9=false" in text
        assert "ENABLE_S5=false" in text
        assert "SECRET=keep" in text
        assert "S14_WICK30_STRICT" in res["enabled"]
    finally:
        td.cleanup()


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
    print("ALL test_live_readiness OK")
