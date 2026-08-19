#!/usr/bin/env python3
"""Backtest paper S16 vs S16 + same-hour TBQ/TSQ strength.

Research only. Does not rewrite paper S16. Stay DRY_RUN.

  python backtest_s16_tbq_net.py --db data/ticks.db --lots 100 --fees
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

from backtest_hhhl_candles import Candle, count_ticks, make_charge_cfg, write_outputs
from flow_lab import flow_bars_from_tick_rows
from mtf_bars import load_tick_rows
from ohlcv_lab import after_charges_inr
from s16_hhhl_wick import FORMULA as S16_FORMULA
from s16_hhhl_wick import simulate_s16
from s16_tbq_net import FORMULA, LAB_NAME, simulate_s16_tbq_net


def _ac_wr(result: Any) -> float:
    trades = list(getattr(result, "trades", []) or [])
    n = len(trades)
    if n == 0:
        return 0.0
    wins = sum(1 for t in trades if (t.gross_pnl_inr - t.fees_inr) > 0)
    return wins / n


def _line(label: str, result: Any) -> None:
    ac = after_charges_inr(result)
    n = result.n_trades
    wr = 100.0 * _ac_wr(result)
    print(
        f"{label}: trades={n} L/S={result.n_long}/{result.n_short} "
        f"after_charges_win%={wr:.1f} gross₹={result.gross_pnl_inr:.1f} "
        f"fees₹={result.fees_inr:.1f} after_charges₹={ac:.1f} (tax excluded)"
    )


def main() -> None:
    ap = argparse.ArgumentParser(
        description="S16 vs S16+TBQ/TSQ hour strength (research, not live)."
    )
    ap.add_argument("--db", type=Path, default=Path("data/ticks.db"))
    ap.add_argument("--lots", type=float, default=100.0)
    ap.add_argument("--fees", action="store_true")
    ap.add_argument("--minutes", type=int, default=60, help="bar size (S16 paper is 60)")
    ap.add_argument(
        "--out-dir",
        type=Path,
        default=Path("data/backtests/s16_tbq_net"),
    )
    args = ap.parse_args()

    if args.fees:
        os.environ["IGNORE_FEES"] = "false"

    print(LAB_NAME)
    print("Paper S16:", S16_FORMULA)
    print("Overlay:", FORMULA)
    print()
    print("Strong up  = hour C>O and TBQ>TSQ → keep / allow LONG")
    print("Weak up    = hour C>O and TBQ<TSQ → get out, no LONG")
    print("Strong down= hour C<O and TBQ<TSQ → keep / allow SHORT")
    print("Weak down  = hour C<O and TBQ>TSQ → get out, no SHORT")
    print("S16 still decides HH/LL vs wick. Weak hours flatten, they do not reverse.")
    print()

    if not args.db.exists():
        raise SystemExit(f"missing db: {args.db}")
    n = count_ticks(args.db)
    if n == 0:
        raise SystemExit(
            f"empty ticks in {args.db}. Cloud ticks.db is often empty — "
            "copy the VM tape. Stay DRY_RUN."
        )
    print(f"ticks={n}  db={args.db}  lots={args.lots:g}  fees={args.fees}  tf={args.minutes}m")
    print("building bars...", flush=True)
    tick_rows = load_tick_rows(args.db)
    bars = flow_bars_from_tick_rows(
        tick_rows,
        int(args.minutes),
        session_align=False,
        session_ticks=True,
        split_token=True,
    )
    candles = [
        Candle(b.time, b.open, b.high, b.low, b.close) for b in bars
    ]
    print(f"bars={len(bars)}", flush=True)
    cfg = make_charge_cfg(fees=bool(args.fees), lots=float(args.lots))
    plain = simulate_s16(
        candles,
        tf=f"{args.minutes}m:s16",
        lots=float(args.lots),
        fees=bool(args.fees),
        session_filter=True,
        min_wick_gap=0.0,
        charge_cfg=cfg,
    )
    overlay = simulate_s16_tbq_net(
        bars,
        tf=f"{args.minutes}m:s16_tbq",
        lots=float(args.lots),
        fees=bool(args.fees),
        session_filter=True,
        min_wick_gap=0.0,
        charge_cfg=cfg,
    )
    print()
    print("=== after Angel charges, tax excluded (paper S16 gap=0) ===")
    _line("S16 plain          ", plain)
    _line("S16 + TBQ/TSQ hour ", overlay)
    d = after_charges_inr(overlay) - after_charges_inr(plain)
    print(f"delta overlay − plain after_charges₹={d:.1f}")
    print()
    write_outputs([plain, overlay], args.out_dir)
    print(
        "Stay DRY_RUN. Paper S16 is unchanged. Do not ENABLE a new book and "
        "do not live-unlock from this tape."
    )


if __name__ == "__main__":
    main()
