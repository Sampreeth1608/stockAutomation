"""Tests for expected-fluctuation → TP/SL mapping."""

from __future__ import annotations

from range_stops import expected_range, median, tp_sl_from_range
from strategy_state_s9 import StateS9Config, StateS9Strategy


def test_median_and_expected_range():
    assert median([]) != median([])  # nan
    assert median([3.0]) == 3.0
    assert median([1.0, 3.0, 2.0]) == 2.0
    assert expected_range([10, 20, 30, 40, 50], window=3) == 40.0


def test_tp_sl_from_range_scales_and_rr():
    # 30m typical ~30 pts → TP≈25.5 SL≈16.5
    tp, sl = tp_sl_from_range(30.0, tp_mult=0.85, sl_mult=0.55, min_rr=1.2)
    assert 20 <= tp <= 30
    assert 12 <= sl <= 20
    assert tp >= 1.2 * sl - 0.05

    # empty / nan → nan pair
    tp2, sl2 = tp_sl_from_range(float("nan"))
    assert tp2 != tp2 and sl2 != sl2


def test_s9_range_stops_set_at_entry():
    cfg = StateS9Config(
        bar_minutes=30,
        tp_points=26,
        sl_points=16,
        require_net_sign=True,
        allow_short=False,
        min_imb_pct=0,
        use_range_stops=True,
        range_window=5,
        tp_range_mult=0.85,
        sl_range_mult=0.55,
        range_fee_be=0.0,
    )
    s = StateS9Strategy(cfg)
    # Neutral warmup bars (no B+S-P+) so we stay flat while ranges accumulate
    for i in range(6):
        base = 10000 + i
        s.on_bar_row(
            {
                "time": f"2026-08-07 {9 + i // 2}:{(i % 2) * 30:02d}:00",
                "open": base,
                "high": base + 20,
                "low": base - 20,
                "close": base,
                "tbq_open": 10000,
                "tsq_open": 10000,
                "tbq_close": 10000 + i,  # tiny drift → not a confirm state
                "tsq_close": 10000 + i,
                "n_ticks": 10,
                "bar_volume": 1000,
            }
        )
    assert s.position == "flat"
    # Force known expected range = 40 → TP 34 / SL 22
    s._ranges = [40.0] * 8
    sig = s.on_bar_row(
        {
            "time": "2026-08-07 12:00:00",
            "open": 10020,
            "high": 10060,
            "low": 10020,
            "close": 10050,
            "tbq_open": 10000,
            "tsq_open": 10000,
            "tbq_close": 13000,
            "tsq_close": 8000,
            "n_ticks": 10,
            "bar_volume": 2000,
        }
    )
    assert sig is not None and sig.action == "BUY", sig
    assert s.active_tp == 34.0
    assert s.active_sl == 22.0
    assert "expR=" in (sig.reason or "")

    # Hit adaptive TP (not fixed 26)
    sig2 = s.on_bar_row(
        {
            "time": "2026-08-07 12:30:00",
            "open": 10050,
            "high": 10090,
            "low": 10050,
            "close": 10084,  # +34 from 10050
            "tbq_open": 13000,
            "tsq_open": 8000,
            "tbq_close": 14000,
            "tsq_close": 7000,
            "n_ticks": 10,
            "bar_volume": 2100,
        }
    )
    assert sig2 is not None and sig2.action == "CLOSE"
    assert "tp +" in (sig2.reason or "")
    assert s.position == "flat"


def main() -> None:
    test_median_and_expected_range()
    test_tp_sl_from_range_scales_and_rr()
    test_s9_range_stops_set_at_entry()
    print("test_range_stops: OK")


if __name__ == "__main__":
    main()
