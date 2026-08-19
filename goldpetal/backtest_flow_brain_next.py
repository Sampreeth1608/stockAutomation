#!/usr/bin/env python3
"""FLOW_BRAIN next-move lab: states → 5s/30s/1m/5m after charges.

Not a live model. Not S7_HOURLY. Not S16. ENABLE_FLOW_BRAIN stays false.

  python backtest_flow_brain_next.py --synthetic --lots 100 --fees
  python backtest_flow_brain_next.py --db data/ticks.db --lots 100 --fees
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from backtest_hhhl_candles import count_ticks
from flow_brain import BOOK, FORMULA
from flow_brain_next import (
    NOT_EVERY_SITUATION,
    TRANSFORMER_NOTE,
    format_lab,
    rich_from_tick_rows,
    run_lab,
    synthetic_ticks,
    write_learn_db,
)
from mtf_bars import load_tick_rows


def main() -> None:
    ap = argparse.ArgumentParser(
        description="FLOW_BRAIN what-happens-next lab (research, not live)."
    )
    ap.add_argument("--db", type=Path, default=Path("data/ticks.db"))
    ap.add_argument("--lots", type=float, default=100.0)
    ap.add_argument("--fees", action="store_true")
    ap.add_argument(
        "--out-dir",
        type=Path,
        default=Path("data/backtests/flow_brain_next"),
    )
    ap.add_argument(
        "--synthetic",
        action="store_true",
        help="toy chop→trend tape. No ticks.db.",
    )
    ap.add_argument("--no-mood", action="store_true")
    ap.add_argument("--no-models", action="store_true")
    ap.add_argument(
        "--model-horizon",
        type=int,
        default=60,
        help="seconds ahead for logreg/RF/GB (default 1m)",
    )
    args = ap.parse_args()

    if args.fees:
        os.environ["IGNORE_FEES"] = "false"

    print(BOOK)
    print(FORMULA)
    print()
    print(
        "Lab: probability surface + labels + 100-tick sequence features + "
        "mood/depth/VWAP/OI/1m candles + walk-forward + logreg/RF/GB."
    )
    print(TRANSFORMER_NOTE)
    print(NOT_EVERY_SITUATION)
    print("ENABLE_FLOW_BRAIN stays false. Stay DRY_RUN. Not S7_HOURLY.")
    print()

    session_filter = True
    if args.synthetic:
        ticks = synthetic_ticks()
        session_filter = False
        print(
            f"synthetic ticks={len(ticks)} lots={args.lots} fees={args.fees}",
            flush=True,
        )
    else:
        if not args.db.exists():
            raise SystemExit(f"missing db: {args.db}")
        n = count_ticks(args.db)
        if n == 0:
            raise SystemExit(
                f"empty ticks in {args.db}. Cloud ticks.db is often empty — "
                "copy the VM tape. Stay DRY_RUN."
            )
        print(f"ticks={n} db={args.db} lots={args.lots} fees={args.fees}", flush=True)
        print("loading ticks...", flush=True)
        rows = load_tick_rows(args.db)
        ticks = rich_from_tick_rows(rows)

    print("labeling states + forward 5s/30s/1m/5m...", flush=True)
    report = run_lab(
        ticks,
        lots=float(args.lots),
        fees=bool(args.fees),
        session_filter=session_filter,
        with_mood=not args.no_mood,
        with_models=not args.no_models,
        model_horizon=int(args.model_horizon),
    )
    text = format_lab(report)
    print(text)
    print()

    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        k: report[k]
        for k in (
            "book",
            "lab",
            "n_ticks",
            "n_labels",
            "lots",
            "fees",
            "fee_be_pts",
            "edges",
            "has_edge",
            "note",
            "transformer",
            "not_every",
            "models",
            "mood_1m",
        )
    }
    packed = {}
    for pack, by_hz in report["tables"].items():
        packed[pack] = {}
        for hz, block in by_hz.items():
            packed[pack][str(hz)] = {
                "wf": block["wf"],
                "cells": {
                    st: {
                        k: v
                        for k, v in cell.items()
                        if k != "pnls"
                    }
                    for st, cell in block["cells"].items()
                },
            }
    summary["tables"] = packed
    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8"
    )
    (out_dir / "report.txt").write_text(text + "\n", encoding="utf-8")
    db_path = out_dir / "learn.db"
    write_learn_db(
        db_path,
        report["labels"],
        models=report["models"],
        has_edge=bool(report["has_edge"]),
        edge_states=list(report["edges"]),
    )
    print(f"Wrote {out_dir / 'summary.json'} and {db_path}")
    if report["n_labels"] < 80:
        print("thin sample. Not a go.")
    print(
        "Stay DRY_RUN. ENABLE_FLOW_BRAIN stays false. Do not live-unlock. "
        "This is not S7_HOURLY and not S16."
    )


if __name__ == "__main__":
    main()
