"""Tests for live readiness checklist (does not arm live)."""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from live_readiness import bot_age_seconds, live_readiness

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


if __name__ == "__main__":
    test_bot_age()
    print("ok age")
    test_readiness_paper_by_default()
    print("ok paper default")
    print("ALL test_live_readiness OK")
