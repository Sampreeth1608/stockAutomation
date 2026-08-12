"""Unit tests for 3-bar volume stack / exhaustion labels."""

from __future__ import annotations

from analyze_vol_trend_exhaustion import (
    analyze,
    outcome_label,
    vol_stack_label,
)


def test_vol_stack_dec_dec_and_inc_inc():
    assert vol_stack_label(50, 80, 120, 0.05) == "DEC_DEC"
    assert vol_stack_label(120, 80, 50, 0.05) == "INC_INC"
    assert vol_stack_label(100, 100, 100, 0.05) == "FLAT_FLAT"


def test_outcome_flip_and_huge():
    assert outcome_label(
        trend="UP", next_d=-20, next_range=25, huge_move=True, halt_pts=5
    ) == "FLIP_HUGE"
    assert outcome_label(
        trend="UP", next_d=2, next_range=3, huge_move=False, halt_pts=5
    ) == "HALT"
    assert outcome_label(
        trend="DOWN", next_d=-15, next_range=20, huge_move=True, halt_pts=5
    ) == "CONTINUE_HUGE"


def test_analyze_dry_up_flags_huge_next():
    # 6 bars: dry-up on UP trend then huge next move
    bars = []
    # pp, p, c volumes decreasing; prices rising; next jumps
    seq = [
        # t0
        dict(time="t0", open=100, high=101, low=99, close=100, bar_volume=200,
             tbq_close=1000, tsq_close=900, net=100, imb_pct=10, ltq_sum=10),
        # t1
        dict(time="t1", open=100, high=102, low=100, close=101.5, bar_volume=150,
             tbq_close=1100, tsq_close=850, net=250, imb_pct=20, ltq_sum=12),
        # t2 dry-up DEC_DEC, UP
        dict(time="t2", open=101.5, high=104, low=101, close=103.5, bar_volume=80,
             tbq_close=1300, tsq_close=800, net=500, imb_pct=30, ltq_sum=8),
        # t3 HUGE move
        dict(time="t3", open=103.5, high=130, low=103, close=128, bar_volume=300,
             tbq_close=1400, tsq_close=700, net=700, imb_pct=40, ltq_sum=40),
        # padding
        dict(time="t4", open=128, high=129, low=120, close=121, bar_volume=100,
             tbq_close=1200, tsq_close=900, net=300, imb_pct=20, ltq_sum=15),
        dict(time="t5", open=121, high=122, low=118, close=119, bar_volume=90,
             tbq_close=1100, tsq_close=950, net=150, imb_pct=12, ltq_sum=10),
    ]
    bars = seq
    r = analyze(
        bars,
        tf="test",
        equal_pct=0.05,
        equal_px_pct=0.0005,
        huge_pct=50,
        halt_pts=5,
        trend_lookback=2,
        min_n=1,
    )
    assert r["n_events"] >= 1
    # find DEC_DEC events
    dec = [e for e in r["events"] if e["vol_stack"] == "DEC_DEC"]
    assert dec, r["events"]
    assert any(e["huge"] for e in dec)


if __name__ == "__main__":
    test_vol_stack_dec_dec_and_inc_inc()
    test_outcome_flip_and_huge()
    test_analyze_dry_up_flags_huge_next()
    print("ok")
