"""3-candle body shrink fade. Research. Not live. No ENABLE."""

from __future__ import annotations

from pathlib import Path

from body_shrink_fade import (
    LAB_NAME,
    setup_side,
    simulate_body_shrink_fade,
    skip_chase,
    third_fill_px,
    wrap_skip,
)
from control_state import ALL_STRATEGY_NAMES, SLIM_PAPER_STRATEGIES
from s18_ohlc_vol_htf import VolBar


def _b(t: str, o: float, h: float, l: float, c: float) -> VolBar:
    return VolBar(t, o, h, l, c)


# Candle 1 green body 10 (100→110). Candle 2 green body 4 sitting lower (104→108).
PREV_G = _b("2026-08-17 10:00:00", 100.0, 112.0, 99.0, 110.0)
CUR_G = _b("2026-08-17 11:00:00", 104.0, 109.0, 103.0, 108.0)
NXT_G = _b("2026-08-17 12:00:00", 108.0, 116.0, 100.0, 102.0)

PREV_R = _b("2026-08-17 10:00:00", 110.0, 111.0, 98.0, 100.0)
CUR_R = _b("2026-08-17 11:00:00", 106.0, 107.0, 101.0, 102.0)
NXT_R = _b("2026-08-17 12:00:00", 102.0, 108.0, 94.0, 107.0)


def test_two_greens_lower_smaller_body_is_short() -> None:
    assert setup_side(PREV_G, CUR_G) == "short"
    assert skip_chase(PREV_G, CUR_G, "long") is True
    assert skip_chase(PREV_G, CUR_G, "short") is False


def test_two_reds_higher_smaller_body_is_long() -> None:
    assert setup_side(PREV_R, CUR_R) == "long"
    assert skip_chase(PREV_R, CUR_R, "short") is True
    assert skip_chase(PREV_R, CUR_R, "long") is False


def test_bigger_second_body_is_not_exhaustion() -> None:
    strong = _b("2026-08-17 11:00:00", 110.0, 130.0, 109.0, 128.0)
    assert setup_side(PREV_G, strong) is None


def test_near_fill_needs_rally_from_open() -> None:
    assert third_fill_px(NXT_G, "short", "open") == 108.0
    near = third_fill_px(NXT_G, "short", "near")
    assert near is not None and near > 108.0
    assert third_fill_px(NXT_G, "short", "extreme") == 116.0
    no_rally = _b("2026-08-17 12:00:00", 108.0, 108.0, 100.0, 101.0)
    assert third_fill_px(no_rally, "short", "near") is None


def test_third_bar_short_beats_fees_on_toy() -> None:
    bars = [PREV_G, CUR_G, NXT_G]
    r = simulate_body_shrink_fade(bars, lots=100, fees=True, session_filter=False, fill="open")
    assert r.n_trades == 1
    assert r.trades[0].side == "SHORT"
    assert r.trades[0].entry_px == 108.0
    assert r.trades[0].exit_px == 102.0
    from body_shrink_fade import after_charges_inr

    assert after_charges_inr(r) > 0


def test_third_bar_long_reverse() -> None:
    bars = [PREV_R, CUR_R, NXT_R]
    r = simulate_body_shrink_fade(bars, lots=1, fees=False, session_filter=False, fill="open")
    assert r.n_trades == 1
    assert r.trades[0].side == "LONG"
    assert r.trades[0].entry_px == 102.0
    assert r.trades[0].exit_px == 107.0


def test_wrap_skip_blocks_chase_long() -> None:
    def always_long(_p, _c):
        return "long", "book long"

    skipped, why = wrap_skip(always_long)(PREV_G, CUR_G)
    assert skipped is None
    assert "body_shrink skip" in why


def test_not_a_paper_book() -> None:
    assert LAB_NAME not in ALL_STRATEGY_NAMES
    assert LAB_NAME not in SLIM_PAPER_STRATEGIES
    root = Path(__file__).resolve().parent
    s16 = (root / "strategy_s16.py").read_text(encoding="utf-8")
    assert "body_shrink" not in s16
    runner = (root / "run_strategy.py").read_text(encoding="utf-8")
    assert "ENABLE_BODY_SHRINK" not in runner


if __name__ == "__main__":
    test_two_greens_lower_smaller_body_is_short()
    test_two_reds_higher_smaller_body_is_long()
    test_bigger_second_body_is_not_exhaustion()
    test_near_fill_needs_rally_from_open()
    test_third_bar_short_beats_fees_on_toy()
    test_third_bar_long_reverse()
    test_wrap_skip_blocks_chase_long()
    test_not_a_paper_book()
    print("body shrink fade tests ok")
