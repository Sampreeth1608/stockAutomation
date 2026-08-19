"""S7 flow brain. New book. Not S16. ENABLE defaults false. Not live."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from control_state import ALL_STRATEGY_NAMES, SLIM_PAPER_STRATEGIES
from flow_brain import (
    ABSORB_BUY,
    BOOK,
    FlowBrain,
    flow_imbalance,
    move_scale,
    simulate_flow_brain,
)
from live_readiness import PAPER_ONLY_BOOKS
from strategy_flow_brain import S7FlowBrainStrategy


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
    strat = S7FlowBrainStrategy(min_hold_s=1.0, cooldown_s=0.0)
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
    assert "s7_from_env" in runner
    assert "ENABLE_S7" in runner
    assert 'on("ENABLE_S7", "false")' in portfolio
    assert '"ENABLE_S7": "false"' in env_bridge
    assert "flow_brain" not in s16
    assert "ENABLE_S16" in (root / "portfolio.py").read_text(encoding="utf-8")


if __name__ == "__main__":
    test_imbalance_and_scale()
    test_bull_expanding_goes_long()
    test_bear_expanding_goes_short()
    test_absorption_does_not_buy()
    test_tbq_reset_does_not_crash()
    test_live_strategy_matches_sim_long()
    test_wired_enable_off_not_slim_not_s16()
    print("s7 flow brain tests ok")
