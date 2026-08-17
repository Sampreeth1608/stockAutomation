"""S14 formula dump on finished candles (no Angel login)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from explain_s14_candles import (
    bar_is_finished,
    bot_python_candidates,
    explain_bar,
    format_line,
    settle_s14_from_walk,
    walk_candles,
)
from strategy_wick import s14_bar_decision

IST = ZoneInfo("Asia/Kolkata")

# Angel 1d Gold Petal dump (finished bars only; Aug 17 still forming at 15:54 IST).
EXCHANGE_1D_AUG = [
    {"time": "2026-08-03 00:00:00", "open": 14379.0, "high": 14418.0, "low": 14300.0, "close": 14324.0},
    {"time": "2026-08-04 00:00:00", "open": 14379.0, "high": 14445.0, "low": 14346.0, "close": 14425.0},
    {"time": "2026-08-05 00:00:00", "open": 14448.0, "high": 14803.0, "low": 14448.0, "close": 14770.0},
    {"time": "2026-08-06 00:00:00", "open": 14800.0, "high": 14932.0, "low": 14750.0, "close": 14823.0},
    {"time": "2026-08-07 00:00:00", "open": 14831.0, "high": 15180.0, "low": 14831.0, "close": 15118.0},
    {"time": "2026-08-10 00:00:00", "open": 15076.0, "high": 15267.0, "low": 15050.0, "close": 15239.0},
    {"time": "2026-08-11 00:00:00", "open": 15298.0, "high": 15489.0, "low": 15270.0, "close": 15319.0},
    {"time": "2026-08-12 00:00:00", "open": 15398.0, "high": 15501.0, "low": 15364.0, "close": 15445.0},
    {"time": "2026-08-13 00:00:00", "open": 15445.0, "high": 15464.0, "low": 15288.0, "close": 15316.0},
    {"time": "2026-08-14 00:00:00", "open": 15273.0, "high": 15425.0, "low": 15157.0, "close": 15394.0},
]


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


def test_settle_fill_at_signal_close() -> None:
    candles = [
        {"time": "2026-08-17 10:00:00", "open": 100.0, "high": 100.0, "low": 90.0, "close": 95.0},
        {"time": "2026-08-17 10:30:00", "open": 95.0, "high": 110.0, "low": 95.0, "close": 108.0},
        {"time": "2026-08-17 11:00:00", "open": 108.0, "high": 108.0, "low": 108.0, "close": 108.0},
    ]
    rows = walk_candles(candles)
    r = settle_s14_from_walk(rows, tf="toy", lots=1, fees=False)
    assert r.n_trades == 2
    assert r.trades[0].side == "SHORT"
    assert r.trades[0].entry_px == 95.0
    assert r.trades[0].exit_px == 108.0
    assert r.trades[0].gross_pts == -13.0
    assert r.trades[1].side == "LONG"
    assert r.trades[1].entry_px == 108.0
    assert r.trades[1].exit_px == 108.0
    assert r.gross_pts == -13.0


def test_settle_exchange_1d_aug() -> None:
    rows = walk_candles(EXCHANGE_1D_AUG)
    actions = [(r["time"][:10], r["action"], r["side"]) for r in rows if r["action"] != "skip"]
    assert actions == [
        ("2026-08-03", "enter", "short"),
        ("2026-08-04", "FLIP", "long"),
        ("2026-08-05", "hold", "long"),
        ("2026-08-06", "FLIP", "short"),
        ("2026-08-07", "FLIP", "long"),
        ("2026-08-10", "FLIP", "short"),
        ("2026-08-11", "hold", "short"),
        ("2026-08-12", "hold", "short"),
        ("2026-08-13", "FLIP", "long"),
        ("2026-08-14", "hold", "long"),
    ]
    gross = settle_s14_from_walk(rows, tf="1d", lots=1, fees=False)
    assert gross.n_trades == 6
    assert [round(t.gross_pts, 1) for t in gross.trades] == [
        -101.0,
        398.0,
        -295.0,
        121.0,
        -77.0,
        78.0,
    ]
    assert gross.gross_pts == 124.0
    assert gross.n_long == 3
    assert gross.n_short == 3
    tape = settle_s14_from_walk(rows, tf="1d", lots=100, fees=True)
    assert tape.n_trades == 6
    assert tape.gross_pts == 12400.0
    assert tape.gross_pnl_inr == 12400.0
    assert round(tape.fees_inr, 0) == 1827
    # Per-trade 30% tax on winners only: gross +₹12.4k becomes after-tax red.
    assert round(tape.after_tax_pnl_inr, 0) == -7061


if __name__ == "__main__":
    test_s14_bar_decision_open_high_beats_lower_wick()
    test_s14_bar_decision_wick_when_both_sides()
    test_walk_prints_formula_numbers()
    test_unfinished_bars_are_not_decided()
    test_bot_python_candidates_is_a_list()
    test_settle_fill_at_signal_close()
    test_settle_exchange_1d_aug()
    print("ALL test_explain_s14_candles OK")
