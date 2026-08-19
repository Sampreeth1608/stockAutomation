#!/usr/bin/env python3
"""Backtest FLOW_BRAIN gate packs on ticks (LTP + TBQ + TSQ).

Not S7_HOURLY. Not S16. Not live.

  python backtest_flow_brain.py --db data/ticks.db --lots 100 --fees
  python backtest_flow_brain.py --synthetic --lots 1,100 --fees
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
    scratchy_then_trend_samples,
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


def _lot_sizes(raw: str) -> list[float]:
    out: list[float] = []
    for bit in str(raw or "100").split(","):
        bit = bit.strip()
        if not bit:
            continue
        out.append(float(bit))
    if not out:
        raise SystemExit("need at least one --lots value")
    return out


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


def _run_packs(
    samples: list[Any],
    *,
    packs: list[str],
    lots: float,
    fees: bool,
    session_filter: bool,
) -> list[dict[str, Any]]:
    cfg = make_charge_cfg(fees=bool(fees), lots=float(lots))
    rows_out: list[dict[str, Any]] = []
    print(f"--- lots={lots:g} fees={fees} session_filter={session_filter} ---", flush=True)
    for name in packs:
        print(f"sim {name}...", flush=True)
        result = simulate_flow_brain(
            samples,
            lots=float(lots),
            fees=bool(fees),
            session_filter=session_filter,
            charge_cfg=cfg,
            gates=GATE_PACKS[name],
        )
        rec = _row(name, result)
        rec["lots"] = lots
        rows_out.append(rec)
        print(
            f"  {name}: trades={rec['trades']} L/S={rec['n_long']}/{rec['n_short']} "
            f"after_charges_win%={rec['win_pct']:.1f} gross₹={rec['gross']:.1f} "
            f"fees₹={rec['fees']:.1f} after_charges₹={rec['after_charges']:.1f}",
            flush=True,
        )
        if rec["trades"] and rec["trades"] <= 12:
            for tr in result.trades:
                print(
                    f"    {tr.side} {tr.entry_time} -> {tr.exit_time} "
                    f"{tr.entry_px:.1f}->{tr.exit_px:.1f} "
                    f"gross₹={tr.gross_pnl_inr:.1f} fees₹={tr.fees_inr:.1f}",
                    flush=True,
                )
    return rows_out


def _print_table(rows_out: list[dict[str, Any]], lots: float) -> None:
    print()
    print(f"=== lots={lots:g} after Angel charges, tax excluded ===")
    print(
        f"{'pack':<12} {'trades':>7} {'L/S':>9} {'win%':>7} "
        f"{'gross₹':>12} {'fees₹':>12} {'after_charges₹':>16}"
    )
    print("-" * 80)
    for rec in rows_out:
        print(
            f"{rec['pack']:<12} {rec['trades']:7d} "
            f"{rec['n_long']:4d}/{rec['n_short']:<4d} {rec['win_pct']:6.1f}% "
            f"{rec['gross']:12.1f} {rec['fees']:12.1f} {rec['after_charges']:16.1f}"
        )
    print()
    by_ac = sorted(rows_out, key=lambda r: r["after_charges"], reverse=True)
    print(
        f"best after_charges: {by_ac[0]['pack']} "
        f"₹{by_ac[0]['after_charges']:.1f}  win%={by_ac[0]['win_pct']:.1f}  "
        f"trades={by_ac[0]['trades']}"
    )


def _print_lot_compare(by_lots: dict[float, list[dict[str, Any]]]) -> None:
    if 1.0 not in by_lots or 100.0 not in by_lots:
        return
    one = {r["pack"]: r for r in by_lots[1.0]}
    hun = {r["pack"]: r for r in by_lots[100.0]}
    print("=== 1 lot vs 100 lots (same toy tape, same fills) ===")
    print(
        f"{'pack':<12} {'t':>4} {'ac₹_1':>10} {'ac₹_1×100':>12} "
        f"{'gross₹_100':>12} {'fees₹_100':>10} {'ac₹_100':>12}"
    )
    print("-" * 86)
    for pack in GATE_PACK_ORDER:
        if pack not in one or pack not in hun:
            continue
        a = one[pack]
        b = hun[pack]
        print(
            f"{pack:<12} {b['trades']:4d} {a['after_charges']:10.1f} "
            f"{a['after_charges'] * 100:12.1f} {b['gross']:12.1f} "
            f"{b['fees']:10.1f} {b['after_charges']:12.1f}"
        )
    print()
    print(
        "Gross scales with lots. After-charges does not: 1-lot fee floor is "
        "~₹48/RT, 100-lot is ~₹308/RT, not ×100. Same fills: ₹165 at 1 lot "
        "is not ₹16,500 at 100 lots."
    )
    print()


def main() -> None:
    ap = argparse.ArgumentParser(description="FLOW_BRAIN tape (research, not live).")
    ap.add_argument("--db", type=Path, default=Path("data/ticks.db"))
    ap.add_argument(
        "--lots",
        default="100",
        help="lot size, or comma list (e.g. 1,100) to compare",
    )
    ap.add_argument("--fees", action="store_true")
    ap.add_argument("--out-dir", type=Path, default=Path("data/backtests/flow_brain"))
    ap.add_argument(
        "--pack",
        default="all",
        help="v1, confirm10, c10_hold, hold_opp, quality, desk, desk_nonet, desk_nohtf, or all",
    )
    ap.add_argument(
        "--synthetic",
        action="store_true",
        help="toy chop→trend tape (the +₹165 / 1-lot desk row). No ticks.db.",
    )
    args = ap.parse_args()

    if args.fees:
        os.environ["IGNORE_FEES"] = "false"

    packs = _pack_names(args.pack)
    lot_sizes = _lot_sizes(args.lots)

    print(BOOK)
    print(FORMULA)
    print()
    print("Pressure = 5s change in TBQ vs TSQ (cumulatives).")
    print("LONG  = price up + buy flow up + expanding")
    print("SHORT = price down + sell flow up + expanding")
    print("Skip absorption (flow without price).")
    print("Packs: v1, confirm10, c10_hold, hold_opp, quality, desk, desk_nonet, desk_nohtf.")
    print("Not S7_HOURLY. Not S16. ENABLE_FLOW_BRAIN stays false. Stay DRY_RUN.")
    print()

    session_filter = True
    if args.synthetic:
        samples = scratchy_then_trend_samples()
        session_filter = False
        print(
            f"synthetic chop→trend samples={len(samples)}  "
            f"lots={','.join(str(x).rstrip('0').rstrip('.') if '.' in str(x) else str(x) for x in lot_sizes)}  "
            f"fees={args.fees}",
            flush=True,
        )
        print("session_filter=False (same as the unit-test +₹165 row).", flush=True)
    else:
        if not args.db.exists():
            raise SystemExit(f"missing db: {args.db}")
        n = count_ticks(args.db)
        if n == 0:
            raise SystemExit(
                f"empty ticks in {args.db}. Cloud ticks.db is often empty — "
                "copy the VM tape. Stay DRY_RUN."
            )
        print(f"ticks={n}  db={args.db}  lots={args.lots}  fees={args.fees}", flush=True)
        print(f"packs={','.join(packs)}", flush=True)
        print("loading ticks...", flush=True)
        rows = load_tick_rows(args.db)
        samples = samples_from_tick_rows(rows)
        print(f"samples={len(samples)}", flush=True)

    print(f"packs={','.join(packs)}", flush=True)
    by_lots: dict[float, list[dict[str, Any]]] = {}
    all_results = []
    for lots in lot_sizes:
        rows_out = _run_packs(
            samples,
            packs=packs,
            lots=lots,
            fees=bool(args.fees),
            session_filter=session_filter,
        )
        by_lots[lots] = rows_out
        all_results.extend(r["result"] for r in rows_out)
        _print_table(rows_out, lots)

    _print_lot_compare(by_lots)
    write_outputs(all_results, args.out_dir)
    last = by_lots[lot_sizes[-1]]
    thin = [r for r in last if 0 < r["trades"] < 20]
    if thin:
        names = ", ".join(f"{r['pack']}={r['trades']}t" for r in thin)
        print(f"thin sample (n<20): {names}. Not a go.")
    print(
        "Stay DRY_RUN. ENABLE_FLOW_BRAIN stays false. Do not live-unlock. "
        "This is not S7_HOURLY and not S16."
    )


if __name__ == "__main__":
    main()
