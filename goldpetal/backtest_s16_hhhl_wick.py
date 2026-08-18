#!/usr/bin/env python3
"""Backtest the one S16 formula: up-close HH/LL, down-close wick, FLIP.

Research only. Do not paper or live-enable until a 100-lot + fees row is picked.

  python3 backtest_s16_hhhl_wick.py --db data/ticks.db --lots 100 --session --fees
  ./venv/bin/python backtest_s16_hhhl_wick.py --from-angel --tf 30m,1h,1d --from 2026-08-02

Default sweeps wick gaps 0,3,5,10 on the down-close path. 30m bars print for gap=3.
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
        "wick_gap",
        "min_wick_gap",
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


def _parse_gaps(raw: str) -> list[float]:
    out: list[float] = []
    for bit in (x.strip() for x in raw.split(",") if x.strip()):
        g = float(bit)
        if g < 0:
            raise SystemExit(f"wick gap must be ≥ 0, got {g}")
        out.append(g)
    if not out:
        raise SystemExit("no wick gaps")
    return out


def _gap_tag(gap: float) -> str:
    if float(gap) == int(gap):
        return f"g{int(gap)}"
    return f"g{gap:g}"


def _gaps_eq(a: float, b: float) -> bool:
    return abs(float(a) - float(b)) < 1e-9


def _split_tf_tag(name: str) -> tuple[str, str]:
    tf, sep, tag = name.partition(":")
    return tf, tag if sep else "g?"


def _group_gap_results(
    results: list[TfResult],
) -> tuple[list[str], list[str], dict[str, dict[str, TfResult]]]:
    tf_order: list[str] = []
    tag_order: list[str] = []
    by: dict[str, dict[str, TfResult]] = {}
    for r in results:
        tf, tag = _split_tf_tag(r.tf)
        if tf not in by:
            by[tf] = {}
            tf_order.append(tf)
        by[tf][tag] = r
        if tag not in tag_order:
            tag_order.append(tag)
    return tf_order, tag_order, by


def print_gap_compare(results: list[TfResult]) -> None:
    """One block per TF: every wick gap, plus Δ₹ vs the first gap (g0)."""
    tf_order, tag_order, by = _group_gap_results(results)
    if not tf_order:
        return
    base = tag_order[0]
    print(f"=== Wick-gap compare — all TF × all gaps (Δ₹ vs {base}) ===")
    for tf in tf_order:
        row = by[tf]
        any_r = next(iter(row.values()))
        print(f"  {tf}  bars={any_r.n_bars}")
        print(
            f"    {'gap':>4}  {'tr':>4}  {'L/S':>7}  {'win%':>6}  "
            f"{'₹':>10}  {'fees':>8}  {'maxDD':>8}  {'Δ₹':>10}"
        )
        base_pnl = row[base].after_tax_pnl_inr if base in row else 0.0
        for tag in tag_order:
            r = row.get(tag)
            if r is None:
                print(f"    {tag:>4}  —")
                continue
            delta = "—" if tag == base else f"{r.after_tax_pnl_inr - base_pnl:+.0f}"
            print(
                f"    {tag:>4}  {r.n_trades:4d}  {r.n_long:3d}/{r.n_short:<3d}  "
                f"{100 * r.win_rate:5.1f}%  {r.after_tax_pnl_inr:10.0f}  "
                f"{r.fees_inr:8.0f}  {r.max_dd_inr:8.0f}  {delta:>10}"
            )
        print()
    print(f"Δ₹ = this gap minus {base}. Positive means the wider wick gap helped after tax.")
    print()


def print_by_day_gaps(results: list[TfResult]) -> None:
    tf_order, tag_order, by = _group_gap_results(results)
    print("=== Day-by-day after-tax ₹ — all TF × all gaps ===")
    for tf in tf_order:
        days: set[str] = set()
        for r in by[tf].values():
            days.update(r.by_day)
        if not days:
            continue
        print(f"  -- {tf} --")
        for day in sorted(days):
            bits: list[str] = []
            for tag in tag_order:
                r = by[tf].get(tag)
                d = r.by_day.get(day) if r else None
                if not d:
                    bits.append(f"{tag}:—")
                    continue
                bits.append(
                    f"{tag}:{d['after_tax_pnl_inr']:+.0f}({int(d['n_trades'])}t)"
                )
            print(f"  {day}  " + "  ".join(bits))
    print()


def write_gap_compare_csv(path: Path, results: list[TfResult]) -> None:
    tf_order, tag_order, by = _group_gap_results(results)
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "tf",
        "gap",
        "n_bars",
        "n_trades",
        "n_long",
        "n_short",
        "win_rate",
        "gross_pts",
        "fees_inr",
        "after_tax_pnl_inr",
        "max_dd_inr",
        "delta_vs_base_inr",
    ]
    base = tag_order[0] if tag_order else ""
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for tf in tf_order:
            base_pnl = (
                by[tf][base].after_tax_pnl_inr if base in by[tf] else 0.0
            )
            for tag in tag_order:
                r = by[tf].get(tag)
                if r is None:
                    continue
                w.writerow(
                    {
                        "tf": tf,
                        "gap": tag,
                        "n_bars": r.n_bars,
                        "n_trades": r.n_trades,
                        "n_long": r.n_long,
                        "n_short": r.n_short,
                        "win_rate": round(r.win_rate, 4),
                        "gross_pts": round(r.gross_pts, 2),
                        "fees_inr": round(r.fees_inr, 2),
                        "after_tax_pnl_inr": round(r.after_tax_pnl_inr, 2),
                        "max_dd_inr": round(r.max_dd_inr, 2),
                        "delta_vs_base_inr": round(
                            r.after_tax_pnl_inr - base_pnl, 2
                        ),
                    }
                )


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
    wick_gaps: list[float],
    print_gap: float,
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
        for gap in wick_gaps:
            tag = _gap_tag(gap)
            r = simulate_s16(
                candles,
                tf=f"{name}:{tag}",
                lots=lots,
                fees=fees,
                session_filter=use_session,
                charge_cfg=cfg,
                min_wick_gap=gap,
            )
            results.append(r)
            walk = walk_candles(candles, min_wick_gap=gap)
            write_walk_csv(out_dir / f"{name}_{tag}_walk.csv", walk)
            if print_bars_tf == name and _gaps_eq(gap, print_gap):
                shown = walk if print_skips else [row for row in walk if row["action"] != "skip"]
                print(flush=True)
                print(
                    f"=== {name} bars wick-gap={gap:g} (finished"
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
                        f"Δ={row['wick_gap']:.1f}  "
                        f"{row['rule']:<32} → {str(row['side']).upper():<5}  "
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
    wick_gaps: list[float],
    print_gap: float,
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
        for gap in wick_gaps:
            tag = _gap_tag(gap)
            r = simulate_s16(
                candles,
                tf=f"{tf}:{tag}",
                lots=lots,
                fees=fees,
                session_filter=False,
                charge_cfg=cfg,
                min_wick_gap=gap,
            )
            results.append(r)
            walk = walk_candles(candles, min_wick_gap=gap)
            write_walk_csv(out_dir / f"{tf}_{tag}_{symbol}.csv", walk)
            if print_bars_tf == tf and _gaps_eq(gap, print_gap):
                shown = walk if print_skips else [row for row in walk if row["action"] != "skip"]
                print(flush=True)
                print(f"=== {tf} {symbol} wick-gap={gap:g} ===", flush=True)
                for row in shown:
                    print(
                        f"{row['time']:<22} gate={row['gate']:<4} Δ={row['wick_gap']:.1f} "
                        f"{row['rule']:<32} → "
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
    ap.add_argument(
        "--wick-gaps",
        default="0,3,5,10",
        help="comma list of min |upper-lower| on down-close wicks",
    )
    ap.add_argument(
        "--print-gap",
        type=float,
        default=3.0,
        help="which wick gap gets the 30m bar dump",
    )
    ap.add_argument("--out", type=Path, default=Path("data/backtests/s16_hhhl_wick"))
    args = ap.parse_args()
    fees = bool(args.fees) and not bool(args.no_fees)
    session_filter = bool(args.session) and not bool(args.no_session)
    print_bars_tf = (args.print_bars or "").strip().lower() or None
    if print_bars_tf == "30":
        print_bars_tf = "30m"

    wick_gaps = _parse_gaps(args.wick_gaps)
    print_gap = float(args.print_gap)
    if not any(_gaps_eq(print_gap, g) for g in wick_gaps):
        print_gap = wick_gaps[0]

    print(FORMULA, flush=True)
    print(
        f"lots={args.lots:g}  fees={fees}  session={session_filter}  "
        f"wick_gaps={','.join(str(g) if g != int(g) else str(int(g)) for g in wick_gaps)}  "
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
            wick_gaps=wick_gaps,
            print_gap=print_gap,
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
            wick_gaps=wick_gaps,
            print_gap=print_gap,
        )

    print_wick_summary(
        results,
        title=(
            f"S16 C>prev HH/LL / C<prev wick-gap  lots={args.lots:g}  fees={fees}"
        ),
    )
    print_gap_compare(results)
    print_by_day_gaps(results)
    write_outputs(results, args.out)
    write_gap_compare_csv(args.out / "gap_compare.csv", results)
    print(f"wrote {args.out} (including gap_compare.csv)", flush=True)
    print("Not paper. Not live. Pick a TF row first.", flush=True)


if __name__ == "__main__":
    main()
