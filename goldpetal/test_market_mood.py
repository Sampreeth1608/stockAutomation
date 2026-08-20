"""Shared market mood — one tape read, not a new paper book."""

from __future__ import annotations

import os
from pathlib import Path

from control_state import ALL_STRATEGY_NAMES, SLIM_PAPER_STRATEGIES
from live_readiness import PAPER_ONLY_BOOKS
from market_mood import (
    MOOD_EXEMPT_BOOKS,
    MoodDetector,
    classify_samples,
    mood_blocks_entry,
    mood_wants_flatten,
)


def _fall() -> list[tuple[float, float, float]]:
    out = []
    px, tbq, tsq = 15466.0, 8000.0, 5000.0
    for i in range(24):
        px -= 4.0
        tbq -= 20.0
        tsq += 80.0
        out.append((px, tbq, tsq))
    return out


def _quiet() -> list[tuple[float, float, float]]:
    return [(15400.0 + (0.2 if i % 2 else -0.2), 6000.0, 5900.0) for i in range(24)]


def _burst() -> list[tuple[float, float, float]]:
    out = [(15400.0, 6000.0, 6000.0) for _ in range(16)]
    px = 15400.0
    for i in range(8):
        px -= 8.0
        out.append((px, 5000.0, 9000.0))
    return out


def _rise() -> list[tuple[float, float, float]]:
    out = []
    px, tbq, tsq = 15300.0, 5000.0, 8000.0
    for _i in range(24):
        px += 4.0
        tbq += 80.0
        tsq -= 20.0
        out.append((px, tbq, tsq))
    return out


def test_fall_start_blocks_long_when_gated() -> None:
    os.environ["MOOD_GATE"] = "true"
    os.environ["MOOD_FLATTEN"] = "true"
    try:
        st = classify_samples(_fall(), gate=True, flatten=True)
        assert st.mood == "FALL_START"
        assert st.allow_long is False
        assert st.flatten_long is True
        blocked, why = mood_blocks_entry(st, "BUY")
        assert blocked and "block_long" in why
        want, _ = mood_wants_flatten(st, "long")
        assert want is True
    finally:
        os.environ.pop("MOOD_GATE", None)
        os.environ.pop("MOOD_FLATTEN", None)


def test_observe_does_not_block() -> None:
    os.environ["MOOD_GATE"] = "false"
    try:
        st = classify_samples(_fall(), gate=False, flatten=False)
        assert st.mood == "FALL_START"
        blocked, why = mood_blocks_entry(st, "BUY")
        assert blocked is False
        assert why == "mood_observe"
    finally:
        os.environ.pop("MOOD_GATE", None)


def test_rise_start_blocks_short_when_gated() -> None:
    st = classify_samples(_rise(), gate=True, flatten=True)
    assert st.mood == "RISE_START"
    assert st.allow_short is False
    blocked, why = mood_blocks_entry(st, "SHORT")
    assert blocked and "block_short" in why
    skipped, skip_why = mood_blocks_entry(st, "BUY", strategy="S13_HHHL_DAY")
    assert skipped is False and skip_why == "mood_exempt"
    overnight, overnight_why = mood_blocks_entry(st, "SHORT", strategy="OVERNIGHT_GAP")
    assert overnight is False and overnight_why == "mood_exempt"


def test_s13_never_mood_flatten() -> None:
    st = classify_samples(_fall(), gate=True, flatten=True)
    want, why = mood_wants_flatten(st, "long", strategy="S13_HHHL_DAY")
    assert want is False
    assert why == "mood_exempt"
    assert "S13_HHHL_DAY" in MOOD_EXEMPT_BOOKS
    assert "S4_OVERNIGHT" in MOOD_EXEMPT_BOOKS
    assert "OVERNIGHT_GAP" in MOOD_EXEMPT_BOOKS


def test_quiet_stands_down_trend_books() -> None:
    st = classify_samples(_quiet(), gate=True)
    assert st.mood == "QUIET"
    assert st.regime in {"CALM", "RANGE", "ACCUMULATION"}
    s8 = st.fit_for("S8_NET_ZIGZAG")
    assert s8 is not None and s8["stance"] == "trade"
    s5 = st.fit_for("S5_MINEDGE")
    assert s5 is not None and s5["stance"] == "trade"
    s16 = st.fit_for("S16_HHHL_WICK_1H")
    assert s16 is not None and s16["stance"] == "trade"
    blocked16, why16 = mood_blocks_entry(st, "SHORT", strategy="S16_HHHL_WICK_1H")
    assert blocked16 is False and why16 == "s16_1h_formula"
    buy16, buy_why = mood_blocks_entry(st, "BUY", strategy="S16_HHHL_WICK_1H")
    assert buy16 is False and buy_why == "s16_1h_formula"
    fb = st.fit_for("FLOW_BRAIN")
    assert fb is not None and fb["stance"] == "stand_down"
    s13 = st.fit_for("S13_HHHL_DAY")
    assert s13 is not None and s13["stance"] == "hold_swing"
    gap = st.fit_for("OVERNIGHT_GAP")
    assert gap is not None and gap["stance"] == "hold_swing"
    blocked, why = mood_blocks_entry(st, "BUY", strategy="S8_NET_ZIGZAG")
    assert blocked is False and why == "mood_ok"
    blocked5, why5 = mood_blocks_entry(st, "BUY", strategy="S5_MINEDGE")
    assert blocked5 is False and why5 == "mood_ok"
    skipped, skip_why = mood_blocks_entry(st, "BUY", strategy="S13_HHHL_DAY")
    assert skipped is False and skip_why == "mood_exempt"


def test_fall_prefers_short_on_trend_books() -> None:
    st = classify_samples(_fall(), gate=True)
    s8 = st.fit_for("S8_NET_ZIGZAG")
    assert s8 is not None
    assert s8["stance"] == "trade"
    assert s8["preferred_side"] == "short"
    blocked_long, _ = mood_blocks_entry(st, "BUY", strategy="S8_NET_ZIGZAG")
    blocked_short, why_s = mood_blocks_entry(st, "SHORT", strategy="S8_NET_ZIGZAG")
    assert blocked_long is True
    assert blocked_short is False
    assert why_s == "mood_ok"
    buy16, why16 = mood_blocks_entry(st, "BUY", strategy="S16_HHHL_WICK_1H")
    short16, why_s16 = mood_blocks_entry(st, "SHORT", strategy="S16_HHHL_WICK_1H")
    assert buy16 is False and why16 == "s16_1h_formula"
    assert short16 is False and why_s16 == "s16_1h_formula"


def test_burst_stands_down_intraday() -> None:
    st = classify_samples(_burst(), gate=True)
    assert st.mood == "BURST"
    blocked, why = mood_blocks_entry(st, "SHORT", strategy="S18_OHLC_VOL_HTF")
    assert blocked and "stand_down" in why
    s16_blocked, s16_why = mood_blocks_entry(st, "SHORT", strategy="S16_HHHL_WICK_1H")
    assert s16_blocked is False and s16_why == "s16_1h_formula"
    flat16, flat_why = mood_wants_flatten(st, "short", strategy="S16_HHHL_WICK_1H")
    assert flat16 is False and flat_why == "s16_1h_formula"


def test_quiet_and_burst() -> None:
    q = classify_samples(_quiet(), gate=False)
    assert q.mood == "QUIET"
    b = classify_samples(_burst(), gate=False)
    assert b.mood == "BURST"
    assert b.allow_long is False and b.allow_short is False


def test_streaming_detector() -> None:
    d = MoodDetector(window=40)
    for px, tbq, tsq in _fall():
        d.update(px, tbq, tsq)
    assert d.last.mood in {"FALL_START", "HEAT", "BURST", "COOL"}
    assert d.last.direction == "down"


def test_warming_up_stands_down_and_blocks_shorts() -> None:
    st = classify_samples([], gate=True)
    assert st.mood == "UNKNOWN"
    assert st.allow_short is False
    assert st.allow_long is False
    s18 = st.fit_for("S18_OHLC_VOL_HTF")
    assert s18 is not None and s18["stance"] == "stand_down"
    s19 = st.fit_for("S19_BODY_CLOSE_1H")
    assert s19 is not None and s19["stance"] == "stand_down"
    blocked, why = mood_blocks_entry(st, "SHORT", strategy="S18_OHLC_VOL_HTF")
    assert blocked and "stand_down" in why
    blocked19, _ = mood_blocks_entry(st, "SHORT", strategy="S19_BODY_CLOSE_1H")
    assert blocked19 is True
    skipped, skip_why = mood_blocks_entry(st, "SHORT", strategy="S13_HHHL_DAY")
    assert skipped is False and skip_why == "mood_exempt"
    s16_blocked, s16_why = mood_blocks_entry(st, "SHORT", strategy="S16_HHHL_WICK_1H")
    assert s16_blocked is False and s16_why == "s16_1h_formula"


def test_rise_start_blocks_hour_book_shorts() -> None:
    st = classify_samples(_rise(), gate=True)
    assert st.mood == "RISE_START"
    assert st.regime == "BREAKOUT"
    for name in (
        "S18_OHLC_VOL_HTF",
        "S19_BODY_CLOSE_1H",
        "S8_NET_ZIGZAG",
    ):
        blocked, why = mood_blocks_entry(st, "SHORT", strategy=name)
        assert blocked, (name, why)
        assert "prefers_long" in why or "block_short" in why, why
    s16_blocked, s16_why = mood_blocks_entry(st, "SHORT", strategy="S16_HHHL_WICK_1H")
    assert s16_blocked is False and s16_why == "s16_1h_formula"


def test_seed_from_db_blocks_short_on_rise() -> None:
    import tempfile

    from storage import init_db, save_tick

    from market_mood import snapshot_mood

    os.environ["MOOD_GATE"] = "true"
    try:
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "ticks.db"
            init_db(db)
            px, tbq, tsq = 15300.0, 5000.0, 8000.0
            for i in range(24):
                px += 4.0
                tbq += 80.0
                tsq -= 20.0
                save_tick(
                    {
                        "last_traded_price": int(px * 100),
                        "total_buy_quantity": tbq,
                        "total_sell_quantity": tsq,
                    },
                    symbol="GOLDPETAL",
                    token="1",
                    received_at=f"2026-08-19T11:{i:02d}:00+05:30",
                    db_path=db,
                )
            det = MoodDetector(window=80)
            st = det.seed_from_db(db)
            snap = snapshot_mood(db)
            assert st.mood == snap.mood == "RISE_START"
            assert st.n_samples >= 8
            blocked, _why = mood_blocks_entry(st, "SHORT", strategy="S18_OHLC_VOL_HTF")
            assert blocked is True
    finally:
        os.environ.pop("MOOD_GATE", None)


def test_mood_gate_defaults_off() -> None:
    os.environ.pop("MOOD_GATE", None)
    from market_mood import mood_gate_on

    assert mood_gate_on() is False
    os.environ["MOOD_GATE"] = "true"
    try:
        assert mood_gate_on() is True
    finally:
        os.environ.pop("MOOD_GATE", None)


def test_set_regime_gate_writes_env() -> None:
    import tempfile

    from market_mood import mood_gate_on, set_regime_gate

    with tempfile.TemporaryDirectory() as td:
        env = Path(td) / ".env"
        env.write_text("MOOD_GATE=false\nFLATTEN_ON_BAD_REGIME=false\n", encoding="utf-8")
        os.environ["MOOD_GATE"] = "false"
        os.environ["FLATTEN_ON_BAD_REGIME"] = "false"
        try:
            res = set_regime_gate(True, path=env)
            assert res.get("ok") is True
            assert res.get("gate_on") is True
            text = env.read_text(encoding="utf-8")
            assert "MOOD_GATE=true" in text
            assert "FLATTEN_ON_BAD_REGIME=true" in text
            assert mood_gate_on() is True
            res2 = set_regime_gate(False, path=env)
            assert res2.get("ok") is True
            assert res2.get("gate_on") is False
            assert "MOOD_GATE=false" in env.read_text(encoding="utf-8")
        finally:
            os.environ.pop("MOOD_GATE", None)
            os.environ.pop("FLATTEN_ON_BAD_REGIME", None)


def test_mood_desk_payload_note_follows_gate() -> None:
    import tempfile

    from market_mood import mood_desk_payload

    missing = Path(tempfile.mkdtemp()) / "no-ticks.db"
    os.environ["MOOD_GATE"] = "false"
    try:
        d = mood_desk_payload(missing)
        assert d["gate_on"] is False
        assert "OFF" in d["note"]
        assert "Gate is on" not in d["note"]
    finally:
        os.environ.pop("MOOD_GATE", None)
    os.environ["MOOD_GATE"] = "true"
    try:
        d = mood_desk_payload(missing)
        assert d["gate_on"] is True
        assert "Market regime is ON" in d["note"]
        assert "Market regime is OFF" not in d["note"]
    finally:
        os.environ.pop("MOOD_GATE", None)


def test_horizon_stack_has_operator_rungs() -> None:
    from market_mood import HORIZON_KEYS, LAYER_SAMPLES, downsample_samples

    assert HORIZON_KEYS == (
        "ticks80",
        "5m",
        "15m",
        "30m",
        "45m",
        "1h",
        "1h15",
        "1h30",
        "1h45",
        "2h",
        "2h15",
        "2h30",
        "2h45",
        "3h",
        "day",
        "week",
    )
    assert LAYER_SAMPLES == 80
    pts = [(100.0 + i, 1.0, 1.0) for i in range(400)]
    out = downsample_samples(pts, 80)
    assert out[0] == pts[0]
    assert out[-1] == pts[-1]
    assert 70 <= len(out) <= 82


def test_htf_fight_stands_down_hour_book_not_s16() -> None:
    from market_mood import HORIZON_SPECS, blend_layers, classify_samples, mood_blocks_entry

    rise = classify_samples(_rise(), gate=True, with_fits=False)
    fall = classify_samples(_fall(), gate=True, with_fits=False)
    layers = {spec.key: rise for spec in HORIZON_SPECS}
    for key in ("2h", "3h", "day", "week"):
        layers[key] = fall
    st = blend_layers(layers, gate=True)
    assert st.alignment == "fighting"
    s18 = st.fit_for("S18_OHLC_VOL_HTF")
    assert s18 is not None and s18["stance"] == "stand_down"
    blocked18, why18 = mood_blocks_entry(st, "BUY", strategy="S18_OHLC_VOL_HTF")
    assert blocked18 is True and "stand_down" in why18
    buy16, why16 = mood_blocks_entry(st, "BUY", strategy="S16_HHHL_WICK_1H")
    assert buy16 is False and why16 == "s16_1h_formula"
    s5 = st.fit_for("S5_MINEDGE")
    assert s5 is not None and s5["stance"] == "trade"
    s13 = st.fit_for("S13_HHHL_DAY")
    assert s13 is not None and s13["stance"] == "hold_swing"
    blocked, why = mood_blocks_entry(st, "BUY", strategy="S13_HHHL_DAY")
    assert blocked is False and why == "mood_exempt"


def test_snapshot_mood_exposes_layers() -> None:
    import tempfile

    from storage import init_db, save_tick

    from market_mood import HORIZON_KEYS, snapshot_mood

    os.environ["MOOD_GATE"] = "true"
    try:
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "ticks.db"
            init_db(db)
            px, tbq, tsq = 15300.0, 5000.0, 8000.0
            for i in range(24):
                px += 4.0
                tbq += 80.0
                tsq -= 20.0
                save_tick(
                    {
                        "last_traded_price": int(px * 100),
                        "total_buy_quantity": tbq,
                        "total_sell_quantity": tsq,
                    },
                    symbol="GOLDPETAL",
                    token="1",
                    received_at=f"2026-08-19T11:{i:02d}:00+05:30",
                    db_path=db,
                )
            snap = snapshot_mood(db)
            keys = [row["key"] for row in snap.layers]
            assert keys == list(HORIZON_KEYS)
            assert snap.mood == "RISE_START"
            ticks = next(row for row in snap.layers if row["key"] == "ticks80")
            assert ticks["ready"] is True
            day = next(row for row in snap.layers if row["key"] == "day")
            assert day["ready"] is True
    finally:
        os.environ.pop("MOOD_GATE", None)


def test_not_a_paper_book() -> None:
    root = Path(__file__).resolve().parent
    assert "MARKET_MOOD" not in ALL_STRATEGY_NAMES
    assert "mood" not in SLIM_PAPER_STRATEGIES
    runner = (root / "run_strategy.py").read_text(encoding="utf-8")
    assert "MOOD_GATE" in runner
    paper = (root / "station.html").read_text(encoding="utf-8").split("const PAPER_BOOKS")[1].split("];")[0]
    assert "MOOD" not in paper
    assert "MARKET_MOOD" not in PAPER_ONLY_BOOKS
    station = (root / "station.html").read_text(encoding="utf-8")
    assert "/api/mood" in station
    assert "mood-pill" in station
    assert "data-fit" in station
    assert 'id="btn-regime"' in station
    assert "/api/desk/regime" in station
    lite = (root / "lite.html").read_text(encoding="utf-8")
    assert "/api/mood" in lite
    assert 'id="btn-regime"' in lite
    panel = (root / "control_panel.py").read_text(encoding="utf-8")
    assert "/api/mood" in panel
    assert "/api/desk/regime" in panel
    env = (root / ".env.example").read_text(encoding="utf-8")
    assert "MOOD_GATE=false" in env
    assert "FLATTEN_ON_BAD_REGIME=false" in env
    assert "MOOD_FLATTEN=false" in env
    assert "MOOD_FIT_MIN=0.40" in env
    runner = (root / "run_strategy.py").read_text(encoding="utf-8")
    assert "mood_blocks_entry" in runner
    assert "OWN_GATE_BOOKS" in (root / "market_mood.py").read_text(encoding="utf-8")
    assert "FORMULA_GATE_BOOKS" in (root / "market_mood.py").read_text(encoding="utf-8")
    assert "MOOD_FLATTEN" in runner
    assert "seed_from_db" in runner
    assert "refresh_layers" in runner
    assert "ENTRY BLOCKED" in runner
    assert "strategy.position = prev" in runner
    assert "AMISE" not in ALL_STRATEGY_NAMES
    assert "MARKET_STATE" not in ALL_STRATEGY_NAMES


if __name__ == "__main__":
    test_fall_start_blocks_long_when_gated()
    test_observe_does_not_block()
    test_rise_start_blocks_short_when_gated()
    test_s13_never_mood_flatten()
    test_quiet_stands_down_trend_books()
    test_fall_prefers_short_on_trend_books()
    test_burst_stands_down_intraday()
    test_quiet_and_burst()
    test_streaming_detector()
    test_warming_up_stands_down_and_blocks_shorts()
    test_rise_start_blocks_hour_book_shorts()
    test_seed_from_db_blocks_short_on_rise()
    test_mood_gate_defaults_off()
    test_set_regime_gate_writes_env()
    test_mood_desk_payload_note_follows_gate()
    test_horizon_stack_has_operator_rungs()
    test_htf_fight_stands_down_hour_book_not_s16()
    test_snapshot_mood_exposes_layers()
    test_not_a_paper_book()
    print("ALL test_market_mood OK")
