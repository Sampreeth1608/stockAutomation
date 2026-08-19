"""You-tab mimic learner — sittings, hours, auto-paper. Never DRY_RUN=false."""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from human_capture import save_examples
from proposals import load_proposals
from s18_ohlc_vol_htf import VolBar
from strategy_amise import AmiseSlotStrategy, vol_to_flow
from you_learn import (
    build_mimic_genome,
    duration_bucket,
    group_sessions,
    hours_profile,
    learn_status,
    maybe_auto_paper,
    propose_mimic,
)

IST = ZoneInfo("Asia/Kolkata")


def _ex(
    action: str,
    *,
    imb: float,
    bull: bool,
    when: datetime | None = None,
    pts: float = 2.0,
    sid: str | None = None,
) -> dict:
    row = {
        "action": action,
        "snapshot": {
            "imb": imb,
            "depth_imb": imb,
            "vwap_gap": 4.0 if bull else -4.0,
            "ltp_velocity_30s": 0.2 if bull else -0.2,
            "spread": 1.0,
            "ltp": 15000.0,
            "last_1m": {
                "bull": bull,
                "bear": not bull,
                "hh": bull,
                "hl": bull,
                "hc": bull,
                "lh": not bull,
                "ll": not bull,
                "lc": not bull,
                "vol_up": True,
                "body_frac": 0.7,
                "upper_wick_frac": 0.1 if bull else 0.55,
                "lower_wick_frac": 0.55 if bull else 0.1,
                "n_ticks": 20,
            },
        },
        "outcomes": {"1m": {"taken_pts": pts if action != "no_trade" else 0.0}},
    }
    if when is not None:
        stamp = when.isoformat(timespec="seconds")
        row["created_at_ist"] = stamp
        row["entry_at"] = stamp
    if sid:
        row["you_session_id"] = sid
    return row


def _enough(*, n_taken: int = 16, n_skips: int = 8, n_days: int = 1, pts: float = 2.0) -> list[dict]:
    t0 = datetime(2026, 8, 18, 10, 0, tzinfo=IST)
    rows: list[dict] = []
    for i in range(n_taken):
        day = 0 if n_days <= 1 else (0 if i < n_taken // 2 else 1)
        act = "buy" if i % 2 == 0 else "short"
        rows.append(
            _ex(
                act,
                imb=0.3 if act == "buy" else -0.3,
                bull=act == "buy",
                when=t0 + timedelta(days=day, minutes=i % 20),
                pts=pts,
            )
        )
    for i in range(n_skips):
        rows.append(
            _ex(
                "no_trade",
                imb=0.0,
                bull=True,
                when=t0 + timedelta(minutes=20 + i),
                pts=0.0,
            )
        )
    return rows


def test_not_ready_until_counts() -> None:
    st = learn_status([_ex("buy", imb=0.2, bull=True)])
    assert st["ready"] is False
    assert st["need_taken"] > 0
    assert st["need_skips"] > 0
    assert st["knowledge_good"] is False


def test_genome_from_clicks() -> None:
    buys = [_ex("buy", imb=0.3, bull=True) for _ in range(8)]
    skips = [_ex("no_trade", imb=-0.2, bull=False) for _ in range(6)]
    g = build_mimic_genome(buys + skips)
    assert g.timeframe == "1m"
    assert g.source == "you_capture"
    assert g.recipe == ""
    assert g.direction in {"long", "both"}
    assert "imb_buy" in g.entry_long or "bull" in g.entry_long or g.entry_long


def test_group_sessions_gap_and_buckets() -> None:
    t0 = datetime(2026, 8, 18, 10, 0, tzinfo=IST)
    same = [
        _ex("buy", imb=0.2, bull=True, when=t0),
        _ex("buy", imb=0.2, bull=True, when=t0 + timedelta(minutes=10)),
    ]
    g1 = group_sessions(same)
    assert len(g1) == 1
    assert g1[0]["bucket"] == "30m"
    later = same + [_ex("buy", imb=0.2, bull=True, when=t0 + timedelta(hours=2))]
    g2 = group_sessions(later)
    assert len(g2) == 2
    day_rows = [
        _ex("buy", imb=0.2, bull=True, when=t0 + timedelta(minutes=30 * i)) for i in range(13)
    ]
    g3 = group_sessions(day_rows)
    assert len(g3) == 1
    assert g3[0]["bucket"] == "day"
    assert duration_bucket(20 * 60) == "30m"
    assert duration_bucket(60 * 60) == "1h"
    assert duration_bucket(3 * 3600) == "3h"
    assert duration_bucket(6 * 3600) == "day"


def test_hours_profile_morning_window() -> None:
    t0 = datetime(2026, 8, 18, 10, 0, tzinfo=IST)
    rows = [
        _ex("buy", imb=0.3, bull=True, when=t0 + timedelta(minutes=i))
        for i in range(0, 31, 10)
    ]
    hours = hours_profile(rows)
    assert hours["open_min"] == 9 * 60 + 50
    assert hours["close_min"] == 10 * 60 + 40
    assert "10:" in hours["label"] or "09:" in hours["label"]
    g = build_mimic_genome(rows + [_ex("no_trade", imb=0.0, bull=True, when=t0)])
    assert g.params["you_open_min"] == float(hours["open_min"])
    assert g.params["you_close_min"] == float(hours["close_min"])
    assert g.params["you_hours_mask"] >= 1 << 10


def test_knowledge_good_needs_sittings() -> None:
    one = _enough(n_days=1)
    st = learn_status(one)
    assert st["ready"] is True
    assert st["n_sessions"] == 1
    assert st["knowledge_good"] is False
    assert any("sitting" in x for x in st["auto_need"])
    two = _enough(n_days=2)
    good = learn_status(two)
    assert good["n_sessions"] >= 2
    assert good["knowledge_good"] is True


def test_propose_writes_lab_row(tmp_path: Path) -> None:
    store = tmp_path / "examples.json"
    props = tmp_path / "proposals.json"
    mimic = tmp_path / "mimic.json"
    rows = [_ex("buy", imb=0.3, bull=True) for _ in range(8)]
    rows += [_ex("short", imb=-0.3, bull=False) for _ in range(8)]
    rows += [_ex("no_trade", imb=0.0, bull=True) for _ in range(6)]
    save_examples(rows, path=store)
    out = propose_mimic(examples_path=store, proposals_path=props, mimic_path=mimic)
    assert out["ok"] is True
    assert out["proposed"] is True
    assert "DRY_RUN" in (out.get("reminder") or "")
    items = load_proposals(path=props)
    assert items
    assert items[0].kind == "research"
    assert items[0].env_patch.get("DRY_RUN") == "true"
    extra = items[0].paper.extra
    assert extra.get("you_mimic") is True
    assert extra.get("genome", {}).get("timeframe") == "1m"
    assert extra.get("knowledge_good") is False
    assert mimic.is_file()


def test_thin_set_does_not_propose(tmp_path: Path) -> None:
    store = tmp_path / "examples.json"
    props = tmp_path / "proposals.json"
    save_examples([_ex("buy", imb=0.2, bull=True)], path=store)
    out = propose_mimic(examples_path=store, proposals_path=props, mimic_path=tmp_path / "m.json")
    assert out["ok"] is False
    assert out["proposed"] is False
    assert not load_proposals(path=props)


def test_maybe_auto_paper_assigns_s21_keep_dry(tmp_path: Path) -> None:
    store = tmp_path / "examples.json"
    props = tmp_path / "proposals.json"
    mimic = tmp_path / "mimic.json"
    slots = tmp_path / "slots"
    env = tmp_path / ".env"
    state = tmp_path / "state.json"
    deploy = tmp_path / "deploy.json"
    env.write_text("DRY_RUN=true\nENABLE_S5=true\n", encoding="utf-8")
    save_examples(_enough(n_days=2), path=store)
    out = maybe_auto_paper(
        examples_path=store,
        proposals_path=props,
        mimic_path=mimic,
        slots_dir=slots,
        env_path=env,
        state_path=state,
        deploy_path=deploy,
    )
    assert out["ok"] is True
    assert out["deployed"] is True
    assert out["slot"] == "S21_AMISE"
    assert out["live_unlocked"] is False
    text = env.read_text(encoding="utf-8")
    assert "ENABLE_S21=true" in text
    assert "DRY_RUN=false" not in text
    assert "DRY_RUN=true" in text
    from amise_slots import load_slot_genome

    loaded = load_slot_genome("S21_AMISE", slots)
    assert loaded is not None
    assert loaded.source == "you_capture"
    bot = AmiseSlotStrategy("S21_AMISE", loaded, seed=False)
    assert bot._you_hours()[0] is not None
    again = maybe_auto_paper(
        examples_path=store,
        proposals_path=props,
        mimic_path=mimic,
        slots_dir=slots,
        env_path=env,
        state_path=state,
        deploy_path=deploy,
    )
    assert again["already"] is True
    assert again["deployed"] is False
    assert again["slot"] == "S21_AMISE"


def test_you_hours_gate_entries_and_flatten() -> None:
    from strategy_genome import StrategyGenome

    g = StrategyGenome(
        name="you mimic 1m TBQ/TSQ",
        timeframe="1m",
        entry_long=("hh", "hl"),
        entry_short=("lh", "ll"),
        params={
            "lookback": 1,
            "atr_n": 1,
            "you_open_min": 600,
            "you_close_min": 630,
            "you_hours_mask": float(1 << 10),
        },
        source="you_capture",
        recipe="",
    ).normalized()
    s = AmiseSlotStrategy("S21_AMISE", g, seed=False)
    assert s._in_you_hours(datetime(2026, 8, 18, 10, 15, tzinfo=IST)) is True
    assert s._in_you_hours(datetime(2026, 8, 18, 12, 0, tzinfo=IST)) is False
    s.position = "long"
    why = s._session_flatten_why(datetime(2026, 8, 18, 12, 0, tzinfo=IST))
    assert why is not None and "you hours" in why
    in_why = s._session_flatten_why(datetime(2026, 8, 18, 10, 15, tzinfo=IST))
    assert in_why is None or "you hours" not in in_why
    b0 = VolBar("2026-08-18 10:00:00", 100, 101, 99, 100.5, 100, 10, 80, 40)
    b1 = VolBar("2026-08-18 10:15:00", 100.5, 103, 100.6, 102.5, 150, 12, 90, 30)
    s2 = AmiseSlotStrategy("S21_AMISE", g, seed=False)
    s2._flow_bars = [vol_to_flow(b0)]
    got = s2._decide_closed(b1)
    assert got is not None
    b2 = VolBar("2026-08-18 11:00:00", 100, 101, 99, 100.5, 100, 10, 80, 40)
    b3 = VolBar("2026-08-18 12:00:00", 100.5, 103, 100.6, 102.5, 150, 12, 90, 30)
    s3 = AmiseSlotStrategy("S21_AMISE", g, seed=False)
    s3._flow_bars = [vol_to_flow(b2)]
    assert s3._decide_closed(b3) is None
    assert s3.last_skip == "outside_you_hours"


if __name__ == "__main__":
    test_not_ready_until_counts()
    test_genome_from_clicks()
    test_group_sessions_gap_and_buckets()
    test_hours_profile_morning_window()
    test_knowledge_good_needs_sittings()
    test_you_hours_gate_entries_and_flatten()
    td = Path(tempfile.mkdtemp())
    (td / "a").mkdir()
    test_propose_writes_lab_row(td / "a")
    (td / "b").mkdir()
    test_thin_set_does_not_propose(td / "b")
    (td / "c").mkdir()
    test_maybe_auto_paper_assigns_s21_keep_dry(td / "c")
    print("ALL test_you_learn OK")
