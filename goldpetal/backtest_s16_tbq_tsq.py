#!/usr/bin/env python3
"""Backtest S16 vs total_buy / total_sell quantity on ticks.db.

Research only. Does not change paper S16. Angel 1h candles have no TBQ/TSQ.

  cd ~/goldpetal-repo/goldpetal
  ./venv/bin/python backtest_s16_tbq_tsq.py --db data/ticks.db --lots 100 --session --fees
  ./venv/bin/python backtest_s16_tbq_tsq.py --db data/ticks.db --tf 1h --lots 100 --session --fees

Default: 1h (paper S16 tf) and 30m, wick gap 0 like paper.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Any

from backtest_hhhl_candles import TIMEFRAMES, make_charge_cfg, write_outputs
from backtest_wick_candles import print_wick_summary
from mtf_bars import build_rich_bars, load_tick_rows
from s16_hhhl_wick import simulate_s16, walk_candles
from s16_tbq_tsq import VARIANTS, candles_from_rich, variant_decide

DEFAULT_TFS = "1h,30m"
DEFAULT_VARIANTS = ",".join(VARIANTS)


def _parse_tfs(raw: str) -> list[tuple[str, int]]:
    by = {n: m for n, m in TIMEFRAMES}
    out: list[tuple[str, int]] = []
    for name in (x.strip().lower() for x in raw.split(",") if x.strip()):
        if name not in by:
            raise SystemExit(f"unknown tf {name!r}. have: {', '.join(by)}")
        out.append((name, by[name]))
    if not out:
        raise SystemExit("no timeframes")
    return out


def _parse_variants(raw: str) -> list[str]:
    out: list[str] = []
    for name in (x.strip() for x in raw.split(",") if x.strip()):
        if name not in VARIANTS:
            raise SystemExit(f"unknown variant {name!r}. have: {', '.join(VARIANTS)}")
        out.append(name)
    if not out:
        raise SystemExit("no variants")
    return out


def write_walk(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "time",
        "open",
        "high",
        "low",
        "close",
        "tbq_open",
        "tbq_close",
        "tsq_open",
        "tsq_close",
        "d_tbq",
        "d_tsq",
        "rule",
        "side",
        "action",
        "pos_before",
        "pos_after",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description="S16 vs TBQ/TSQ backtest (ticks only)")
    ap.add_argument("--db", default="data/ticks.db")
    ap.add_argument("--tf", default=DEFAULT_TFS)
    ap.add_argument("--variants", default=DEFAULT_VARIANTS)
    ap.add_argument("--lots", type=float, default=100.0)
    ap.add_argument("--session", action=argparse.BooleanOptionalAction, default=True)
    ap.add_argument("--fees", action=argparse.BooleanOptionalAction, default=True)
    ap.add_argument("--min-wick-gap", type=float, default=0.0)
    ap.add_argument("--out-dir", default="data/backtests/s16_tbq_tsq")
    args = ap.parse_args()

    db = Path(args.db)
    if not db.is_file():
        raise SystemExit(f"missing {db}")
    rows = load_tick_rows(db)
    if len(rows) < 50:
        raise SystemExit(f"{db} has {len(rows)} ticks — need the VM ticks.db")

    tfs = _parse_tfs(args.tf)
    names = _parse_variants(args.variants)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print()
    print("S16 + total_buy_quantity / total_sell_quantity  (research, not paper)")
    print(f"  db={db} ticks={len(rows)} lots={args.lots:g} session={args.session} fees={args.fees}")
    print(f"  wick_gap={args.min_wick_gap:g} (paper S16 uses 0)")
    for key, (label, _fn) in VARIANTS.items():
        if key in names:
            print(f"  {key:16} {label}")
    print()

    results = []
    baseline: dict[str, float] = {}
    for tf_name, minutes in tfs:
        bars = build_rich_bars(rows, tf_name, minutes)
        candles = candles_from_rich(bars)
        if len(candles) < 3:
            print(f"skip {tf_name}: {len(candles)} bars")
            continue
        for name in names:
            decide = variant_decide(name, min_wick_gap=args.min_wick_gap)
            res = simulate_s16(
                candles,
                tf=f"{tf_name}:{name}",
                lots=args.lots,
                fees=args.fees,
                session_filter=args.session,
                min_wick_gap=args.min_wick_gap,
                decide=decide,
            )
            results.append(res)
            if name == "s16":
                baseline[tf_name] = float(res.after_tax_pnl_inr)
            walk = walk_candles(candles, min_wick_gap=args.min_wick_gap, decide=decide)
            for row, c in zip(walk, candles[1:]):
                row["tbq_open"] = c.tbq_open
                row["tbq_close"] = c.tbq_close
                row["tsq_open"] = c.tsq_open
                row["tsq_close"] = c.tsq_close
                row["d_tbq"] = c.d_tbq
                row["d_tsq"] = c.d_tsq
            write_walk(out_dir / f"walk_{tf_name}_{name}.csv", walk)

    write_outputs(results, out_dir)

    print_wick_summary(results, title="S16 vs TBQ/TSQ")
    if baseline and results:
        print("Δ after-tax vs paper s16 on the same tf:")
        print(f"  {'row':>22}  {'pnl_₹':>10}  {'Δ vs s16':>10}")
        print("  " + "-" * 46)
        for r in results:
            tf, _, var = r.tf.partition(":")
            base = baseline.get(tf)
            delta = "" if base is None or var == "s16" else f"{r.after_tax_pnl_inr - base:+.1f}"
            print(f"  {r.tf:>22}  {r.after_tax_pnl_inr:10.1f}  {delta:>10}")
        print()
    print(f"wrote {out_dir}")
    print("Do not paper a qty variant until a 100-lot + fees row beats s16.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
