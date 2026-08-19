#!/usr/bin/env python3
"""Backtest FLOW_BRAIN on ticks (LTP + TBQ + TSQ). Not S7_HOURLY. Not S16. Not live.

  python backtest_flow_brain.py --db data/ticks.db --lots 100 --fees
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

from backtest_hhhl_candles import count_ticks, make_charge_cfg, write_outputs
from flow_brain import BOOK, FORMULA, samples_from_tick_rows, simulate_flow_brain
from mtf_bars import load_tick_rows
from ohlcv_lab import after_charges_inr


def _ac_wr(result: Any) -> float:
    trades = list(getattr(result, "trades", []) or [])
    n = len(trades)
    if n == 0:
        return 0.0
    wins = sum(1 for t in trades if (t.gross_pnl_inr - t.fees_inr) > 0)
    return wins / n


def main() -> None:
    ap = argparse.ArgumentParser(description="FLOW_BRAIN tape (research, not live).")
    ap.add_argument("--db", type=Path, default=Path("data/ticks.db"))
    ap.add_argument("--lots", type=float, default=100.0)
    ap.add_argument("--fees", action="store_true")
    ap.add_argument("--out-dir", type=Path, default=Path("data/backtests/flow_brain"))
    args = ap.parse_args()

    if args.fees:
        os.environ["IGNORE_FEES"] = "false"

    print(BOOK)
    print(FORMULA)
    print()
    print("Pressure = 5s change in TBQ vs TSQ (cumulatives).")
    print("LONG  = price up + buy flow up + expanding")
    print("SHORT = price down + sell flow up + expanding")
    print("Skip absorption (flow without price). Exit on decay.")
    print("Not S7_HOURLY. Not S16. ENABLE_FLOW_BRAIN stays false. Stay DRY_RUN.")
    print()

    if not args.db.exists():
        raise SystemExit(f"missing db: {args.db}")
    n = count_ticks(args.db)
    if n == 0:
        raise SystemExit(
            f"empty ticks in {args.db}. Cloud ticks.db is often empty — "
            "copy the VM tape. Stay DRY_RUN."
        )
    print(f"ticks={n}  db={args.db}  lots={args.lots:g}  fees={args.fees}", flush=True)
    print("loading ticks...", flush=True)
    rows = load_tick_rows(args.db)
    samples = samples_from_tick_rows(rows)
    print(f"samples={len(samples)}", flush=True)
    cfg = make_charge_cfg(fees=bool(args.fees), lots=float(args.lots))
    result = simulate_flow_brain(
        samples,
        lots=float(args.lots),
        fees=bool(args.fees),
        session_filter=True,
        charge_cfg=cfg,
    )
    ac = after_charges_inr(result)
    wr = 100.0 * _ac_wr(result)
    print()
    print("=== after Angel charges, tax excluded ===")
    print(
        f"{BOOK}: trades={result.n_trades} L/S={result.n_long}/{result.n_short} "
        f"after_charges_win%={wr:.1f} gross₹={result.gross_pnl_inr:.1f} "
        f"fees₹={result.fees_inr:.1f} after_charges₹={ac:.1f} (tax excluded)"
    )
    print()
    write_outputs([result], args.out_dir)
    print(
        "Stay DRY_RUN. ENABLE_FLOW_BRAIN stays false. Do not live-unlock. "
        "This is not S7_HOURLY and not S16."
    )


if __name__ == "__main__":
    main()
