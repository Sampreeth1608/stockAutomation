#!/usr/bin/env python3
"""Export S8 zigzag retune DB → CSV for future parameter sweeps."""

from __future__ import annotations

import argparse
from pathlib import Path

from zigzag_recorder import DEFAULT_DB, ZigzagRecorder


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--out", default="data/zigzag_retune.csv")
    args = ap.parse_args()
    rec = ZigzagRecorder(db_path=Path(args.db), enabled=True)
    n = rec.export_csv(args.out)
    print(f"exported {n} rows → {args.out}")


if __name__ == "__main__":
    main()
