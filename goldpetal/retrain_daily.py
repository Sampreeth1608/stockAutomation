#!/usr/bin/env python3
"""Daily / multi-day retrain from archived Gold Petal ticks.

1) Archives ticks from SQLite into data/archive/YYYY-MM-DD/
2) Concatenates recent N days (or all)
3) Trains predicting models into data/models/

Examples:
  python retrain_daily.py
  python retrain_daily.py --days 5
  python retrain_daily.py --all-days
"""

from __future__ import annotations

import argparse
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from archive_ticks import ARCHIVE_ROOT, archive_days
from train_models import train

IST = ZoneInfo("Asia/Kolkata")


def _collect_csvs(root: Path, days: int | None) -> list[Path]:
    folders = sorted([p for p in root.iterdir() if p.is_dir()], key=lambda p: p.name)
    if days is not None:
        cutoff = (datetime.now(IST).date() - timedelta(days=days - 1)).isoformat()
        folders = [p for p in folders if p.name >= cutoff]
    return [p / "full_ticks.csv" for p in folders if (p / "full_ticks.csv").exists()]


def main() -> None:
    parser = argparse.ArgumentParser(description="Archive + retrain Gold Petal models")
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Use last N archived days (default 7)",
    )
    parser.add_argument(
        "--all-days",
        action="store_true",
        help="Use every archived day",
    )
    parser.add_argument(
        "--skip-archive",
        action="store_true",
        help="Do not re-export from SQLite",
    )
    parser.add_argument(
        "--horizon",
        type=int,
        default=20,
        help="Model label horizon in rows",
    )
    parser.add_argument(
        "--model-dir",
        type=str,
        default="data/models",
    )
    args = parser.parse_args()

    if not args.skip_archive:
        print("Archiving ticks from DB...")
        archive_days()

    root = ARCHIVE_ROOT
    csvs = _collect_csvs(root, None if args.all_days else args.days)
    if not csvs:
        raise SystemExit(
            "No archived CSVs found. Run with market data, or: python archive_ticks.py"
        )

    frames = [pd.read_csv(p) for p in csvs]
    combined = pd.concat(frames, ignore_index=True)
    combined = combined.drop_duplicates(subset=["time"], keep="last")
    combined = combined.sort_values("time")
    print(f"Training on {len(combined)} rows from {len(csvs)} day file(s)")

    tmp = Path(tempfile.mkdtemp()) / "train_ticks.csv"
    combined.to_csv(tmp, index=False)
    train(
        csv_path=tmp,
        model_dir=Path(args.model_dir),
        horizon=args.horizon,
        threshold_bps=2.0,
        train_frac=0.7,
    )
    print("\nTraining overnight (S4) models...")
    try:
        from train_overnight import train as train_s4

        train_s4(Path(args.model_dir), late_minutes=30, skip_archive=True)
    except SystemExit as exc:
        print(f"S4 overnight train skipped: {exc}")

    print(
        "Retrain complete. Restart the bot to load new models:\n"
        "  pkill -f run_strategy.py; pkill -f supervise.sh; "
        "tmux kill-session -t goldpetal 2>/dev/null; "
        "tmux new -d -s goldpetal './supervise.sh'"
    )


if __name__ == "__main__":
    main()
