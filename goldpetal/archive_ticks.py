#!/usr/bin/env python3
"""Archive Gold Petal ticks day-by-day for training / replay.

Writes:
  data/archive/YYYY-MM-DD/full_ticks.csv
  data/archive/YYYY-MM-DD/meta.json

Examples:
  python archive_ticks.py                  # archive all days present in DB
  python archive_ticks.py --date 2026-08-04
  python archive_ticks.py --today
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from export_full_ticks import HEADERS, row_from_tick
from storage import DB_PATH, connect, init_db

IST = ZoneInfo("Asia/Kolkata")
ARCHIVE_ROOT = Path(__file__).resolve().parent / "data" / "archive"


def _day_key(received_at: str | None) -> str | None:
    if not received_at:
        return None
    try:
        dt = datetime.fromisoformat(received_at)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=IST)
        return dt.astimezone(IST).strftime("%Y-%m-%d")
    except ValueError:
        return received_at[:10] if len(received_at) >= 10 else None


def archive_days(
    *,
    date: str | None = None,
    out_root: Path = ARCHIVE_ROOT,
    db_path: Path = DB_PATH,
) -> dict[str, int]:
    init_db(db_path)
    with connect(db_path) as conn:
        rows = list(
            conn.execute(
                """
                SELECT received_at, exchange_timestamp, raw_json
                FROM ticks
                ORDER BY id ASC
                """
            )
        )

    by_day: dict[str, list] = defaultdict(list)
    for row in rows:
        day = _day_key(row["received_at"])
        if day is None:
            continue
        if date and day != date:
            continue
        by_day[day].append(row)

    counts: dict[str, int] = {}
    for day, day_rows in sorted(by_day.items()):
        folder = out_root / day
        folder.mkdir(parents=True, exist_ok=True)
        csv_path = folder / "full_ticks.csv"
        with csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=HEADERS, extrasaction="ignore")
            writer.writeheader()
            for row in day_rows:
                writer.writerow(
                    row_from_tick(
                        row["received_at"],
                        row["exchange_timestamp"],
                        row["raw_json"],
                    )
                )
        meta = {
            "date": day,
            "ticks": len(day_rows),
            "csv": str(csv_path),
            "archived_at": datetime.now(IST).isoformat(timespec="seconds"),
        }
        (folder / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        counts[day] = len(day_rows)
        print(f"Archived {len(day_rows)} ticks -> {csv_path}")
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description="Archive ticks by IST calendar day")
    parser.add_argument("--date", type=str, default="", help="YYYY-MM-DD only")
    parser.add_argument("--today", action="store_true", help="Archive only today IST")
    parser.add_argument(
        "--out",
        type=str,
        default=str(ARCHIVE_ROOT),
        help="Archive root directory",
    )
    args = parser.parse_args()
    date = args.date.strip() or None
    if args.today:
        date = datetime.now(IST).strftime("%Y-%m-%d")
    counts = archive_days(date=date, out_root=Path(args.out))
    if not counts:
        print("No ticks matched.")
    else:
        print(f"Done. Days={len(counts)} total_ticks={sum(counts.values())}")


if __name__ == "__main__":
    main()
