"""Quality gates: fakeout HH/LL, weak S14 wicks, EV / Kelly / Wilson."""

from __future__ import annotations

from quality_filters import (
    expected_value,
    hhll_break_ok,
    kelly_fraction,
    s14_wick_quality,
    s8_quality_ok,
    wilson_lower,
)


def test_hh_needs_close_beyond_prior_high() -> None:
    ok, why = hhll_break_ok(
        side="long",
        o=104,
        h=112,
        l=103,
        c=106,
        prev_h=105,
        prev_l=99,
        min_close_beyond=3,
    )
    assert ok is False
    assert "fakeout_close" in why


def test_hh_rejects_long_upper_wick() -> None:
    ok, why = hhll_break_ok(
        side="long",
        o=104,
        h=120,
        l=103,
        c=109,
        prev_h=105,
        prev_l=99,
        min_close_beyond=3,
        max_break_wick_frac=0.6,
    )
    assert ok is False
    assert "fakeout_wick" in why


def test_hh_accepts_close_through() -> None:
    ok, why = hhll_break_ok(
        side="long",
        o=104,
        h=112,
        l=103,
        c=110,
        prev_h=105,
        prev_l=99,
        min_close_beyond=3,
    )
    assert ok is True
    assert "hh_beyond" in why


def test_s14_open_high_not_filtered() -> None:
    ok, why = s14_wick_quality(100, 100, 90, 95, "open=high SHORT", min_gap=3, min_frac=0.12)
    assert ok is True
    assert why == "open_hold"


def test_s14_weak_wick_skipped() -> None:
    ok, why = s14_wick_quality(100, 103, 99, 101, "wick (bar closed)", min_gap=3, min_frac=0.12)
    assert ok is False
    assert "weak_wick" in why


def test_s8_needs_strong_imbalance() -> None:
    ok, why = s8_quality_ok(10.0, min_imb_pct=14.0, rising=True)
    assert ok is False
    ok2, _ = s8_quality_ok(20.0, min_imb_pct=14.0, rising=True)
    assert ok2 is True
    ok3, why3 = s8_quality_ok(20.0, min_imb_pct=14.0, rising=False)
    assert ok3 is False
    assert "not_rising" in why3


def test_wilson_and_kelly() -> None:
    assert 0.35 < wilson_lower(6, 10, z=1.0) < 0.6
    assert wilson_lower(0, 0) == 0.5
    assert expected_value(0.6, 100.0, -80.0) > 0
    assert kelly_fraction(0.6, 100.0, -80.0) > 0
    assert kelly_fraction(0.4, 50.0, -80.0) < 0


if __name__ == "__main__":
    test_hh_needs_close_beyond_prior_high()
    test_hh_rejects_long_upper_wick()
    test_hh_accepts_close_through()
    test_s14_open_high_not_filtered()
    test_s14_weak_wick_skipped()
    test_s8_needs_strong_imbalance()
    test_wilson_and_kelly()
    print("ALL test_quality_filters OK")
