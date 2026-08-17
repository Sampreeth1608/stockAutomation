"""Tick-replay backtest for S14 wick FLIP + 2m open-hold."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from backtest_s14_tick import s14_cfg, simulate_s14_ticks
from strategy_wick import WickCandleStrategy

IST = ZoneInfo("Asia/Kolkata")


def test_s14_cfg_is_flip_and_open_hold() -> None:
    c = s14_cfg()
    assert c.reenter is True
    assert c.wick_anytime is True
    assert c.open_hold_minutes == 2.0
    assert c.nowick_body is False
    assert c.entry_strict is False
    assert c.exit_strict is False


def test_replay_matches_live_wick_flip() -> None:
    rows = [
        ("2026-08-17T10:00:00+05:30", 100.0),
        ("2026-08-17T10:10:00+05:30", 90.0),
        ("2026-08-17T10:11:00+05:30", 95.0),  # lower wick → BUY
        ("2026-08-17T10:20:00+05:30", 120.0),
        ("2026-08-17T10:21:00+05:30", 100.0),  # upper wick → SHORT
        ("2026-08-17T10:25:00+05:30", 99.0),
    ]
    r = simulate_s14_ticks(rows, tf="toy:s14", lots=1, fees=False, open_hold_minutes=0)
    assert r.n_trades == 2
    assert r.trades[0].side == "LONG"
    assert r.trades[0].entry_px == 95.0
    assert r.trades[0].exit_px == 100.0
    assert r.trades[1].side == "SHORT"
    assert r.trades[1].entry_px == 100.0
    assert r.trades[1].exit_px == 99.0


def test_replay_open_high_shorts() -> None:
    rows = [
        ("2026-08-17T10:30:00+05:30", 100.0),
        ("2026-08-17T10:31:00+05:30", 99.0),
        ("2026-08-17T10:32:00+05:30", 98.0),  # open=high 2m → SHORT
        ("2026-08-17T10:40:00+05:30", 97.0),
    ]
    live = WickCandleStrategy("S14_WICK30_STRICT", s14_cfg(open_hold_minutes=2), seed=False)
    actions = []
    for raw, px in rows:
        sig = live.on_tick(datetime.fromisoformat(raw).astimezone(IST), px)
        if sig is not None:
            actions.append(sig.action)
    assert "SHORT" in actions
    assert live.position == "short"

    r = simulate_s14_ticks(rows, tf="toy:oh", lots=1, fees=False, open_hold_minutes=2)
    assert r.n_trades >= 1
    assert r.trades[0].side == "SHORT"
    assert r.trades[0].entry_px == 98.0


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
            rows, tf=f"toy:{name}", lots=1, fees=False, open_hold_minutes=0, bar_minutes=minutes
        )
        assert r.n_bars >= 1


if __name__ == "__main__":
    test_s14_cfg_is_flip_and_open_hold()
    test_replay_matches_live_wick_flip()
    test_replay_open_high_shorts()
    test_equal_open_high_low_does_not_force()
    test_multi_tf_runs()
    print("ALL test_backtest_s14_tick OK")
