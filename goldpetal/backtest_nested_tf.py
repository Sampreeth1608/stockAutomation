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
    worksheet_rows,
)


def _is_per_inner(name: str) -> bool:
    return str(name).count(":") == 2


def _mode_of(name: str) -> str:
    parts = str(name).split(":")
    return parts[1] if len(parts) > 1 else ""


def _print_table(results: list[Any], title: str) -> None:
    print()
    print(f"=== {title} ===")
    print(
        f"{'row':>18}  {'bars':>6}  {'trades':>6}  {'L/S':>7}  "
        f"{'ac_win%':>7}  {'gross₹':>10}  {'fees₹':>10}  {'after_charges₹':>14}"
    )
    print("-" * 102)
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
            f"{r.tf:>18}  {r.n_bars:6d}  {n:6d}  "
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
        "--no-per-inner",
        action="store_true",
        help="only mixed-inner rows (15m:vol), skip 15m:vol:5m style rows",
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
    print("Modes (positive → long next parent, negative → short):")
    print("  sum   body net  Σ(close − open)")
    print("  vol   volume net  red inner volume is negative, green is positive")
    print("  tbq   TBQ net     same sign stack")
    print("  tsq   TSQ net     same sign stack")
    print("  book  snapshot    Σ(TBQ − TSQ) at each inner close")
    print("  vote  count       green inner bars − red inner bars")
    print()
    print("Example — 3×5m inside one 15m:")
    print("  1st 5m  C−O=−8  vol=595  signed vol=−595")
    print("  2nd 5m  C−O=−5  vol=489  signed vol=−489")
    print("  3rd 5m  C−O=+5  vol=317  signed vol=+317")
    print("  15m volume total 1401; volume net −767 → next 15m DOWN")
    print("  Row 15m:vol:5m is that net. Row 15m:vol mixes 1m+3m+5m.")
    print()
    print("Same worksheet on every parent (signed inner volume → next parent):")
    for line in worksheet_rows():
        print(f"  {line}")
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
        per_inner=not args.no_per_inner,
    )
    vol_inner = [
        r
        for r in results
        if _is_per_inner(r.tf) and _mode_of(r.tf) == "vol"
    ]
    body_inner = [
        r
        for r in results
        if _is_per_inner(r.tf) and _mode_of(r.tf) == "sum"
    ]
    tbq_inner = [
        r
        for r in results
        if _is_per_inner(r.tf) and _mode_of(r.tf) == "tbq"
    ]
    tsq_inner = [
        r
        for r in results
        if _is_per_inner(r.tf) and _mode_of(r.tf) == "tsq"
    ]
    if vol_inner:
        _print_table(
            vol_inner,
            "signed VOLUME net of inners → next parent  (your 1401 vs −767 rule, all TFs)",
        )
    if body_inner:
        _print_table(
            body_inner,
            "signed BODY net of inners → next parent  (C−O, all TFs)",
        )
    if tbq_inner:
        _print_table(
            tbq_inner,
            "signed TBQ net of inners → next parent  (same rule, all TFs)",
        )
    if tsq_inner:
        _print_table(
            tsq_inner,
            "signed TSQ net of inners → next parent  (same rule, all TFs)",
        )
    _print_table(
        results,
        "all rows  (mixed + per-inner; after charges, tax excluded)",
    )
    write_outputs(results, args.out_dir)
    print(
        "Stay DRY_RUN. Paper 100 lots is not live. NESTED_TF_NET is research-only: "
        "do not ENABLE_* and do not live-unlock from this tape."
    )


if __name__ == "__main__":
    main()
