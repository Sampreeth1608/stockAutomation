#!/usr/bin/env python3
"""Backtest S18: 1h green/HH/up-close + volume-up + above yesterday.

Research only. Not paper. Not live.

  ./venv/bin/python backtest_s18_ohlc_vol_htf.py --db data/ticks.db --lots 100 --session --fees
"""

from __future__ import annotations

import argparse
from pathlib import Path

from backtest_hhhl_candles import print_by_day, write_outputs
from backtest_wick_candles import print_wick_summary
from s18_ohlc_vol_htf import (
    FORMULA,
    S18_NAME,
    build_vol_bars,
    load_vol_rows,
    simulate_s18,
)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default="data/ticks.db")
    ap.add_argument("--lots", type=float, default=100.0)
    ap.add_argument("--fees", action="store_true")
    ap.add_argument("--session", action="store_true")
    ap.add_argument("--out-dir", default="data/backtests/s18_ohlc_vol_htf")
    args = ap.parse_args()

    db = Path(args.db)
    rows = load_vol_rows(db)
    hours = build_vol_bars(rows, 60)
    days = build_vol_bars(rows, 1440)
    print(S18_NAME, "(research, not paper)")
    print(FORMULA)
    print(f"ticks={len(rows)} hours={len(hours)} days={len(days)} db={db}")
    result = simulate_s18(
        hours,
        days,
        lots=float(args.lots),
        fees=bool(args.fees),
        session_filter=bool(args.session),
    )
    print_wick_summary([result], title=S18_NAME)
    print_by_day([result])
    out = Path(args.out_dir)
    write_outputs([result], out)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
