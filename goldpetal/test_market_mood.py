"""Shared market mood — one tape read, not a new paper book."""

from __future__ import annotations

import os
from pathlib import Path

from control_state import ALL_STRATEGY_NAMES, SLIM_PAPER_STRATEGIES
from live_readiness import PAPER_ONLY_BOOKS
from market_mood import (
    MOOD_EXEMPT_BOOKS,
    MoodDetector,
    classify_samples,
    mood_blocks_entry,
    mood_wants_flatten,
)


def _fall() -> list[tuple[float, float, float]]:
    out = []
    px, tbq, tsq = 15466.0, 8000.0, 5000.0
    for i in range(24):
        px -= 4.0
        tbq -= 20.0
        tsq += 80.0
        out.append((px, tbq, tsq))
    return out


def _quiet() -> list[tuple[float, float, float]]:
    return [(15400.0 + (0.2 if i % 2 else -0.2), 6000.0, 5900.0) for i in range(24)]


def _burst() -> list[tuple[float, float, float]]:
    out = [(15400.0, 6000.0, 6000.0) for _ in range(16)]
    px = 15400.0
    for i in range(8):
        px -= 8.0
        out.append((px, 5000.0, 9000.0))
    return out


def _rise() -> list[tuple[float, float, float]]:
    out = []
    px, tbq, tsq = 15300.0, 5000.0, 8000.0
    for _i in range(24):
        px += 4.0
        tbq += 80.0
        tsq -= 20.0
        out.append((px, tbq, tsq))
    return out


def test_fall_start_blocks_long_when_gated() -> None:
    os.environ["MOOD_GATE"] = "true"
    os.environ["MOOD_FLATTEN"] = "true"
    try:
        st = classify_samples(_fall(), gate=True, flatten=True)
        assert st.mood == "FALL_START"
        assert st.allow_long is False
        assert st.flatten_long is True
        blocked, why = mood_blocks_entry(st, "BUY")
        assert blocked and "block_long" in why
        want, _ = mood_wants_flatten(st, "long")
        assert want is True
    finally:
        os.environ.pop("MOOD_GATE", None)
        os.environ.pop("MOOD_FLATTEN", None)


def test_observe_does_not_block() -> None:
    os.environ["MOOD_GATE"] = "false"
    try:
        st = classify_samples(_fall(), gate=False, flatten=False)
        assert st.mood == "FALL_START"
        blocked, why = mood_blocks_entry(st, "BUY")
        assert blocked is False
        assert why == "mood_observe"
    finally:
        os.environ.pop("MOOD_GATE", None)


def test_rise_start_blocks_short_when_gated() -> None:
    st = classify_samples(_rise(), gate=True, flatten=True)
    assert st.mood == "RISE_START"
    assert st.allow_short is False
    blocked, why = mood_blocks_entry(st, "SHORT")
    assert blocked and "block_short" in why
    skipped, skip_why = mood_blocks_entry(st, "BUY", strategy="S13_HHHL_DAY")
    assert skipped is False and skip_why == "mood_exempt"


def test_s13_never_mood_flatten() -> None:
    st = classify_samples(_fall(), gate=True, flatten=True)
    want, why = mood_wants_flatten(st, "long", strategy="S13_HHHL_DAY")
    assert want is False
    assert why == "mood_exempt"
    assert "S13_HHHL_DAY" in MOOD_EXEMPT_BOOKS
    assert "S4_OVERNIGHT" in MOOD_EXEMPT_BOOKS


def test_quiet_and_burst() -> None:
    q = classify_samples(_quiet(), gate=False)
    assert q.mood == "QUIET"
    b = classify_samples(_burst(), gate=False)
    assert b.mood == "BURST"
    assert b.allow_long is False and b.allow_short is False


def test_streaming_detector() -> None:
    d = MoodDetector(window=40)
    for px, tbq, tsq in _fall():
        d.update(px, tbq, tsq)
    assert d.last.mood in {"FALL_START", "HEAT", "BURST", "COOL"}
    assert d.last.direction == "down"


def test_not_a_paper_book() -> None:
    root = Path(__file__).resolve().parent
    assert "MARKET_MOOD" not in ALL_STRATEGY_NAMES
    assert "mood" not in SLIM_PAPER_STRATEGIES
    runner = (root / "run_strategy.py").read_text(encoding="utf-8")
    assert "MOOD_GATE" in runner
    paper = (root / "station.html").read_text(encoding="utf-8").split("const PAPER_BOOKS")[1].split("];")[0]
    assert "MOOD" not in paper
    assert "MARKET_MOOD" not in PAPER_ONLY_BOOKS
    station = (root / "station.html").read_text(encoding="utf-8")
    assert "/api/mood" in station
    assert "mood-pill" in station
    lite = (root / "lite.html").read_text(encoding="utf-8")
    assert "/api/mood" in lite
    panel = (root / "control_panel.py").read_text(encoding="utf-8")
    assert "/api/mood" in panel
    env = (root / ".env.example").read_text(encoding="utf-8")
    assert "MOOD_GATE=false" in env
    assert "MOOD_FLATTEN=false" in env
    runner = (root / "run_strategy.py").read_text(encoding="utf-8")
    assert "mood_blocks_entry" in runner
    assert "MOOD_FLATTEN" in runner


if __name__ == "__main__":
    test_fall_start_blocks_long_when_gated()
    test_observe_does_not_block()
    test_rise_start_blocks_short_when_gated()
    test_s13_never_mood_flatten()
    test_quiet_and_burst()
    test_streaming_detector()
    test_not_a_paper_book()
    print("ALL test_market_mood OK")
