"""Smoke test for daily HH/LL notes + overnight/swing sims (synthetic OHLC)."""

from __future__ import annotations

from backtest_s4_hhhl_daily import (
    build_day_notes,
    simulate_overnight,
    simulate_swing,
    summarize,
)


def _days() -> list[dict]:
    # Hand-built days so HH/LL fires clearly.
    rows = [
        ("2026-08-01", 100, 110, 95, 108),   # warmup green
        ("2026-08-02", 108, 120, 105, 118),  # HH + green → long
        ("2026-08-03", 118, 125, 112, 124),  # HH + green → hold / another overnight long
        ("2026-08-04", 124, 122, 100, 102),  # not HH, red → long exit for swing
        ("2026-08-05", 102, 101, 90, 92),    # LL + red → short
        ("2026-08-06", 92, 98, 91, 97),      # HL + green → short exit
        ("2026-08-07", 97, 105, 96, 104),    # HH + green → long
    ]
    out = []
    for d, o, h, l, c in rows:
        out.append(
            {
                "time": f"{d} 00:00:00",
                "date": d,
                "open": float(o),
                "high": float(h),
                "low": float(l),
                "close": float(c),
                "range_pts": float(h - l),
                "n_ticks": 100,
            }
        )
    return out


def test_day_notes_hh_ll() -> None:
    notes = build_day_notes(_days(), min_range=5)
    assert notes[0].signal == "warmup"
    assert notes[1].long_entry and notes[1].hh and notes[1].green
    assert notes[1].signal == "LONG_ENTRY"
    assert notes[4].short_entry and notes[4].ll
    assert notes[4].signal == "SHORT_ENTRY"


def test_swing_and_overnight_produce_trades() -> None:
    days = _days()
    swing = simulate_swing(days, lots=1, fees=False, min_range=5, no_flip=True)
    over = simulate_overnight(days, lots=1, fees=False, min_range=5)
    assert swing, "expected swing trades"
    assert over, "expected overnight trades"
    s_sum = summarize(swing)
    o_sum = summarize(over)
    assert s_sum["n_trades"] >= 1
    assert o_sum["n_trades"] >= 1


if __name__ == "__main__":
    test_day_notes_hh_ll()
    print("ok notes")
    test_swing_and_overnight_produce_trades()
    print("ok sims")
    print("ALL test_s4_hhhl_daily OK")
