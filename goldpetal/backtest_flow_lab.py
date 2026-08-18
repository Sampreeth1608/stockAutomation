#!/usr/bin/env python3
"""Order-flow + OI Strategy Factory backtest — research only. Not paper. Not live.

Ranks factory recipes after Angel charges (tax excluded) against paper
S16 and S18. Depth recipes need ticks.db or a full-depth CSV. OI recipes
run on Upstox candles that include open interest.

  ./venv/bin/python backtest_flow_lab.py --csv /tmp/goldpetal_1h_oi.csv \\
      --csv-30m /tmp/goldpetal_30m_oi.csv --lots 100 --fees
  ./venv/bin/python backtest_flow_lab.py --upstox-json 1h.json --upstox-json-30m 30m.json
  ./venv/bin/python backtest_flow_lab.py --db data/ticks.db --minutes 60 --lots 100 --fees
  ./venv/bin/python backtest_flow_lab.py --ticks ticks.csv --minutes 1 --lookback 5
  ./venv/bin/python backtest_flow_lab.py --csv hours.csv --strategy rvol_oi,score --one-by-one
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from backtest_hhhl_candles import print_by_day, write_outputs
from backtest_ohlcv_lab import (
    MIN_TRADES,
    _baseline_s16,
    _book_verdict,
    _days_from_hours,
    _fill_trade_stats,
    _print_trades,
    _verdict,
    _write_lab_trades,
)
from flow_lab import (
    EXIT_ATR,
    EXIT_FLIP,
    FORMULA,
    LAB_NAME,
    RECIPES,
    FlowBar,
    FlowParams,
    LabMetrics,
    flow_bars_from_db,
    flow_bars_from_full_csv,
    flow_bars_from_ohlcv_csv,
    flow_bars_from_upstox_json,
    run_factory_book,
    selected_recipes,
    session_bars,
    tape_flags,
    write_ohlcv_oi_csv,
)
from ohlcv_lab import after_charges_inr
from s18_ohlc_vol_htf import simulate_s18


def _fmt(m: LabMetrics) -> str:
    return (
        f"{m.name:14} {m.family:10} {m.exit_mode:8} "
        f"n={m.n_trades:4d} L/S={m.n_long}/{m.n_short} "
        f"AC₹={m.after_charges:10.1f} exp₹={m.expectancy:8.1f} "
        f"PF={m.profit_factor:5.2f} avgW₹={m.avg_win:8.1f} avgL₹={m.avg_loss:8.1f} "
        f"DDac₹={m.max_dd:10.1f} wf={m.stability:>4} "
        f"wr%={100.0 * m.win_rate:5.1f}"
    )


def _print_table(title: str, rows: list[LabMetrics]) -> None:
    print()
    print(title)
    print(
        f"{'name':14} {'family':10} {'exit':8} {'n':>5} {'L/S':>7} "
        f"{'after_charges₹':>14} {'exp₹':>9} {'PF':>6} {'avgW₹':>9} {'avgL₹':>9} "
        f"{'DDac₹':>10} {'wf':>4} {'wr%':>6}"
    )
    ranked = sorted(rows, key=lambda m: m.after_charges, reverse=True)
    for m in ranked:
        print(
            f"{m.name:14} {m.family:10} {m.exit_mode:8} {m.n_trades:5d} "
            f"{m.n_long:3d}/{m.n_short:<3d} {m.after_charges:14.1f} "
            f"{m.expectancy:9.1f} {m.profit_factor:6.2f} {m.avg_win:9.1f} "
            f"{m.avg_loss:9.1f} {m.max_dd:10.1f} {m.stability:>4} "
            f"{100.0 * m.win_rate:6.1f}"
        )
    print("Rank column is after_charges ₹ (tax excluded). wr% is last on purpose.")


def _run_tf(
    bars: list[FlowBar],
    *,
    tf: str,
    lots: float,
    fees: bool,
    n_folds: int,
    recipes: tuple[Any, ...],
    params: FlowParams,
) -> list[LabMetrics]:
    rows: list[LabMetrics] = []
    kw = dict(lots=lots, fees=fees, flatten_eod=True, params=params)
    for rec in recipes:
        for exit_mode in (EXIT_FLIP, EXIT_ATR):
            m = run_factory_book(
                bars,
                rec.name,
                exit_mode=exit_mode,
                family=rec.family,
                n_folds=n_folds,
                tf=f"{tf}:{rec.name}:{exit_mode}",
                **kw,
            )
            rows.append(m)
    return rows


def _print_one(
    *,
    index: int,
    rec: Any,
    rows_1h: list[LabMetrics],
    rows_30: list[LabMetrics],
    s16: LabMetrics,
    s18: LabMetrics | None,
    trades: bool,
) -> None:
    print()
    print("=" * 88)
    print(f"{index}. {rec.name}  [{rec.family}]  {rec.idea}")
    print("=" * 88)
    flip = next((m for m in rows_1h if m.name == rec.name and m.exit_mode == EXIT_FLIP), None)
    atr = next((m for m in rows_1h if m.name == rec.name and m.exit_mode == EXIT_ATR), None)
    if flip is not None:
        print("1h flip+EOD (same hold style as S16/S18):")
        print(" ", _fmt(flip))
        if flip.wf_rows:
            for i, fold in enumerate(flip.wf_rows, 1):
                print(
                    f"    walk-forward fold {i}: n={fold.n_trades} "
                    f"AC₹={fold.after_charges:.1f} exp₹={fold.expectancy:.1f} "
                    f"PF={fold.profit_factor:.2f} wr%={100.0 * fold.win_rate:.1f}"
                )
        print(" ", _book_verdict(flip, s16, s18))
        if trades and flip.result is not None:
            _print_trades(
                flip.result,
                title=f"1h {rec.name} flip+EOD BUY/SHORT (after charges, tax excluded)",
            )
    if atr is not None:
        print()
        print("1h 2ATR target / 1.5ATR trail:")
        print(" ", _fmt(atr))
        print(" ", _book_verdict(atr, s16, s18))
    m30_flip = next((m for m in rows_30 if m.name == rec.name and m.exit_mode == EXIT_FLIP), None)
    m30_atr = next((m for m in rows_30 if m.name == rec.name and m.exit_mode == EXIT_ATR), None)
    if m30_flip is not None:
        print()
        print("30m flip+EOD (research baseline, not the paper 1h books):")
        print(" ", _fmt(m30_flip))
    if m30_atr is not None:
        print("30m 2ATR / 1.5ATR trail (research baseline):")
        print(" ", _fmt(m30_atr))


def _load_bars(args: argparse.Namespace) -> tuple[list[FlowBar], list[FlowBar], str]:
    m30: list[FlowBar] = []
    if args.upstox_json:
        hours = flow_bars_from_upstox_json(Path(args.upstox_json))
        source = str(args.upstox_json)
        if args.upstox_json_30m:
            m30 = flow_bars_from_upstox_json(Path(args.upstox_json_30m))
        if args.cache_csv:
            write_ohlcv_oi_csv(hours, Path(args.cache_csv))
        return hours, m30, source
    if args.ticks:
        hours = flow_bars_from_full_csv(Path(args.ticks), minutes=int(args.minutes))
        source = f"ticks-csv {args.ticks} {args.minutes}m n_bars={len(hours)}"
        return hours, m30, source
    if args.csv:
        hours = flow_bars_from_ohlcv_csv(Path(args.csv))
        source = str(args.csv)
        if args.csv_30m:
            m30 = flow_bars_from_ohlcv_csv(Path(args.csv_30m))
        return hours, m30, source
    db = Path(args.db)
    hours = flow_bars_from_db(db, minutes=int(args.minutes))
    m30 = flow_bars_from_db(db, minutes=30) if int(args.minutes) != 30 else list(hours)
    source = f"ticks {db} minutes={args.minutes} n_bars={len(hours)}"
    return hours, m30, source


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default="data/ticks.db")
    ap.add_argument("--csv", type=Path, default=None)
    ap.add_argument("--csv-30m", type=Path, default=None)
    ap.add_argument("--ticks", type=Path, default=None, help="full-depth tick CSV")
    ap.add_argument("--upstox-json", type=Path, default=None)
    ap.add_argument("--upstox-json-30m", type=Path, default=None)
    ap.add_argument("--cache-csv", type=Path, default=None, help="write OI CSV from --upstox-json")
    ap.add_argument("--minutes", type=int, default=60, help="bar size when aggregating ticks")
    ap.add_argument("--lookback", type=int, default=20)
    ap.add_argument("--atr-n", type=int, default=14)
    ap.add_argument("--lots", type=float, default=100.0)
    ap.add_argument("--fees", action="store_true", default=True)
    ap.add_argument("--no-fees", action="store_true")
    ap.add_argument("--session", action="store_true", default=True)
    ap.add_argument("--no-session", action="store_true")
    ap.add_argument("--folds", type=int, default=3)
    ap.add_argument("--strategy", action="append", default=None)
    ap.add_argument("--one-by-one", action="store_true")
    ap.add_argument("--trades", action="store_true", help="print BUY/SHORT rows in --one-by-one")
    ap.add_argument("--out-dir", default="data/backtests/flow_lab")
    args = ap.parse_args()
    fees = bool(args.fees) and not bool(args.no_fees)
    session_filter = bool(args.session) and not bool(args.no_session)
    params = FlowParams(lookback=int(args.lookback), atr_n=int(args.atr_n))

    hours, m30, source = _load_bars(args)
    if session_filter:
        hours = session_bars(hours)
        m30 = session_bars(m30) if m30 else m30

    flags = tape_flags(hours)
    print(LAB_NAME, "(research — not a paper book, not live)")
    print(FORMULA)
    print(
        f"tape book={flags['book']} l1={flags['l1']} oi={flags['oi']} "
        f"volume={flags['volume']} source={source}",
        flush=True,
    )
    if not flags["book"]:
        print(
            "No TBQ/TSQ or 5-level depth on this tape — depth recipes are skipped. "
            "Run on the VM with --db data/ticks.db (or --ticks full CSV) for those."
        )
    if not flags["oi"]:
        print("No OI on this tape — OI overlay recipes are skipped.")

    try:
        picked = selected_recipes(args.strategy, flags=flags)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    skipped = []
    want_names: list[str] = []
    if args.strategy:
        for item in args.strategy:
            want_names.extend(x.strip() for x in str(item).split(",") if x.strip())
        picked_names = {r.name for r in picked}
        for n in want_names:
            if n not in picked_names:
                skipped.append(n)
    else:
        picked_names = {r.name for r in picked}
        skipped = [r.name for r in RECIPES if r.name not in picked_names]
    if skipped:
        print("skipped (tape missing fields):", ", ".join(skipped))
    if not picked:
        raise SystemExit("no factory recipes left for this tape")

    need = max(int(args.lookback), int(args.atr_n)) + 5
    if len(hours) < need:
        raise SystemExit(
            f"need ≥{need} bars (lookback={args.lookback}), got {len(hours)} from {source}"
        )

    for rec in picked:
        print(f"  {rec.name:14} [{rec.family}] prio={rec.priority}  {rec.idea}")
    print(
        f"lots={args.lots:g} fees={fees} session={session_filter} "
        f"hours={len(hours)} {hours[0].time}→{hours[-1].time} "
        f"30m={len(m30)} folds={args.folds}",
        flush=True,
    )

    days = _days_from_hours(hours)
    lab_1h = _run_tf(
        hours,
        tf="1h",
        lots=float(args.lots),
        fees=fees,
        n_folds=int(args.folds),
        recipes=picked,
        params=params,
    )
    s16_1h = _fill_trade_stats(
        _baseline_s16(hours, tf="1h:S16", lots=float(args.lots), fees=fees)
    )
    s18_1h: LabMetrics | None = None
    if flags["volume"] and days:
        res18 = simulate_s18(
            hours,
            days,
            tf="1h:S18",
            lots=float(args.lots),
            fees=fees,
            session_filter=True,
        )
        s18_1h = _fill_trade_stats(
            LabMetrics(
                name="S18",
                family="paper",
                exit_mode="flip_eod",
                n_trades=res18.n_trades,
                n_long=res18.n_long,
                n_short=res18.n_short,
                after_charges=after_charges_inr(res18),
                expectancy=0.0,
                profit_factor=0.0,
                avg_win=0.0,
                avg_loss=0.0,
                max_dd=0.0,
                win_rate=0.0,
                result=res18,
            )
        )

    lab_30: list[LabMetrics] = []
    all_rows = list(lab_1h) + [s16_1h]
    if s18_1h is not None:
        all_rows.append(s18_1h)
    if m30:
        lab_30 = _run_tf(
            m30,
            tf="30m",
            lots=float(args.lots),
            fees=fees,
            n_folds=int(args.folds),
            recipes=picked,
            params=params,
        )
        all_rows.extend(lab_30)

    if args.one_by_one:
        print()
        print(
            "Testing one factory recipe at a time. Rank after charges, tax excluded. "
            f"S16 AC₹={s16_1h.after_charges:.1f}"
            + (f"  S18 AC₹={s18_1h.after_charges:.1f}" if s18_1h is not None else "")
            + "."
        )
        for i, rec in enumerate(picked, 1):
            _print_one(
                index=i,
                rec=rec,
                rows_1h=lab_1h,
                rows_30=lab_30,
                s16=s16_1h,
                s18=s18_1h,
                trades=bool(args.trades),
            )
        print()
        print("Recap vs S16/S18 (1h, after charges):")

    _print_table(
        "1h flow/OI factory vs S16/S18  (after charges, tax excluded)",
        lab_1h + [s16_1h] + ([s18_1h] if s18_1h is not None else []),
    )
    print(_verdict(lab_1h, s16_1h, s18_1h))
    if lab_30:
        _print_table(
            "30m flow/OI factory  (research baseline, after charges)",
            lab_30,
        )
    best = max(lab_1h, key=lambda m: m.after_charges)
    if best.result is not None and best.result.trades and not args.one_by_one:
        print()
        print(
            f"Trade-by-trade for best 1h factory row {best.name}/{best.exit_mode} "
            f"(BUY=LONG, SHORT=SHORT). Full CSVs under {args.out_dir}."
        )
        print_by_day([best.result])
        _print_trades(
            best.result,
            title=f"1h {best.name} {best.exit_mode} BUY/SHORT (after charges, tax excluded)",
        )

    out = Path(args.out_dir)
    results = [m.result for m in all_rows if m.result is not None]
    write_outputs(results, out)
    _write_lab_trades(out, all_rows)
    print(f"wrote {out}")
    print(
        "Stay DRY_RUN. Paper 100 lots is not live. FLOW_LAB is research-only: "
        "not on the desk, not ENABLE_*, not a paper book. Depth edge claims "
        "need a real ticks.db, not the synthetic 15-minute CSV."
    )


if __name__ == "__main__":
    main()
