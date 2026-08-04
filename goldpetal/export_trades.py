#!/usr/bin/env python3
"""Export complete trade history (entry/exit prices, timestamps, net PnL).

Run anytime on the VM:
  cd ~/goldpetal && source .venv/bin/activate
  python export_trades.py

Writes:
  data/trades_s1.csv
  data/trades_s2.csv
  data/trades_all.csv
"""

from __future__ import annotations

import argparse
from pathlib import Path

from storage import build_trades, export_trades_csv


def _print_summary(label: str, trades: list[dict]) -> None:
    closed = [t for t in trades if str(t.get("status", "")).startswith("CLOSED")]
    open_n = sum(1 for t in trades if t.get("status") == "OPEN")
    pnl = 0.0
    for t in closed:
        if t.get("net_pnl") != "" and t.get("net_pnl") is not None:
            pnl += float(t["net_pnl"])
    print(f"\n=== {label} ===")
    print(f"trades={len(trades)} closed={len(closed)} open={open_n} net_pnl_sum={pnl:.2f}")
    if not trades:
        print("(no trades yet)")
        return
    print(
        "trade# | side | status | entry_ts | entry | exit_ts | exit | net_pnl | pct"
    )
    print("-" * 100)
    for t in trades[-30:]:  # last 30 for console
        print(
            f"{t.get('trade_no')} | {t.get('side')} | {t.get('status')} | "
            f"{t.get('entry_ts')} | {t.get('entry_price')} | "
            f"{t.get('exit_ts') or '-'} | {t.get('exit_price') or '-'} | "
            f"{t.get('net_pnl') if t.get('net_pnl') != '' else '-'} | "
            f"{t.get('net_pnl_pct') if t.get('net_pnl_pct') != '' else '-'}"
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
        help="Only one strategy: S1_NETDELTA or S2_BALANCE (default: all)",
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

    # Default: write S1, S2, and combined
    targets = [
        ("S1_NETDELTA", out_dir / "trades_s1.csv"),
        ("S2_BALANCE", out_dir / "trades_s2.csv"),
        (None, out_dir / "trades_all.csv"),
    ]
    for strat, path in targets:
        n, closed, pnl = export_trades_csv(path, strategy=strat)
        label = strat or "ALL"
        print(f"[{label}] {n} trades ({closed} closed, pnl={pnl:.2f}) -> {path}")

    if not args.quiet:
        _print_summary("S1_NETDELTA", build_trades(strategy="S1_NETDELTA"))
        _print_summary("S2_BALANCE", build_trades(strategy="S2_BALANCE"))
        _print_summary("ALL", build_trades(strategy=None))


if __name__ == "__main__":
    main()
