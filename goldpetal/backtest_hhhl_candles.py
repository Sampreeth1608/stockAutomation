#!/usr/bin/env python3
"""HH / LL candle breakout strategy — multi-TF backtest from tick OHLC.

Rules (confirmed + symmetric completion for the cut-off downtrend line):

  Uptrend (LONG)
    Entry:  high > prev_high  AND  close > open
    Exit:   high < prev_high  AND  close < open

  Downtrend (SHORT)
    Entry:  low  < prev_low   AND  close < open
    Exit:   low  > prev_low   AND  close > open

Bars built from ticks: 1m 3m 5m 10m 15m 30m 45m 1h 2h 3h 1d
Plus a day-by-day PnL breakdown per timeframe.

  python3 backtest_hhhl_candles.py --db data/ticks.db
  python3 backtest_hhhl_candles.py --db data/analytics_mac/ticks.db --lots 1
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from charges import apply_charges_and_tax, charges_from_env
from mtf_bars import build_rich_bars, load_tick_rows, parse_ts

IST = ZoneInfo("Asia/Kolkata")

# User-requested timeframes (+ daily for day-by-day candle mode)
TIMEFRAMES: list[tuple[str, int]] = [
    ("1m", 1),
    ("3m", 3),
    ("5m", 5),
    ("10m", 10),
    ("15m", 15),
    ("30m", 30),
    ("45m", 45),
    ("1h", 60),
    ("2h", 120),
    ("3h", 180),
    ("1d", 1440),
]


@dataclass
class Candle:
    time: str
    open: float
    high: float
    low: float
    close: float

    @property
    def day(self) -> str:
        return self.time[:10]


@dataclass
class Trade:
    tf: str
    side: str  # LONG | SHORT
    entry_time: str
    entry_px: float
    exit_time: str
    exit_px: float
    gross_pts: float
    gross_pnl_inr: float
    after_tax_pnl_inr: float
    fees_inr: float
    lots: float

    @property
    def day(self) -> str:
        return self.entry_time[:10]


@dataclass
class TfResult:
    tf: str
    n_bars: int
    n_trades: int
    n_long: int
    n_short: int
    win_rate: float
    gross_pts: float
    gross_pnl_inr: float
    after_tax_pnl_inr: float
    fees_inr: float
    max_dd_inr: float
    by_day: dict[str, dict[str, float]] = field(default_factory=dict)
    trades: list[Trade] = field(default_factory=list)


def candles_from_rich(bars: Iterable[Any]) -> list[Candle]:
    out: list[Candle] = []
    for b in bars:
        out.append(
            Candle(
                time=str(b.time),
                open=float(b.open),
                high=float(b.high),
                low=float(b.low),
                close=float(b.close),
            )
        )
    return out


def long_entry(cur: Candle, prev: Candle) -> bool:
    return cur.high > prev.high and cur.close > cur.open


def long_exit(cur: Candle, prev: Candle) -> bool:
    return cur.high < prev.high and cur.close < cur.open


def short_entry(cur: Candle, prev: Candle) -> bool:
    return cur.low < prev.low and cur.close < cur.open


def short_exit(cur: Candle, prev: Candle) -> bool:
    return cur.low > prev.low and cur.close > cur.open


def simulate(
    candles: list[Candle],
    *,
    tf: str,
    lots: float = 1.0,
    allow_short: bool = True,
    allow_long: bool = True,
) -> TfResult:
    """Fill at signal-bar close. Flip when opposite entry (= exit of current)."""
    cfg = charges_from_env()
    trades: list[Trade] = []
    side: str | None = None
    entry_px = 0.0
    entry_time = ""

    def close_trade(exit_c: Candle) -> None:
        nonlocal side, entry_px, entry_time
        assert side is not None
        if side == "LONG":
            pts = (exit_c.close - entry_px) * lots
            order_side = "BUY"
        else:
            pts = (entry_px - exit_c.close) * lots
            order_side = "SELL"
        settled = apply_charges_and_tax(
            pts,
            cfg,
            side=order_side,
            entry_price=entry_px,
            exit_price=exit_c.close,
        )
        trades.append(
            Trade(
                tf=tf,
                side=side,
                entry_time=entry_time,
                entry_px=entry_px,
                exit_time=exit_c.time,
                exit_px=exit_c.close,
                gross_pts=float(pts),
                gross_pnl_inr=float(settled["gross_pnl"]),
                after_tax_pnl_inr=float(settled["pnl_after_tax"]),
                fees_inr=float(settled["charges"]),
                lots=lots,
            )
        )
        side = None

    for i in range(1, len(candles)):
        prev, cur = candles[i - 1], candles[i]
        want_long = allow_long and long_entry(cur, prev)
        want_short = allow_short and short_entry(cur, prev)
        exit_long = long_exit(cur, prev)
        exit_short = short_exit(cur, prev)

        if side == "LONG":
            if exit_long or want_short:
                close_trade(cur)
                if want_short:
                    side = "SHORT"
                    entry_px = cur.close
                    entry_time = cur.time
            continue
        if side == "SHORT":
            if exit_short or want_long:
                close_trade(cur)
                if want_long:
                    side = "LONG"
                    entry_px = cur.close
                    entry_time = cur.time
            continue

        # flat
        if want_long and not want_short:
            side = "LONG"
            entry_px = cur.close
            entry_time = cur.time
        elif want_short and not want_long:
            side = "SHORT"
            entry_px = cur.close
            entry_time = cur.time

    # mark-to-market last bar if still open (optional flat force)
    if side is not None and candles:
        close_trade(candles[-1])

    by_day: dict[str, dict[str, float]] = {}
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in trades:
        equity += t.after_tax_pnl_inr
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
        d = by_day.setdefault(
            t.day,
            {
                "n_trades": 0.0,
                "gross_pts": 0.0,
                "gross_pnl_inr": 0.0,
                "after_tax_pnl_inr": 0.0,
            },
        )
        d["n_trades"] += 1
        d["gross_pts"] += t.gross_pts
        d["gross_pnl_inr"] += t.gross_pnl_inr
        d["after_tax_pnl_inr"] += t.after_tax_pnl_inr

    wins = sum(1 for t in trades if t.gross_pts > 0)
    n = len(trades)
    return TfResult(
        tf=tf,
        n_bars=len(candles),
        n_trades=n,
        n_long=sum(1 for t in trades if t.side == "LONG"),
        n_short=sum(1 for t in trades if t.side == "SHORT"),
        win_rate=(wins / n) if n else 0.0,
        gross_pts=sum(t.gross_pts for t in trades),
        gross_pnl_inr=sum(t.gross_pnl_inr for t in trades),
        after_tax_pnl_inr=sum(t.after_tax_pnl_inr for t in trades),
        fees_inr=sum(t.fees_inr for t in trades),
        max_dd_inr=max_dd,
        by_day=by_day,
        trades=trades,
    )


def count_ticks(db: Path) -> int:
    con = sqlite3.connect(db)
    try:
        n = int(con.execute("SELECT COUNT(*) FROM ticks").fetchone()[0])
    except sqlite3.Error:
        n = 0
    finally:
        con.close()
    return n


def run_all(
    db: Path,
    *,
    lots: float = 1.0,
    tfs: list[tuple[str, int]] | None = None,
    allow_short: bool = True,
    allow_long: bool = True,
) -> list[TfResult]:
    rows = load_tick_rows(db)
    if not rows:
        raise SystemExit(f"no ticks in {db}")
    results: list[TfResult] = []
    for name, minutes in tfs or TIMEFRAMES:
        bars = build_rich_bars(rows, name, minutes)
        candles = candles_from_rich(bars)
        results.append(
            simulate(
                candles,
                tf=name,
                lots=lots,
                allow_short=allow_short,
                allow_long=allow_long,
            )
        )
    return results


def print_summary(results: list[TfResult]) -> None:
    print()
    print(
        f"{'TF':>4}  {'bars':>6}  {'trades':>6}  {'L/S':>7}  "
        f"{'win%':>6}  {'gross_pts':>10}  {'pnl_₹':>10}  {'maxDD_₹':>10}"
    )
    print("-" * 78)
    for r in results:
        print(
            f"{r.tf:>4}  {r.n_bars:6d}  {r.n_trades:6d}  "
            f"{r.n_long:3d}/{r.n_short:<3d}  {100 * r.win_rate:5.1f}%  "
            f"{r.gross_pts:10.1f}  {r.after_tax_pnl_inr:10.1f}  {r.max_dd_inr:10.1f}"
        )
    print()


def print_by_day(results: list[TfResult]) -> None:
    print("=== Day-by-day (after-tax ₹) ===")
    days: set[str] = set()
    for r in results:
        days.update(r.by_day)
    for day in sorted(days):
        bits = []
        for r in results:
            d = r.by_day.get(day)
            if not d:
                continue
            bits.append(f"{r.tf}:{d['after_tax_pnl_inr']:+.0f}({int(d['n_trades'])}t)")
        if bits:
            print(f"  {day}  " + "  ".join(bits))
    print()


def write_outputs(results: list[TfResult], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = []
    for r in results:
        row = {k: v for k, v in asdict(r).items() if k != "trades"}
        summary.append(row)
        trades_path = out_dir / f"trades_{r.tf}.csv"
        with trades_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(
                f,
                fieldnames=[
                    "tf",
                    "side",
                    "entry_time",
                    "entry_px",
                    "exit_time",
                    "exit_px",
                    "gross_pts",
                    "gross_pnl_inr",
                    "after_tax_pnl_inr",
                    "fees_inr",
                    "lots",
                ],
            )
            w.writeheader()
            for t in r.trades:
                w.writerow(asdict(t))
    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    print(f"Wrote {out_dir}/summary.json and trades_*.csv")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=Path("data/ticks.db"))
    ap.add_argument("--lots", type=float, default=1.0)
    ap.add_argument("--long-only", action="store_true")
    ap.add_argument("--short-only", action="store_true")
    ap.add_argument(
        "--tfs",
        default="",
        help="comma list e.g. 5m,15m,1h (default: all requested)",
    )
    ap.add_argument(
        "--out-dir",
        type=Path,
        default=Path("data/backtests/hhhl_candles"),
    )
    args = ap.parse_args()

    if not args.db.exists():
        raise SystemExit(f"missing db: {args.db}")
    n = count_ticks(args.db)
    if n == 0:
        raise SystemExit(
            f"0 ticks in {args.db}. Sync from VM first:\n"
            f"  ./scripts/sync_analytics_mac.sh\n"
            f"  python3 backtest_hhhl_candles.py --db data/analytics_mac/ticks.db"
        )

    tfs = TIMEFRAMES
    if args.tfs.strip():
        want = {x.strip() for x in args.tfs.split(",") if x.strip()}
        tfs = [(n, m) for n, m in TIMEFRAMES if n in want]
        if not tfs:
            raise SystemExit(f"no matching tfs in {want}")

    rows = load_tick_rows(args.db)
    t0 = parse_ts(rows[0]["received_at"])
    t1 = parse_ts(rows[-1]["received_at"])
    print(
        f"HH/LL candle backtest  db={args.db}  ticks={n}  "
        f"range={t0.isoformat(timespec='seconds')} → {t1.isoformat(timespec='seconds')}  "
        f"lots={args.lots}"
    )
    print(
        "Rules: LONG H>prevH & C>O / exit H<prevH & C<O | "
        "SHORT L<prevL & C<O / exit L>prevL & C>O"
    )

    results = run_all(
        args.db,
        lots=args.lots,
        tfs=tfs,
        allow_long=not args.short_only,
        allow_short=not args.long_only,
    )
    print_summary(results)
    print_by_day(results)
    write_outputs(results, args.out_dir)


if __name__ == "__main__":
    main()
