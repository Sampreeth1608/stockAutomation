"""Read stored Gold Petal ticks."""

from __future__ import annotations

import argparse
from pathlib import Path

from storage import count_ticks, export_csv, latest_ticks


def main() -> None:
    parser = argparse.ArgumentParser(description="Read stored Gold Petal ticks")
    parser.add_argument("--latest", type=int, default=20, help="Show latest N ticks")
    parser.add_argument(
        "--export",
        type=str,
        default="",
        help="Export CSV path, e.g. data/goldpetal_ticks.csv",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional export row limit",
    )
    args = parser.parse_args()

    total = count_ticks()
    print(f"Total ticks in DB: {total}")

    rows = latest_ticks(limit=args.latest)
    if not rows:
        print("No ticks yet. Run: python collect_ticks.py")
        return

    print(f"\nLatest {len(rows)} ticks:")
    print("received_at | symbol | ltp | volume")
    print("-" * 60)
    for row in rows:
        print(
            f"{row['received_at']} | {row['symbol']} | {row['ltp']} | {row['volume']}"
        )

    if args.export:
        path = Path(args.export)
        n = export_csv(path, limit=args.limit)
        print(f"\nExported {n} rows to {path}")


if __name__ == "__main__":
    main()
