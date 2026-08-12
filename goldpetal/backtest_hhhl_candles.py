#!/usr/bin/env python3
"""HH / LL candle breakout strategy — multi-TF backtest from tick OHLC.

Rules:

  LONG  entry: high > prev_high AND close > open
        exit:  high < prev_high AND close < open
  SHORT entry: low  < prev_low  AND close < open
        exit:  low  > prev_low  AND close > open

Filters (recommended):
  --session     MCX hours Mon–Fri 09:00–23:30 IST (entries + force flat after)
  --min-range N require candle range (H−L) ≥ N pts to enter
  --no-flip     exit goes flat; no opposite entry on the same bar
  --fees        Angel fee schedule + 30% tax (IGNORE_FEES off)

  python3 backtest_hhhl_candles.py --db data/analytics_mac/ticks.db \\
      --lots 1 --fees --session --min-range 5 --no-flip
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from charges import ChargeConfig, apply_charges_and_tax, zero_charge_config
from mtf_bars import build_rich_bars, load_tick_rows, parse_ts

IST = ZoneInfo("Asia/Kolkata")

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

    @property
    def range_pts(self) -> float:
        return float(self.high - self.low)


@dataclass
class Trade:
    tf: str
    side: str
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


def in_session(
    candle: Candle,
    *,
    open_hhmm: str = "09:00",
    close_hhmm: str = "23:30",
) -> bool:
    """MCX Gold Petal default: Mon–Fri open_hhmm–close_hhmm IST."""
    dt = parse_ts(candle.time)
    if dt.weekday() >= 5:
        return False
    oh, om = (int(x) for x in open_hhmm.split(":"))
    ch, cm = (int(x) for x in close_hhmm.split(":"))
    start = dt.replace(hour=oh, minute=om, second=0, microsecond=0)
    end = dt.replace(hour=ch, minute=cm, second=0, microsecond=0)
    return start <= dt <= end


def make_charge_cfg(*, fees: bool, lots: float) -> ChargeConfig:
    """lot_size=lots so turnover (and ₹ PnL) scale with lot count."""
    if not fees:
        return zero_charge_config(lot_size=float(lots))
    return ChargeConfig(
        brokerage_per_order=20.0,
        brokerage_promo=False,
        mcx_txn_rate=0.0000210,
        ctt_sell_rate=0.0001,
        sebi_rate=0.000001,
        stamp_buy_rate=0.00002,
        gst_rate=0.18,
        tax_rate=0.30,
        lot_size=float(lots),
        turnover_mult=1.0,
        ignore_fees=False,
    )


def simulate(
    candles: list[Candle],
    *,
    tf: str,
    lots: float = 1.0,
    allow_short: bool = True,
    allow_long: bool = True,
    fees: bool = False,
    session_filter: bool = False,
    min_range: float = 0.0,
    no_flip: bool = False,
    market_open: str = "09:00",
    market_close: str = "23:30",
    charge_cfg: ChargeConfig | None = None,
) -> TfResult:
    """Fill at signal-bar close.

    no_flip=False (legacy): opposite entry on exit bar flips.
    no_flip=True: exit → flat only; opposite waits for a later bar.
    """
    cfg = charge_cfg or make_charge_cfg(fees=fees, lots=lots)
    trades: list[Trade] = []
    side: str | None = None
    entry_px = 0.0
    entry_time = ""

    def close_trade(exit_c: Candle) -> None:
        nonlocal side, entry_px, entry_time
        assert side is not None
        if side == "LONG":
            pts = exit_c.close - entry_px
            order_side = "BUY"
        else:
            pts = entry_px - exit_c.close
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
                gross_pts=float(pts) * float(cfg.lot_size),
                gross_pnl_inr=float(settled["gross_pnl"]),
                after_tax_pnl_inr=float(settled["pnl_after_tax"]),
                fees_inr=float(settled["charges"]),
                lots=float(cfg.lot_size),
            )
        )
        side = None

    def can_enter(cur: Candle) -> bool:
        if min_range > 0 and cur.range_pts < min_range:
            return False
        if session_filter and not in_session(
            cur, open_hhmm=market_open, close_hhmm=market_close
        ):
            return False
        return True

    for i in range(1, len(candles)):
        prev, cur = candles[i - 1], candles[i]
        sess_ok = (not session_filter) or in_session(
            cur, open_hhmm=market_open, close_hhmm=market_close
        )

        # Force flat when session filter is on and bar is outside session.
        if side is not None and session_filter and not sess_ok:
            close_trade(cur)
            continue

        want_long = allow_long and long_entry(cur, prev)
        want_short = allow_short and short_entry(cur, prev)
        exit_long = long_exit(cur, prev)
        exit_short = short_exit(cur, prev)

        if side == "LONG":
            if exit_long or (want_short and not no_flip):
                close_trade(cur)
                if want_short and not no_flip and can_enter(cur):
                    side = "SHORT"
                    entry_px = cur.close
                    entry_time = cur.time
            continue
        if side == "SHORT":
            if exit_short or (want_long and not no_flip):
                close_trade(cur)
                if want_long and not no_flip and can_enter(cur):
                    side = "LONG"
                    entry_px = cur.close
                    entry_time = cur.time
            continue

        # flat — new entries only
        if not can_enter(cur):
            continue
        if want_long and not want_short:
            side = "LONG"
            entry_px = cur.close
            entry_time = cur.time
        elif want_short and not want_long:
            side = "SHORT"
            entry_px = cur.close
            entry_time = cur.time

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
                "fees_inr": 0.0,
            },
        )
        d["n_trades"] += 1
        d["gross_pts"] += t.gross_pts
        d["gross_pnl_inr"] += t.gross_pnl_inr
        d["after_tax_pnl_inr"] += t.after_tax_pnl_inr
        d["fees_inr"] += t.fees_inr

    wins = sum(1 for t in trades if t.gross_pnl_inr > 0)
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


def db_diagnostics(db: Path) -> str:
    lines: list[str] = [f"path={db.resolve()}"]
    if not db.exists():
        lines.append("exists=False")
        return "\n  ".join(lines)
    lines.append(f"is_file={db.is_file()} is_dir={db.is_dir()}")
    if db.is_dir():
        lines.append("ERROR: path is a directory (gcloud scp nested?). Remove it and re-sync.")
        try:
            lines.append("contents=" + ", ".join(sorted(p.name for p in db.iterdir())[:20]))
        except OSError as exc:
            lines.append(f"listdir_error={exc}")
        return "\n  ".join(lines)
    lines.append(f"size_mb={db.stat().st_size / (1024 * 1024):.1f}")
    for suffix in ("-wal", "-shm"):
        side = Path(str(db) + suffix)
        if side.exists():
            lines.append(f"{suffix} size_mb={side.stat().st_size / (1024 * 1024):.1f}")
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            tables = [
                r[0]
                for r in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                )
            ]
            lines.append(f"tables={tables}")
            if "ticks" in tables:
                n = int(con.execute("SELECT COUNT(*) FROM ticks").fetchone()[0])
                n_ltp = int(
                    con.execute(
                        "SELECT COUNT(*) FROM ticks WHERE ltp IS NOT NULL"
                    ).fetchone()[0]
                )
                lines.append(f"ticks_rows={n} ticks_with_ltp={n_ltp}")
                if n:
                    lo, hi = con.execute(
                        "SELECT MIN(received_at), MAX(received_at) FROM ticks"
                    ).fetchone()
                    lines.append(f"received_at_range={lo} → {hi}")
            else:
                lines.append("ERROR: no ticks table")
        finally:
            con.close()
    except sqlite3.Error as exc:
        lines.append(f"sqlite_error={exc}")
    return "\n  ".join(lines)


def count_ticks(db: Path) -> int:
    if not db.is_file():
        return 0
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            return int(con.execute("SELECT COUNT(*) FROM ticks").fetchone()[0])
        finally:
            con.close()
    except sqlite3.Error:
        return 0


def run_all(
    db: Path,
    *,
    lots: float = 1.0,
    tfs: list[tuple[str, int]] | None = None,
    allow_short: bool = True,
    allow_long: bool = True,
    fees: bool = False,
    session_filter: bool = False,
    min_range: float = 0.0,
    no_flip: bool = False,
    market_open: str = "09:00",
    market_close: str = "23:30",
) -> list[TfResult]:
    rows = load_tick_rows(db)
    if not rows:
        raise SystemExit(f"no ticks in {db}")
    cfg = make_charge_cfg(fees=fees, lots=lots)
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
                fees=fees,
                session_filter=session_filter,
                min_range=min_range,
                no_flip=no_flip,
                market_open=market_open,
                market_close=market_close,
                charge_cfg=cfg,
            )
        )
    return results


def print_summary(results: list[TfResult]) -> None:
    print()
    print(
        f"{'TF':>4}  {'bars':>6}  {'trades':>6}  {'L/S':>7}  "
        f"{'win%':>6}  {'gross_pts':>10}  {'fees_₹':>10}  {'pnl_₹':>10}  {'maxDD_₹':>10}"
    )
    print("-" * 92)
    for r in results:
        print(
            f"{r.tf:>4}  {r.n_bars:6d}  {r.n_trades:6d}  "
            f"{r.n_long:3d}/{r.n_short:<3d}  {100 * r.win_rate:5.1f}%  "
            f"{r.gross_pts:10.1f}  {r.fees_inr:10.1f}  "
            f"{r.after_tax_pnl_inr:10.1f}  {r.max_dd_inr:10.1f}"
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
    ap.add_argument("--fees", action="store_true", help="Angel fees + 30% tax")
    ap.add_argument(
        "--session",
        action="store_true",
        help="Only trade Mon–Fri 09:00–23:30 IST; flatten outside",
    )
    ap.add_argument(
        "--min-range",
        type=float,
        default=0.0,
        help="Min candle H−L (pts) required to enter",
    )
    ap.add_argument(
        "--no-flip",
        action="store_true",
        help="Exit to flat only; no reverse entry on same bar",
    )
    ap.add_argument("--market-open", default="09:00")
    ap.add_argument("--market-close", default="23:30")
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

    # Ensure dotenv IGNORE_FEES=true does not override --fees
    if args.fees:
        os.environ["IGNORE_FEES"] = "false"

    if not args.db.exists():
        raise SystemExit(f"missing db: {args.db}")
    n = count_ticks(args.db)
    if n == 0:
        raise SystemExit(
            "0 ticks readable in db.\n"
            f"  {db_diagnostics(args.db)}\n\n"
            "Fix on Mac:\n"
            "  ./scripts/sync_analytics_mac.sh\n"
            "  python3 backtest_hhhl_candles.py --db data/analytics_mac/ticks.db --lots 1"
        )

    tfs = TIMEFRAMES
    if args.tfs.strip():
        want = {x.strip() for x in args.tfs.split(",") if x.strip()}
        tfs = [(n_, m) for n_, m in TIMEFRAMES if n_ in want]
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
    print(
        f"Filters: fees={args.fees} session={args.session} "
        f"({args.market_open}-{args.market_close}) "
        f"min_range={args.min_range} no_flip={args.no_flip}"
    )

    results = run_all(
        args.db,
        lots=args.lots,
        tfs=tfs,
        allow_long=not args.short_only,
        allow_short=not args.long_only,
        fees=args.fees,
        session_filter=args.session,
        min_range=args.min_range,
        no_flip=args.no_flip,
        market_open=args.market_open,
        market_close=args.market_close,
    )
    print_summary(results)
    print_by_day(results)
    write_outputs(results, args.out_dir)


if __name__ == "__main__":
    main()
