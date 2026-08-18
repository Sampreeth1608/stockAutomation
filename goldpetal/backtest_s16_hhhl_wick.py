#!/usr/bin/env python3
"""Backtest the one S16 formula: up-close HH/LL, down-close wick, FLIP.

Research only. Do not paper or live-enable until a 100-lot + fees row is picked.

  python3 backtest_s16_hhhl_wick.py --db data/ticks.db --lots 100 --session --fees
  ./venv/bin/python backtest_s16_hhhl_wick.py --from-angel --tf 30m,1h,1d --from 2026-08-02
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from backtest_hhhl_candles import (
    TIMEFRAMES,
    Candle,
    TfResult,
    make_charge_cfg,
    print_by_day,
    write_outputs,
)
from backtest_wick_candles import (
    build_ohlc_candles,
    load_ltp_rows,
    print_wick_summary,
)
from s16_hhhl_wick import FORMULA, simulate_s16, walk_candles

IST = ZoneInfo("Asia/Kolkata")
DEFAULT_TICK_TFS = ",".join(n for n, _ in TIMEFRAMES)


def candles_from_dicts(rows: list[dict[str, Any]]) -> list[Candle]:
    out: list[Candle] = []
    for r in rows:
        t = str(r["time"]).replace("T", " ")[:19]
        out.append(
            Candle(
                time=t,
                open=float(r["open"]),
                high=float(r["high"]),
                low=float(r["low"]),
                close=float(r["close"]),
            )
        )
    return out


def write_walk_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "time",
        "open",
        "high",
        "low",
        "close",
        "prev_close",
        "prev_high",
        "prev_low",
        "upper",
        "lower",
        "gate",
        "hhhl",
        "wick",
        "rule",
        "side",
        "action",
        "pos_before",
        "pos_after",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def _parse_tick_tfs(raw: str) -> list[tuple[str, int]]:
    by = {n: m for n, m in TIMEFRAMES}
    out: list[tuple[str, int]] = []
    for name in (x.strip().lower() for x in raw.split(",") if x.strip()):
        if name == "30":
            name = "30m"
        if name in {"d", "day", "daily"}:
            name = "1d"
        if name not in by:
            raise SystemExit(f"unknown tf {name!r}. have: {', '.join(by)}")
        out.append((name, by[name]))
    if not out:
        raise SystemExit("no timeframes")
    return out


def run_from_ticks(
    db: Path,
    *,
    lots: float,
    fees: bool,
    session_filter: bool,
    tfs: list[tuple[str, int]],
    drop_last: bool,
    out_dir: Path,
    print_bars_tf: str | None,
    print_skips: bool,
) -> list[TfResult]:
    rows = load_ltp_rows(db)
    if not rows:
        raise SystemExit(f"no ticks in {db}")
    cfg = make_charge_cfg(fees=fees, lots=lots)
    results: list[TfResult] = []
    for name, minutes in tfs:
        candles = build_ohlc_candles(rows, minutes)
        if drop_last and len(candles) >= 2:
            candles = candles[:-1]
        use_session = session_filter and name != "1d"
        r = simulate_s16(
            candles,
            tf=f"{name}:s16",
            lots=lots,
            fees=fees,
            session_filter=use_session,
            charge_cfg=cfg,
        )
        results.append(r)
        walk = walk_candles(candles)
        write_walk_csv(out_dir / f"{name}_walk.csv", walk)
        if print_bars_tf == name:
            shown = walk if print_skips else [row for row in walk if row["action"] != "skip"]
            print(flush=True)
            print(
                f"=== {name} bars (finished"
                + ("" if print_skips else ", skips hidden")
                + ") ===",
                flush=True,
            )
            for row in shown:
                print(
                    f"{row['time']:<22} O={row['open']:.1f} H={row['high']:.1f} "
                    f"L={row['low']:.1f} C={row['close']:.1f}  "
                    f"gate={row['gate']:<4}  "
                    f"U={row['upper']:.1f} Lw={row['lower']:.1f}  "
                    f"hhhl={row['hhhl']:<5} wick={row['wick']:<5}  "
                    f"{row['rule']:<28} → {str(row['side']).upper():<5}  "
                    f"{row['action']:<5}  {row['pos_before']}→{row['pos_after']}",
                    flush=True,
                )
    return results


def run_from_angel(
    *,
    lots: float,
    fees: bool,
    session_filter: bool,
    tfs: list[str],
    date_from: str,
    date_to: str,
    out_dir: Path,
    print_bars_tf: str | None,
    print_skips: bool,
) -> list[TfResult]:
    from explain_s14_candles import (
        ANGEL_INTERVAL,
        bar_is_finished,
        fetch_angel_candles,
        in_session_dt,
        parse_bar_ts,
        reexec_with_bot_python,
    )
    from auth import login
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
    cfg = make_charge_cfg(fees=fees, lots=lots)
    results: list[TfResult] = []
    for tf in tfs:
        raw = fetch_angel_candles(
            api,
            token=token,
            interval=ANGEL_INTERVAL[tf],
            start=start,
            end=end,
            exchange=str(contract.get("exchange") or "MCX"),
        )
        if session_filter and tf != "1d":
            raw = [b for b in raw if in_session_dt(parse_bar_ts(b["time"]), tf=tf)]
        raw = [b for b in raw if bar_is_finished(b["time"], tf, now)]
        candles = candles_from_dicts(raw)
        r = simulate_s16(
            candles,
            tf=f"{tf}:s16",
            lots=lots,
            fees=fees,
            session_filter=False,
            charge_cfg=cfg,
        )
        results.append(r)
        walk = walk_candles(candles)
        write_walk_csv(out_dir / f"{tf}_{symbol}.csv", walk)
        if print_bars_tf == tf:
            shown = walk if print_skips else [row for row in walk if row["action"] != "skip"]
            print(flush=True)
            print(f"=== {tf} {symbol} ===", flush=True)
            for row in shown:
                print(
                    f"{row['time']:<22} gate={row['gate']:<4} {row['rule']:<28} → "
                    f"{str(row['side']).upper():<5} {row['action']}",
                    flush=True,
                )
    return results


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=Path("data/ticks.db"))
    ap.add_argument("--from-angel", action="store_true")
    ap.add_argument("--tf", default="")
    ap.add_argument("--from", dest="date_from", default="2026-08-02")
    ap.add_argument("--to", dest="date_to", default="")
    ap.add_argument("--lots", type=float, default=100.0)
    ap.add_argument("--fees", action="store_true", default=True)
    ap.add_argument("--no-fees", action="store_true")
    ap.add_argument("--session", action="store_true", default=True)
    ap.add_argument("--no-session", action="store_true")
    ap.add_argument("--keep-last", action="store_true", help="keep still-forming last tick bar")
    ap.add_argument("--print-bars", default="30m", help="print this TF's bars (or '')")
    ap.add_argument("--print-skips", action="store_true", help="include skip rows in the bar dump")
    ap.add_argument("--out", type=Path, default=Path("data/backtests/s16_hhhl_wick"))
    args = ap.parse_args()
    fees = bool(args.fees) and not bool(args.no_fees)
    session_filter = bool(args.session) and not bool(args.no_session)
    print_bars_tf = (args.print_bars or "").strip().lower() or None
    if print_bars_tf == "30":
        print_bars_tf = "30m"

    print(FORMULA, flush=True)
    print(
        f"lots={args.lots:g}  fees={fees}  session={session_filter}  "
        f"source={'Angel' if args.from_angel else args.db}",
        flush=True,
    )

    if args.from_angel:
        from explain_s14_candles import ANGEL_INTERVAL, _parse_tfs

        tfs = _parse_tfs(args.tf or "30m,1h,1d")
        results = run_from_angel(
            lots=args.lots,
            fees=fees,
            session_filter=session_filter,
            tfs=tfs,
            date_from=args.date_from,
            date_to=args.date_to,
            out_dir=args.out,
            print_bars_tf=print_bars_tf,
            print_skips=bool(args.print_skips),
        )
    else:
        tfs = _parse_tick_tfs(args.tf or DEFAULT_TICK_TFS)
        results = run_from_ticks(
            args.db,
            lots=args.lots,
            fees=fees,
            session_filter=session_filter,
            tfs=tfs,
            drop_last=not args.keep_last,
            out_dir=args.out,
            print_bars_tf=print_bars_tf,
            print_skips=bool(args.print_skips),
        )

    print_wick_summary(
        results,
        title=f"S16 C>prev HH/LL / C<prev wick  lots={args.lots:g}  fees={fees}",
    )
    print_by_day(results)
    write_outputs(results, args.out)
    print(f"wrote {args.out}", flush=True)
    print("Not paper. Not live. Pick a TF row first.", flush=True)


if __name__ == "__main__":
    main()
