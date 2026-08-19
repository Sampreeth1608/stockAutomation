#!/usr/bin/env python3
"""Backtest nested inner-candle net → next-timeframe bias.

Research only. Not a paper book. Not live. Stay DRY_RUN.

  ./venv/bin/python backtest_nested_tf.py --db data/ticks.db --lots 100 --fees
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

from backtest_hhhl_candles import count_ticks, write_outputs
from mtf_bars import load_tick_rows
from nested_tf_net import (
    FORMULA,
    LAB_NAME,
    MODES,
    PARENTS,
    after_charges_inr,
    after_charges_win_rate,
    bars_from_ticks,
    inner_recipe_line,
    simulate_all,
    tf_label,
)


def _print_table(results: list[Any], title: str) -> None:
    print()
    print(f"=== {title} ===")
    print(
        f"{'row':>16}  {'bars':>6}  {'trades':>6}  {'L/S':>7}  "
        f"{'ac_win%':>7}  {'gross₹':>10}  {'fees₹':>10}  {'after_charges₹':>14}"
    )
    print("-" * 98)
    ranked = sorted(
        results,
        key=lambda r: (after_charges_inr(r), r.n_trades),
        reverse=True,
    )
    for r in ranked:
        n = r.n_trades
        wr = 100.0 * after_charges_win_rate(r)
        ac = after_charges_inr(r)
        print(
            f"{r.tf:>16}  {r.n_bars:6d}  {n:6d}  "
            f"{r.n_long:3d}/{r.n_short:<3d}  {wr:6.1f}%  "
            f"{r.gross_pnl_inr:10.1f}  {r.fees_inr:10.1f}  {ac:14.1f}"
        )
    print()
    print("Rank column is after_charges ₹ (tax excluded). wr% is last on purpose.")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Nested inner-candle net → next TF bias (research, not live)."
    )
    ap.add_argument("--db", type=Path, default=Path("data/ticks.db"))
    ap.add_argument("--lots", type=float, default=100.0)
    ap.add_argument("--fees", action="store_true", help="Angel fees; rank tax excluded")
    ap.add_argument(
        "--modes",
        default="sum,vol,tbq,tsq,book,vote",
        help="comma list: sum (body), vol (up-vol−down-vol), tbq, tsq, book (TBQ−TSQ), vote",
    )
    ap.add_argument(
        "--tfs",
        default="",
        help="comma list of parents e.g. 15m,1h,1d (default: all)",
    )
    ap.add_argument(
        "--out-dir",
        type=Path,
        default=Path("data/backtests/nested_tf_net"),
    )
    args = ap.parse_args()

    if args.fees:
        os.environ["IGNORE_FEES"] = "false"

    print(LAB_NAME)
    print(FORMULA)
    print()
    print("Modes (same inner stack; positive → long next parent, negative → short):")
    print("  sum   body net  Σ(close − open)")
    print("  vol   volume net  Σ(volume × sign(close − open))  = up-volume − down-volume")
    print("  tbq   TBQ net     Σ(TBQ × sign(close − open))")
    print("  tsq   TSQ net     Σ(TSQ × sign(close − open))")
    print("  book  snapshot    Σ(TBQ − TSQ) at each inner close")
    print("  vote  count       green inner bars − red inner bars")
    print()
    print("Inner recipe (session-aligned from 09:00 IST):")
    for label, minutes in PARENTS:
        print(f"  {label:>4}  {inner_recipe_line(minutes)}")
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
    print("loading ticks then building 1m bars (can take a few minutes)...", flush=True)

    modes = tuple(
        m.strip().lower()
        for m in str(args.modes).split(",")
        if m.strip()
    ) or MODES
    want = {
        t.strip().lower()
        for t in str(args.tfs).split(",")
        if t.strip()
    }
    parents = tuple(
        (label, minutes)
        for label, minutes in PARENTS
        if not want or label.lower() in want or tf_label(minutes) in want
    )
    if not parents:
        raise SystemExit("no parent timeframes matched --tfs")

    tick_rows = load_tick_rows(args.db)
    print(f"loaded {len(tick_rows)} rows", flush=True)
    bars_by_min = bars_from_ticks(tick_rows, progress=True)
    print("simulating nested nets...", flush=True)
    results = simulate_all(
        bars_by_min,
        lots=float(args.lots),
        fees=bool(args.fees),
        modes=modes,
        parents=parents,
    )
    _print_table(
        results,
        "nested inner net → next parent  (after charges, tax excluded)",
    )
    write_outputs(results, args.out_dir)
    print(
        "Stay DRY_RUN. Paper 100 lots is not live. NESTED_TF_NET is research-only: "
        "do not ENABLE_* and do not live-unlock from this tape."
    )


if __name__ == "__main__":
    main()
