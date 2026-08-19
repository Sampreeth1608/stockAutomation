"""You-tab mimic learner — Lab proposal only. Never ENABLE / DRY_RUN=false."""

from __future__ import annotations

import tempfile
from pathlib import Path

from human_capture import save_examples
from proposals import load_proposals
from you_learn import build_mimic_genome, learn_status, propose_mimic


def _ex(action: str, *, imb: float, bull: bool) -> dict:
    return {
        "action": action,
        "snapshot": {
            "imb": imb,
            "depth_imb": imb,
            "vwap_gap": 4.0 if bull else -4.0,
            "ltp_velocity_30s": 0.2 if bull else -0.2,
            "spread": 1.0,
            "ltp": 15000.0,
            "last_1m": {
                "bull": bull,
                "bear": not bull,
                "hh": bull,
                "hl": bull,
                "hc": bull,
                "lh": not bull,
                "ll": not bull,
                "lc": not bull,
                "vol_up": True,
                "body_frac": 0.7,
                "upper_wick_frac": 0.1 if bull else 0.55,
                "lower_wick_frac": 0.55 if bull else 0.1,
                "n_ticks": 20,
            },
        },
        "outcomes": {"1m": {"taken_pts": 2.0 if action != "no_trade" else 0.0}},
    }


def test_not_ready_until_counts() -> None:
    st = learn_status([_ex("buy", imb=0.2, bull=True)])
    assert st["ready"] is False
    assert st["need_taken"] > 0
    assert st["need_skips"] > 0


def test_genome_from_clicks() -> None:
    buys = [_ex("buy", imb=0.3, bull=True) for _ in range(8)]
    skips = [_ex("no_trade", imb=-0.2, bull=False) for _ in range(6)]
    g = build_mimic_genome(buys + skips)
    assert g.timeframe == "1m"
    assert g.source == "you_capture"
    assert g.direction in {"long", "both"}
    assert "imb_buy" in g.entry_long or "bull" in g.entry_long or g.entry_long


def test_propose_writes_lab_row(tmp_path: Path) -> None:
    store = tmp_path / "examples.json"
    props = tmp_path / "proposals.json"
    mimic = tmp_path / "mimic.json"
    rows = [_ex("buy", imb=0.3, bull=True) for _ in range(8)]
    rows += [_ex("short", imb=-0.3, bull=False) for _ in range(8)]
    rows += [_ex("no_trade", imb=0.0, bull=True) for _ in range(6)]
    save_examples(rows, path=store)
    out = propose_mimic(examples_path=store, proposals_path=props, mimic_path=mimic)
    assert out["ok"] is True
    assert out["proposed"] is True
    assert "DRY_RUN" in (out.get("reminder") or "")
    items = load_proposals(path=props)
    assert items
    assert items[0].kind == "research"
    assert items[0].env_patch.get("DRY_RUN") == "true"
    extra = items[0].paper.extra
    assert extra.get("you_mimic") is True
    assert extra.get("genome", {}).get("timeframe") == "1m"
    assert mimic.is_file()


def test_thin_set_does_not_propose(tmp_path: Path) -> None:
    store = tmp_path / "examples.json"
    props = tmp_path / "proposals.json"
    save_examples([_ex("buy", imb=0.2, bull=True)], path=store)
    out = propose_mimic(examples_path=store, proposals_path=props, mimic_path=tmp_path / "m.json")
    assert out["ok"] is False
    assert out["proposed"] is False
    assert not load_proposals(path=props)


if __name__ == "__main__":
    test_not_ready_until_counts()
    test_genome_from_clicks()
    td = Path(tempfile.mkdtemp())
    (td / "a").mkdir()
    test_propose_writes_lab_row(td / "a")
    (td / "b").mkdir()
    test_thin_set_does_not_propose(td / "b")
    print("ALL test_you_learn OK")
