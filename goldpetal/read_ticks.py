"""Read stored Gold Petal ticks and strategy signals."""

from __future__ import annotations

import argparse
from pathlib import Path

from storage import (
    count_ticks,
    export_csv,
    export_signals_csv,
    latest_signals,
    latest_ticks,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Read stored Gold Petal ticks/signals")
    parser.add_argument("--latest", type=int, default=20, help="Show latest N ticks")
    parser.add_argument("--signals", type=int, default=0, help="Show latest N signals")
    parser.add_argument(
        "--strategy",
        type=str,
        default="",
        help="Filter signals: S1_NETDELTA or S2_BALANCE",
    )
    parser.add_argument(
        "--export",
        type=str,
        default="",
        help="Export ticks CSV path, e.g. data/goldpetal_ticks.csv",
    )
    parser.add_argument(
        "--export-signals",
        type=str,
        default="",
        help="Export signals CSV path, e.g. data/s1_signals.csv",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional export row limit",
    )
    parser.add_argument(
        "--no-ticks",
        action="store_true",
        help="Do not print tick rows",
    )
    args = parser.parse_args()

    total = count_ticks()
    print(f"Total ticks in DB: {total}")
    strategy = args.strategy.strip() or None

    if args.signals:
        rows = latest_signals(limit=args.signals, strategy=strategy)
        if not rows:
            print("No signals yet for that filter.")
        else:
            label = strategy or "ALL"
            print(f"\nLatest {len(rows)} signals [{label}]:")
            for row in rows:
                print(
                    f"{row['time_label']} | {row['strategy']} | {row['action']} | "
                    f"pos={row['position_after']} "
                    f"| net={row['net']} netΔ={row['net_delta']} "
                    f"| {row['reason']}"
                )

    if args.export_signals:
        path = Path(args.export_signals)
        n = export_signals_csv(path, strategy=strategy, limit=args.limit)
        print(f"\nExported {n} signals to {path}")

    if args.no_ticks and not args.export:
        return

    if not args.no_ticks:
        rows = latest_ticks(limit=args.latest)
        if not rows:
            print("No ticks yet. Run: python collect_ticks.py  or  python run_strategy.py")
        else:
            print(f"\nLatest {len(rows)} ticks:")
            print("received_at | symbol | ltp | bp | sp | volume")
            print("-" * 70)
            for row in rows:
                print(
                    f"{row['received_at']} | {row['symbol']} | {row['ltp']} | "
                    f"{row['bp']} | {row['sp']} | {row['volume']}"
                )

    if args.export:
        path = Path(args.export)
        n = export_csv(path, limit=args.limit)
        print(f"\nExported {n} tick rows to {path}")


if __name__ == "__main__":
    main()
