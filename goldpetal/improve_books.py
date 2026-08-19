#!/usr/bin/env python3
"""Improve every live paper book over time. Never auto-deploy.

Closed trades train the hour/side entry gate for every book that is on
the desk, including AMISE slots after Lab Approve. S18 searches packs.
Filled AMISE chairs mutate their own genome and, if the tape is stronger
after charges, write a Lab pending row that overwrites that same slot.

Never rewrites S13/S16 formulas. Never ENABLE. Never DRY_RUN=false.
You still Approve pack/genome changes.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from amise_slots import (
    allocated_slots,
    is_amise_slot,
    load_slot_genome,
    short_amise,
)
from control_state import paper_strategy_names
from strategy_genome import RESEARCH_KIND, mutate_genome

IST = ZoneInfo("Asia/Kolkata")
ROOT = Path(__file__).resolve().parent
AMISE_DIR = ROOT / "data" / "amise"
LAST_IMPROVE = AMISE_DIR / "improve.json"

FORMULA_LOCKED = frozenset({"S13_HHHL_DAY", "S16_HHHL_WICK_1H"})
S5_NAME = "S5_MINEDGE"
S8_NAME = "S8_NET_ZIGZAG"
S18_NAME = "S18_OHLC_VOL_HTF"

# Mutation must beat the genome already sitting in the chair.
IMPROVE_SELF_MULT = 1.05
MUTATE_SCALES = (-0.15, -0.10, -0.05, 0.05, 0.10, 0.15)


def _now_iso() -> str:
    return datetime.now(IST).isoformat(timespec="seconds")


def _write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _self_bar(current_ac: float) -> float:
    if current_ac <= 0:
        return 0.01
    return float(current_ac) * IMPROVE_SELF_MULT


def book_improve_plan(name: str, *, filled: bool | None = None) -> dict[str, Any]:
    """How this book is allowed to get better. Formulas for S13/S16 stay."""
    if name in FORMULA_LOCKED:
        return {
            "book": name,
            "mode": "hour_gate",
            "approve": "none",
            "note": "Champion — closed-trade hour gate only. Factory must beat this book. Formulas stay.",
        }
    if is_amise_slot(name):
        on = bool(filled) if filled is not None else load_slot_genome(name) is not None
        return {
            "book": name,
            "mode": "amise_mutate" if on else "empty",
            "approve": "Lab",
            "note": (
                f"Mutate {short_amise(name)} in place after Lab Approve of a stronger genome."
                if on
                else "Empty chair — Approve a new factory challenger first."
            ),
        }
    if name == S18_NAME:
        return {
            "book": name,
            "mode": "s18_pack",
            "approve": "ML tab",
            "note": "Pack search on the tape. Approve on ML copies active.json.",
        }
    if name == S5_NAME:
        return {
            "book": name,
            "mode": "s5_ml",
            "approve": "ML tab",
            "note": "Weekly logistic improve (also on full AMISE improve). Hour gate always.",
        }
    if name == S8_NAME:
        return {
            "book": name,
            "mode": "s8_nn_weekly",
            "approve": "ML tab",
            "note": "Hour gate always. Deep NN stays ./weekly_s8_nn.sh (too heavy for auto-lab).",
        }
    return {
        "book": name,
        "mode": "hour_gate",
        "approve": "none",
        "note": "Closed-trade hour/side gate (EDGE_ML). No formula rewrite.",
    }


def improve_roster(*, slots_folder: Path | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    filled = set(allocated_slots(slots_folder))
    for name in paper_strategy_names():
        row = book_improve_plan(
            name, filled=(name in filled) if is_amise_slot(name) else None
        )
        rows.append(row)
    return rows


def improve_desk_payload(*, slots_folder: Path | None = None) -> dict[str, Any]:
    last = {}
    if LAST_IMPROVE.is_file():
        try:
            raw = json.loads(LAST_IMPROVE.read_text(encoding="utf-8"))
            last = raw if isinstance(raw, dict) else {}
        except (OSError, json.JSONDecodeError):
            last = {}
    return {
        "ok": True,
        "roster": improve_roster(slots_folder=slots_folder),
        "last_run_at": last.get("updated_at_ist") or "",
        "last": {
            "proposed_ids": last.get("proposed_ids") or [],
            "s18_proposed": bool((last.get("s18") or {}).get("proposed")),
            "amise": last.get("amise") or [],
            "hour_gate": last.get("hour_gate") or {},
        },
        "note": (
            "Every paper book, including new S21+ after you Approve, keeps learning "
            "from closed trades (hour gate). AMISE filled chairs can propose a same-slot "
            "genome improve. S18 packs go to the ML tab. S13/S16 formulas never change. "
            "Nothing ENABLES or sets DRY_RUN=false."
        ),
    }


def _closed_trades(strategy: str, db: Path | None) -> list[dict[str, Any]]:
    try:
        from charges import paper_lots
        from storage import build_trades

        kwargs: dict[str, Any] = {
            "strategy": strategy,
            "signal_limit": 1200,
            "lot_size": paper_lots(),
        }
        if db is not None:
            kwargs["db_path"] = db
        rows = build_trades(**kwargs)
        return [
            t
            for t in rows
            if str(t.get("status") or "").startswith("CLOSED")
        ]
    except Exception:
        return []


def _fit_hour_gates(db: Path | None) -> dict[str, Any]:
    try:
        from trade_learner import get_learner, refresh_learn_strategies

        refresh_learn_strategies()
        lr = get_learner()
        lr.fit_from_db(db)
        return {
            "ok": True,
            "n": int(lr.n),
            "note": lr.note,
            "books": sorted(lr.by_book.keys()),
        }
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _run_s18(
    db: Path,
    *,
    lots: float,
    fees: bool,
    propose: bool,
    proposals_path: Path | None,
) -> dict[str, Any]:
    from learn_s18 import learn

    out_dir = ROOT / "data" / "learn" / "s18"
    kwargs: dict[str, Any] = dict(
        lots=float(lots),
        fees=bool(fees),
        session=True,
        train_frac=0.7,
        min_test_trades=2,
        out_dir=out_dir,
        pack_path=out_dir / "active.json",
        propose=bool(propose),
    )
    if proposals_path is not None:
        kwargs["proposals_path"] = proposals_path
    try:
        payload = learn(db, **kwargs)
    except Exception as exc:
        return {"ok": False, "proposed": False, "error": f"{type(exc).__name__}: {exc}"}
    return {
        "ok": True,
        "proposed": bool(payload.get("proposed")),
        "proposal_id": payload.get("proposal_id") or "",
        "note": payload.get("note") or "",
        "pack": (payload.get("pack") or {}).get("name"),
    }


def _run_s5(db: Path) -> dict[str, Any]:
    """Logistic S5 weekly. Writes an ML pending row. Never ENABLE."""
    try:
        from evolve_s5_ml import cmd_train
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    ns = argparse.Namespace(
        db=str(db),
        tf=5,
        horizon=6,
        req_pts=50.0,
        model_dir=str(ROOT / "data" / "models"),
        model_path=str(ROOT / "data" / "models" / "s5_minedge_ml.joblib"),
        budget_note="amise-improve",
    )
    try:
        code = int(cmd_train(ns) or 0)
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return {"ok": code == 0, "note": "S5 ML proposal written if the sample was strong enough."}


def improve_amise_slot(
    bars: list[Any],
    slot: str,
    *,
    s16: Any,
    s18: Any,
    lots: float,
    fees: bool,
    n_folds: int,
    strong: bool,
    propose: bool,
    proposals_path: Path | None,
    week_id: str,
    slots_folder: Path | None,
    holdout_bars: list[Any] | None,
    closed_n: int = 0,
) -> dict[str, Any]:
    from research_factory import (
        evaluate_challenger,
        proposal_from_challenger,
        run_genome_book,
    )
    from proposals import add_proposal

    current = load_slot_genome(slot, slots_folder)
    if current is None:
        return {
            "slot": slot,
            "status": "empty",
            "proposed": False,
            "note": "no genome in this chair yet",
        }
    parent = run_genome_book(
        bars, current, lots=lots, fees=fees, n_folds=0, skip_wf=True, tf="improve-parent"
    )
    parent_ac = float(parent.after_charges)
    bar = _self_bar(parent_ac)
    best: dict[str, Any] | None = None
    best_ac = bar
    tried: list[dict[str, Any]] = []
    for scale in MUTATE_SCALES:
        g = mutate_genome(current, scale=scale)
        quick = run_genome_book(
            bars, g, lots=lots, fees=fees, n_folds=0, skip_wf=True, tf="improve-screen"
        )
        q_ac = float(quick.after_charges)
        if q_ac < bar:
            tried.append(
                {
                    "scale": scale,
                    "screen_ac": round(q_ac, 2),
                    "skip": "not ahead of current genome",
                }
            )
            continue
        row = evaluate_challenger(
            bars,
            g,
            s16=s16,
            s18=s18,
            lots=lots,
            fees=fees,
            n_folds=n_folds,
            robustness=True,
            strong=strong,
            holdout_bars=holdout_bars,
        )
        m_ac = float((row.get("metrics") or {}).get("after_charges") or 0.0)
        fails = list(row.get("fails") or [])
        if m_ac < bar:
            fails.append(
                f"does not beat current {slot} (need ≥ ₹{bar:.1f}, have ₹{m_ac:.1f})"
            )
            row["fails"] = fails
            row["proposed"] = False
            row["supervisor"] = (
                f"Rejected improve {slot}: still behind the genome in the chair."
            )
        row.pop("_metrics", None)
        row.pop("_genome", None)
        row.pop("_rob", None)
        tried.append(
            {
                "scale": scale,
                "after_charges": round(m_ac, 2),
                "proposed": bool(row.get("proposed")),
                "fails": fails[:4],
            }
        )
        if row.get("proposed") and m_ac > best_ac:
            best = row
            best_ac = m_ac
    out: dict[str, Any] = {
        "slot": slot,
        "status": "kept",
        "proposed": False,
        "proposal_id": "",
        "parent_after_charges": round(parent_ac, 2),
        "closed_n": int(closed_n),
        "tried": tried,
        "note": (
            f"{short_amise(slot)} kept current genome "
            f"(AC₹={parent_ac:.1f}, closed={closed_n})."
        ),
    }
    if best is None:
        return out
    stamp = f"{week_id}:improve:{slot}"
    if propose:
        prop = proposal_from_challenger(
            best,
            week_id=stamp,
            target_slot=slot,
            parent_after_charges=parent_ac,
        )
        if proposals_path is not None:
            saved = add_proposal(prop, path=proposals_path)
        else:
            saved = add_proposal(prop)
        out["proposal_id"] = saved.id
    out["status"] = "proposed"
    out["proposed"] = True
    out["genome"] = best.get("genome")
    out["metrics"] = best.get("metrics")
    out["note"] = (
        f"Proposed same-slot improve for {short_amise(slot)} "
        f"AC₹={best_ac:.1f} vs current ₹{parent_ac:.1f}. Approve on Lab. "
        "Does not ENABLE a new chair."
    )
    return out


def run_improve(
    bars: list[Any] | None,
    *,
    db: Path | None = None,
    lots: float = 100.0,
    fees: bool = True,
    n_folds: int = 3,
    strong: bool = True,
    propose: bool = False,
    full: bool = False,
    proposals_path: Path | None = None,
    slots_folder: Path | None = None,
    library_path: Path | None = None,
    skip_s18: bool = False,
    skip_amise: bool = False,
    out_path: Path | None = None,
) -> dict[str, Any]:
    """Improve pass. ``propose=True`` writes pending rows only."""
    from research_factory import champion_s16, champion_s18, holdout_split

    del library_path  # reserved; factory library stays the factory's
    hour_gate = _fit_hour_gates(db)
    s18_row: dict[str, Any] = {"ok": True, "proposed": False, "skipped": True}
    if not skip_s18 and db is not None:
        s18_row = _run_s18(
            db,
            lots=lots,
            fees=fees,
            propose=propose,
            proposals_path=proposals_path,
        )
    s5_row: dict[str, Any] = {"ok": True, "skipped": True}
    if full and db is not None:
        s5_row = _run_s5(db)
    amise_rows: list[dict[str, Any]] = []
    proposed_ids: list[str] = []
    if s18_row.get("proposal_id"):
        proposed_ids.append(str(s18_row["proposal_id"]))
    stamp = datetime.now(IST).strftime("improve-%Y%m%d")
    if not skip_amise:
        tape = list(bars or [])
        slots = allocated_slots(slots_folder)
        if tape and slots:
            _train, hold = holdout_split(tape)
            s16 = champion_s16(tape, lots=lots, fees=fees)
            s18m = champion_s18(tape, lots=lots, fees=fees)
            for slot in slots:
                closed_n = len(_closed_trades(slot, db)) if db is not None else 0
                row = improve_amise_slot(
                    tape,
                    slot,
                    s16=s16,
                    s18=s18m,
                    lots=lots,
                    fees=fees,
                    n_folds=n_folds,
                    strong=strong,
                    propose=propose,
                    proposals_path=proposals_path,
                    week_id=stamp,
                    slots_folder=slots_folder,
                    holdout_bars=hold,
                    closed_n=closed_n,
                )
                amise_rows.append(row)
                if row.get("proposal_id"):
                    proposed_ids.append(str(row["proposal_id"]))
        elif not slots:
            amise_rows.append(
                {
                    "slot": "",
                    "status": "none",
                    "proposed": False,
                    "note": "No filled AMISE chairs yet. Approve a factory challenger first.",
                }
            )
        else:
            amise_rows.append(
                {
                    "slot": "",
                    "status": "no_tape",
                    "proposed": False,
                    "note": "Need 1h bars to score AMISE genome mutations.",
                }
            )
    payload = {
        "updated_at_ist": _now_iso(),
        "kind": RESEARCH_KIND,
        "paper": True,
        "live": False,
        "enable": False,
        "dry_run_required": True,
        "roster": improve_roster(slots_folder=slots_folder),
        "hour_gate": hour_gate,
        "s18": s18_row,
        "s5": s5_row,
        "s8": {
            "skipped": True,
            "note": "Deep NN stays ./weekly_s8_nn.sh. Hour gate still fits S8 closed trades.",
        },
        "amise": amise_rows,
        "proposed_ids": proposed_ids,
        "formula_locked": sorted(FORMULA_LOCKED),
        "note": (
            "Improve pass stored. Hour gate uses closed trades for every paper book "
            "(including new S21+). AMISE mutations overwrite the same chair after "
            "Lab Approve. S18 packs go to the ML tab. S13/S16 formulas unchanged. "
            "Not ENABLE. Keep DRY_RUN=true."
        ),
    }
    _write_json(out_path or LAST_IMPROVE, payload)
    return payload


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=Path("data/ticks.db"))
    ap.add_argument("--propose", action="store_true")
    ap.add_argument("--lots", type=float, default=100.0)
    ap.add_argument("--fees", action="store_true", default=True)
    ap.add_argument("--no-fees", action="store_true")
    ap.add_argument("--full", action="store_true", help="also run S5 logistic")
    ap.add_argument("--no-s18", action="store_true")
    ap.add_argument("--no-amise", action="store_true")
    args = ap.parse_args()
    fees = bool(args.fees) and not bool(args.no_fees)
    hours: list[Any] = []
    try:
        from backtest_flow_lab import _load_bars
        from flow_lab import session_bars

        ns = argparse.Namespace(
            db=str(args.db),
            csv=None,
            csv_30m=None,
            ticks=None,
            upstox_json=None,
            upstox_json_30m=None,
            cache_csv=None,
            minutes=60,
        )
        hours, _m30, _source = _load_bars(ns)
        hours = session_bars(hours)
    except Exception as exc:
        print(f"tape load skipped: {type(exc).__name__}: {exc}")
    print("Improve books — closed-trade hour gate + AMISE same-slot + S18 packs.")
    print("S13/S16 formulas stay. You Approve. Never DRY_RUN=false.", flush=True)
    payload = run_improve(
        hours,
        db=args.db,
        lots=float(args.lots),
        fees=fees,
        propose=bool(args.propose),
        full=bool(args.full),
        skip_s18=bool(args.no_s18),
        skip_amise=bool(args.no_amise),
    )
    print(f"hour_gate n={((payload.get('hour_gate') or {}).get('n'))}")
    s18 = payload.get("s18") or {}
    print(f"s18 proposed={s18.get('proposed')} id={s18.get('proposal_id') or '-'}")
    for row in payload.get("amise") or []:
        print(
            f"  {row.get('slot') or '(none)'}: {row.get('status')} "
            f"proposed={row.get('proposed')} {row.get('note')}"
        )
    print(f"wrote {LAST_IMPROVE}")
    print("Approve AMISE improves on Lab (same chair). Approve S18 on ML. Keep DRY_RUN=true.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
