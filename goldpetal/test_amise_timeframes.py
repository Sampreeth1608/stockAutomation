"""AMISE factory timeframes — session bars, token split, paper TF. Not live."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from amise_timeframes import (
    LAB_TF_ROWS,
    LAB_TIMEFRAMES,
    daily_bar_close,
    floor_session_bar,
    minutes_for_label,
    parse_tf,
)
from flow_lab import flow_bars_from_tick_rows
from strategy_amise import AmiseSlotStrategy, amise_slot_from_env
from strategy_genome import StrategyGenome

IST = ZoneInfo("Asia/Kolkata")


def _g(**kw) -> StrategyGenome:
    raw = dict(
        name="hh-hl",
        entry_long=("hh", "hl"),
        entry_short=("lh", "ll"),
        no_trade=(),
        direction="both",
        params={"lookback": 1, "atr_n": 1},
    )
    raw.update(kw)
    return StrategyGenome(**raw).normalized()


def _tick(t: str, px: float, vol: float, token: str) -> dict:
    return {
        "received_at": t,
        "ltp": px,
        "volume": vol,
        "bp": 0,
        "sp": 0,
        "raw_json": "",
        "token": token,
        "symbol": token,
    }


def test_all_user_rungs_parse() -> None:
    assert len(LAB_TIMEFRAMES) == 19
    assert [t.label for t in LAB_TIMEFRAMES] == [a for a, _ in LAB_TF_ROWS]
    samples = {
        "3m": 3,
        "5min": 5,
        "10m": 10,
        "15min": 15,
        "30m": 30,
        "45min": 45,
        "1h": 60,
        "1hour": 60,
        "1h:15min": 75,
        "1h15": 75,
        "1h:15": 75,
        "1h30": 90,
        "1h:45min": 105,
        "2h": 120,
        "2h:15min": 135,
        "2:30min": 150,
        "2h30": 150,
        "2:45min": 165,
        "3h": 180,
        "3:15 min": 195,
        "3:30min": 210,
        "3:45min": 225,
        "1d": 1440,
        "daily": 1440,
        "day": 1440,
    }
    for raw, mins in samples.items():
        spec = parse_tf(raw)
        assert spec.minutes == mins, (raw, spec.label, spec.minutes, mins)
    assert minutes_for_label("1h15") == 75
    assert parse_tf(75).label == "1h15"
    assert parse_tf("1d").daily is True
    assert parse_tf("1h").flatten_eod is True
    assert parse_tf("1d").flatten_eod is False
    assert parse_tf("1d").min_trades == 6
    assert parse_tf("3h").min_trades == 8
    assert parse_tf("15m").min_trades == 20


def test_session_floor_75m_from_open_not_midnight() -> None:
    ts = datetime(2026, 8, 18, 9, 10, tzinfo=IST)
    key75 = floor_session_bar(ts, 75)
    assert key75.hour == 9 and key75.minute == 0
    later = datetime(2026, 8, 18, 10, 20, tzinfo=IST)
    key75b = floor_session_bar(later, 75)
    assert key75b.hour == 10 and key75b.minute == 15
    key60 = floor_session_bar(ts, 60)
    assert key60.hour == 9 and key60.minute == 0
    key60b = floor_session_bar(datetime(2026, 8, 18, 10, 5, tzinfo=IST), 60)
    assert key60b.hour == 10 and key60b.minute == 0
    day = floor_session_bar(datetime(2026, 8, 18, 15, 0, tzinfo=IST), 1440)
    assert day.hour == 9 and day.minute == 0
    close = daily_bar_close(day)
    assert close.hour == 23 and close.minute == 30
    assert close.date() == day.date()


def test_token_split_does_not_share_volume() -> None:
    rows = [
        _tick("2026-08-18 10:00:01", 100.0, 1000.0, "tokA"),
        _tick("2026-08-18 10:00:30", 101.0, 1100.0, "tokA"),
        _tick("2026-08-18 10:01:00", 200.0, 50.0, "tokB"),
        _tick("2026-08-18 10:01:20", 201.0, 80.0, "tokB"),
    ]
    merged = flow_bars_from_tick_rows(
        rows, 5, session_align=True, session_ticks=True, split_token=False
    )
    split = flow_bars_from_tick_rows(
        rows, 5, session_align=True, session_ticks=True, split_token=True
    )
    assert len(split) >= 2
    assert split[0].close == 101.0
    assert split[1].open == 200.0
    assert split[1].volume == 80.0
    # Without a split, cumulative volume of B minus A is not a real bar volume.
    if merged:
        assert merged[0].open == 100.0


def test_amise_slot_uses_genome_tf_not_env_hour(monkeypatch=None) -> None:
    g = _g(timeframe="15m")
    s = AmiseSlotStrategy("S21_AMISE", g, seed=False)
    assert s.bar_minutes == 15
    assert s.holds_overnight is False
    s.position = "long"
    why = s._session_flatten_why(datetime(2026, 8, 18, 23, 30, tzinfo=IST))
    assert why is None
    daily = AmiseSlotStrategy("S21_AMISE", _g(timeframe="1d"), seed=False)
    assert daily.bar_minutes == 1440
    assert daily.holds_overnight is True
    daily.position = "long"
    assert daily._session_flatten_why(datetime(2026, 8, 18, 23, 30, tzinfo=IST)) is None
    key = datetime(2026, 8, 18, 9, 10, tzinfo=IST)
    assert s._floor_bar(key).minute == 0
    assert s._floor_bar(key).hour == 9


def test_you_mimic_hours_flatten(monkeypatch=None) -> None:
    g = _g(
        timeframe="1m",
        params={
            "lookback": 1,
            "atr_n": 1,
            "you_open_min": 600,
            "you_close_min": 630,
            "you_hours_mask": float(1 << 10),
        },
    )
    s = AmiseSlotStrategy("S21_AMISE", g, seed=False)
    assert s._in_you_hours(datetime(2026, 8, 18, 10, 15, tzinfo=IST)) is True
    assert s._in_you_hours(datetime(2026, 8, 18, 12, 0, tzinfo=IST)) is False
    s.position = "long"
    why = s._session_flatten_why(datetime(2026, 8, 18, 12, 0, tzinfo=IST))
    assert why is not None and "you hours" in why


def test_amise_slot_from_env_reads_genome_tf(tmp_path) -> None:
    from amise_slots import assign_slot

    g = _g(timeframe="30m", name="half-hour")
    assign_slot(g, proposal_id="p", folder=tmp_path)
    import os

    os.environ["S21_BAR_MINUTES"] = "60"
    os.environ["S18_BAR_MINUTES"] = "60"
    slot = amise_slot_from_env("S21_AMISE", slots_dir=tmp_path)
    try:
        assert slot.bar_minutes == 30
        assert slot._tf.label == "30m"
    finally:
        slot  # keep DRY_RUN untouched
        os.environ.pop("S21_BAR_MINUTES", None)


if __name__ == "__main__":
    import tempfile
    from pathlib import Path as P

    test_all_user_rungs_parse()
    test_session_floor_75m_from_open_not_midnight()
    test_token_split_does_not_share_volume()
    test_amise_slot_uses_genome_tf_not_env_hour()
    test_you_mimic_hours_flatten()
    td = P(tempfile.mkdtemp())
    test_amise_slot_from_env_reads_genome_tf(td)
    print("ALL test_amise_timeframes OK")
