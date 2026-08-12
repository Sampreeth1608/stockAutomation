"""Print / export 30-min sheet-style trading log."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from storage import count_bars, export_sheet_csv, sheet_rows

IST = ZoneInfo("Asia/Kolkata")


def _fmt(value) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.2f}" if abs(value) < 1000 else f"{value:.0f}"
    return str(value)


def main() -> None:
    parser = argparse.ArgumentParser(description="Gold Petal sheet-style bar log")
    parser.add_argument("--latest", type=int, default=50, help="Show latest N rows")
    parser.add_argument(
        "--export",
        type=str,
        default="",
        help="Export CSV path (default auto file under data/ if --export auto)",
    )
    args = parser.parse_args()

    total = count_bars()
    print(f"Total 30-min bars in DB: {total}")
    if total == 0:
        print("No bars yet. Run during market: python run_strategy.py")
        return

    rows = sheet_rows(limit=args.latest)
    print(
        "TIME | CMP | PRICEΔ | BP | BPΔ | SP | SPΔ | BP-SP | NETΔ | STRAT | SIGNAL | POS"
    )
    print("-" * 120)
    for row in rows:
        print(
            f"{row['time']} | {_fmt(row['cmp'])} | {_fmt(row['price_delta'])} | "
            f"{_fmt(row['bp'])} | {_fmt(row['bp_delta'])} | "
            f"{_fmt(row['sp'])} | {_fmt(row['sp_delta'])} | "
            f"{_fmt(row['net'])} | {_fmt(row['net_delta'])} | "
            f"{row.get('strategy') or '-'} | {row['signal'] or '-'} | {row['position'] or '-'}"
        )
        if row["reason"]:
            print(f"  reason: {row['reason']}")

    export_path = args.export
    if export_path == "auto":
        stamp = datetime.now(IST).strftime("%Y%m%d_%H%M%S")
        export_path = f"data/sheet_log_{stamp}.csv"

    if export_path:
        path = Path(export_path)
        n = export_sheet_csv(path, limit=args.latest)
        print(f"\nExported {n} sheet rows to {path}")


if __name__ == "__main__":
    main()
