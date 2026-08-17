"""S14 formula dump on finished candles (no Angel login)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from explain_s14_candles import (
    bar_is_finished,
    bot_python_candidates,
    explain_bar,
    format_line,
    walk_candles,
)
from strategy_wick import s14_bar_decision

IST = ZoneInfo("Asia/Kolkata")


def test_s14_bar_decision_open_high_beats_lower_wick() -> None:
    side, why = s14_bar_decision(100.0, 100.0, 90.0, 95.0)
    assert side == "short"
    assert "open=high" in why


def test_s14_bar_decision_wick_when_both_sides() -> None:
    side, why = s14_bar_decision(100.0, 105.0, 90.0, 102.0)
    assert side == "long"
    assert "wick" in why


def test_walk_prints_formula_numbers() -> None:
    candles = [
        {"time": "2026-08-17 10:00:00", "open": 100.0, "high": 100.0, "low": 90.0, "close": 95.0},
        {"time": "2026-08-17 10:30:00", "open": 95.0, "high": 110.0, "low": 95.0, "close": 108.0},
        {"time": "2026-08-17 11:00:00", "open": 108.0, "high": 108.0, "low": 108.0, "close": 108.0},
    ]
    rows = walk_candles(candles)
    assert rows[0]["open_eq_high"] is True
    assert rows[0]["side"] == "short"
    assert rows[0]["action"] == "enter"
    assert rows[0]["upper"] == 0.0
    assert rows[0]["lower"] == 5.0
    assert rows[1]["open_eq_low"] is True
    assert rows[1]["side"] == "long"
    assert rows[1]["action"] == "FLIP"
    assert rows[2]["side"] == "skip"
    assert rows[2]["action"] == "skip"
    line = format_line(rows[0])
    assert "U=0.0" in line
    assert "SHORT" in line
    row = explain_bar(
        time="x", o=100.0, h=110.0, l=90.0, c=100.0, pos="flat"
    )
    assert row["open_eq_high"] is False
    assert row["open_eq_low"] is False
    assert row["upper"] == 10.0
    assert row["lower"] == 10.0
    assert row["side"] == "skip"


def test_unfinished_bars_are_not_decided() -> None:
    now = datetime.fromisoformat("2026-08-17T15:54:00+05:30").astimezone(IST)
    assert bar_is_finished("2026-08-17 15:00:00", "30m", now) is True
    assert bar_is_finished("2026-08-17 15:30:00", "30m", now) is False
    assert bar_is_finished("2026-08-17 15:00:00", "1h", now) is False
    assert bar_is_finished("2026-08-17 14:00:00", "1h", now) is True
    assert bar_is_finished("2026-08-17 00:00:00", "1d", now) is False
    assert bar_is_finished("2026-08-14 00:00:00", "1d", now) is True


def test_bot_python_candidates_is_a_list() -> None:
    found = bot_python_candidates()
    assert isinstance(found, list)


if __name__ == "__main__":
    test_s14_bar_decision_open_high_beats_lower_wick()
    test_s14_bar_decision_wick_when_both_sides()
    test_walk_prints_formula_numbers()
    test_unfinished_bars_are_not_decided()
    test_bot_python_candidates_is_a_list()
    print("ALL test_explain_s14_candles OK")
