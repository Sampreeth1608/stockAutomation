#!/usr/bin/env python3
"""Backtest S20: buy a bounced low, short a rejected high.

Sweeps the same formula on 30m (if given) / 1h / 2h / 4h / 1d, plus research
raw-HL, wick-fade, and 1h+4h+day agreement. Compares S16 (1h) and S13/S4 (1d).
Rank **after Angel charges, tax excluded**. 1h bounce lost that rank vs S16/S18
on the Aug-26 Gold Petal tape — leave ENABLE_S20=false.

  ./venv/bin/python backtest_s20_fade_hl.py --csv hours.csv --lots 100 --fees
  ./venv/bin/python backtest_s20_fade_hl.py --csv hours.csv --csv-30m 30m.csv --lots 100 --fees
  ./venv/bin/python backtest_s20_fade_hl.py --db data/ticks.db --lots 100 --session --fees
  ./venv/bin/python backtest_s20_fade_hl.py --from-angel --from 2026-08-02 --lots 100 --fees
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
from s16_hhhl_wick import hhhl_bar_decision, s16_bar_decision, simulate_s16
from s18_ohlc_vol_htf import VolBar, build_vol_bars, load_vol_rows, simulate_s18
from s20_fade_hl import (
    FORMULA,
    RAW_FORMULA,
    S20_NAME,
    WICK_FORMULA,
    after_charges_inr,
    aggregate_candles,
    candles_from_ohlc,
    fade_bounce_decision,
    fade_raw_decision,
    fade_wick_decision,
    simulate_mtf_s20,
    simulate_s20,
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


def _load_csv(path: Path) -> list[Candle]:
    with path.open(newline="") as f:
        rows = list(csv.DictReader(f))
    return candles_from_ohlc(rows)


def _vol_from_candles(hours: list[Candle], src_rows: list[dict[str, Any]] | None) -> list[VolBar]:
    if not src_rows:
        return [
            VolBar(c.time, c.open, c.high, c.low, c.close, 0.0) for c in hours
        ]
    by_t = {str(r["time"]).replace("T", " ")[:19]: r for r in src_rows}
    out: list[VolBar] = []
    for c in hours:
        raw = by_t.get(c.time, {})
        try:
            vol = float(raw.get("volume") or raw.get("vol") or 0.0)
        except (TypeError, ValueError):
            vol = 0.0
        out.append(VolBar(c.time, c.open, c.high, c.low, c.close, vol))
    return out


def _days_from_hours(hours: list[Candle]) -> list[Candle]:
    return aggregate_candles(hours, 1440)


def _load_angel(date_from: str, date_to: str) -> tuple[list[Candle], list[Candle], str]:
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
    return candles_from_ohlc(raw_h), candles_from_ohlc(raw_d), f"Angel {symbol}"


def _run_tf(
    candles: list[Candle],
    *,
    name: str,
    lots: float,
    fees: bool,
    session_filter: bool,
    decide: Any,
) -> Any:
    return simulate_s20(
        candles,
        tf=name,
        lots=lots,
        fees=fees,
        session_filter=session_filter,
        decide=decide,
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
    ap.add_argument("--out-dir", default="data/backtests/s20_fade_hl")
    args = ap.parse_args()
    fees = bool(args.fees) and not bool(args.no_fees)
    session_filter = bool(args.session) and not bool(args.no_session)

    vol_hours: list[VolBar] = []
    days_src: list[Candle] = []
    m30: list[Candle] = []
    csv_rows: list[dict[str, Any]] | None = None

    if args.from_angel:
        hours, days_src, source = _load_angel(args.date_from, args.date_to)
    elif args.csv:
        csv_rows = list(csv.DictReader(args.csv.open(newline="")))
        hours = candles_from_ohlc(csv_rows)
        days_src = _days_from_hours(hours)
        vol_hours = _vol_from_candles(hours, csv_rows)
        source = str(args.csv)
        if args.csv_30m:
            m30 = _load_csv(args.csv_30m)
    else:
        db = Path(args.db)
        rows = load_vol_rows(db)
        vol_hours = build_vol_bars(rows, 60)
        hours = [
            Candle(b.time, b.open, b.high, b.low, b.close) for b in vol_hours
        ]
        days_vol = build_vol_bars(rows, 1440)
        days_src = [Candle(b.time, b.open, b.high, b.low, b.close) for b in days_vol]
        m30_vol = build_vol_bars(rows, 30)
        m30 = [Candle(b.time, b.open, b.high, b.low, b.close) for b in m30_vol]
        source = f"ticks {db} n={len(rows)}"

    if len(hours) < 2:
        raise SystemExit(f"need ≥2 bars, got {len(hours)} from {source}")

    h2 = aggregate_candles(hours, 120)
    h4 = aggregate_candles(hours, 240)
    days = days_src or _days_from_hours(hours)

    print(S20_NAME, "(buy bounced low / short rejected high — research until ENABLE_S20)")
    print(FORMULA)
    print(RAW_FORMULA)
    print(WICK_FORMULA)
    print(
        f"lots={args.lots:g} fees={fees} session={session_filter} "
        f"hours={len(hours)} {hours[0].time}→{hours[-1].time} "
        f"2h={len(h2)} 4h={len(h4)} days={len(days)} 30m={len(m30)} source={source}",
        flush=True,
    )

    kw = dict(lots=float(args.lots), fees=fees)
    results: list[Any] = []
    labeled: list[tuple[str, Any]] = []

    def add(label: str, res: Any) -> None:
        results.append(res)
        labeled.append((label, res))

    bounce = fade_bounce_decision
    if m30:
        add(
            "30m S20 bounce (paper formula)",
            _run_tf(m30, name="30m:S20", session_filter=session_filter, decide=bounce, **kw),
        )
        add(
            "30m RESEARCH raw LL/HH",
            _run_tf(m30, name="30m:S20_raw", session_filter=session_filter, decide=fade_raw_decision, **kw),
        )
    add(
        "1h S20 bounce (this paper book)",
        _run_tf(hours, name="1h:S20", session_filter=session_filter, decide=bounce, **kw),
    )
    add(
        "1h RESEARCH raw LL/HH",
        _run_tf(hours, name="1h:S20_raw", session_filter=session_filter, decide=fade_raw_decision, **kw),
    )
    add(
        "1h RESEARCH wick fade",
        _run_tf(hours, name="1h:S20_wick", session_filter=session_filter, decide=fade_wick_decision, **kw),
    )
    add(
        "2h S20 bounce",
        _run_tf(h2, name="2h:S20", session_filter=session_filter, decide=bounce, **kw),
    )
    add(
        "4h S20 bounce",
        _run_tf(h4, name="4h:S20", session_filter=session_filter, decide=bounce, **kw),
    )
    add(
        "1d S20 bounce (hold overnight)",
        _run_tf(days, name="1d:S20", session_filter=False, decide=bounce, **kw),
    )
    add(
        "1d RESEARCH raw LL/HH",
        _run_tf(days, name="1d:S20_raw", session_filter=False, decide=fade_raw_decision, **kw),
    )
    add(
        "1h S20 mtf no-fight (4h+day)",
        simulate_mtf_s20(
            hours,
            [("4h", h4, 240), ("1d", days, 1440)],
            tf="1h:S20_mtf",
            session_filter=session_filter,
            require_all=False,
            **kw,
        ),
    )
    add(
        "1h S20 mtf ALL fire (4h+day)",
        simulate_mtf_s20(
            hours,
            [("4h", h4, 240), ("1d", days, 1440)],
            tf="1h:S20_mtf_all",
            session_filter=session_filter,
            require_all=True,
            **kw,
        ),
    )
    add(
        "1h S16 HHHL+wick gap0 (paper)",
        simulate_s16(
            hours,
            tf="1h:S16",
            session_filter=session_filter,
            min_wick_gap=0.0,
            **kw,
        ),
    )
    add(
        "1d S13 S16 daily (paper)",
        simulate_s16(
            days,
            tf="1d:S13",
            session_filter=False,
            min_wick_gap=0.0,
            decide=lambda a, b: s16_bar_decision(a, b, min_wick_gap=0.0),
            **kw,
        ),
    )
    add(
        "1d S4 HHHL (research)",
        simulate_s16(
            days,
            tf="1d:S4",
            session_filter=False,
            decide=hhhl_bar_decision,
            **kw,
        ),
    )
    has_vol = any(getattr(b, "volume", 0.0) > 0 for b in vol_hours)
    if has_vol and days:
        day_vol = [
            VolBar(c.time, c.open, c.high, c.low, c.close, 0.0) for c in days
        ]
        add(
            "1h S18 OHLC+vol+day (paper)",
            simulate_s18(
                vol_hours,
                day_vol,
                tf="1h:S18",
                session_filter=session_filter,
                **kw,
            ),
        )

    print_wick_summary(
        results,
        title=f"S20 fade HL vs TFs vs S16/S13  lots={args.lots:g}  fees={fees}",
    )
    print("Rank after charges, tax excluded. Do not rank on after-tax pnl_₹.")
    for label, res in labeled:
        _line(label, res)
    s20_1h = next(r for r in results if r.tf == "1h:S20")
    print_by_day([s20_1h])
    out = Path(args.out_dir)
    write_outputs(results, out)
    print(f"wrote {out}")
    print(
        "Stay DRY_RUN. Paper 100 lots is not live. Do not live-unlock S20. "
        "ENABLE_S20 stays false unless a later tape beats S16/S18 (1h) or S13 (1d)."
    )


if __name__ == "__main__":
    main()
