"""Order-flow + OI Strategy Factory — research only. Not paper. Not live."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from control_state import ALL_STRATEGY_NAMES, SLIM_PAPER_STRATEGIES
from live_readiness import PAPER_ONLY_BOOKS
from flow_lab import (
    DECIDERS,
    EXIT_ATR,
    EXIT_FLIP,
    LAB_NAME,
    ML_FEATURE_NAMES,
    RECIPES,
    FlowBar,
    FlowParams,
    build_flow_features,
    decide_absorb,
    decide_depth_ltp,
    decide_imb_confirm,
    decide_micro,
    decide_oi_div,
    decide_rvol_oi,
    decide_score,
    feat_vector,
    microprice,
    selected_recipes,
    signed_imb,
    simulate_flow,
    tape_flags,
    weighted_px,
)
from ohlcv_lab import after_charges_inr, score_result


def _b(
    t: str,
    o: float,
    h: float,
    l: float,
    c: float,
    v: float = 100.0,
    *,
    oi: float = 0.0,
    tbq: float = 0.0,
    tsq: float = 0.0,
    buy5: float = 0.0,
    sell5: float = 0.0,
    buy_px: tuple[float, ...] = (),
    buy_qty: tuple[float, ...] = (),
    sell_px: tuple[float, ...] = (),
    sell_qty: tuple[float, ...] = (),
) -> FlowBar:
    return FlowBar(
        t,
        o,
        h,
        l,
        c,
        v,
        0.0,
        tbq,
        tsq,
        oi,
        buy5,
        sell5,
        buy_px,
        buy_qty,
        sell_px,
        sell_qty,
    )


def _series(
    n: int,
    *,
    start: str = "2026-08-10 09:00:00",
    px: float = 100.0,
    rng: float = 2.0,
    vol: float = 100.0,
    oi: float = 10_000.0,
    tbq: float = 100.0,
    tsq: float = 100.0,
    buy5: float = 50.0,
    sell5: float = 50.0,
    step_hours: int = 1,
) -> list[FlowBar]:
    t0 = datetime.strptime(start, "%Y-%m-%d %H:%M:%S")
    bars: list[FlowBar] = []
    for i in range(n):
        t = (t0 + timedelta(hours=step_hours * i)).strftime("%Y-%m-%d %H:%M:%S")
        bars.append(
            _b(
                t,
                px,
                px + rng / 2.0,
                px - rng / 2.0,
                px,
                vol,
                oi=oi + i,
                tbq=tbq,
                tsq=tsq,
                buy5=buy5,
                sell5=sell5,
                buy_px=(px - 1.0,),
                buy_qty=(buy5,),
                sell_px=(px + 1.0,),
                sell_qty=(sell5,),
            )
        )
    return bars


P = FlowParams(lookback=3, atr_n=2)


def test_signed_imb_and_microprice() -> None:
    assert abs(signed_imb(125.0, 75.0) - 0.25) < 1e-12
    assert abs(signed_imb(75.0, 125.0) + 0.25) < 1e-12
    assert signed_imb(0.0, 0.0) == 0.0
    # Ask×BidQty + Bid×AskQty / (BidQty+AskQty)
    m = microprice(100.0, 2.0, 102.0, 1.0)
    assert abs(m - 304.0 / 3.0) < 1e-12
    w = weighted_px((10.0, 9.0), (2.0, 2.0))
    assert abs(w - 9.5) < 1e-12


def test_selected_recipes_filters_tape() -> None:
    all_r = selected_recipes(None, flags={"book": True, "l1": True, "oi": True})
    assert [r.name for r in all_r] == [r.name for r in RECIPES]
    oi_only = selected_recipes(None, flags={"book": False, "l1": False, "oi": True})
    names = [r.name for r in oi_only]
    assert "rvol_oi" in names
    assert "score" in names
    assert "candle" in names
    assert "depth_ltp" not in names
    assert "micro" not in names
    picked = selected_recipes(["rvol_oi", "score"], flags={"book": False, "l1": False, "oi": True})
    assert [r.name for r in picked] == ["score", "rvol_oi"]
    try:
        selected_recipes(["nope"])
    except ValueError as exc:
        assert "unknown" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_depth_ltp_needs_price_confirmation() -> None:
    bars = _series(3, px=100.0, vol=100.0, buy5=80.0, sell5=20.0, tbq=80.0, tsq=20.0)
    # bullish close, volume up, bid-heavy book
    bars.append(
        _b(
            "2026-08-10 12:00:00",
            100.0,
            104.0,
            100.0,
            103.5,
            200.0,
            oi=10_010,
            tbq=80.0,
            tsq=20.0,
            buy5=80.0,
            sell5=20.0,
            buy_px=(103.0,),
            buy_qty=(80.0,),
            sell_px=(104.0,),
            sell_qty=(20.0,),
        )
    )
    feats = build_flow_features(bars, P)
    side, why = decide_depth_ltp(bars, feats, 3, P, {})
    assert side == "long", why
    # same book, bearish candle → no long
    bars2 = _series(3, px=100.0, vol=100.0, buy5=80.0, sell5=20.0, tbq=80.0, tsq=20.0)
    bars2.append(
        _b(
            "2026-08-10 12:00:00",
            104.0,
            104.0,
            96.0,
            96.5,
            200.0,
            oi=10_010,
            tbq=80.0,
            tsq=20.0,
            buy5=80.0,
            sell5=20.0,
            buy_px=(96.0,),
            buy_qty=(80.0,),
            sell_px=(97.0,),
            sell_qty=(20.0,),
        )
    )
    feats2 = build_flow_features(bars2, P)
    side2, _ = decide_depth_ltp(bars2, feats2, 3, P, {})
    assert side2 != "long"


def test_imb_confirm_threshold() -> None:
    bars = _series(3, px=100.0, vol=100.0, tbq=50.0, tsq=50.0)
    bars.append(
        _b(
            "2026-08-10 12:00:00",
            100.0,
            103.0,
            100.0,
            102.5,
            200.0,
            oi=10_010,
            tbq=130.0,
            tsq=70.0,
            buy5=130.0,
            sell5=70.0,
        )
    )
    feats = build_flow_features(bars, P)
    f = feats[3]
    assert f is not None
    assert abs(f.imb - 0.30) < 1e-9
    side, why = decide_imb_confirm(bars, feats, 3, P, {})
    assert side == "long", why


def test_micro_ltp_below_microprice_is_long() -> None:
    bars = _series(3, px=100.0, vol=100.0)
    # LTP 100, bid 99.5×10, ask 100.2×2 → micro pulled toward ask (upward pressure)
    bars.append(
        _b(
            "2026-08-10 12:00:00",
            100.1,
            100.6,
            100.0,
            100.4,
            200.0,
            oi=10_010,
            tbq=100.0,
            tsq=1.0,
            buy5=100.0,
            sell5=1.0,
            buy_px=(100.0,),
            buy_qty=(100.0,),
            sell_px=(101.0,),
            sell_qty=(1.0,),
        )
    )
    feats = build_flow_features(bars, P)
    f = feats[3]
    assert f is not None
    assert f.micro > bars[3].close
    assert f.micro_gap > 0
    side, why = decide_micro(bars, feats, 3, P, {})
    assert side == "long", why


def test_absorb_arms_then_shorts() -> None:
    bars = _series(3, px=100.0, vol=100.0, tbq=100.0, tsq=100.0)
    # TBQ jumps, LTP flat
    bars.append(
        _b(
            "2026-08-10 12:00:00",
            100.0,
            100.2,
            99.8,
            100.05,
            120.0,
            oi=10_010,
            tbq=150.0,
            tsq=100.0,
            buy5=150.0,
            sell5=100.0,
        )
    )
    # then sell pressure + red candle
    bars.append(
        _b(
            "2026-08-10 13:00:00",
            100.0,
            100.1,
            98.0,
            98.2,
            150.0,
            oi=10_012,
            tbq=140.0,
            tsq=160.0,
            buy5=80.0,
            sell5=160.0,
        )
    )
    feats = build_flow_features(bars, P)
    st: dict = {}
    s0, w0 = decide_absorb(bars, feats, 3, P, st)
    assert s0 is None, w0
    assert st.get("arm") == "short_wait"
    s1, w1 = decide_absorb(bars, feats, 4, P, st)
    assert s1 == "short", w1


def test_oi_inventions() -> None:
    bars = _series(3, px=100.0, vol=100.0, oi=10_000.0)
    bars.append(
        _b(
            "2026-08-10 12:00:00",
            100.0,
            104.0,
            100.0,
            103.5,
            200.0,
            oi=10_400.0,
        )
    )
    feats = build_flow_features(bars, P)
    side, why = decide_rvol_oi(bars, feats, 3, P, {})
    assert side == "long", why
    # price up 3 bars, OI down → short divergence
    bars_d = _series(3, px=100.0, vol=100.0, oi=10_000.0)
    bars_d[0] = _b(bars_d[0].time, 90.0, 92.0, 89.0, 91.0, 100.0, oi=12_000.0)
    bars_d[1] = _b(bars_d[1].time, 91.0, 94.0, 90.0, 93.0, 100.0, oi=11_500.0)
    bars_d[2] = _b(bars_d[2].time, 93.0, 96.0, 92.0, 95.0, 100.0, oi=11_000.0)
    bars_d.append(_b("2026-08-10 12:00:00", 95.0, 99.0, 95.0, 98.0, 200.0, oi=10_200.0))
    feats_d = build_flow_features(bars_d, P)
    side_d, why_d = decide_oi_div(bars_d, feats_d, 3, P, {})
    assert side_d == "short", why_d


def test_score_and_ml_vector() -> None:
    bars = _series(3, px=100.0, vol=100.0, tbq=80.0, tsq=20.0, buy5=80.0, sell5=20.0)
    bars.append(
        _b(
            "2026-08-10 12:00:00",
            100.0,
            104.0,
            100.0,
            103.5,
            200.0,
            oi=10_400.0,
            tbq=80.0,
            tsq=20.0,
            buy5=80.0,
            sell5=20.0,
            buy_px=(103.0,),
            buy_qty=(80.0,),
            sell_px=(104.0,),
            sell_qty=(20.0,),
        )
    )
    feats = build_flow_features(bars, P)
    f = feats[3]
    assert f is not None
    vec = feat_vector(f)
    assert set(vec) == set(ML_FEATURE_NAMES)
    assert f.score > 0
    side, why = decide_score(bars, feats, 3, FlowParams(lookback=3, atr_n=2, score_th=10.0), {})
    assert side == "long", why


def test_simulate_flip_not_empty_on_oi_long() -> None:
    bars = _series(6, px=100.0, vol=100.0, oi=10_000.0)
    # last tradable bars: rising with OI and volume
    bars[3] = _b(bars[3].time, 100.0, 104.0, 100.0, 103.0, 220.0, oi=10_500.0)
    bars[4] = _b(bars[4].time, 103.0, 107.0, 103.0, 106.0, 240.0, oi=11_000.0)
    bars[5] = _b(bars[5].time, 106.0, 106.0, 104.0, 104.5, 80.0, oi=11_100.0)
    res = simulate_flow(
        bars, "rvol_oi", params=P, lots=100.0, fees=True, flatten_eod=True, tf="m"
    )
    m = score_result(res, name="rvol_oi", family="oi", exit_mode=EXIT_FLIP)
    assert abs(m.after_charges - after_charges_inr(res)) < 1e-9
    assert m.after_charges >= res.after_tax_pnl_inr - 1e-9
    # ATR path exists
    res_a = simulate_flow(
        bars, "rvol_oi", params=P, lots=100.0, fees=True, flatten_eod=True, exit_mode=EXIT_ATR, tf="a"
    )
    assert res_a.n_bars == len(bars)


def test_tape_flags() -> None:
    empty = [_b("2026-08-10 09:00:00", 1, 1, 1, 1, 1)]
    f = tape_flags(empty)
    assert f["book"] is False
    assert f["oi"] is False
    rich = [_b("2026-08-10 09:00:00", 1, 1, 1, 1, 1, oi=10, tbq=2, tsq=1, buy5=2, sell5=1,
               buy_px=(1.0,), buy_qty=(2.0,), sell_px=(1.1,), sell_qty=(1.0,))]
    r = tape_flags(rich)
    assert r["book"] and r["l1"] and r["oi"]


def test_every_recipe_has_decider() -> None:
    assert set(DECIDERS) == {r.name for r in RECIPES}


def test_not_wired_to_paper_or_live() -> None:
    names = [r.name for r in RECIPES]
    for n in names + [LAB_NAME]:
        assert n not in ALL_STRATEGY_NAMES
        assert n not in SLIM_PAPER_STRATEGIES
        assert n not in PAPER_ONLY_BOOKS
    root = Path(__file__).resolve().parent
    station = (root / "station.html").read_text(encoding="utf-8")
    paper = station.split("const PAPER_BOOKS")[1].split("];")[0]
    portfolio = (root / "portfolio.py").read_text(encoding="utf-8")
    runner = (root / "run_strategy.py").read_text(encoding="utf-8")
    assert "ENABLE_FLOW_LAB" not in portfolio
    assert "flow_lab" not in portfolio
    assert "flow_lab" not in runner
    assert "FLOW_LAB" not in paper
    assert "depth_ltp" not in ALL_STRATEGY_NAMES


if __name__ == "__main__":
    test_signed_imb_and_microprice()
    test_selected_recipes_filters_tape()
    test_depth_ltp_needs_price_confirmation()
    test_imb_confirm_threshold()
    test_micro_ltp_below_microprice_is_long()
    test_absorb_arms_then_shorts()
    test_oi_inventions()
    test_score_and_ml_vector()
    test_simulate_flip_not_empty_on_oi_long()
    test_tape_flags()
    test_every_recipe_has_decider()
    test_not_wired_to_paper_or_live()
    print("ALL test_flow_lab OK")
