"""FLOW_BRAIN. New book. Not S7_HOURLY. Not S16. ENABLE defaults false. Not live."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from control_state import ALL_STRATEGY_NAMES, SLIM_PAPER_STRATEGIES
from flow_brain import (
    ABSORB_BUY,
    BOOK,
    GATE_PACKS,
    FlowBrain,
    FlowGates,
    after_charges_win_rate,
    flow_imbalance,
    move_scale,
    simulate_flow_brain,
)
from live_readiness import PAPER_ONLY_BOOKS
from strategy_flow_brain import FlowBrainLiveStrategy


def _dt(i: int, *, step: float = 0.25) -> datetime:
    return datetime(2026, 8, 17, 10, 0, 0) + timedelta(seconds=step * i)


def _bull_expanding(n: int = 80) -> list[tuple[datetime, float, float, float]]:
    ltp, tbq, tsq = 100.0, 10_000.0, 10_000.0
    out = []
    for i in range(n):
        if i < 32:
            ltp += 0.15
            tbq += 30
            tsq += 25
        else:
            ltp += 1.4
            tbq += 500
            tsq += 8
        out.append((_dt(i), ltp, tbq, tsq))
    return out


def _bear_expanding(n: int = 80) -> list[tuple[datetime, float, float, float]]:
    ltp, tbq, tsq = 200.0, 10_000.0, 10_000.0
    out = []
    for i in range(n):
        if i < 32:
            ltp -= 0.15
            tbq += 25
            tsq += 30
        else:
            ltp -= 1.4
            tbq += 8
            tsq += 500
        out.append((_dt(i), ltp, tbq, tsq))
    return out


def _absorb_buy(n: int = 60) -> list[tuple[datetime, float, float, float]]:
    ltp, tbq, tsq = 150.0, 10_000.0, 10_000.0
    out = []
    for i in range(n):
        tbq += 400
        tsq += 5
        out.append((_dt(i), ltp, tbq, tsq))
    return out


def test_imbalance_and_scale() -> None:
    assert abs(flow_imbalance(120, 80) - 0.2) < 1e-9
    assert move_scale(2, 20) == "small"
    assert move_scale(10, 20) == "medium"
    assert move_scale(20, 20) == "large"
    assert move_scale(40, 20) == "extreme"
    assert move_scale(0.5, 20) == "none"


def test_bull_expanding_goes_long() -> None:
    r = simulate_flow_brain(_bull_expanding(), lots=1, fees=False, session_filter=False)
    assert r.n_trades >= 1
    assert r.trades[0].side == "LONG"


def test_bear_expanding_goes_short() -> None:
    r = simulate_flow_brain(_bear_expanding(), lots=1, fees=False, session_filter=False)
    assert r.n_trades >= 1
    assert r.trades[0].side == "SHORT"


def test_absorption_does_not_buy() -> None:
    r = simulate_flow_brain(_absorb_buy(), lots=1, fees=False, session_filter=False)
    assert r.n_trades == 0
    brain = FlowBrain()
    saw_absorb = False
    for dt, ltp, tbq, tsq in _absorb_buy():
        snap = brain.push(dt.timestamp(), ltp, tbq, tsq)
        if snap and snap.state == ABSORB_BUY:
            saw_absorb = True
            assert snap.want is None
    assert saw_absorb


def test_tbq_reset_does_not_crash() -> None:
    samples = _bull_expanding(40)
    samples.append((_dt(40), 160.0, 50.0, 40.0))
    samples.extend(
        (_dt(41 + i), 160.0 + i * 0.1, 50.0 + i, 40.0 + i) for i in range(10)
    )
    r = simulate_flow_brain(samples, lots=1, fees=False, session_filter=False)
    assert r.n_trades >= 0


def test_live_strategy_matches_sim_long() -> None:
    strat = FlowBrainLiveStrategy(min_hold_s=1.0, cooldown_s=0.0)
    got = None
    for dt, ltp, tbq, tsq in _bull_expanding():
        res = strat.on_tick(
            dt, ltp, {"total_buy_quantity": tbq, "total_sell_quantity": tsq}
        )
        if res is not None and res.action == "BUY":
            got = res
            break
    assert got is not None
    assert strat.position == "long"


def _bull_then_weak() -> list[tuple[datetime, float, float, float]]:
    ltp, tbq, tsq = 100.0, 10_000.0, 10_000.0
    out: list[tuple[datetime, float, float, float]] = []
    t0 = datetime(2026, 8, 17, 10, 0, 0)
    for i in range(40):
        ltp += 1.4
        tbq += 500
        tsq += 8
        out.append((t0 + timedelta(seconds=i), ltp, tbq, tsq))
    for i in range(40, 90):
        ltp += 0.4
        tbq += 5
        tsq += 80
        out.append((t0 + timedelta(seconds=i), ltp, tbq, tsq))
    return out


def _bull_then_bear() -> list[tuple[datetime, float, float, float]]:
    ltp, tbq, tsq = 200.0, 10_000.0, 10_000.0
    out: list[tuple[datetime, float, float, float]] = []
    t0 = datetime(2026, 8, 17, 10, 0, 0)
    i = 0
    for _ in range(50):
        ltp += 1.4
        tbq += 500
        tsq += 8
        out.append((t0 + timedelta(seconds=i), ltp, tbq, tsq))
        i += 1
    for _ in range(50):
        ltp -= 1.4
        tbq += 8
        tsq += 500
        out.append((t0 + timedelta(seconds=i), ltp, tbq, tsq))
        i += 1
    return out


def _scratchy_then_trend() -> list[tuple[datetime, float, float, float]]:
    """Chop that v1 scratches, then a real expansion quality can hold."""
    ltp, tbq, tsq = 2340.0, 50_000.0, 50_000.0
    out: list[tuple[datetime, float, float, float]] = []
    t0 = datetime(2026, 8, 17, 10, 0, 0)
    i = 0
    for _minute in range(25):
        for _s in range(30):
            ltp += 0.3
            tbq += 80
            tsq += 20
            out.append((t0 + timedelta(seconds=i), ltp, tbq, tsq))
            i += 1
        for _s in range(30):
            ltp -= 0.3
            tbq += 20
            tsq += 80
            out.append((t0 + timedelta(seconds=i), ltp, tbq, tsq))
            i += 1
    for _s in range(480):
        ltp += 1.0
        tbq += 400
        tsq += 10
        out.append((t0 + timedelta(seconds=i), ltp, tbq, tsq))
        i += 1
    return out


def test_v1_pack_matches_ungated() -> None:
    samples = _bull_expanding()
    a = simulate_flow_brain(samples, lots=1, fees=False, session_filter=False)
    b = simulate_flow_brain(
        samples, lots=1, fees=False, session_filter=False, gates=GATE_PACKS["v1"]
    )
    assert a.n_trades == b.n_trades
    assert a.trades[0].side == b.trades[0].side == "LONG"


def test_confirm_delays_entry() -> None:
    samples = _bull_expanding()
    raw = simulate_flow_brain(samples, lots=1, fees=False, session_filter=False)
    blocked = simulate_flow_brain(
        samples,
        lots=1,
        fees=False,
        session_filter=False,
        gates=FlowGates(name="block", confirm_s=10_000.0, min_hold_s=1.0, cooldown_s=0.0),
    )
    assert raw.n_trades >= 1
    assert blocked.n_trades == 0


def test_no_flip_does_not_reverse() -> None:
    samples = _bull_then_bear()
    flipped = simulate_flow_brain(
        samples,
        lots=1,
        fees=False,
        session_filter=False,
        gates=FlowGates(
            name="flip",
            min_hold_s=5.0,
            cooldown_s=10_000.0,
            allow_flip=True,
            persist_until_opposite=True,
        ),
    )
    held = simulate_flow_brain(
        samples,
        lots=1,
        fees=False,
        session_filter=False,
        gates=FlowGates(
            name="noflip",
            min_hold_s=5.0,
            cooldown_s=10_000.0,
            allow_flip=False,
            persist_until_opposite=True,
        ),
    )
    assert any(t.side == "SHORT" for t in flipped.trades)
    assert all(t.side == "LONG" for t in held.trades)
    assert held.n_trades == 1


def test_persist_does_not_exit_on_mild_decay() -> None:
    samples = _bull_then_weak()
    scratch = simulate_flow_brain(
        samples,
        lots=1,
        fees=False,
        session_filter=False,
        gates=FlowGates(name="scratch", min_hold_s=5.0, cooldown_s=0.0),
    )
    persist = simulate_flow_brain(
        samples,
        lots=1,
        fees=False,
        session_filter=False,
        gates=FlowGates(
            name="persist",
            min_hold_s=5.0,
            cooldown_s=0.0,
            persist_until_opposite=True,
            allow_flip=False,
        ),
    )
    assert scratch.n_trades >= 1
    assert persist.n_trades == 1
    assert persist.trades[0].exit_time > scratch.trades[0].exit_time


def test_quality_fewer_trades_higher_winrate_than_v1() -> None:
    samples = _scratchy_then_trend()
    v1 = simulate_flow_brain(
        samples, lots=1, fees=True, session_filter=False, gates=GATE_PACKS["v1"]
    )
    quality = simulate_flow_brain(
        samples, lots=1, fees=True, session_filter=False, gates=GATE_PACKS["quality"]
    )
    assert v1.n_trades > 1
    assert quality.n_trades < v1.n_trades
    assert quality.n_trades >= 1
    q_wr = after_charges_win_rate(quality)
    v_wr = after_charges_win_rate(v1)
    assert q_wr > v_wr


def test_s8_net_sign_blocks_long_when_net_negative() -> None:
    ltp, tbq, tsq = 100.0, 1_000.0, 80_000.0
    samples: list[tuple[datetime, float, float, float]] = []
    t0 = datetime(2026, 8, 17, 10, 0, 0)
    for i in range(80):
        ltp += 1.4
        tbq += 400
        tsq += 8
        samples.append((t0 + timedelta(seconds=i), ltp, tbq, tsq))
    blocked = simulate_flow_brain(
        samples,
        lots=1,
        fees=False,
        session_filter=False,
        gates=FlowGates(
            name="net",
            min_hold_s=1.0,
            cooldown_s=0.0,
            require_expanding=False,
            require_net_sign=True,
        ),
    )
    raw = simulate_flow_brain(
        samples,
        lots=1,
        fees=False,
        session_filter=False,
        gates=FlowGates(
            name="raw",
            min_hold_s=1.0,
            cooldown_s=0.0,
            require_expanding=False,
        ),
    )
    assert raw.n_trades >= 1
    assert blocked.n_trades == 0


def test_s19_1m_required_waits_for_aligned_minute() -> None:
    samples = _bull_expanding()
    gated = simulate_flow_brain(
        samples,
        lots=1,
        fees=False,
        session_filter=False,
        gates=FlowGates(
            name="s19",
            min_hold_s=1.0,
            cooldown_s=0.0,
            require_expanding=False,
            require_s19_1m=True,
        ),
    )
    assert gated.n_trades == 0


def test_s5_fee_cover_blocks_tiny_expected() -> None:
    samples = _bull_expanding()
    gated = simulate_flow_brain(
        samples,
        lots=1,
        fees=False,
        session_filter=False,
        gates=FlowGates(
            name="fee",
            min_hold_s=1.0,
            cooldown_s=0.0,
            require_expanding=False,
            fee_cover=True,
            min_edge_pts=10_000.0,
            edge_safety=1.0,
        ),
    )
    assert gated.n_trades == 0


def test_s5_tp_exits_in_profit() -> None:
    samples = _bull_then_weak()
    r = simulate_flow_brain(
        samples,
        lots=1,
        fees=False,
        session_filter=False,
        gates=FlowGates(
            name="tp",
            min_hold_s=1.0,
            cooldown_s=0.0,
            require_expanding=False,
            persist_until_opposite=True,
            allow_flip=False,
            tp_pts=40.0,
        ),
    )
    assert r.n_trades >= 1
    assert r.trades[0].gross_pnl_inr >= 39.0
    assert r.trades[0].exit_time < samples[-1][0].strftime("%Y-%m-%d %H:%M:%S")


def test_desk_fewer_trades_higher_winrate_than_v1() -> None:
    samples = _scratchy_then_trend()
    v1 = simulate_flow_brain(
        samples, lots=1, fees=True, session_filter=False, gates=GATE_PACKS["v1"]
    )
    desk = simulate_flow_brain(
        samples, lots=1, fees=True, session_filter=False, gates=GATE_PACKS["desk"]
    )
    desk100 = simulate_flow_brain(
        samples, lots=100, fees=True, session_filter=False, gates=GATE_PACKS["desk"]
    )
    assert "desk" in GATE_PACKS
    assert GATE_PACKS["desk"].require_s19_1m is False
    assert GATE_PACKS["desk"].allow_flip is False
    assert v1.n_trades > 1
    assert desk.n_trades < v1.n_trades
    assert desk.n_trades >= 1
    # lots=1: 25pt TP still loses to ~₹48 fees. 100 lots is the VM rank
    # (fee BE ~3pts on the 548k tape). Clip winners must cover that.
    from ohlcv_lab import after_charges_inr

    assert after_charges_win_rate(desk100) >= 0.99
    assert after_charges_inr(desk100) > 0


def test_live_confirm_blocks_buy() -> None:
    strat = FlowBrainLiveStrategy(
        min_hold_s=1.0,
        cooldown_s=0.0,
        gates=FlowGates(name="block", confirm_s=10_000.0),
    )
    got = None
    for dt, ltp, tbq, tsq in _bull_expanding():
        res = strat.on_tick(
            dt, ltp, {"total_buy_quantity": tbq, "total_sell_quantity": tsq}
        )
        if res is not None and res.action == "BUY":
            got = res
            break
    assert got is None
    assert strat.position == "flat"


def test_wired_enable_off_not_slim_not_s16() -> None:
    assert BOOK in ALL_STRATEGY_NAMES
    assert BOOK not in SLIM_PAPER_STRATEGIES
    assert BOOK in PAPER_ONLY_BOOKS
    root = Path(__file__).resolve().parent
    station = (root / "station.html").read_text(encoding="utf-8")
    paper = station.split("const PAPER_BOOKS")[1].split("];")[0]
    assert BOOK not in paper
    runner = (root / "run_strategy.py").read_text(encoding="utf-8")
    portfolio = (root / "portfolio.py").read_text(encoding="utf-8")
    env_bridge = (root / "analytics" / "env_bridge.py").read_text(encoding="utf-8")
    s16 = (root / "strategy_s16.py").read_text(encoding="utf-8")
    assert "flow_brain_from_env" in runner
    assert "ENABLE_FLOW_BRAIN" in runner
    assert "ENABLE_S7" not in runner
    assert "S7_FLOW_BRAIN" not in runner
    assert 'on("ENABLE_FLOW_BRAIN", "false")' in portfolio
    assert '"ENABLE_FLOW_BRAIN": "false"' in env_bridge
    assert "flow_brain" not in s16
    assert "ENABLE_S16" in (root / "portfolio.py").read_text(encoding="utf-8")
    env_ex = (root / ".env.example").read_text(encoding="utf-8")
    # VM copy is git-show of a few files; .env.example may be stale.
    if "ENABLE_FLOW_BRAIN" in env_ex:
        assert "ENABLE_FLOW_BRAIN=false" in env_ex
    if "FLOW_BRAIN_GATE_PACK" in env_ex:
        assert "FLOW_BRAIN_GATE_PACK=v1" in env_ex


if __name__ == "__main__":
    test_imbalance_and_scale()
    test_bull_expanding_goes_long()
    test_bear_expanding_goes_short()
    test_absorption_does_not_buy()
    test_tbq_reset_does_not_crash()
    test_live_strategy_matches_sim_long()
    test_v1_pack_matches_ungated()
    test_confirm_delays_entry()
    test_no_flip_does_not_reverse()
    test_persist_does_not_exit_on_mild_decay()
    test_quality_fewer_trades_higher_winrate_than_v1()
    test_s8_net_sign_blocks_long_when_net_negative()
    test_s19_1m_required_waits_for_aligned_minute()
    test_s5_fee_cover_blocks_tiny_expected()
    test_s5_tp_exits_in_profit()
    test_desk_fewer_trades_higher_winrate_than_v1()
    test_live_confirm_blocks_buy()
    test_wired_enable_off_not_slim_not_s16()
    print("flow brain tests ok")
