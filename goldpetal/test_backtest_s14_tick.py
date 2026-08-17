"""Tick-replay backtest for S14 same-candle open=high/low + wick on close."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from backtest_s14_tick import s14_cfg, simulate_s14_ticks
from strategy_wick import WickCandleStrategy

IST = ZoneInfo("Asia/Kolkata")


def test_s14_cfg_is_flip_and_open_hold_on_close() -> None:
    c = s14_cfg()
    assert c.reenter is True
    assert c.wick_anytime is False
    assert c.wick_on_close is True
    assert c.open_hold_minutes == 0.0
    assert c.open_hold_on_close is True
    assert c.nowick_body is False
    assert c.entry_strict is False
    assert c.exit_strict is False


def test_replay_matches_live_wick_flip() -> None:
    rows = [
        ("2026-08-17T10:00:00+05:30", 100.0),
        ("2026-08-17T10:05:00+05:30", 90.0),
        ("2026-08-17T10:10:00+05:30", 105.0),
        ("2026-08-17T10:29:00+05:30", 102.0),  # forming — no trade
        ("2026-08-17T10:30:00+05:30", 100.0),  # neither OH/OL, lower wick → LONG @ 100
        ("2026-08-17T10:35:00+05:30", 90.0),
        ("2026-08-17T10:40:00+05:30", 120.0),
        ("2026-08-17T10:50:00+05:30", 100.0),
        ("2026-08-17T11:00:00+05:30", 99.0),  # neither OH/OL, upper wick → SHORT @ 99
        ("2026-08-17T11:10:00+05:30", 98.0),
    ]
    r = simulate_s14_ticks(rows, tf="toy:s14", lots=1, fees=False, open_hold_minutes=2)
    assert r.n_trades == 2
    assert r.trades[0].side == "LONG"
    assert r.trades[0].entry_px == 100.0
    assert r.trades[0].exit_px == 99.0
    assert r.trades[1].side == "SHORT"
    assert r.trades[1].entry_px == 99.0
    assert r.trades[1].exit_px == 98.0


def test_replay_open_high_shorts_on_close() -> None:
    rows = [
        ("2026-08-17T10:00:00+05:30", 100.0),
        ("2026-08-17T10:10:00+05:30", 90.0),
        ("2026-08-17T10:29:00+05:30", 95.0),
        ("2026-08-17T10:30:00+05:30", 94.0),  # 10:00 closed open=high → SHORT @ 94
        ("2026-08-17T10:40:00+05:30", 93.0),
    ]
    live = WickCandleStrategy("S14_WICK30_STRICT", s14_cfg(), seed=False)
    actions = []
    for raw, px in rows:
        sig = live.on_tick(datetime.fromisoformat(raw).astimezone(IST), px)
        if sig is not None:
            actions.append(sig.action)
    assert actions == ["SHORT"]
    assert live.position == "short"

    r = simulate_s14_ticks(rows, tf="toy:oh", lots=1, fees=False, open_hold_minutes=2)
    assert r.n_trades >= 1
    assert r.trades[0].side == "SHORT"
    assert r.trades[0].entry_px == 94.0


def test_two_minutes_does_not_force() -> None:
    rows = [
        ("2026-08-17T10:30:00+05:30", 100.0),
        ("2026-08-17T10:31:00+05:30", 99.0),
        ("2026-08-17T10:32:00+05:30", 98.0),
        ("2026-08-17T10:40:00+05:30", 97.0),
    ]
    r = simulate_s14_ticks(rows, tf="toy:early", lots=1, fees=False, open_hold_minutes=2)
    assert r.n_trades == 0


def test_equal_open_high_low_does_not_force() -> None:
    rows = [
        ("2026-08-17T11:00:00+05:30", 100.0),
        ("2026-08-17T11:01:00+05:30", 100.0),
        ("2026-08-17T11:02:00+05:30", 100.0),
        ("2026-08-17T11:10:00+05:30", 100.0),
    ]
    r = simulate_s14_ticks(rows, tf="toy:flat", lots=1, fees=False, open_hold_minutes=2)
    assert r.n_trades == 0


def test_multi_tf_runs() -> None:
    from backtest_s14_tick import _parse_tfs

    tfs = _parse_tfs("5m,15m,30,1d")
    assert tfs == [("5m", 5), ("15m", 15), ("30m", 30), ("1d", 1440)]
    rows = [
        ("2026-08-17T10:00:00+05:30", 100.0),
        ("2026-08-17T10:10:00+05:30", 90.0),
        ("2026-08-17T10:11:00+05:30", 95.0),
        ("2026-08-17T10:21:00+05:30", 100.0),
        ("2026-08-17T11:00:00+05:30", 99.0),
    ]
    for name, minutes in tfs:
        r = simulate_s14_ticks(
            rows, tf=f"toy:{name}", lots=1, fees=False, open_hold_minutes=2, bar_minutes=minutes
        )
        assert r.n_bars >= 1


if __name__ == "__main__":
    test_s14_cfg_is_flip_and_open_hold_on_close()
    test_replay_matches_live_wick_flip()
    test_replay_open_high_shorts_on_close()
    test_two_minutes_does_not_force()
    test_equal_open_high_low_does_not_force()
    test_multi_tf_runs()
    print("ALL test_backtest_s14_tick OK")
