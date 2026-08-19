"""AI Strategy Research Factory — research only. Not paper. Not live."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from control_state import ALL_STRATEGY_NAMES, SLIM_PAPER_STRATEGIES, load_state
from live_readiness import PAPER_ONLY_BOOKS
from flow_lab import FlowBar, FlowParams, build_flow_features, simulate_flow
from ohlcv_lab import LabMetrics
from proposals import PaperResult, StrategyProposal, add_proposal, get_proposal
from research_desk import decide_research, research_desk_payload
from research_factory import (
    FAST_LAB_KWARGS,
    LAB_NAME,
    gate_failures,
    proposal_from_challenger,
    run_research_lab,
)
from research_features import (
    ATOMS,
    FUNCS,
    compile_genome,
    eval_atom,
)
from s11_desk import decide_proposal_for_desk, ml_desk_payload
from strategy_genome import (
    RESEARCH_KIND,
    RESEARCH_STRATEGY,
    StrategyGenome,
    combine_genomes,
    env_patch_is_safe,
    mutate_genome,
    research_env_patch,
)


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
        float(sum(buy_qty)),
        float(sum(sell_qty)),
        buy_px,
        buy_qty,
        sell_px,
        sell_qty,
    )


def _uptrend(n: int = 60) -> list[FlowBar]:
    bars: list[FlowBar] = []
    px = 100.0
    day0 = datetime(2026, 5, 4, 9, 0, 0)
    d = 0
    while len(bars) < n:
        base = day0 + timedelta(days=d)
        for h in range(5):
            ts = (base + timedelta(hours=h)).strftime("%Y-%m-%d %H:%M:%S")
            o = px
            c = px + 1.5
            bars.append(
                _b(
                    ts,
                    o,
                    c + 0.4,
                    o - 0.2,
                    c,
                    v=120.0 + h,
                    oi=2000.0 + len(bars),
                    tbq=80.0,
                    tsq=40.0,
                    buy_px=(c - 0.5,),
                    buy_qty=(10.0,),
                    sell_px=(c + 0.5,),
                    sell_qty=(6.0,),
                )
            )
            px = c
            if len(bars) >= n:
                break
        d += 1
    return bars


def _m(*, n: int = 25, ac: float = 1000.0, wf: int = 3, folds: int = 3) -> LabMetrics:
    return LabMetrics(
        name="x",
        family="t",
        exit_mode="flip_eod",
        n_trades=n,
        n_long=12,
        n_short=13,
        after_charges=ac,
        expectancy=ac / max(n, 1),
        profit_factor=1.6,
        avg_win=80.0,
        avg_loss=-40.0,
        max_dd=100.0,
        win_rate=0.55,
        wf_wins=wf,
        wf_folds=folds,
    )


def test_atoms_match_funcs() -> None:
    assert set(FUNCS) == {a.name for a in ATOMS}


def test_hh_hl_and_no_trade_spread() -> None:
    bars = [
        _b("2026-08-10 09:00:00", 100, 101, 99, 100.5, 100),
        _b("2026-08-10 10:00:00", 100.5, 103, 100.6, 102.5, 150),
    ]
    p = FlowParams(lookback=1, atr_n=1)
    # Force features: need lookback; use a longer tape for feats, eval atoms on bars only.
    assert eval_atom("hh", bars, [None, None], 1, p) is True
    assert eval_atom("hl", bars, [None, None], 1, p) is True
    assert eval_atom("hc", bars, [None, None], 1, p) is True
    assert eval_atom("bull", bars, [None, None], 1, p) is False  # needs FlowFeat.green
    wide = _b(
        "2026-08-10 11:00:00",
        102,
        103,
        101,
        102.5,
        80,
        buy_px=(100.0,),
        buy_qty=(1.0,),
        sell_px=(110.0,),
        sell_qty=(1.0,),
    )
    tape = _uptrend(25) + [wide]
    feats = build_flow_features(tape, FlowParams(lookback=5, atr_n=3, spread_wide_mult=1.1))
    g = StrategyGenome(
        name="hh-only",
        entry_long=("hh",),
        entry_short=(),
        no_trade=("spread_wide",),
        direction="long",
    ).normalized()
    decide = compile_genome(g)
    st: dict = {}
    # last bar is wide L1 — no-trade if feature marked spread_wide
    want, why = decide(tape, feats, len(tape) - 1, FlowParams(lookback=5, atr_n=3, spread_wide_mult=1.1), st)
    assert want is None
    assert "no-trade" in why or why in {"warmup", "hold"}


def test_genome_compile_long_on_hh_hl_bull() -> None:
    bars = _uptrend(30)
    p = FlowParams(lookback=5, atr_n=3)
    feats = build_flow_features(bars, p)
    g = StrategyGenome(
        name="hh+hl+bull",
        entry_long=("hh", "hl", "bull"),
        entry_short=("lh", "ll", "bear"),
        no_trade=("rvol_extreme",),
        direction="both",
    ).normalized()
    decide = compile_genome(g)
    hits = 0
    for i in range(len(bars)):
        want, _why = decide(bars, feats, i, p, {})
        if want == "long":
            hits += 1
        assert want in {None, "long", "short"}
    assert hits >= 1
    res = simulate_flow(bars, g.genome_id, params=p, lots=1.0, fees=False, decide_fn=decide)
    assert res.n_trades >= 0


def test_mutate_and_combine() -> None:
    a = StrategyGenome(name="A", entry_long=("hh",), entry_short=("lh",), params={"rvol": 1.5}).normalized()
    b = StrategyGenome(name="B", entry_long=("vol_up",), entry_short=("vol_up",)).normalized()
    c = combine_genomes(a, b)
    assert "hh" in c.entry_long and "vol_up" in c.entry_long
    m = mutate_genome(a, scale=0.10)
    assert abs(m.params["rvol"] - 1.65) < 1e-9
    assert m.genome_id != a.genome_id


def test_env_patch_never_enable() -> None:
    patch = research_env_patch()
    assert patch == {"DRY_RUN": "true"}
    assert env_patch_is_safe(patch)
    assert not env_patch_is_safe({"DRY_RUN": "true", "ENABLE_S16": "true"})
    assert env_patch_is_safe({"DRY_RUN": "true", "ENABLE_S21": "true"})
    assert env_patch_is_safe({"DRY_RUN": "true", "ENABLE_S25": "true"})
    assert not env_patch_is_safe({"DRY_RUN": "true", "ENABLE_S21": "false"})
    assert not env_patch_is_safe({"DRY_RUN": "false"})


def test_fast_lab_skips_heavy_ml() -> None:
    assert FAST_LAB_KWARGS["sklearn"] is False
    assert FAST_LAB_KWARGS["include_recipes"] is False
    assert FAST_LAB_KWARGS["robustness"] is False
    assert FAST_LAB_KWARGS["n_folds"] == 2
    assert FAST_LAB_KWARGS["max_compose"] == 8


def test_gates_reject_thin_and_champion_loss() -> None:
    s16 = _m(n=20, ac=500)
    s18 = _m(n=10, ac=800)
    thin = _m(n=5, ac=9000)
    assert any("thin" in x for x in gate_failures(thin, s16=s16, s18=s18, rob=None))
    lose = _m(n=25, ac=100)
    fails = gate_failures(lose, s16=s16, s18=s18, rob=None)
    assert any("S16" in x for x in fails)
    assert any("S18" in x for x in fails)
    win = _m(n=25, ac=1200, wf=2)
    assert gate_failures(win, s16=s16, s18=s18, rob=None) == []


def test_factory_rejects_short_tape(tmp_path: Path) -> None:
    bars = _uptrend(40)
    lib = tmp_path / "library.json"
    props = tmp_path / "proposals.json"
    out = run_research_lab(
        bars,
        lots=100.0,
        fees=True,
        n_folds=3,
        params=FlowParams(lookback=5, atr_n=3),
        include_recipes=False,
        max_compose=8,
        robustness=False,
        sklearn=False,
        propose=True,
        proposals_path=props,
        library_path=lib,
        week_id="lab-test",
    )
    assert out["counts"]["found"] >= 1
    assert out["counts"]["passed_validation"] == 0
    assert out["proposed_ids"] == []
    assert out["sklearn_importances"] == []
    assert lib.exists()
    from proposals import load_proposals

    assert (not props.exists()) or load_proposals(props) == []


def test_proposal_and_lab_approve_assigns_s21(tmp_path: Path) -> None:
    props = tmp_path / "proposals.json"
    env = tmp_path / ".env"
    state = tmp_path / "state.json"
    slots = tmp_path / "slots"
    env.write_text("DRY_RUN=true\nENABLE_S21=false\n", encoding="utf-8")
    row = {
        "genome": StrategyGenome(
            name="demo-breakout",
            entry_long=("hh", "hl", "bull", "vol_up"),
            entry_short=("lh", "ll", "bear"),
        ).normalized().to_dict(),
        "metrics": {
            "n_trades": 40,
            "after_charges": 1200.0,
            "win_rate": 0.6,
            "profit_factor": 1.8,
            "stability": "3/3",
        },
        "robustness": {"cost_2x_pass": True},
        "supervisor": "test propose",
        "fails": [],
    }
    prop = proposal_from_challenger(row, week_id="lab-test")
    assert prop.kind == RESEARCH_KIND
    assert prop.strategy == RESEARCH_STRATEGY
    assert prop.env_patch == {"DRY_RUN": "true"}
    assert "ENABLE" not in "".join(prop.env_patch.keys())
    add_proposal(prop, path=props)
    live = decide_research(prop.id, "approved_live", proposals_path=props)
    assert live["ok"] is False
    assert live.get("live_blocked") is True
    still = get_proposal(prop.id, path=props)
    assert still is not None and still.status == "pending"
    inv = decide_research(prop.id, "investigate", note="look again", proposals_path=props)
    assert inv["ok"] is True
    assert inv["status"] == "pending"
    res = decide_research(
        prop.id,
        "approved_paper",
        note="name S21",
        proposals_path=props,
        env_path=env,
        slots_dir=slots,
        state_path=state,
        apply_env=True,
        queue_live=True,
        sync_environ=False,
    )
    assert res["ok"] is True
    assert res["slot"] == "S21_AMISE"
    assert res["control_touched"] is True
    assert res["env_applied"] is True
    assert res["status"] == "approved_paper"
    assert res["live_unlocked"] is False
    assert res["dry_run_forced"] is True
    text = env.read_text(encoding="utf-8")
    assert "DRY_RUN=true" in text
    assert "DRY_RUN=false" not in text
    assert "ENABLE_S21=true" in text
    st = load_state(state)
    assert "S21_AMISE" in st.paper_approved
    assert "S21_AMISE" in st.live_approved
    assert RESEARCH_STRATEGY not in st.paper_approved
    from amise_slots import load_slot_genome

    g = load_slot_genome("S21_AMISE", slots)
    assert g is not None
    assert "hh" in g.entry_long
    refused = decide_proposal_for_desk(
        prop.id,
        "approved_paper",
        proposals_path=props,
        apply_env=True,
    )
    assert refused["ok"] is False
    assert refused.get("lab") is True


def test_lab_reject_does_not_assign(tmp_path: Path) -> None:
    props = tmp_path / "proposals.json"
    env = tmp_path / ".env"
    state = tmp_path / "state.json"
    slots = tmp_path / "slots"
    env.write_text("DRY_RUN=true\nENABLE_S21=false\n", encoding="utf-8")
    row = {
        "genome": StrategyGenome(
            name="nope",
            entry_long=("hh",),
            entry_short=("lh",),
        ).normalized().to_dict(),
        "metrics": {"n_trades": 40, "after_charges": 1200.0, "win_rate": 0.6},
        "robustness": {"cost_2x_pass": True},
        "supervisor": "no",
        "fails": [],
    }
    prop = proposal_from_challenger(row, week_id="lab-test")
    add_proposal(prop, path=props)
    res = decide_research(
        prop.id,
        "rejected",
        proposals_path=props,
        env_path=env,
        slots_dir=slots,
        state_path=state,
        apply_env=True,
        sync_environ=False,
    )
    assert res["ok"] is True
    assert res["slot"] is None
    assert res["control_touched"] is False
    assert "ENABLE_S21=true" not in env.read_text(encoding="utf-8")
    st = load_state(state)
    assert "S21_AMISE" not in st.paper_approved


def test_ml_hides_research_and_refuses_pending(tmp_path: Path) -> None:
    props = tmp_path / "proposals.json"
    env = tmp_path / ".env"
    env.write_text("DRY_RUN=true\n", encoding="utf-8")
    p = add_proposal(
        StrategyProposal(
            id="lab1",
            week_id="lab-test",
            kind=RESEARCH_KIND,
            strategy=RESEARCH_STRATEGY,
            title="factory demo",
            summary="should not appear on ML",
            paper=PaperResult(n_trades=20, win_rate=0.5, after_tax_pnl_inr=10.0),
            safety_ok=True,
            env_patch={"DRY_RUN": "true"},
        ),
        path=props,
    )
    payload = ml_desk_payload(root=tmp_path, env_path=env, proposals_path=props)
    ids = [x.get("id") for x in (payload.get("proposals") or {}).get("pending") or []]
    assert p.id not in ids
    refused = decide_proposal_for_desk(
        p.id,
        "approved_paper",
        proposals_path=props,
        env_path=env,
        apply_env=True,
    )
    assert refused["ok"] is False
    assert refused.get("lab") is True
    still = get_proposal(p.id, path=props)
    assert still is not None and still.status == "pending"
    desk = research_desk_payload(proposals_path=props)
    assert any(x.get("id") == p.id for x in desk.get("pending") or [])


def test_not_wired_to_paper_or_live() -> None:
    root = Path(__file__).resolve().parent
    for n in [LAB_NAME, RESEARCH_STRATEGY]:
        assert n not in ALL_STRATEGY_NAMES
        assert n not in SLIM_PAPER_STRATEGIES
        assert n not in PAPER_ONLY_BOOKS
    station = (root / "station.html").read_text(encoding="utf-8")
    paper = station.split("const PAPER_BOOKS")[1].split("];")[0]
    assert "RESEARCH_FACTORY" not in paper
    assert "S21_AMISE" in paper
    runner = (root / "run_strategy.py").read_text(encoding="utf-8")
    portfolio = (root / "portfolio.py").read_text(encoding="utf-8")
    assert "ENABLE_RESEARCH" not in runner
    assert "research_factory" not in runner
    assert "ENABLE_RESEARCH" not in portfolio
    desk = (root / "research_desk.py").read_text(encoding="utf-8")
    assert "from research_factory" not in desk
    assert "import research_factory" not in desk
    genome_head = (root / "strategy_genome.py").read_text(encoding="utf-8").split("def params_from_genome")[0]
    assert "from flow_lab import" not in genome_head
    assert "S21_AMISE" in ALL_STRATEGY_NAMES
    assert "S24_AMISE" in SLIM_PAPER_STRATEGIES
    assert "S11_DISCOVERED" not in SLIM_PAPER_STRATEGIES


if __name__ == "__main__":
    from pathlib import Path as P
    import tempfile

    test_atoms_match_funcs()
    test_hh_hl_and_no_trade_spread()
    test_genome_compile_long_on_hh_hl_bull()
    test_mutate_and_combine()
    test_env_patch_never_enable()
    test_fast_lab_skips_heavy_ml()
    test_gates_reject_thin_and_champion_loss()
    td = P(tempfile.mkdtemp())
    for name in ("a", "b", "c", "d"):
        (td / name).mkdir()
    test_factory_rejects_short_tape(td / "a")
    test_proposal_and_lab_approve_assigns_s21(td / "b")
    test_lab_reject_does_not_assign(td / "d")
    test_ml_hides_research_and_refuses_pending(td / "c")
    test_not_wired_to_paper_or_live()
    print("ALL test_research_factory OK")
