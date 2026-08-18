#!/usr/bin/env python3
"""Backtest S19: 1h aligned body + close vs prev.

Also prints close-follow (research, not paper), S16, and S18 on the same hours.
Rank **after Angel charges, tax excluded**. S19 lost that rank on the Aug-26
Gold Petal 1h tape — leave ENABLE_S19=false.

  ./venv/bin/python backtest_s19_body_close.py --db data/ticks.db --lots 100 --session --fees
  ./venv/bin/python backtest_s19_body_close.py --from-angel --from 2026-08-02 --lots 100 --fees
  ./venv/bin/python backtest_s19_body_close.py --csv hours.csv --lots 100 --fees
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from backtest_hhhl_candles import Candle, print_by_day, write_outputs
from backtest_wick_candles import print_wick_summary
from s16_hhhl_wick import simulate_s16
from s18_ohlc_vol_htf import S18_NAME, VolBar, build_vol_bars, load_vol_rows, simulate_s18
from s19_body_close import (
    CLOSE_FOLLOW_FORMULA,
    FORMULA,
    S19_NAME,
    after_charges_inr,
    hours_from_ohlc,
    simulate_close_follow,
    simulate_s19,
)

IST = ZoneInfo("Asia/Kolkata")


def _line(label: str, result: Any) -> None:
    ac = after_charges_inr(result)
    ac_wins = sum(1 for t in result.trades if (t.gross_pnl_inr - t.fees_inr) > 0)
    n = result.n_trades
    wr = (100.0 * ac_wins / n) if n else 0.0
    print(
        f"{label}: trades={n} L/S={result.n_long}/{result.n_short} "
        f"gross_win%={100 * result.win_rate:.1f} after_charges_win%={wr:.1f} "
        f"gross₹={result.gross_pnl_inr:.1f} fees₹={result.fees_inr:.1f} "
        f"after_charges₹={ac:.1f} (tax excluded) after_tax₹={result.after_tax_pnl_inr:.1f}"
    )


def _load_csv(path: Path) -> tuple[list[VolBar], list[VolBar]]:
    with path.open(newline="") as f:
        rows = list(csv.DictReader(f))
    hours = hours_from_ohlc(rows)
    by_day: dict[str, VolBar] = {}
    for b in hours:
        day = b.time[:10]
        prev = by_day.get(day)
        if prev is None:
            by_day[day] = VolBar(
                day + " 00:00:00", b.open, b.high, b.low, b.close, b.volume
            )
        else:
            by_day[day] = VolBar(
                prev.time,
                prev.open,
                max(prev.high, b.high),
                min(prev.low, b.low),
                b.close,
                prev.volume + b.volume,
            )
    days = [by_day[k] for k in sorted(by_day)]
    return hours, days


def _load_angel(date_from: str, date_to: str) -> tuple[list[VolBar], list[VolBar], str]:
    from auth import login
    from explain_s14_candles import (
        bar_is_finished,
        fetch_angel_candles,
        in_session_dt,
        parse_bar_ts,
        reexec_with_bot_python,
    )
    from symbols import find_goldpetal_futures

    reexec_with_bot_python()
    start = datetime.strptime(date_from, "%Y-%m-%d").replace(tzinfo=IST)
    if date_to:
        end = datetime.strptime(date_to, "%Y-%m-%d").replace(
            hour=23, minute=30, tzinfo=IST
        )
    else:
        end = datetime.now(IST)
    contract = find_goldpetal_futures()
    token = str(contract["token"])
    symbol = str(contract["symbol"])
    print(
        f"contract {symbol} token={token} expiry={contract.get('expiry', '-')}",
        flush=True,
    )
    api = login().api
    now = datetime.now(IST)
    exch = str(contract.get("exchange") or "MCX")
    raw_h = fetch_angel_candles(
        api, token=token, interval="ONE_HOUR", start=start, end=end, exchange=exch
    )
    raw_d = fetch_angel_candles(
        api, token=token, interval="ONE_DAY", start=start, end=end, exchange=exch
    )
    raw_h = [
        b
        for b in raw_h
        if in_session_dt(parse_bar_ts(b["time"]), tf="1h")
        and bar_is_finished(b["time"], "1h", now)
    ]
    raw_d = [b for b in raw_d if bar_is_finished(b["time"], "1d", now)]
    hours = hours_from_ohlc(raw_h)
    days = hours_from_ohlc(raw_d)
    return hours, days, f"Angel {symbol}"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default="data/ticks.db")
    ap.add_argument("--from-angel", action="store_true")
    ap.add_argument("--csv", type=Path, default=None)
    ap.add_argument("--from", dest="date_from", default="2026-08-02")
    ap.add_argument("--to", dest="date_to", default="")
    ap.add_argument("--lots", type=float, default=100.0)
    ap.add_argument("--fees", action="store_true", default=True)
    ap.add_argument("--no-fees", action="store_true")
    ap.add_argument("--session", action="store_true", default=True)
    ap.add_argument("--no-session", action="store_true")
    ap.add_argument("--out-dir", default="data/backtests/s19_body_close")
    args = ap.parse_args()
    fees = bool(args.fees) and not bool(args.no_fees)
    session_filter = bool(args.session) and not bool(args.no_session)

    if args.from_angel:
        hours, days, source = _load_angel(args.date_from, args.date_to)
    elif args.csv:
        hours, days = _load_csv(args.csv)
        source = str(args.csv)
    else:
        db = Path(args.db)
        rows = load_vol_rows(db)
        hours = build_vol_bars(rows, 60)
        days = build_vol_bars(rows, 1440)
        source = f"ticks {db} n={len(rows)}"

    if len(hours) < 2:
        raise SystemExit(f"need ≥2 1h bars, got {len(hours)} from {source}")

    print(S19_NAME, "(aligned body+close — research until ENABLE_S19)")
    print(FORMULA)
    print(CLOSE_FOLLOW_FORMULA)
    print(
        f"lots={args.lots:g} fees={fees} session={session_filter} "
        f"hours={len(hours)} {hours[0].time}→{hours[-1].time} "
        f"days={len(days)} source={source}",
        flush=True,
    )
    kw = dict(
        lots=float(args.lots),
        fees=fees,
        session_filter=session_filter,
    )
    s19 = simulate_s19(hours, tf="1h:S19", **kw)
    follow = simulate_close_follow(hours, **kw)
    candles = [Candle(b.time, b.open, b.high, b.low, b.close) for b in hours]
    s16 = simulate_s16(
        candles,
        tf="1h:S16",
        lots=float(args.lots),
        fees=fees,
        session_filter=session_filter,
        min_wick_gap=0.0,
    )
    results = [s19, follow, s16]
    has_vol = any(b.volume > 0 for b in hours)
    s18 = None
    if has_vol and days:
        s18 = simulate_s18(hours, days, tf="1h:S18", **kw)
        results.append(s18)
    print_wick_summary(
        results,
        title=f"S19 vs close-follow vs S16 vs S18  lots={args.lots:g}  fees={fees}",
    )
    _line("S19 aligned (this book)", s19)
    _line("RESEARCH close-follow (not paper)", follow)
    _line("S16 HHHL+wick gap0 (paper 1h)", s16)
    if s18 is not None:
        _line("S18 OHLC+vol+day (paper)", s18)
    else:
        print("S18 skipped — need 1h volume + day bars (ticks.db or CSV with volume).")
    print_by_day([s19])
    out = Path(args.out_dir)
    write_outputs(results, out)
    print(f"wrote {out}")
    print(
        "Rank after charges, tax excluded. Stay DRY_RUN. Paper 100 lots is not live. "
        "Do not live-unlock S19. ENABLE_S19 stays false unless a later tape beats S16/S18."
    )


if __name__ == "__main__":
    main()
