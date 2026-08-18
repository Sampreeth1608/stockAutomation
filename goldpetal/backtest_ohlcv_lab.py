#!/usr/bin/env python3
"""OHLCV Strategy Laboratory backtest — research only. Not paper. Not live.

Seven OHLCV strategies × two exits (flip+EOD, 2ATR/1.5ATR trail), ranked
after Angel charges (tax excluded) by expectancy / profit factor /
drawdown / walk-forward. Win rate is printed last on purpose.

  ./venv/bin/python backtest_ohlcv_lab.py --csv /tmp/goldpetal_1h_upstox.csv \\
      --csv-30m /tmp/goldpetal_30m_upstox.csv --lots 100 --fees --one-by-one
  ./venv/bin/python backtest_ohlcv_lab.py --csv hours.csv --strategy breakout --one-by-one --lots 100 --fees
  ./venv/bin/python backtest_ohlcv_lab.py --db data/ticks.db --lots 100 --session --fees
  ./venv/bin/python backtest_ohlcv_lab.py --from-angel --from 2026-08-02 --lots 100 --fees
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from backtest_hhhl_candles import Candle, Trade, print_by_day, write_outputs
from s16_hhhl_wick import simulate_s16
from s18_ohlc_vol_htf import VolBar, build_vol_bars, load_vol_rows, simulate_s18
from s19_body_close import hours_from_ohlc
from s20_fade_hl import aggregate_candles
from ohlcv_lab import (
    EXIT_ATR,
    EXIT_FLIP,
    FORMULA,
    LAB_NAME,
    STRATEGIES,
    LabMetrics,
    after_charges_inr,
    run_lab_book,
    selected_strategies,
    session_bars,
    trade_after_charges,
)

IST = ZoneInfo("Asia/Kolkata")
MIN_TRADES = 20


def _days_from_hours(hours: list[VolBar]) -> list[VolBar]:
    candles = [Candle(b.time, b.open, b.high, b.low, b.close) for b in hours]
    days = aggregate_candles(candles, 1440)
    by_t = {c.time[:10]: 0.0 for c in days}
    for b in hours:
        by_t[b.day] = by_t.get(b.day, 0.0) + float(b.volume)
    return [
        VolBar(c.time, c.open, c.high, c.low, c.close, by_t.get(c.time[:10], 0.0))
        for c in days
    ]


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
    return hours_from_ohlc(raw_h), hours_from_ohlc(raw_d), f"Angel {symbol}"


def _fmt(m: LabMetrics) -> str:
    return (
        f"{m.name:12} {m.family:9} {m.exit_mode:8} "
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
        f"{'name':12} {'family':9} {'exit':8} {'n':>5} {'L/S':>7} "
        f"{'after_charges₹':>14} {'exp₹':>9} {'PF':>6} {'avgW₹':>9} {'avgL₹':>9} "
        f"{'DDac₹':>10} {'wf':>4} {'wr%':>6}"
    )
    ranked = sorted(rows, key=lambda m: m.after_charges, reverse=True)
    for m in ranked:
        print(
            f"{m.name:12} {m.family:9} {m.exit_mode:8} {m.n_trades:5d} "
            f"{m.n_long:3d}/{m.n_short:<3d} {m.after_charges:14.1f} "
            f"{m.expectancy:9.1f} {m.profit_factor:6.2f} {m.avg_win:9.1f} "
            f"{m.avg_loss:9.1f} {m.max_dd:10.1f} {m.stability:>4} "
            f"{100.0 * m.win_rate:6.1f}"
        )
    print("Rank column is after_charges ₹ (tax excluded). wr% is last on purpose.")


def _write_lab_trades(out_dir: Path, rows: list[LabMetrics]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    board = out_dir / "scoreboard.csv"
    with board.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "name",
                "family",
                "exit_mode",
                "n_trades",
                "n_long",
                "n_short",
                "after_charges",
                "expectancy",
                "profit_factor",
                "avg_win",
                "avg_loss",
                "max_dd_after_charges",
                "walk_forward",
                "win_rate",
            ],
        )
        w.writeheader()
        for m in rows:
            w.writerow(
                {
                    "name": m.name,
                    "family": m.family,
                    "exit_mode": m.exit_mode,
                    "n_trades": m.n_trades,
                    "n_long": m.n_long,
                    "n_short": m.n_short,
                    "after_charges": f"{m.after_charges:.2f}",
                    "expectancy": f"{m.expectancy:.2f}",
                    "profit_factor": f"{m.profit_factor:.4f}",
                    "avg_win": f"{m.avg_win:.2f}",
                    "avg_loss": f"{m.avg_loss:.2f}",
                    "max_dd_after_charges": f"{m.max_dd:.2f}",
                    "walk_forward": m.stability,
                    "win_rate": f"{m.win_rate:.4f}",
                }
            )
    for m in rows:
        if m.result is None:
            continue
        path = out_dir / f"trades_{m.result.tf}.csv"
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(
                f,
                fieldnames=[
                    "tf",
                    "signal",
                    "side",
                    "entry_time",
                    "entry_px",
                    "exit_time",
                    "exit_px",
                    "gross_pts",
                    "gross_pnl_inr",
                    "fees_inr",
                    "after_charges_inr",
                    "after_tax_pnl_inr",
                    "lots",
                ],
            )
            w.writeheader()
            t: Trade
            for t in m.result.trades:
                w.writerow(
                    {
                        "tf": t.tf,
                        "signal": "BUY" if t.side == "LONG" else "SHORT",
                        "side": t.side,
                        "entry_time": t.entry_time,
                        "entry_px": t.entry_px,
                        "exit_time": t.exit_time,
                        "exit_px": t.exit_px,
                        "gross_pts": t.gross_pts,
                        "gross_pnl_inr": t.gross_pnl_inr,
                        "fees_inr": t.fees_inr,
                        "after_charges_inr": trade_after_charges(t),
                        "after_tax_pnl_inr": t.after_tax_pnl_inr,
                        "lots": t.lots,
                    }
                )


def _baseline_s16(hours: list[VolBar], *, tf: str, lots: float, fees: bool) -> LabMetrics:
    candles = [Candle(b.time, b.open, b.high, b.low, b.close) for b in hours]
    res = simulate_s16(
        candles,
        tf=tf,
        lots=lots,
        fees=fees,
        session_filter=True,
        min_wick_gap=0.0,
    )
    return LabMetrics(
        name="S16",
        family="paper",
        exit_mode="flip_eod",
        n_trades=res.n_trades,
        n_long=res.n_long,
        n_short=res.n_short,
        after_charges=after_charges_inr(res),
        expectancy=(after_charges_inr(res) / res.n_trades) if res.n_trades else 0.0,
        profit_factor=0.0,
        avg_win=0.0,
        avg_loss=0.0,
        max_dd=0.0,
        win_rate=0.0,
        result=res,
    )


def _fill_trade_stats(m: LabMetrics) -> LabMetrics:
    from ohlcv_lab import score_result

    if m.result is None:
        return m
    return score_result(
        m.result,
        name=m.name,
        family=m.family,
        exit_mode=m.exit_mode,
        wf_wins=m.wf_wins,
        wf_folds=m.wf_folds,
    )


def _print_trades(result: Any, *, title: str) -> None:
    print()
    print(title)
    trades = list(getattr(result, "trades", []) or [])
    if not trades:
        print("  (no trades)")
        return
    print("  n  signal  entry_time            entry      exit_time             exit        AC₹")
    for i, t in enumerate(trades, 1):
        sig = "BUY  " if t.side == "LONG" else "SHORT"
        print(
            f"  {i:2d}  {sig}  {t.entry_time}  {t.entry_px:8.1f}  "
            f"{t.exit_time}  {t.exit_px:8.1f}  {trade_after_charges(t):10.1f}"
        )


def _book_verdict(m: LabMetrics, s16: LabMetrics, s18: LabMetrics | None) -> str:
    bits: list[str] = []
    if m.n_trades < MIN_TRADES:
        bits.append(f"thin sample ({m.n_trades} < {MIN_TRADES} trades)")
    if m.after_charges <= 0:
        bits.append("after charges ≤ 0")
    if m.after_charges > s16.after_charges:
        bits.append(f"beats S16 (AC₹={s16.after_charges:.1f})")
    else:
        bits.append(f"loses to S16 (AC₹={s16.after_charges:.1f})")
    if s18 is not None:
        if m.after_charges > s18.after_charges:
            bits.append(f"beats S18 (AC₹={s18.after_charges:.1f})")
        else:
            bits.append(f"loses to S18 (AC₹={s18.after_charges:.1f})")
    enough = (
        m.n_trades >= MIN_TRADES
        and m.after_charges > 0
        and m.after_charges > s16.after_charges
        and (s18 is None or m.after_charges > s18.after_charges)
    )
    if enough:
        bits.append("still research — do not ENABLE unless asked to paper")
    else:
        bits.append("do not paper, do not ENABLE")
    return "; ".join(bits)


def _print_one(
    *,
    index: int,
    name: str,
    family: str,
    idea: str,
    rows_1h: list[LabMetrics],
    rows_30: list[LabMetrics],
    s16: LabMetrics,
    s18: LabMetrics | None,
) -> None:
    print()
    print("=" * 88)
    print(f"{index}. {name}  [{family}]  {idea}")
    print("=" * 88)
    flip = next((m for m in rows_1h if m.name == name and m.exit_mode == EXIT_FLIP), None)
    atr = next((m for m in rows_1h if m.name == name and m.exit_mode == EXIT_ATR), None)
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
        if flip.result is not None:
            _print_trades(
                flip.result,
                title=f"1h {name} flip+EOD BUY/SHORT (after charges, tax excluded)",
            )
    if atr is not None:
        print()
        print("1h 2ATR target / 1.5ATR trail:")
        print(" ", _fmt(atr))
        print(" ", _book_verdict(atr, s16, s18))
    m30_flip = next((m for m in rows_30 if m.name == name and m.exit_mode == EXIT_FLIP), None)
    if m30_flip is not None:
        print()
        print("30m flip+EOD (research baseline, not the paper 1h books):")
        print(" ", _fmt(m30_flip))


def _run_tf(
    bars: list[VolBar],
    *,
    tf: str,
    lots: float,
    fees: bool,
    n_folds: int,
    strategies: tuple[tuple[str, str, str], ...] | None = None,
) -> list[LabMetrics]:
    rows: list[LabMetrics] = []
    kw = dict(lots=lots, fees=fees, flatten_eod=True)
    use = strategies or STRATEGIES
    for name, family, _idea in use:
        for exit_mode in (EXIT_FLIP, EXIT_ATR):
            m = run_lab_book(
                bars,
                name,
                exit_mode=exit_mode,
                family=family,
                n_folds=n_folds,
                tf=f"{tf}:{name}:{exit_mode}",
                **kw,
            )
            rows.append(m)
    return rows


def _verdict(lab_rows: list[LabMetrics], s16: LabMetrics, s18: LabMetrics | None) -> str:
    s16_ac = s16.after_charges
    s18_ac = s18.after_charges if s18 is not None else None
    beat: list[LabMetrics] = []
    for m in lab_rows:
        if m.n_trades < MIN_TRADES or m.after_charges <= 0:
            continue
        if m.after_charges <= s16_ac:
            continue
        if s18_ac is not None and m.after_charges <= s18_ac:
            continue
        beat.append(m)
    if not beat:
        extra = (
            f"S16 AC₹={s16_ac:.1f}"
            + (f" S18 AC₹={s18_ac:.1f}" if s18_ac is not None else "")
        )
        return (
            f"No lab book beats S16"
            + (" and S18" if s18 is not None else "")
            + f" after charges with ≥{MIN_TRADES} trades ({extra}). "
            "Research only. Do not paper. Do not ENABLE."
        )
    names = ", ".join(f"{m.name}/{m.exit_mode} AC₹={m.after_charges:.1f}" for m in beat)
    return (
        f"Lab beat S16"
        + (" and S18" if s18 is not None else "")
        + f" after charges (≥{MIN_TRADES} trades): {names}. "
        "Still research — do not ENABLE unless the operator asks to paper it."
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default="data/ticks.db")
    ap.add_argument("--from-angel", action="store_true")
    ap.add_argument("--csv", type=Path, default=None)
    ap.add_argument("--csv-30m", type=Path, default=None)
    ap.add_argument("--from", dest="date_from", default="2026-08-02")
    ap.add_argument("--to", dest="date_to", default="")
    ap.add_argument("--lots", type=float, default=100.0)
    ap.add_argument("--fees", action="store_true", default=True)
    ap.add_argument("--no-fees", action="store_true")
    ap.add_argument("--session", action="store_true", default=True)
    ap.add_argument("--no-session", action="store_true")
    ap.add_argument("--folds", type=int, default=3)
    ap.add_argument(
        "--strategy",
        action="append",
        default=None,
        help="test only these lab names (repeat or comma: breakout,climax,pullback,consol,vwap,rvol_mom,three)",
    )
    ap.add_argument(
        "--one-by-one",
        action="store_true",
        help="print each strategy separately with BUY/SHORT trades (1h flip+EOD)",
    )
    ap.add_argument("--out-dir", default="data/backtests/ohlcv_lab")
    args = ap.parse_args()
    fees = bool(args.fees) and not bool(args.no_fees)
    session_filter = bool(args.session) and not bool(args.no_session)

    m30: list[VolBar] = []
    days: list[VolBar] = []
    if args.from_angel:
        hours, days, source = _load_angel(args.date_from, args.date_to)
    elif args.csv:
        with args.csv.open(newline="") as f:
            hours = hours_from_ohlc(list(csv.DictReader(f)))
        days = _days_from_hours(hours)
        source = str(args.csv)
        if args.csv_30m:
            with args.csv_30m.open(newline="") as f:
                m30 = hours_from_ohlc(list(csv.DictReader(f)))
    else:
        db = Path(args.db)
        rows = load_vol_rows(db)
        hours = build_vol_bars(rows, 60)
        days = build_vol_bars(rows, 1440)
        m30 = build_vol_bars(rows, 30)
        source = f"ticks {db} n={len(rows)}"

    if session_filter:
        hours = session_bars(hours)
        m30 = session_bars(m30) if m30 else m30

    if len(hours) < 25:
        raise SystemExit(f"need ≥25 1h bars, got {len(hours)} from {source}")

    try:
        picked = selected_strategies(args.strategy)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    print(LAB_NAME, "(research — not a paper book, not live)")
    print(FORMULA)
    for name, family, idea in picked:
        print(f"  {name:10} [{family}] {idea}")
    print(
        f"lots={args.lots:g} fees={fees} session={session_filter} "
        f"hours={len(hours)} {hours[0].time}→{hours[-1].time} "
        f"30m={len(m30)} days={len(days)} folds={args.folds} "
        f"one_by_one={bool(args.one_by_one)} source={source}",
        flush=True,
    )

    kw_base = dict(
        lots=float(args.lots),
        fees=fees,
        n_folds=int(args.folds),
        strategies=picked,
    )
    lab_1h = _run_tf(hours, tf="1h", **kw_base)
    s16_1h = _fill_trade_stats(
        _baseline_s16(hours, tf="1h:S16", lots=float(args.lots), fees=fees)
    )
    s18_1h: LabMetrics | None = None
    has_vol = any(b.volume > 0 for b in hours)
    if has_vol and days:
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
        lab_30 = _run_tf(m30, tf="30m", **kw_base)
        s16_30 = _fill_trade_stats(
            _baseline_s16(m30, tf="30m:S16", lots=float(args.lots), fees=fees)
        )
        all_rows.extend(lab_30)
        all_rows.append(s16_30)

    if args.one_by_one:
        print()
        print(
            "Testing one strategy at a time on 1h (flip+EOD trades). "
            "Rank after charges, tax excluded. S16 AC₹="
            f"{s16_1h.after_charges:.1f}"
            + (
                f"  S18 AC₹={s18_1h.after_charges:.1f}"
                if s18_1h is not None
                else ""
            )
            + "."
        )
        for i, (name, family, idea) in enumerate(picked, 1):
            _print_one(
                index=i,
                name=name,
                family=family,
                idea=idea,
                rows_1h=lab_1h,
                rows_30=lab_30,
                s16=s16_1h,
                s18=s18_1h,
            )
        print()
        print("Recap vs S16/S18 (1h, after charges):")
        _print_table(
            "1h OHLCV lab vs S16/S18  (after charges, tax excluded)",
            lab_1h + [s16_1h] + ([s18_1h] if s18_1h is not None else []),
        )
        print(_verdict(lab_1h, s16_1h, s18_1h))
    else:
        _print_table(
            "1h OHLCV lab vs S16/S18  (after charges, tax excluded)",
            lab_1h + [s16_1h] + ([s18_1h] if s18_1h is not None else []),
        )
        print(_verdict(lab_1h, s16_1h, s18_1h))
        if lab_30:
            s16_30_row = next(m for m in all_rows if m.name == "S16" and m.result and m.result.tf.startswith("30m"))
            _print_table(
                "30m OHLCV lab vs S16  (after charges, tax excluded)",
                lab_30 + [s16_30_row],
            )
            print(_verdict(lab_30, s16_30_row, None))
            print(
                "30m S16 is a research baseline on 30m bars, not the paper 1h S16 book. "
                "Paper rank is the 1h table vs S16 and S18."
            )
        best = max(lab_1h, key=lambda m: m.after_charges)
        if best.result is not None and best.result.trades:
            print()
            print(
                f"Trade-by-trade for best 1h lab row {best.name}/{best.exit_mode} "
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
        "Stay DRY_RUN. Paper 100 lots is not live. OHLCV lab is research-only: "
        "not on the desk, not ENABLE_*, not a paper book."
    )


if __name__ == "__main__":
    main()
