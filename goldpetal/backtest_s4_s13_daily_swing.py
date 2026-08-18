#!/usr/bin/env python3
"""Backtest paper S4 (HH/LL daily) vs S13 (S16 daily) — hold until opposite.

This is the live paper formula, not ``backtest_s4_hhhl_daily.py`` (that one
still has min_range / next-open overnight / CLOSE-only).

Fill analog: finished **1d close** ≈ last-15m LTP of that day. Confirm window
on the bot is last 15m before MARKET_CLOSE; the backtest uses the day close.

No next-open flatten. No EOD flatten. ``session_filter=False`` so 00:00 1d
bars are not treated as outside MCX hours. Leftover swing is marked at the
last 1d close for accounting; paper would still be in that trade.

Research only. Do not paper until a 100-lot + fees row is picked.

  ./venv/bin/python backtest_s4_s13_daily_swing.py --db data/ticks.db --lots 100 --fees
  ./venv/bin/python backtest_s4_s13_daily_swing.py --from-angel --from 2026-08-02 --lots 100 --fees
  ./venv/bin/python backtest_s4_s13_daily_swing.py --csv days.csv --lots 100 --fees
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from backtest_hhhl_candles import Candle, TfResult, make_charge_cfg, print_by_day, write_outputs
from backtest_s16_hhhl_wick import candles_from_dicts, write_walk_csv
from backtest_wick_candles import build_ohlc_candles, load_ltp_rows, print_wick_summary
from s16_hhhl_wick import hhhl_bar_decision, s16_bar_decision, simulate_s16, walk_candles

IST = ZoneInfo("Asia/Kolkata")

S4_ROW = "S4_HHHL"
S13_ROW = "S13_S16"

FORMULA = (
    "Daily swing, fill at day close ≈ last-15m. Hold until opposite. FLIP.\n"
    "  S4  HH+green LONG / LL+red SHORT. Inside days hold. Ignore C vs prevC.\n"
    "  S13 S16 on the day: C>prevC → HH/LL; C<prevC → wick g0; C=prevC skip."
)


def run_books(
    candles: list[Candle],
    *,
    lots: float,
    fees: bool,
) -> list[TfResult]:
    """S4 HH/LL and S13 S16 on the same 1d tape. No session flatten."""
    cfg = make_charge_cfg(fees=fees, lots=lots)
    s4 = simulate_s16(
        candles,
        tf=S4_ROW,
        lots=lots,
        fees=fees,
        session_filter=False,
        charge_cfg=cfg,
        min_wick_gap=0.0,
        decide=hhhl_bar_decision,
    )
    s13 = simulate_s16(
        candles,
        tf=S13_ROW,
        lots=lots,
        fees=fees,
        session_filter=False,
        charge_cfg=cfg,
        min_wick_gap=0.0,
        decide=lambda a, b: s16_bar_decision(a, b, min_wick_gap=0.0),
    )
    return [s4, s13]


def walk_books(candles: list[Candle]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    s4 = walk_candles(candles, min_wick_gap=0.0, decide=hhhl_bar_decision)
    s13 = walk_candles(
        candles,
        min_wick_gap=0.0,
        decide=lambda a, b: s16_bar_decision(a, b, min_wick_gap=0.0),
    )
    return s4, s13


def print_compare(results: list[TfResult]) -> None:
    by = {r.tf: r for r in results}
    s4 = by.get(S4_ROW)
    s13 = by.get(S13_ROW)
    if s4 is None or s13 is None:
        return
    delta = s13.after_tax_pnl_inr - s4.after_tax_pnl_inr
    print("=== S4 HH/LL vs S13 S16 — daily swing, hold until opposite ===")
    print(
        f"  S4   trades={s4.n_trades}  L/S={s4.n_long}/{s4.n_short}  "
        f"win%={100 * s4.win_rate:.1f}  ₹={s4.after_tax_pnl_inr:.0f}  "
        f"fees={s4.fees_inr:.0f}  maxDD={s4.max_dd_inr:.0f}"
    )
    print(
        f"  S13  trades={s13.n_trades}  L/S={s13.n_long}/{s13.n_short}  "
        f"win%={100 * s13.win_rate:.1f}  ₹={s13.after_tax_pnl_inr:.0f}  "
        f"fees={s13.fees_inr:.0f}  maxDD={s13.max_dd_inr:.0f}"
    )
    print(f"  Δ S13−S4 after-tax ₹ = {delta:+.0f}")
    print()


def print_day_walk(
    s4_walk: list[dict[str, Any]],
    s13_walk: list[dict[str, Any]],
    *,
    print_skips: bool,
) -> None:
    print("=== Day walk (fill = day close ≈ last-15m) ===")
    print(
        f"  {'date':<12} {'O':>8} {'H':>8} {'L':>8} {'C':>8}  "
        f"{'S4':<28}  {'S13':<28}"
    )
    for a, b in zip(s4_walk, s13_walk, strict=True):
        if not print_skips and a["action"] == "skip" and b["action"] == "skip":
            continue
        day = str(a["time"])[:10]

        def _bit(row: dict[str, Any]) -> str:
            act = str(row["action"])
            if act == "skip":
                return f"skip {row['rule']}"
            side = str(row["side"]).upper()
            return f"{act} {side} {row['rule']}"

        print(
            f"  {day:<12} {a['open']:8.1f} {a['high']:8.1f} "
            f"{a['low']:8.1f} {a['close']:8.1f}  "
            f"{_bit(a):<28}  {_bit(b):<28}"
        )
    print()
    print(
        "Leftover swing is marked at the last 1d close for accounting. "
        "Paper S4/S13 would still be in that trade (no EOD flatten)."
    )
    print()


def write_compare_csv(path: Path, results: list[TfResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    by = {r.tf: r for r in results}
    s4 = by[S4_ROW]
    s13 = by[S13_ROW]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "book",
                "n_bars",
                "n_trades",
                "n_long",
                "n_short",
                "win_rate",
                "gross_pts",
                "fees_inr",
                "after_tax_pnl_inr",
                "max_dd_inr",
                "delta_vs_s4_inr",
            ],
        )
        w.writeheader()
        for r in (s4, s13):
            w.writerow(
                {
                    "book": r.tf,
                    "n_bars": r.n_bars,
                    "n_trades": r.n_trades,
                    "n_long": r.n_long,
                    "n_short": r.n_short,
                    "win_rate": round(r.win_rate, 4),
                    "gross_pts": round(r.gross_pts, 2),
                    "fees_inr": round(r.fees_inr, 2),
                    "after_tax_pnl_inr": round(r.after_tax_pnl_inr, 2),
                    "max_dd_inr": round(r.max_dd_inr, 2),
                    "delta_vs_s4_inr": round(r.after_tax_pnl_inr - s4.after_tax_pnl_inr, 2),
                }
            )


def load_csv_days(path: Path) -> list[Candle]:
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise SystemExit(f"no rows in {path}")
    out: list[dict[str, Any]] = []
    for r in rows:
        t = str(r.get("time") or r.get("date") or "").strip()
        if len(t) == 10:
            t = t + " 00:00:00"
        out.append(
            {
                "time": t,
                "open": r["open"],
                "high": r["high"],
                "low": r["low"],
                "close": r["close"],
            }
        )
    return candles_from_dicts(out)


def load_tick_days(db: Path, *, drop_last: bool) -> list[Candle]:
    rows = load_ltp_rows(db)
    if not rows:
        raise SystemExit(f"no ticks in {db}")
    candles = build_ohlc_candles(rows, 1440)
    if drop_last and len(candles) >= 2:
        candles = candles[:-1]
    return candles


def load_angel_days(*, date_from: str, date_to: str) -> list[Candle]:
    from auth import login
    from explain_s14_candles import (
        bar_is_finished,
        fetch_angel_candles,
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
    raw = fetch_angel_candles(
        login().api,
        token=token,
        interval="ONE_DAY",
        start=start,
        end=end,
        exchange=str(contract.get("exchange") or "MCX"),
    )
    now = datetime.now(IST)
    raw = [b for b in raw if bar_is_finished(b["time"], "1d", now)]
    return candles_from_dicts(raw)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=Path("data/ticks.db"))
    ap.add_argument("--from-angel", action="store_true")
    ap.add_argument("--csv", type=Path, default=None, help="OHLC CSV (time/date,open,high,low,close)")
    ap.add_argument("--from", dest="date_from", default="2026-08-02")
    ap.add_argument("--to", dest="date_to", default="")
    ap.add_argument("--lots", type=float, default=100.0)
    ap.add_argument("--fees", action="store_true", default=True)
    ap.add_argument("--no-fees", action="store_true")
    ap.add_argument("--keep-last", action="store_true", help="keep still-forming last tick day")
    ap.add_argument(
        "--hide-skips",
        action="store_true",
        help="hide days both books skipped",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=Path("data/backtests/s4_s13_daily_swing"),
    )
    args = ap.parse_args()
    fees = bool(args.fees) and not bool(args.no_fees)

    if args.from_angel:
        candles = load_angel_days(date_from=args.date_from, date_to=args.date_to)
        source = "Angel 1d"
    elif args.csv:
        candles = load_csv_days(args.csv)
        source = str(args.csv)
    else:
        candles = load_tick_days(args.db, drop_last=not args.keep_last)
        source = str(args.db)

    if len(candles) < 2:
        raise SystemExit(f"need ≥2 daily bars, got {len(candles)} from {source}")

    print(FORMULA, flush=True)
    print(
        f"lots={args.lots:g}  fees={fees}  session_filter=False  "
        f"bars={len(candles)}  {candles[0].time[:10]}→{candles[-1].time[:10]}  "
        f"source={source}",
        flush=True,
    )

    results = run_books(candles, lots=args.lots, fees=fees)
    s4_walk, s13_walk = walk_books(candles)

    print_wick_summary(
        results,
        title=f"S4 vs S13 daily swing  lots={args.lots:g}  fees={fees}",
    )
    print_compare(results)
    print_by_day(results)
    print_day_walk(s4_walk, s13_walk, print_skips=not bool(args.hide_skips))

    args.out.mkdir(parents=True, exist_ok=True)
    write_outputs(results, args.out)
    write_compare_csv(args.out / "compare.csv", results)
    write_walk_csv(args.out / "s4_walk.csv", s4_walk)
    write_walk_csv(args.out / "s13_walk.csv", s13_walk)
    print(f"wrote {args.out}", flush=True)
    print("Not paper. Not live. Paste the compare table and day walk.", flush=True)


if __name__ == "__main__":
    main()
