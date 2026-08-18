"""OHLCV Strategy Laboratory — research only. Not paper. Not live."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from control_state import ALL_STRATEGY_NAMES, SLIM_PAPER_STRATEGIES
from live_readiness import PAPER_ONLY_BOOKS
from ohlcv_lab import (
    EXIT_ATR,
    EXIT_FLIP,
    LAB_NAME,
    LabParams,
    STRATEGIES,
    after_charges_inr,
    build_features,
    decide_breakout,
    decide_climax,
    decide_consol,
    decide_pullback,
    decide_rvol_mom,
    decide_three,
    decide_vwap,
    max_dd_after_charges,
    profit_factor,
    rvol_class,
    score_result,
    selected_strategies,
    session_bars,
    simulate_lab,
    trade_after_charges,
)
from s18_ohlc_vol_htf import VolBar


def _b(t: str, o: float, h: float, l: float, c: float, v: float = 100.0) -> VolBar:
    return VolBar(t, o, h, l, c, v)


def _series(
    n: int,
    *,
    start: str = "2026-08-10 09:00:00",
    px: float = 100.0,
    rng: float = 2.0,
    vol: float = 100.0,
    step_hours: int = 1,
) -> list[VolBar]:
    t0 = datetime.strptime(start, "%Y-%m-%d %H:%M:%S")
    bars: list[VolBar] = []
    for i in range(n):
        t = (t0 + timedelta(hours=step_hours * i)).strftime("%Y-%m-%d %H:%M:%S")
        o = px
        h = px + rng / 2.0
        l = px - rng / 2.0
        c = px
        bars.append(_b(t, o, h, l, c, vol))
    return bars


P = LabParams(lookback=3, atr_n=2, box=3)


def test_selected_strategies_order_and_filter() -> None:
    assert selected_strategies(None) == STRATEGIES
    picked = selected_strategies(["vwap", "breakout"])
    assert [n for n, _, _ in picked] == ["breakout", "vwap"]
    try:
        selected_strategies(["nope"])
    except ValueError as exc:
        assert "unknown" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_rvol_class() -> None:
    assert rvol_class(0.4) == "low"
    assert rvol_class(1.0) == "normal"
    assert rvol_class(1.5) == "high"
    assert rvol_class(2.5) == "extreme"


def test_breakout_long_and_short() -> None:
    bars = _series(3, px=100.0, rng=2.0, vol=100.0)
    # prev 3-bar high is 101. Close 108, upper 25%, 2× volume.
    bars.append(_b("2026-08-10 12:00:00", 100.0, 110.0, 100.0, 108.0, 200.0))
    feats = build_features(bars, P)
    st: dict = {}
    side, why = decide_breakout(bars, feats, 3, P, st)
    assert side == "long", why
    bars_s = _series(3, px=100.0, rng=2.0, vol=100.0)
    bars_s.append(_b("2026-08-10 12:00:00", 100.0, 100.0, 90.0, 92.0, 200.0))
    feats_s = build_features(bars_s, P)
    side_s, why_s = decide_breakout(bars_s, feats_s, 3, P, st)
    assert side_s == "short", why_s


def test_breakout_skips_low_volume() -> None:
    bars = _series(3, px=100.0, rng=2.0, vol=100.0)
    bars.append(_b("2026-08-10 12:00:00", 100.0, 110.0, 100.0, 108.0, 100.0))
    feats = build_features(bars, P)
    side, _ = decide_breakout(bars, feats, 3, P, {})
    assert side is None


def test_climax_waits_then_buys_recovery() -> None:
    bars = _series(3, px=100.0, rng=2.0, vol=100.0)
    # large bearish + 2× vol + close near low → do not short
    bars.append(_b("2026-08-10 12:00:00", 110.0, 110.0, 90.0, 91.0, 250.0))
    bars.append(_b("2026-08-10 13:00:00", 92.0, 108.0, 92.0, 106.0, 80.0))
    feats = build_features(bars, P)
    st: dict = {}
    s0, w0 = decide_climax(bars, feats, 3, P, st)
    assert s0 is None, w0
    assert st.get("pending") is not None
    s1, w1 = decide_climax(bars, feats, 4, P, st)
    assert s1 == "long", w1
    # do not chase the climax bar itself
    st2: dict = {}
    decide_climax(bars, feats, 3, P, st2)
    assert st2["pending"][0] == "long"


def test_pullback_long_after_weak_reds() -> None:
    bars = _series(5, px=100.0, rng=2.0, vol=100.0)
    # impulse green, large, high volume, close > SMA
    bars.append(_b("2026-08-10 14:00:00", 100.0, 120.0, 99.0, 119.0, 200.0))
    bars.append(_b("2026-08-10 15:00:00", 118.0, 118.0, 114.0, 115.0, 60.0))
    bars.append(_b("2026-08-10 16:00:00", 115.0, 116.0, 112.0, 113.0, 50.0))
    bars.append(_b("2026-08-10 17:00:00", 113.0, 118.0, 112.0, 117.0, 70.0))
    p = LabParams(lookback=5, atr_n=2, box=3)
    feats = build_features(bars, p)
    st: dict = {}
    sides = []
    for i in range(len(bars)):
        side, why = decide_pullback(bars, feats, i, p, st)
        sides.append((i, side, why, st.get("phase"), st.get("pb_count")))
    long_hits = [x for x in sides if x[1] == "long"]
    assert long_hits, sides


def test_consol_breakout() -> None:
    bars = _series(6, px=100.0, rng=8.0, vol=200.0)
    bars.append(_b("2026-08-10 15:00:00", 100.0, 100.6, 99.5, 100.1, 40.0))
    bars.append(_b("2026-08-10 16:00:00", 100.1, 100.5, 99.6, 100.0, 35.0))
    bars.append(_b("2026-08-10 17:00:00", 100.0, 100.4, 99.7, 100.0, 30.0))
    bars.append(_b("2026-08-10 18:00:00", 100.0, 108.0, 99.8, 107.0, 400.0))
    p = LabParams(lookback=6, atr_n=2, box=3)
    feats = build_features(bars, p)
    i = len(bars) - 1
    side, why = decide_consol(bars, feats, i, p, {})
    assert side == "long", (why, feats[i])


def test_vwap_pullback_not_first_bar() -> None:
    bars = [
        _b("2026-08-17 09:00:00", 100.0, 120.0, 100.0, 120.0, 100.0),
        _b("2026-08-17 10:00:00", 112.0, 116.0, 110.0, 115.0, 150.0),
    ]
    p = LabParams(lookback=1, atr_n=1)
    feats = build_features(bars, p)
    assert decide_vwap(bars, feats, 0, p, {})[0] is None
    assert feats[1] is not None
    side, why = decide_vwap(bars, feats, 1, p, {})
    assert side == "long", (why, feats[1].vwap, bars[1].low, bars[1].close)


def test_rvol_momentum_long() -> None:
    bars = _series(4, px=100.0, rng=2.0, vol=100.0)
    bars.append(_b("2026-08-10 13:00:00", 100.0, 112.0, 100.0, 111.0, 200.0))
    p = LabParams(lookback=3, atr_n=2)
    feats = build_features(bars, p)
    side, why = decide_rvol_mom(bars, feats, 4, p, {})
    assert side == "long", (why, feats[4])


def test_three_candle_long() -> None:
    bars = _series(4, px=100.0, rng=2.0, vol=100.0)
    bars.append(_b("2026-08-10 13:00:00", 100.0, 110.0, 99.0, 109.0, 150.0))  # c1 strong
    bars.append(_b("2026-08-10 14:00:00", 108.0, 109.0, 105.0, 106.0, 80.0))  # c2 pullback
    bars.append(_b("2026-08-10 15:00:00", 106.0, 114.0, 105.0, 113.0, 160.0))  # c3 break
    p = LabParams(lookback=3, atr_n=2)
    feats = build_features(bars, p)
    i = len(bars) - 1
    side, why = decide_three(bars, feats, i, p, {})
    assert side == "long", (why, feats[i - 2], feats[i])


def test_flip_eod_and_atr_exits() -> None:
    bars = _series(3, px=100.0, rng=2.0, vol=100.0)
    bars.append(_b("2026-08-10 12:00:00", 100.0, 110.0, 100.0, 108.0, 200.0))  # long
    bars.append(_b("2026-08-10 13:00:00", 108.0, 109.0, 107.0, 108.5, 80.0))
    bars.append(_b("2026-08-11 09:00:00", 108.5, 109.0, 107.0, 108.0, 80.0))
    r = simulate_lab(
        bars, "breakout", params=P, lots=1.0, fees=False, flatten_eod=True, tf="t"
    )
    assert r.n_long == 1
    assert r.trades[0].entry_px == 108.0
    # flattened at last bar of 2026-08-10 (13:00), not on next day
    assert r.trades[0].exit_time == "2026-08-10 13:00:00"

    # ATR: freeze ATR at entry, exit at close when 2×ATR target prints on close
    atr_bars = _series(3, px=100.0, rng=2.0, vol=100.0)
    atr_bars.append(_b("2026-08-10 12:00:00", 100.0, 110.0, 100.0, 108.0, 200.0))
    # huge close well beyond 2 ATR (ATR on these toys is a few pts)
    atr_bars.append(_b("2026-08-10 13:00:00", 108.0, 140.0, 107.0, 139.0, 80.0))
    atr_bars.append(_b("2026-08-10 14:00:00", 139.0, 140.0, 138.0, 139.0, 80.0))
    r2 = simulate_lab(
        atr_bars,
        "breakout",
        exit_mode=EXIT_ATR,
        params=P,
        lots=1.0,
        fees=False,
        flatten_eod=False,
        tf="atr",
    )
    assert r2.n_long == 1
    assert r2.trades[0].exit_px == 139.0
    # honest fill is the close, not the 140 high
    assert r2.trades[0].exit_px != 140.0


def test_rank_metrics_use_after_charges_not_winrate() -> None:
    bars = _series(3, px=100.0, rng=2.0, vol=100.0)
    bars.append(_b("2026-08-10 12:00:00", 100.0, 110.0, 100.0, 108.0, 200.0))
    bars.append(_b("2026-08-10 13:00:00", 108.0, 109.0, 90.0, 91.0, 80.0))
    r = simulate_lab(
        bars, "breakout", params=P, lots=100.0, fees=True, flatten_eod=False, tf="m"
    )
    m = score_result(r, name="breakout", family="breakout", exit_mode=EXIT_FLIP)
    assert abs(m.after_charges - after_charges_inr(r)) < 1e-9
    assert abs(m.after_charges - (r.gross_pnl_inr - r.fees_inr)) < 1e-9
    assert m.after_charges >= r.after_tax_pnl_inr - 1e-9
    assert abs(max_dd_after_charges(r.trades) - m.max_dd) < 1e-9
    assert profit_factor(r.trades) >= 0.0
    if r.trades:
        assert abs(trade_after_charges(r.trades[0]) - (r.trades[0].gross_pnl_inr - r.trades[0].fees_inr)) < 1e-9


def test_session_bars_drop_weekend() -> None:
    bars = [
        _b("2026-08-14 10:00:00", 100, 101, 99, 100, 10),  # Friday
        _b("2026-08-15 10:00:00", 100, 101, 99, 100, 10),  # Saturday
        _b("2026-08-17 10:00:00", 100, 101, 99, 100, 10),  # Monday
    ]
    out = session_bars(bars)
    assert [b.time[:10] for b in out] == ["2026-08-14", "2026-08-17"]


def test_not_wired_to_paper_or_live() -> None:
    names = [n for n, _, _ in STRATEGIES]
    for n in names + [LAB_NAME]:
        assert n not in ALL_STRATEGY_NAMES
        assert n not in SLIM_PAPER_STRATEGIES
        assert n not in PAPER_ONLY_BOOKS
    root = Path(__file__).resolve().parent
    station = (root / "station.html").read_text(encoding="utf-8")
    paper = station.split("const PAPER_BOOKS")[1].split("];")[0]
    portfolio = (root / "portfolio.py").read_text(encoding="utf-8")
    runner = (root / "run_strategy.py").read_text(encoding="utf-8")
    assert "ENABLE_OHLCV" not in portfolio
    assert "ohlcv_lab" not in runner
    assert "breakout" not in paper or "OHLCV" not in paper
    for n in names:
        assert n not in ALL_STRATEGY_NAMES


if __name__ == "__main__":
    test_selected_strategies_order_and_filter()
    test_rvol_class()
    test_breakout_long_and_short()
    test_breakout_skips_low_volume()
    test_climax_waits_then_buys_recovery()
    test_pullback_long_after_weak_reds()
    test_consol_breakout()
    test_vwap_pullback_not_first_bar()
    test_rvol_momentum_long()
    test_three_candle_long()
    test_flip_eod_and_atr_exits()
    test_rank_metrics_use_after_charges_not_winrate()
    test_session_bars_drop_weekend()
    test_not_wired_to_paper_or_live()
    print("ALL test_ohlcv_lab OK")
