#!/usr/bin/env python3
"""Backtest S19: 1h aligned body + close vs prev (paper).

Also prints a close-follow-every-hour research row (not paper) and S18 on
the same hours so you can see coverage vs Angel charges.

  ./venv/bin/python backtest_s19_body_close.py --db data/ticks.db --lots 100 --session --fees
"""

from __future__ import annotations

import argparse
from pathlib import Path

from backtest_hhhl_candles import print_by_day, write_outputs
from backtest_wick_candles import print_wick_summary
from s18_ohlc_vol_htf import S18_NAME, build_vol_bars, load_vol_rows, simulate_s18
from s19_body_close import (
    CLOSE_FOLLOW_FORMULA,
    FORMULA,
    S19_NAME,
    after_charges_inr,
    simulate_close_follow,
    simulate_s19,
)


def _line(label: str, result) -> None:
    ac = after_charges_inr(result)
    print(
        f"{label}: trades={result.n_trades} L/S={result.n_long}/{result.n_short} "
        f"win%={100 * result.win_rate:.1f} gross₹={result.gross_pnl_inr:.1f} "
        f"fees₹={result.fees_inr:.1f} after_charges₹={ac:.1f} "
        f"(tax excluded) after_tax₹={result.after_tax_pnl_inr:.1f}"
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default="data/ticks.db")
    ap.add_argument("--lots", type=float, default=100.0)
    ap.add_argument("--fees", action="store_true")
    ap.add_argument("--session", action="store_true")
    ap.add_argument("--out-dir", default="data/backtests/s19_body_close")
    args = ap.parse_args()

    db = Path(args.db)
    rows = load_vol_rows(db)
    hours = build_vol_bars(rows, 60)
    days = build_vol_bars(rows, 1440)
    print(S19_NAME, "(paper aligned body+close, not live)")
    print(FORMULA)
    print(CLOSE_FOLLOW_FORMULA)
    print(f"ticks={len(rows)} hours={len(hours)} days={len(days)} db={db}")
    kw = dict(
        lots=float(args.lots),
        fees=bool(args.fees),
        session_filter=bool(args.session),
    )
    s19 = simulate_s19(hours, **kw)
    follow = simulate_close_follow(hours, **kw)
    s18 = simulate_s18(hours, days, **kw)
    print_wick_summary([s19, follow, s18], title="S19 paper vs close-follow research vs S18")
    _line(S19_NAME, s19)
    _line("RESEARCH close-follow (not paper)", follow)
    _line(S18_NAME, s18)
    print_by_day([s19])
    out = Path(args.out_dir)
    write_outputs([s19, follow, s18], out)
    print(f"wrote {out}")
    print("Stay DRY_RUN. Paper 100 lots is not live size. Do not live-unlock S19.")


if __name__ == "__main__":
    main()
