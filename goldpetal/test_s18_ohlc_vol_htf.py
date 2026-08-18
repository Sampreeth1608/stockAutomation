"""S18: one practical AND of OHLC + volume-up + yesterday. Not paper."""

from __future__ import annotations

from pathlib import Path

from control_state import ALL_STRATEGY_NAMES, SLIM_PAPER_STRATEGIES
from s18_ohlc_vol_htf import (
    S18_NAME,
    VolBar,
    attach_completed_day,
    s18_bar_decision,
    simulate_s18,
)


def _b(
    t: str, o: float, h: float, l: float, c: float, vol: float = 0.0
) -> VolBar:
    return VolBar(t, o, h, l, c, vol)


PREV = _b("2026-08-17 10:00:00", 100.0, 105.0, 99.0, 104.0, 1000.0)
DAY = _b("2026-08-16 00:00:00", 90.0, 130.0, 80.0, 95.0, 8000.0)


def test_long_needs_every_leg() -> None:
    cur = _b("2026-08-17 11:00:00", 104.0, 120.0, 103.0, 110.0, 2000.0)
    side, why = s18_bar_decision(PREV, cur, DAY)
    assert side == "long"
    assert "vol" in why
    weak_vol = _b("2026-08-17 11:00:00", 104.0, 120.0, 103.0, 110.0, 500.0)
    assert s18_bar_decision(PREV, weak_vol, DAY)[0] is None
    no_hh = _b("2026-08-17 11:00:00", 104.0, 104.5, 103.0, 110.0, 2000.0)
    assert s18_bar_decision(PREV, no_hh, DAY)[0] is None
    below_day = _b("2026-08-17 11:00:00", 104.0, 120.0, 103.0, 110.0, 2000.0)
    low_day = _b("2026-08-16 00:00:00", 90.0, 200.0, 80.0, 150.0, 8000.0)
    assert s18_bar_decision(PREV, below_day, low_day)[0] is None
    assert s18_bar_decision(PREV, cur, None)[0] is None


def test_short_needs_every_leg() -> None:
    cur = _b("2026-08-17 11:00:00", 104.0, 104.5, 90.0, 92.0, 2500.0)
    side, why = s18_bar_decision(PREV, cur, DAY)
    assert side == "short"
    assert "C<day" in why
    red_but_not_ll = _b("2026-08-17 11:00:00", 104.0, 104.5, 100.0, 92.0, 2500.0)
    assert s18_bar_decision(PREV, red_but_not_ll, DAY)[0] is None


def test_completed_day_not_today() -> None:
    hours = [
        _b("2026-08-17 09:00:00", 100, 101, 99, 100.5, 10),
        _b("2026-08-17 10:00:00", 100.5, 102, 100, 101, 20),
    ]
    days = [_b("2026-08-16 00:00:00", 90, 110, 80, 95, 8000)]
    attached = attach_completed_day(hours, days)
    assert attached[0] is not None
    assert attached[0].close == 95.0


def test_same_session_prev_skips_first_hour() -> None:
    hours = [
        _b("2026-08-17 09:00:00", 100, 101, 99, 100.5, 0),
        _b("2026-08-17 10:00:00", 100.5, 120, 100, 110, 2000),
        _b("2026-08-17 11:00:00", 110, 130, 109, 125, 3000),
    ]
    days = [_b("2026-08-16 00:00:00", 90, 140, 80, 95, 8000)]
    # 09:00 has no same-session prev in the walk (i starts at 1, but 09 vs 10:
    # 09 volume 0, 10 volume 2000, green HH C>prev C>day → long at 10:00)
    r = simulate_s18(hours, days, lots=1.0, fees=False, session_filter=False)
    assert r.n_trades >= 1


def test_not_wired_to_paper_or_station() -> None:
    assert S18_NAME not in ALL_STRATEGY_NAMES
    assert S18_NAME not in SLIM_PAPER_STRATEGIES
    root = Path(__file__).resolve().parent
    station = (root / "station.html").read_text(encoding="utf-8")
    runner = (root / "run_strategy.py").read_text(encoding="utf-8")
    assert S18_NAME not in station
    assert "s18_ohlc_vol_htf" not in runner
    assert "ENABLE_S18" not in runner


if __name__ == "__main__":
    test_long_needs_every_leg()
    test_short_needs_every_leg()
    test_completed_day_not_today()
    test_same_session_prev_skips_first_hour()
    test_not_wired_to_paper_or_station()
    print("ALL test_s18_ohlc_vol_htf OK")
