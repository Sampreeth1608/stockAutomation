"""Learn a mimic genome from You-tab captures. Learning ≠ deploy.

Fits simple tape relationships (TBQ/TSQ, VWAP, 1m OHLC, depth) to what
you actually clicked. Writes a pending Lab proposal. Approve names the
next AMISE slot in paper. Never ENABLE from here. Never DRY_RUN=false.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from human_capture import EXAMPLES_PATH, capture_summary, load_examples
from proposals import PaperResult, StrategyProposal, add_proposal
from strategy_genome import (
    RESEARCH_KIND,
    RESEARCH_STRATEGY,
    StrategyGenome,
    env_patch_is_safe,
    research_env_patch,
)

IST = ZoneInfo("Asia/Kolkata")
ROOT = Path(__file__).resolve().parent
MIMIC_PATH = ROOT / "data" / "human_capture" / "mimic.json"
MIN_TAKEN = 8
MIN_SKIPS = 4
MIN_TOTAL = 16
LIFT_TAKE = 0.55
LIFT_SKIP = 0.45

# Snapshot flags → research atoms (1m bar + tape).
LONG_FLAGS: tuple[tuple[str, str], ...] = (
    ("bull", "bull"),
    ("hh", "hh"),
    ("hl", "hl"),
    ("hc", "hc"),
    ("vol_up", "vol_up"),
    ("lower_wick", "lower_wick"),
    ("close_high", "close_high"),
    ("px_gt_vwap", "px_gt_vwap"),
    ("imb_buy", "imb_buy"),
    ("depth_buy", "depth_buy"),
    ("mom_up", "mom_up"),
)
SHORT_FLAGS: tuple[tuple[str, str], ...] = (
    ("bear", "bear"),
    ("lh", "lh"),
    ("ll", "ll"),
    ("lc", "lc"),
    ("vol_up", "vol_up"),
    ("upper_wick", "upper_wick"),
    ("close_low", "close_low"),
    ("px_lt_vwap", "px_lt_vwap"),
    ("imb_sell", "imb_sell"),
    ("depth_sell", "depth_sell"),
    ("mom_dn", "mom_dn"),
)
SKIP_FLAGS: tuple[tuple[str, str], ...] = (
    ("spread_wide", "spread_wide"),
    ("rvol_extreme", "rvol_extreme"),
)


def _now_iso() -> str:
    return datetime.now(IST).isoformat(timespec="seconds")


def _bar(ex: dict[str, Any]) -> dict[str, Any]:
    snap = ex.get("snapshot") or {}
    return dict(snap.get("last_1m") or snap.get("last_5m") or snap.get("last_1h") or {})


def example_flags(ex: dict[str, Any]) -> dict[str, bool]:
    """What the tape looked like when you clicked. Not an order."""
    snap = dict(ex.get("snapshot") or {})
    b = _bar(ex)
    imb = float(snap.get("imb") or 0.0)
    dimb = float(snap.get("depth_imb") or 0.0)
    gap = snap.get("vwap_gap")
    spread = snap.get("spread")
    ltp = snap.get("ltp")
    vel = float(snap.get("ltp_velocity_30s") or 0.0)
    body = float(b.get("body_frac") or 0.0)
    upper = float(b.get("upper_wick_frac") or 0.0)
    lower = float(b.get("lower_wick_frac") or 0.0)
    n_ticks = float(b.get("n_ticks") or 0.0)
    flags = {
        "bull": bool(b.get("bull")),
        "bear": bool(b.get("bear")),
        "hh": bool(b.get("hh")),
        "hl": bool(b.get("hl")),
        "hc": bool(b.get("hc")),
        "lh": bool(b.get("lh")),
        "ll": bool(b.get("ll")),
        "lc": bool(b.get("lc")),
        "vol_up": bool(b.get("vol_up")),
        "oi_up": bool(b.get("oi_up")),
        "lower_wick": lower >= 0.40,
        "upper_wick": upper >= 0.40,
        "close_high": bool(b.get("bull") and body >= 0.45 and upper <= 0.25),
        "close_low": bool(b.get("bear") and body >= 0.45 and lower <= 0.25),
        "px_gt_vwap": gap is not None and float(gap) > 0,
        "px_lt_vwap": gap is not None and float(gap) < 0,
        "imb_buy": imb >= 0.08,
        "imb_sell": imb <= -0.08,
        "depth_buy": dimb >= 0.08,
        "depth_sell": dimb <= -0.08,
        "mom_up": vel > 0,
        "mom_dn": vel < 0,
        "spread_wide": (
            spread is not None
            and ltp is not None
            and float(ltp) > 0
            and (float(spread) / float(ltp)) >= 0.0008
        ),
        "rvol_extreme": n_ticks >= 400,
    }
    return flags


def _rate(rows: list[dict[str, Any]], flag: str) -> float:
    if not rows:
        return 0.0
    hits = 0
    for r in rows:
        if example_flags(r).get(flag):
            hits += 1
    return hits / float(len(rows))


def pick_atoms(
    taken: list[dict[str, Any]],
    skipped: list[dict[str, Any]],
    pairs: tuple[tuple[str, str], ...],
    *,
    limit: int = 4,
) -> list[str]:
    scored: list[tuple[float, str]] = []
    for flag, atom in pairs:
        t = _rate(taken, flag)
        s = _rate(skipped, flag) if skipped else 0.0
        if t < LIFT_TAKE:
            continue
        if skipped and s > LIFT_SKIP and t - s < 0.12:
            continue
        scored.append((t - s, atom))
    scored.sort(key=lambda x: (-x[0], x[1]))
    out: list[str] = []
    for _, atom in scored:
        if atom not in out:
            out.append(atom)
        if len(out) >= limit:
            break
    return out


def learn_status(items: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = items if items is not None else load_examples()
    buys = [r for r in rows if r.get("action") == "buy"]
    shorts = [r for r in rows if r.get("action") == "short"]
    skips = [r for r in rows if r.get("action") == "no_trade"]
    taken = buys + shorts
    n = len(rows)
    need_taken = max(0, MIN_TAKEN - len(taken))
    need_skips = max(0, MIN_SKIPS - len(skips))
    need_total = max(0, MIN_TOTAL - n)
    ready = need_taken == 0 and need_skips == 0 and need_total == 0
    summary = capture_summary(rows)
    return {
        "n": n,
        "n_buy": len(buys),
        "n_short": len(shorts),
        "n_no_trade": len(skips),
        "n_taken": len(taken),
        "min_taken": MIN_TAKEN,
        "min_skips": MIN_SKIPS,
        "min_total": MIN_TOTAL,
        "need_taken": need_taken,
        "need_skips": need_skips,
        "need_total": need_total,
        "ready": ready,
        "win_rate_1m": summary.get("win_rate_1m"),
        "note": (
            "Ready to propose a 1m mimic on Lab."
            if ready
            else (
                f"Need {need_taken} more BUY/SHORT, {need_skips} more NO TRADE "
                f"(and {need_total} total clicks). NO TRADE is the filter."
            )
        ),
    }


def build_mimic_genome(items: list[dict[str, Any]]) -> StrategyGenome:
    buys = [r for r in items if r.get("action") == "buy"]
    shorts = [r for r in items if r.get("action") == "short"]
    skips = [r for r in items if r.get("action") == "no_trade"]
    long_e = pick_atoms(buys, skips, LONG_FLAGS) if buys else []
    short_e = pick_atoms(shorts, skips, SHORT_FLAGS) if shorts else []
    if not long_e and buys:
        long_e = ["bull", "imb_buy"]
    if not short_e and shorts:
        short_e = ["bear", "imb_sell"]
    no_trade = ["spread_wide", "rvol_extreme"]
    for flag, atom in SKIP_FLAGS:
        if _rate(skips, flag) >= 0.35 and atom not in no_trade:
            no_trade.append(atom)
    if buys and not shorts:
        direction = "long"
        short_e = []
    elif shorts and not buys:
        direction = "short"
        long_e = []
    else:
        direction = "both"
    return StrategyGenome(
        name="you mimic 1m TBQ/TSQ",
        direction=direction,
        timeframe="1m",
        entry_long=tuple(long_e),
        entry_short=tuple(short_e),
        no_trade=tuple(no_trade),
        exit="flip_eod",
        params={"imb_th": 0.08, "depth_th": 0.08},
        source="you_capture",
        recipe="you_mimic",
        notes=(
            "Inferred from You-tab clicks (LTP, TBQ, TSQ, 1m candles, book). "
            "Not a rewrite of S13/S16. Lab Approve → next AMISE paper slot."
        ),
    ).normalized()


def propose_mimic(
    *,
    examples_path: Path = EXAMPLES_PATH,
    proposals_path: Path | None = None,
    mimic_path: Path | None = None,
) -> dict[str, Any]:
    """Write a pending Lab row. Does not ENABLE. Does not arm Angel."""
    items = load_examples(examples_path)
    status = learn_status(items)
    if not status["ready"]:
        return {"ok": False, "error": status["note"], "learn": status, "proposed": False}
    genome = build_mimic_genome(items)
    summary = capture_summary(items)
    wr = float(summary.get("win_rate_1m") or 0.0)
    n_taken = int(summary.get("n_taken") or 0)
    patch = research_env_patch()
    if not env_patch_is_safe(patch):
        raise RuntimeError("you-mimic env_patch must stay DRY_RUN=true")
    extra = {
        "genome": genome.to_dict(),
        "lab": "RESEARCH_FACTORY",
        "paper_next": True,
        "live": False,
        "enable": False,
        "you_mimic": True,
        "n_examples": status["n"],
        "n_buy": status["n_buy"],
        "n_short": status["n_short"],
        "n_no_trade": status["n_no_trade"],
        "vs_coded": summary.get("vs_coded") or {},
    }
    prop = StrategyProposal(
        id="",
        week_id="you-mimic",
        kind=RESEARCH_KIND,
        strategy=RESEARCH_STRATEGY,
        title="You mimic (1m TBQ/TSQ/LTP)",
        summary=(
            f"{n_taken} of your clicks → {genome.timeframe} "
            f"long={' AND '.join(genome.entry_long) or '—'} "
            f"short={' AND '.join(genome.entry_short) or '—'}. "
            "Approve on Lab for paper. Keep DRY_RUN=true."
        ),
        paper=PaperResult(
            n_trades=n_taken,
            win_rate=wr,
            extra=extra,
        ),
        model_path="data/human_capture/examples.json",
        safety_ok=n_taken >= 20 and wr >= 0.52,
        safety_reasons=[
            "human mimic from You-tab tape (not factory 3-fold gates)",
            "Lab Approve still required",
            "never DRY_RUN=false from You tab",
        ],
        status="pending",
        env_patch=patch,
    )
    kwargs: dict[str, Any] = {}
    if proposals_path is not None:
        kwargs["path"] = proposals_path
    saved = add_proposal(prop, **kwargs)
    dest = mimic_path if mimic_path is not None else MIMIC_PATH
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        json.dumps(
            {
                "updated_at_ist": _now_iso(),
                "genome": genome.to_dict(),
                "learn": status,
                "proposal_id": saved.id,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "ok": True,
        "proposed": True,
        "proposal_id": saved.id,
        "genome": genome.to_dict(),
        "learn": status,
        "reminder": (
            "Pending on Lab. Approve names the next AMISE paper slot. "
            "Restart the bot after Approve. Keep DRY_RUN=true until you type LIVE yourself."
        ),
    }
