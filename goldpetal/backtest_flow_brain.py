#!/usr/bin/env python3
"""Backtest FLOW_BRAIN gate packs on ticks (LTP + TBQ + TSQ).

Not S7_HOURLY. Not S16. Not live.

  python backtest_flow_brain.py --db data/ticks.db --lots 100 --fees
  python backtest_flow_brain.py --db data/ticks.db --lots 100 --fees --pack desk
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

from backtest_hhhl_candles import count_ticks, make_charge_cfg, write_outputs
from flow_brain import (
    BOOK,
    FORMULA,
    GATE_PACK_ORDER,
    GATE_PACKS,
    after_charges_win_rate,
    samples_from_tick_rows,
    simulate_flow_brain,
)
from mtf_bars import load_tick_rows
from ohlcv_lab import after_charges_inr


def _pack_names(raw: str) -> list[str]:
    text = (raw or "all").strip().lower()
    if text in {"all", "*"}:
        return list(GATE_PACK_ORDER)
    names = [p.strip() for p in text.split(",") if p.strip()]
    bad = [n for n in names if n not in GATE_PACKS]
    if bad:
        known = ", ".join(GATE_PACK_ORDER)
        raise SystemExit(f"unknown pack(s) {bad}. known: {known}")
    return names


def _row(name: str, result: Any) -> dict[str, Any]:
    ac = after_charges_inr(result)
    wr = 100.0 * after_charges_win_rate(result)
    return {
        "pack": name,
        "result": result,
        "trades": result.n_trades,
        "n_long": result.n_long,
        "n_short": result.n_short,
        "win_pct": wr,
        "gross": result.gross_pnl_inr,
        "fees": result.fees_inr,
        "after_charges": ac,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="FLOW_BRAIN tape (research, not live).")
    ap.add_argument("--db", type=Path, default=Path("data/ticks.db"))
    ap.add_argument("--lots", type=float, default=100.0)
    ap.add_argument("--fees", action="store_true")
    ap.add_argument("--out-dir", type=Path, default=Path("data/backtests/flow_brain"))
    ap.add_argument(
        "--pack",
        default="all",
        help="v1, confirm10, hold_opp, quality, desk, or all (default all)",
    )
    args = ap.parse_args()

    if args.fees:
        os.environ["IGNORE_FEES"] = "false"

    packs = _pack_names(args.pack)

    print(BOOK)
    print(FORMULA)
    print()
    print("Pressure = 5s change in TBQ vs TSQ (cumulatives).")
    print("LONG  = price up + buy flow up + expanding")
    print("SHORT = price down + sell flow up + expanding")
    print("Skip absorption (flow without price).")
    print("Packs add confirm / min-hold / no-flip / persist / quality / desk.")
    print("desk = confirm10 + no-flip + S8 net + HTF agree-if-set + S5 25/10.")
    print("548k tape: v1 7.1% −₹30L; confirm10 22.4% −₹25k; old desk 0 trades.")
    print("v1 is the 8k-trade unfiltered tape. Rank after charges, tax excluded.")
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
    print(f"packs={','.join(packs)}", flush=True)
    print("loading ticks...", flush=True)
    rows = load_tick_rows(args.db)
    samples = samples_from_tick_rows(rows)
    print(f"samples={len(samples)}", flush=True)
    cfg = make_charge_cfg(fees=bool(args.fees), lots=float(args.lots))

    rows_out: list[dict[str, Any]] = []
    results = []
    for name in packs:
        print(f"sim {name}...", flush=True)
        result = simulate_flow_brain(
            samples,
            lots=float(args.lots),
            fees=bool(args.fees),
            session_filter=True,
            charge_cfg=cfg,
            gates=GATE_PACKS[name],
        )
        rec = _row(name, result)
        rows_out.append(rec)
        results.append(result)
        print(
            f"  {name}: trades={rec['trades']} L/S={rec['n_long']}/{rec['n_short']} "
            f"after_charges_win%={rec['win_pct']:.1f} gross₹={rec['gross']:.1f} "
            f"fees₹={rec['fees']:.1f} after_charges₹={rec['after_charges']:.1f}",
            flush=True,
        )

    print()
    print("=== after Angel charges, tax excluded ===")
    print(
        f"{'pack':<10} {'trades':>7} {'L/S':>9} {'win%':>7} "
        f"{'gross₹':>12} {'fees₹':>12} {'after_charges₹':>16}"
    )
    print("-" * 78)
    for rec in rows_out:
        print(
            f"{rec['pack']:<10} {rec['trades']:7d} "
            f"{rec['n_long']:4d}/{rec['n_short']:<4d} {rec['win_pct']:6.1f}% "
            f"{rec['gross']:12.1f} {rec['fees']:12.1f} {rec['after_charges']:16.1f}"
        )
    print()
    by_ac = sorted(rows_out, key=lambda r: r["after_charges"], reverse=True)
    by_wr = sorted(rows_out, key=lambda r: (r["win_pct"], r["after_charges"]), reverse=True)
    print(
        f"best after_charges: {by_ac[0]['pack']} "
        f"₹{by_ac[0]['after_charges']:.1f}  win%={by_ac[0]['win_pct']:.1f}  "
        f"trades={by_ac[0]['trades']}"
    )
    print(
        f"best after_charges_win%: {by_wr[0]['pack']} "
        f"{by_wr[0]['win_pct']:.1f}%  after_charges₹={by_wr[0]['after_charges']:.1f}  "
        f"trades={by_wr[0]['trades']}"
    )
    v1 = next((r for r in rows_out if r["pack"] == "v1"), None)
    if v1 is not None:
        print(
            f"v1 baseline: trades={v1['trades']} win%={v1['win_pct']:.1f} "
            f"after_charges₹={v1['after_charges']:.1f}"
        )
        for rec in rows_out:
            if rec["pack"] == "v1":
                continue
            d_wr = rec["win_pct"] - v1["win_pct"]
            d_ac = rec["after_charges"] - v1["after_charges"]
            print(
                f"  {rec['pack']} vs v1: win% {d_wr:+.1f}pp  "
                f"after_charges ₹{d_ac:+.1f}  trades {rec['trades'] - v1['trades']:+d}"
            )
    print()
    write_outputs(results, args.out_dir)
    print(
        "Stay DRY_RUN. ENABLE_FLOW_BRAIN stays false. Do not live-unlock. "
        "This is not S7_HOURLY and not S16. 3 trades at 100% that still "
        "lose after fees is not a go."
    )


if __name__ == "__main__":
    main()
