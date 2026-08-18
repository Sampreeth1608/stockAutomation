#!/usr/bin/env python3
"""Count S17 close×high×body buckets, then settle hhhl/wick/and/or books.

Research only. Do not paper or live-enable until a 100-lot + fees row is picked.

  ./venv/bin/python backtest_s17_close_high_body.py --db data/ticks.db --session --lots 100 --fees
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from backtest_hhhl_candles import TIMEFRAMES, make_charge_cfg, print_by_day, write_outputs
from backtest_wick_candles import build_ohlc_candles, load_ltp_rows, print_wick_summary
from s17_close_high_body import (
    BOOK_MODES,
    FORMULA,
    LISTED_BUCKETS,
    MISSING_BUCKETS,
    USER_COLUMNS,
    BucketCount,
    bucket_kind,
    counts_as_dict,
    ordered_bucket_names,
    simulate_s17,
    tally_rows,
    walk_candles,
)

DEFAULT_TICK_TFS = ",".join(n for n, _ in TIMEFRAMES)


def _parse_books(raw: str) -> list[str]:
    out: list[str] = []
    for bit in (x.strip().lower() for x in raw.split(",") if x.strip()):
        if bit not in BOOK_MODES:
            raise SystemExit(f"unknown book {bit!r}. have: {', '.join(BOOK_MODES)}")
        if bit not in out:
            out.append(bit)
    return out


def _parse_tick_tfs(raw: str) -> list[tuple[str, int]]:
    by = {n: m for n, m in TIMEFRAMES}
    out: list[tuple[str, int]] = []
    for name in (x.strip().lower() for x in raw.split(",") if x.strip()):
        if name == "30":
            name = "30m"
        if name in {"d", "day", "daily"}:
            name = "1d"
        if name not in by:
            raise SystemExit(f"unknown tf {name!r}. have: {', '.join(by)}")
        out.append((name, by[name]))
    if not out:
        raise SystemExit("no timeframes")
    return out


def write_walk_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "time",
        "open",
        "high",
        "low",
        "close",
        "prev_close",
        "prev_high",
        "prev_low",
        "close_vs",
        "high_vs",
        "low_vs",
        "body",
        "bucket",
        "kind",
        "upper",
        "lower",
        "wick_gap",
        "wick",
        "hhhl",
        "agree",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def _print_completeness() -> None:
    print(FORMULA, flush=True)
    print("Your six columns:", flush=True)
    for col, close_s, high_s, body in USER_COLUMNS:
        print(f"  {col}.  {close_s}  {high_s}  {body}  →  {LISTED_BUCKETS[int(col) - 1]}", flush=True)
    print(
        "Same grid, not listed yet:  "
        + ", ".join(MISSING_BUCKETS)
        + "  | leftover: C=pC, H=pH, C=O",
        flush=True,
    )
    print("Leftover (C=pC / H=pH / doji) is skipped in every book.", flush=True)
    print(flush=True)


def print_bucket_table(tf: str, rows: list[dict[str, Any]], counts: dict[str, BucketCount]) -> None:
    n_all = len(rows) or 1
    print(f"=== {tf}  bars={len(rows)}  (session bars vs previous candle) ===")
    print(
        f"{'kind':<8}  {'bucket':<14}  {'n':>5}  {'%':>5}  "
        f"{'HL/LL/eq':>12}  {'wick L/S/—':>12}  {'hhhl L/S/—':>12}  "
        f"{'and':>4}  {'or':>4}  {'fight':>5}  {'Δ≥3/5/10':>11}"
    )
    print("-" * 122)
    for name in ordered_bucket_names(counts):
        b = counts.get(name) or BucketCount()
        if b.n == 0 and bucket_kind(name) == "leftover":
            continue
        pct = 100.0 * b.n / n_all
        print(
            f"{bucket_kind(name):<8}  {name:<14}  {b.n:5d}  {pct:4.1f}%  "
            f"{b.n_hl:3d}/{b.n_ll:<3d}/{b.n_low_eq:<3d}  "
            f"{b.n_wick_long:3d}/{b.n_wick_short:<3d}/{b.n_wick_none:<3d}  "
            f"{b.n_hhhl_long:3d}/{b.n_hhhl_short:<3d}/{b.n_hhhl_none:<3d}  "
            f"{b.n_agree:4d}  {b.n_or:4d}  {b.n_fight:5d}  "
            f"{b.n_gap_ge3:3d}/{b.n_gap_ge5:<3d}/{b.n_gap_ge10:<3d}"
        )
    listed_n = sum((counts.get(k) or BucketCount()).n for k in LISTED_BUCKETS)
    missing_n = sum((counts.get(k) or BucketCount()).n for k in MISSING_BUCKETS)
    leftover_n = len(rows) - listed_n - missing_n
    print(
        f"{'total':<8}  {'listed/miss/left':<14}  "
        f"{listed_n:5d}/{missing_n:<3d}/{leftover_n:<3d}   "
        f"and=HHHL∧wick  or=HHHL∨wick  Δ=|U−L|",
        flush=True,
    )
    print(flush=True)


def print_listed_matrix(summary: dict[str, Any]) -> None:
    print("=== Listed columns across TFs (n bars) ===")
    labels = [
        "1 up_hh_g",
        "2 up_hh_r",
        "3 dn_lh_g",
        "4 dn_lh_r",
        "5 dn_hh_g",
        "6 up_lh_r",
    ]
    hdr = f"{'tf':>5}  " + "  ".join(f"{lab:>10}" for lab in labels) + f"  {'miss':>5}  {'left':>5}"
    print(hdr)
    for tf, block in summary.items():
        listed = block["listed"]
        bits = [f"{listed[k]:10d}" for k in LISTED_BUCKETS]
        print(
            f"{tf:>5}  "
            + "  ".join(bits)
            + f"  {block['missing_n']:5d}  {block['leftover_n']:5d}"
        )
    print()


def print_bar_dump(tf: str, rows: list[dict[str, Any]], *, leftover: bool) -> None:
    shown = rows if leftover else [r for r in rows if r["kind"] != "leftover"]
    print(
        f"=== {tf} bars "
        + ("(all kinds)" if leftover else "(listed + missing; leftover hidden)")
        + " ===",
        flush=True,
    )
    for row in shown:
        print(
            f"{row['time']:<22} O={row['open']:.1f} H={row['high']:.1f} "
            f"L={row['low']:.1f} C={row['close']:.1f}  "
            f"{row['kind']:<8} {row['bucket']:<14}  "
            f"low={row['low_vs']:<3}  U={row['upper']:.1f} Lw={row['lower']:.1f}  "
            f"hhhl={row['hhhl']:<5} wick={row['wick']:<5} {row['agree']}",
            flush=True,
        )
    print(flush=True)


def run_from_ticks(
    db: Path,
    *,
    session_filter: bool,
    tfs: list[tuple[str, int]],
    drop_last: bool,
    out_dir: Path,
    print_bars_tf: str | None,
    print_leftover: bool,
    lots: float,
    fees: bool,
    books: list[str],
    min_wick_gap: float,
) -> dict[str, Any]:
    ltp = load_ltp_rows(db)
    if not ltp:
        raise SystemExit(f"no ticks in {db}")
    summary: dict[str, Any] = {}
    out_dir.mkdir(parents=True, exist_ok=True)
    cfg = make_charge_cfg(fees=fees, lots=lots)
    book_results = []
    for name, minutes in tfs:
        candles = build_ohlc_candles(ltp, minutes)
        if drop_last and len(candles) >= 2:
            candles = candles[:-1]
        use_session = session_filter and name != "1d"
        rows = walk_candles(candles, session_filter=use_session)
        counts = tally_rows(rows)
        print_bucket_table(name, rows, counts)
        write_walk_csv(out_dir / f"{name}_walk.csv", rows)
        if print_bars_tf == name:
            print_bar_dump(name, rows, leftover=print_leftover)
        listed_n = sum((counts.get(k) or BucketCount()).n for k in LISTED_BUCKETS)
        missing_n = sum((counts.get(k) or BucketCount()).n for k in MISSING_BUCKETS)
        summary[name] = {
            "n_bars": len(rows),
            "listed": {k: (counts.get(k) or BucketCount()).n for k in LISTED_BUCKETS},
            "missing": {k: (counts.get(k) or BucketCount()).n for k in MISSING_BUCKETS},
            "missing_n": missing_n,
            "leftover_n": len(rows) - listed_n - missing_n,
            "counts": counts_as_dict(counts),
        }
        for mode in books:
            r = simulate_s17(
                candles,
                tf=f"{name}:{mode}",
                mode=mode,
                lots=lots,
                fees=fees,
                session_filter=use_session,
                charge_cfg=cfg,
                min_wick_gap=min_wick_gap,
            )
            book_results.append(r)
    print_listed_matrix(summary)
    if book_results:
        print_wick_summary(
            book_results,
            title=(
                f"S17 books  lots={lots:g}  fees={fees}  wick_gap={min_wick_gap:g}"
            ),
        )
        day_rows = [r for r in book_results if r.tf.startswith(("30m:", "45m:", "1h:", "2h:", "3h:"))]
        print_by_day(day_rows or book_results)
        write_outputs(book_results, out_dir)
    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=Path("data/ticks.db"))
    ap.add_argument("--tf", default="")
    ap.add_argument("--session", action="store_true", default=True)
    ap.add_argument("--no-session", action="store_true")
    ap.add_argument("--keep-last", action="store_true")
    ap.add_argument("--print-bars", default="", help="print this TF's bars (or 30m)")
    ap.add_argument(
        "--print-leftover",
        action="store_true",
        help="include C=pC / H=pH / doji rows in the bar dump",
    )
    ap.add_argument("--lots", type=float, default=100.0)
    ap.add_argument("--fees", action="store_true", default=True)
    ap.add_argument("--no-fees", action="store_true")
    ap.add_argument(
        "--book",
        default="hhhl,wick,and,or",
        help="FLIP books to settle, or '' for counts only",
    )
    ap.add_argument("--wick-gap", type=float, default=3.0)
    ap.add_argument("--out", type=Path, default=Path("data/backtests/s17_close_high_body"))
    args = ap.parse_args()
    session_filter = bool(args.session) and not bool(args.no_session)
    fees = bool(args.fees) and not bool(args.no_fees)
    print_bars_tf = (args.print_bars or "").strip().lower() or None
    if print_bars_tf == "30":
        print_bars_tf = "30m"
    books = _parse_books(args.book) if (args.book or "").strip() else []

    _print_completeness()
    print(
        f"session={session_filter}  lots={args.lots:g}  fees={fees}  "
        f"books={','.join(books) or 'none'}  wick_gap={args.wick_gap:g}  "
        f"source={args.db}",
        flush=True,
    )
    tfs = _parse_tick_tfs(args.tf or DEFAULT_TICK_TFS)
    run_from_ticks(
        args.db,
        session_filter=session_filter,
        tfs=tfs,
        drop_last=not args.keep_last,
        out_dir=args.out,
        print_bars_tf=print_bars_tf,
        print_leftover=bool(args.print_leftover),
        lots=args.lots,
        fees=fees,
        books=books,
        min_wick_gap=float(args.wick_gap),
    )
    print(f"wrote {args.out}", flush=True)
    print("Not paper. Not live. Pick a TF:book row first.", flush=True)


if __name__ == "__main__":
    main()
