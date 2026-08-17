#!/usr/bin/env python3
"""Tick-replay backtest for live S14 (same closed candle: open=high/low, else wick).

Replays every LTP through WickCandleStrategy. Decisions run once the candle
is finished (first tick of the next bar):

  open = high → SHORT, open = low → LONG, both → skip this check
  else wick: lower>upper LONG, upper>lower SHORT, equal skip

  python3 backtest_s14_tick.py --db data/ticks.db --lots 100 --session --fees
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from backtest_hhhl_candles import (
    Trade,
    TfResult,
    make_charge_cfg,
    write_outputs,
)
from backtest_wick_candles import _tf_result_from_trades, load_ltp_rows
from charges import ChargeConfig, apply_charges_and_tax
from mtf_bars import parse_ts
from strategy_wick import WickCandleStrategy, WickConfig

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
DEFAULT_TFS = ",".join(n for n, _ in TIMEFRAMES)
_TF_BY_NAME = {n: m for n, m in TIMEFRAMES}


def s14_cfg(*, open_hold_minutes: float = 2.0, bar_minutes: int = 30) -> WickConfig:
    """``open_hold_minutes>0`` enables open=high/low on the closed candle (not a timer)."""
    return WickConfig(
        bar_minutes=bar_minutes,
        min_range=0.0,
        confirm_minutes=1,
        nowick_body=False,
        nowick_only=False,
        entry_strict=False,
        exit_strict=False,
        reenter=True,
        wick_anytime=False,
        wick_on_close=True,
        open_hold_minutes=0.0,
        open_hold_on_close=float(open_hold_minutes) > 0,
        allow_long=True,
        allow_short=True,
    )


def in_session_dt(
    dt: datetime, *, open_hhmm: str = "09:00", close_hhmm: str = "23:30"
) -> bool:
    if dt.weekday() >= 5:
        return False
    oh, om = (int(x) for x in open_hhmm.split(":"))
    ch, cm = (int(x) for x in close_hhmm.split(":"))
    start = dt.replace(hour=oh, minute=om, second=0, microsecond=0)
    end = dt.replace(hour=ch, minute=cm, second=0, microsecond=0)
    return start <= dt <= end


def _close_leg(
    *,
    tf: str,
    side: str,
    entry_time: str,
    entry_px: float,
    exit_time: str,
    exit_px: float,
    cfg: ChargeConfig,
) -> Trade:
    if side == "LONG":
        pts = exit_px - entry_px
        order_side = "BUY"
    else:
        pts = entry_px - exit_px
        order_side = "SELL"
    settled = apply_charges_and_tax(
        pts,
        cfg,
        side=order_side,
        entry_price=entry_px,
        exit_price=exit_px,
    )
    return Trade(
        tf=tf,
        side=side,
        entry_time=entry_time,
        entry_px=entry_px,
        exit_time=exit_time,
        exit_px=exit_px,
        gross_pts=float(pts) * float(cfg.lot_size),
        gross_pnl_inr=float(settled["gross_pnl"]),
        after_tax_pnl_inr=float(settled["pnl_after_tax"]),
        fees_inr=float(settled["charges"]),
        lots=float(cfg.lot_size),
    )


def simulate_s14_ticks(
    rows: list[tuple[str, float]],
    *,
    tf: str = "30m:s14",
    lots: float = 1.0,
    fees: bool = False,
    session_filter: bool = False,
    open_hold_minutes: float = 2.0,
    bar_minutes: int = 30,
    market_open: str = "09:00",
    market_close: str = "23:30",
    charge_cfg: ChargeConfig | None = None,
) -> TfResult:
    """Replay LTPs through live S14. Fill at the signal tick."""
    cfg = charge_cfg or make_charge_cfg(fees=fees, lots=lots)
    strat = WickCandleStrategy("S14_WICK30_STRICT", s14_cfg(
        open_hold_minutes=open_hold_minutes,
        bar_minutes=bar_minutes,
    ), seed=False)

    trades: list[Trade] = []
    book_side: str | None = None
    entry_px = 0.0
    entry_time = ""
    n_bars = 0
    last_key = None
    last_px = 0.0
    last_ts = ""

    def flatten(when: str, px: float) -> None:
        nonlocal book_side, entry_px, entry_time
        if book_side is None:
            return
        trades.append(
            _close_leg(
                tf=tf,
                side=book_side,
                entry_time=entry_time,
                entry_px=entry_px,
                exit_time=when,
                exit_px=px,
                cfg=cfg,
            )
        )
        book_side = None
        strat.position = "flat"
        strat.entry_price = None

    def apply_signal(action: str, when: str, px: float) -> None:
        nonlocal book_side, entry_px, entry_time
        if action == "CLOSE":
            flatten(when, px)
            return
        new_side = "LONG" if action == "BUY" else "SHORT"
        if book_side == new_side:
            return
        if book_side is not None:
            flatten(when, px)
        book_side = new_side
        entry_px = px
        entry_time = when

    for raw_ts, ltp in rows:
        now = parse_ts(raw_ts)
        px = float(ltp)
        when = now.strftime("%Y-%m-%d %H:%M:%S")
        last_px = px
        last_ts = when

        if session_filter and not in_session_dt(
            now, open_hhmm=market_open, close_hhmm=market_close
        ):
            flatten(when, px)
            continue

        result = strat.on_tick(now, px)
        key = strat._bar_key
        if key is not None and key != last_key:
            n_bars += 1
            last_key = key
        if result is None:
            continue
        if result.action in {"BUY", "SHORT", "CLOSE"}:
            apply_signal(result.action, when, px)

    flatten(last_ts, last_px)
    return _tf_result_from_trades(tf, n_bars, trades)


def _print_row(r: TfResult) -> None:
    print(
        f"{r.tf:>22}  bars={r.n_bars:5d}  trades={r.n_trades:6d}  "
        f"{r.n_long}/{r.n_short}  win={100 * r.win_rate:5.1f}%  "
        f"pts={r.gross_pts:10.1f}  pnl={r.after_tax_pnl_inr:12.1f}  "
        f"fees={r.fees_inr:10.1f}  dd={r.max_dd_inr:12.1f}",
        flush=True,
    )


def _parse_tfs(raw: str) -> list[tuple[str, int]]:
    want = [x.strip() for x in raw.split(",") if x.strip()]
    out: list[tuple[str, int]] = []
    for name in want:
        key = name.lower().replace(" ", "")
        if key in {"d", "day", "1d", "daily"}:
            key = "1d"
        if key == "30":
            key = "30m"
        if key not in _TF_BY_NAME:
            raise SystemExit(f"unknown tf {name!r}; use {DEFAULT_TFS}")
        out.append((key, _TF_BY_NAME[key]))
    if not out:
        raise SystemExit("no timeframes")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=Path("data/ticks.db"))
    ap.add_argument("--lots", type=float, default=100.0)
    ap.add_argument("--fees", action="store_true")
    ap.add_argument("--session", action="store_true")
    ap.add_argument(
        "--open-hold",
        type=float,
        default=2.0,
        help=">0 apply open=high/low on the closed candle; 0 is wick-only",
    )
    ap.add_argument(
        "--tfs",
        default=DEFAULT_TFS,
        help=f"comma list (default: {DEFAULT_TFS})",
    )
    ap.add_argument("--market-open", default="09:00")
    ap.add_argument("--market-close", default="23:30")
    ap.add_argument(
        "--out-dir",
        type=Path,
        default=Path("data/backtests/s14_tick"),
    )
    ap.add_argument(
        "--compare",
        action="store_true",
        help="also print wick-only (no open=high/low) for each TF",
    )
    args = ap.parse_args()

    if not args.db.exists():
        raise SystemExit(f"missing {args.db}")
    n = 0
    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    try:
        n = int(con.execute("SELECT COUNT(*) FROM ticks WHERE ltp IS NOT NULL").fetchone()[0])
    finally:
        con.close()
    if n <= 0:
        raise SystemExit(
            f"no ticks in {args.db}. Copy the VM tape, then:\n"
            f"  python3 backtest_s14_tick.py --db data/ticks.db --lots 100 --session --fees"
        )

    tfs = _parse_tfs(args.tfs)
    rows = load_ltp_rows(args.db)
    t0 = parse_ts(rows[0][0])
    t1 = parse_ts(rows[-1][0])
    charge_cfg = make_charge_cfg(fees=args.fees, lots=args.lots)
    print(
        f"S14 tick replay  db={args.db}  ticks={n}  "
        f"{t0.isoformat(timespec='seconds')} → {t1.isoformat(timespec='seconds')}  "
        f"lots={args.lots}"
    )
    print(
        "Rules: on the same finished candle | "
        "open=high SHORT | open=low LONG | open=high and open=low skip | "
        "else lower>upper LONG | upper>lower SHORT | equal skip"
    )
    print(
        f"Filters: fees={args.fees} session={args.session} "
        f"({args.market_open}-{args.market_close})  tfs={','.join(n for n, _ in tfs)}"
    )
    print(
        "Note: open=high / open=low and wick all wait for the bar to close "
        "(no +2-minute look). --compare is wick-only (no open=high/low).",
        flush=True,
    )

    results: list[TfResult] = []
    for name, minutes in tfs:
        full = simulate_s14_ticks(
            rows,
            tf=f"{name}:s14",
            lots=args.lots,
            fees=args.fees,
            session_filter=args.session,
            open_hold_minutes=args.open_hold,
            bar_minutes=minutes,
            market_open=args.market_open,
            market_close=args.market_close,
            charge_cfg=charge_cfg,
        )
        results.append(full)
        _print_row(full)
        if args.compare:
            wick_only = simulate_s14_ticks(
                rows,
                tf=f"{name}:s14_wickonly",
                lots=args.lots,
                fees=args.fees,
                session_filter=args.session,
                open_hold_minutes=0.0,
                bar_minutes=minutes,
                market_open=args.market_open,
                market_close=args.market_close,
                charge_cfg=charge_cfg,
            )
            results.append(wick_only)
            _print_row(wick_only)

    print()
    print("=== Day-by-day (after-tax ₹) ===")
    days: set[str] = set()
    for r in results:
        days.update(r.by_day)
    for day in sorted(days):
        print(f"  {day}")
        for r in results:
            d = r.by_day.get(day)
            if not d:
                continue
            print(
                f"    {r.tf:>16}  trades={int(d['n_trades']):5d}  "
                f"pnl={d['after_tax_pnl_inr']:+12.0f}  fees={d['fees_inr']:10.0f}"
            )
        print()
    write_outputs(results, args.out_dir)


if __name__ == "__main__":
    main()
