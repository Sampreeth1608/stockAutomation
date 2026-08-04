#!/usr/bin/env python3
"""Export complete trade history (entry/exit prices, timestamps, net PnL).

Run anytime on the VM:
  cd ~/goldpetal && source .venv/bin/activate
  python export_trades.py

Writes:
  data/trades_s1.csv
  data/trades_s2.csv
  data/trades_s3.csv
  data/trades_s4.csv
  data/trades_s5.csv
  data/trades_all.csv
"""

from __future__ import annotations

import argparse
from pathlib import Path

from storage import build_trades, export_trades_csv


def _print_summary(label: str, trades: list[dict]) -> None:
    closed = [t for t in trades if str(t.get("status", "")).startswith("CLOSED")]
    open_n = sum(1 for t in trades if t.get("status") == "OPEN")

    def _num(t: dict, key: str) -> float:
        v = t.get(key, "")
        if v == "" or v is None:
            return 0.0
        return float(v)

    gross = sum(_num(t, "gross_pnl") for t in closed)
    fees = sum(_num(t, "charges") for t in closed)
    tax = sum(_num(t, "tax") for t in closed)
    after_tax = sum(
        _num(t, "pnl_after_tax") if t.get("pnl_after_tax", "") != "" else _num(t, "net_pnl")
        for t in closed
    )
    print(f"\n=== {label} ===")
    print(
        f"trades={len(trades)} closed={len(closed)} open={open_n} "
        f"gross={gross:.2f} fees={fees:.2f} tax={tax:.2f} after_tax={after_tax:.2f}"
    )
    if not trades:
        print("(no trades yet)")
        return
    print(
        "trade# | side | status | entry | exit | gross | fees | tax | after_tax"
    )
    print("-" * 100)
    for t in trades[-30:]:
        print(
            f"{t.get('trade_no')} | {t.get('side')} | {t.get('status')} | "
            f"{t.get('entry_price')} | {t.get('exit_price') or '-'} | "
            f"{t.get('gross_pnl') if t.get('gross_pnl') != '' else '-'} | "
            f"{t.get('charges') if t.get('charges') != '' else '-'} | "
            f"{t.get('tax') if t.get('tax') != '' else '-'} | "
            f"{t.get('pnl_after_tax') if t.get('pnl_after_tax') != '' else t.get('net_pnl')}"
        )
    if len(trades) > 30:
        print(f"... ({len(trades) - 30} earlier trades in CSV)")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export Gold Petal trade journal with entry/exit/PnL"
    )
    parser.add_argument(
        "--out-dir",
        type=str,
        default="data",
        help="Directory for CSV files (default: data)",
    )
    parser.add_argument(
        "--strategy",
        type=str,
        default="",
        help="Only one strategy: S1..S5 name (default: all)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only write CSVs, minimal console output",
    )
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    strategy = args.strategy.strip() or None

    if strategy:
        path = out_dir / f"trades_{strategy.lower()}.csv"
        n, closed, pnl = export_trades_csv(path, strategy=strategy)
        print(f"Wrote {n} trades ({closed} closed, pnl={pnl:.2f}) -> {path}")
        if not args.quiet:
            _print_summary(strategy, build_trades(strategy=strategy))
        return

    # Default: write S1..S5 and combined
    targets = [
        ("S1_NETDELTA", out_dir / "trades_s1.csv"),
        ("S2_BALANCE", out_dir / "trades_s2.csv"),
        ("S3_ML", out_dir / "trades_s3.csv"),
        ("S4_OVERNIGHT", out_dir / "trades_s4.csv"),
        ("S5_MINEDGE", out_dir / "trades_s5.csv"),
        (None, out_dir / "trades_all.csv"),
    ]
    for strat, path in targets:
        n, closed, pnl = export_trades_csv(path, strategy=strat)
        label = strat or "ALL"
        print(f"[{label}] {n} trades ({closed} closed, pnl={pnl:.2f}) -> {path}")

    if not args.quiet:
        _print_summary("S1_NETDELTA", build_trades(strategy="S1_NETDELTA"))
        _print_summary("S2_BALANCE", build_trades(strategy="S2_BALANCE"))
        _print_summary("S3_ML", build_trades(strategy="S3_ML"))
        _print_summary("S4_OVERNIGHT", build_trades(strategy="S4_OVERNIGHT"))
        _print_summary("S5_MINEDGE", build_trades(strategy="S5_MINEDGE"))
        _print_summary("ALL", build_trades(strategy=None))


if __name__ == "__main__":
    main()
