#!/usr/bin/env python3
"""Wick-length long/short — multi-TF backtest from tick OHLC (S14 candidate).

  LONG  when lower wick > upper wick
  SHORT when upper wick > lower wick
  HOLD: exit to flat on opposite signal; do not reverse on that same candle.
  STRICT exit: only flatten on a decisive opposite (frac50 or pin2 or bald body).

Fill at signal-bar close. Bald candles (no wick on either side) use the body:
green → long, red → short. That rule is included in every wick preset.

  python3 backtest_wick_candles.py --db data/ticks.db --lots 1 --session --fees
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from typing import Any
from pathlib import Path

from backtest_hhhl_candles import (
    TIMEFRAMES,
    Candle,
    TfResult,
    Trade,
    count_ticks,
    db_diagnostics,
    in_session,
    make_charge_cfg,
    print_by_day,
    write_outputs,
)
from charges import ChargeConfig, apply_charges_and_tax
from mtf_bars import floor_bar, parse_ts
from wick_candles import wick_exit_strict, wick_side

HOLD_TIMEFRAMES: list[tuple[str, int]] = [
    ("15m", 15),
    ("30m", 30),
    ("45m", 45),
    ("1h", 60),
    ("2h", 120),
    ("3h", 180),
    ("1d", 1440),
]

_HOLD = {"reenter": False, "nowick_body": True}
PRESETS: list[tuple[str, dict[str, Any]]] = [
    ("raw", {**_HOLD, "min_diff": 0.0, "min_frac": 0.0, "min_body_ratio": 0.0}),
    ("diff5", {**_HOLD, "min_diff": 5.0, "min_frac": 0.0, "min_body_ratio": 0.0}),
    ("diff10", {**_HOLD, "min_diff": 10.0, "min_frac": 0.0, "min_body_ratio": 0.0}),
    ("frac50", {**_HOLD, "min_frac": 0.5, "min_diff": 0.0, "min_body_ratio": 0.0}),
    ("pin2", {**_HOLD, "min_body_ratio": 2.0, "min_diff": 0.0, "min_frac": 0.0}),
    ("nowick", {**_HOLD, "nowick_only": True, "min_diff": 0.0, "min_frac": 0.0, "min_body_ratio": 0.0}),
    ("raw_strict", {**_HOLD, "min_diff": 0.0, "min_frac": 0.0, "min_body_ratio": 0.0, "exit_strict": True}),
    ("pin2_strict", {**_HOLD, "min_body_ratio": 2.0, "min_diff": 0.0, "min_frac": 0.0, "exit_strict": True}),
]


def simulate_wick(
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
    min_diff: float = 0.0,
    min_frac: float = 0.0,
    min_body_ratio: float = 0.0,
    nowick_eps: float = 1.0,
    nowick_body: bool = True,
    nowick_only: bool = False,
    reenter: bool = False,
    exit_strict: bool = False,
    market_open: str = "09:00",
    market_close: str = "23:30",
    charge_cfg: ChargeConfig | None = None,
) -> TfResult:
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

    def signal(cur: Candle) -> str | None:
        want = wick_side(
            cur.open,
            cur.high,
            cur.low,
            cur.close,
            min_diff=min_diff,
            min_frac=min_frac,
            min_body_ratio=min_body_ratio,
            min_range=min_range,
            nowick_eps=nowick_eps,
            nowick_body=nowick_body,
            nowick_only=nowick_only,
        )
        if want == "long" and not allow_long:
            return None
        if want == "short" and not allow_short:
            return None
        return want

    def exit_want(cur: Candle) -> str | None:
        if exit_strict:
            want = wick_exit_strict(
                cur.open,
                cur.high,
                cur.low,
                cur.close,
                min_range=min_range,
                nowick_eps=nowick_eps,
                nowick_body=nowick_body,
            )
        else:
            want = signal(cur)
        if want == "long" and not allow_long:
            return None
        if want == "short" and not allow_short:
            return None
        return want

    def can_enter(cur: Candle) -> bool:
        if session_filter and not in_session(
            cur, open_hhmm=market_open, close_hhmm=market_close
        ):
            return False
        return True

    for cur in candles:
        sess_ok = (not session_filter) or in_session(
            cur, open_hhmm=market_open, close_hhmm=market_close
        )
        if side is not None and session_filter and not sess_ok:
            close_trade(cur)
            continue

        want = signal(cur)
        xwant = exit_want(cur)
        want_long = want == "long"
        want_short = want == "short"

        if side == "LONG":
            if xwant == "short":
                close_trade(cur)
                if reenter and can_enter(cur) and want_short:
                    side = "SHORT"
                    entry_px = cur.close
                    entry_time = cur.time
            continue
        if side == "SHORT":
            if xwant == "long":
                close_trade(cur)
                if reenter and can_enter(cur) and want_long:
                    side = "LONG"
                    entry_px = cur.close
                    entry_time = cur.time
            continue

        if not can_enter(cur) or want is None:
            continue
        if want_long:
            side = "LONG"
            entry_px = cur.close
            entry_time = cur.time
        elif want_short:
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


def load_ltp_rows(db: Path) -> list[tuple[str, float]]:
    """OHLC-only tick load — skip raw_json parse (was the slow path)."""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return [
            (str(r[0]), float(r[1]))
            for r in con.execute(
                "SELECT received_at, ltp FROM ticks "
                "WHERE ltp IS NOT NULL ORDER BY received_at ASC, id ASC"
            )
        ]
    finally:
        con.close()


def build_ohlc_candles(rows: list[tuple[str, float]], minutes: int) -> list[Candle]:
    candles: list[Candle] = []
    cur_key = None
    o = h = l = c = None

    def flush(key) -> None:
        nonlocal o, h, l, c
        if o is None or h is None or l is None or c is None:
            return
        candles.append(
            Candle(key.strftime("%Y-%m-%d %H:%M:%S"), float(o), float(h), float(l), float(c))
        )
        o = h = l = c = None

    for received_at, ltp in rows:
        ts = parse_ts(received_at)
        key = floor_bar(ts, minutes)
        px = float(ltp)
        if cur_key is None:
            cur_key = key
        if key != cur_key:
            flush(cur_key)
            cur_key = key
        if o is None:
            o = h = l = c = px
        else:
            h = max(h, px)
            l = min(l, px)
            c = px
    if cur_key is not None:
        flush(cur_key)
    return candles


def print_wick_summary(results: list[TfResult]) -> None:
    print()
    print(
        f"{'row':>18}  {'bars':>6}  {'trades':>6}  {'L/S':>7}  "
        f"{'win%':>6}  {'gross_pts':>10}  {'fees_₹':>10}  {'pnl_₹':>10}  {'maxDD_₹':>10}"
    )
    print("-" * 102)
    for r in results:
        print(
            f"{r.tf:>18}  {r.n_bars:6d}  {r.n_trades:6d}  "
            f"{r.n_long:3d}/{r.n_short:<3d}  {100 * r.win_rate:5.1f}%  "
            f"{r.gross_pts:10.1f}  {r.fees_inr:10.1f}  "
            f"{r.after_tax_pnl_inr:10.1f}  {r.max_dd_inr:10.1f}"
        )
    print()


def run_all(
    db: Path,
    *,
    lots: float = 1.0,
    tfs: list[tuple[str, int]] | None = None,
    presets: list[tuple[str, dict[str, Any]]] | None = None,
    allow_short: bool = True,
    allow_long: bool = True,
    fees: bool = False,
    session_filter: bool = False,
    min_range: float = 5.0,
    no_flip: bool = True,
    nowick_eps: float = 1.0,
    market_open: str = "09:00",
    market_close: str = "23:30",
) -> list[TfResult]:
    rows = load_ltp_rows(db)
    if not rows:
        raise SystemExit(f"no ticks in {db}")
    cfg = make_charge_cfg(fees=fees, lots=lots)
    results: list[TfResult] = []
    chosen_tfs = tfs or HOLD_TIMEFRAMES
    chosen_presets = presets or PRESETS
    for name, minutes in chosen_tfs:
        candles = build_ohlc_candles(rows, minutes)
        use_session = session_filter and name != "1d"
        for preset_name, filt in chosen_presets:
            r = simulate_wick(
                candles,
                tf=f"{name}:{preset_name}",
                lots=lots,
                allow_short=allow_short,
                allow_long=allow_long,
                fees=fees,
                session_filter=use_session,
                min_range=min_range,
                no_flip=no_flip,
                nowick_eps=nowick_eps,
                market_open=market_open,
                market_close=market_close,
                charge_cfg=cfg,
                **filt,
            )
            results.append(r)
            print(
                f"{r.tf:>16}  bars={r.n_bars:5d}  trades={r.n_trades:4d}  "
                f"{r.n_long}/{r.n_short}  win={100 * r.win_rate:5.1f}%  "
                f"pts={r.gross_pts:8.1f}  pnl={r.after_tax_pnl_inr:9.1f}",
                flush=True,
            )
    return results


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Wick-length long/short multi-TF backtest from ticks."
    )
    ap.add_argument("--db", type=Path, default=Path("data/ticks.db"))
    ap.add_argument("--lots", type=float, default=1.0)
    ap.add_argument("--long-only", action="store_true")
    ap.add_argument("--short-only", action="store_true")
    ap.add_argument("--fees", action="store_true")
    ap.add_argument("--session", action="store_true")
    ap.add_argument("--min-range", type=float, default=5.0)
    ap.add_argument("--nowick-eps", type=float, default=1.0, help="pts: wick ≤ this counts as no wick")
    ap.add_argument("--no-flip", action="store_true", default=True)
    ap.add_argument("--allow-flip", action="store_true")
    ap.add_argument("--market-open", default="09:00")
    ap.add_argument("--market-close", default="23:30")
    ap.add_argument("--tfs", default="")
    ap.add_argument(
        "--presets",
        default="",
        help="comma list: raw,diff5,diff10,frac50,pin2,nowick,raw_strict,pin2_strict (default: all hold)",
    )
    ap.add_argument(
        "--out-dir",
        type=Path,
        default=Path("data/backtests/wick_candles"),
    )
    args = ap.parse_args()
    if args.fees:
        os.environ["IGNORE_FEES"] = "false"
    no_flip = not args.allow_flip

    if not args.db.exists():
        raise SystemExit(f"missing db: {args.db}")
    n = count_ticks(args.db)
    if n == 0:
        raise SystemExit(
            "0 ticks readable in db.\n"
            f"  {db_diagnostics(args.db)}\n"
        )

    tfs = HOLD_TIMEFRAMES
    if args.tfs.strip():
        if args.tfs.strip().lower() == "all":
            tfs = TIMEFRAMES
        else:
            want = {x.strip() for x in args.tfs.split(",") if x.strip()}
            tfs = [(n_, m) for n_, m in TIMEFRAMES if n_ in want]
            if not tfs:
                raise SystemExit(f"no matching tfs in {want}")
    presets = PRESETS
    if args.presets.strip():
        want_p = {x.strip() for x in args.presets.split(",") if x.strip()}
        presets = [(n_, f) for n_, f in PRESETS if n_ in want_p]
        if not presets:
            raise SystemExit(f"no matching presets in {want_p}")

    ltp_rows = load_ltp_rows(args.db)
    t0 = parse_ts(ltp_rows[0][0])
    t1 = parse_ts(ltp_rows[-1][0])
    print(
        f"Wick-length HOLD backtest  db={args.db}  ticks={n}  "
        f"range={t0.isoformat(timespec='seconds')} → {t1.isoformat(timespec='seconds')}  "
        f"lots={args.lots}"
    )
    print(
        "Rules: LONG lower>upper wick | SHORT upper>lower wick | "
        "bald → body C>O long / C<O short | "
        "HOLD: exit to flat, no reverse on that candle | "
        "strict: exit only frac50/pin2/bald"
    )
    print(
        f"Filters: fees={args.fees} session={args.session} "
        f"({args.market_open}-{args.market_close}) "
        f"min_range={args.min_range} nowick_eps={args.nowick_eps} no_flip={no_flip}"
    )

    results = run_all(
        args.db,
        lots=args.lots,
        tfs=tfs,
        presets=presets,
        allow_long=not args.short_only,
        allow_short=not args.long_only,
        fees=args.fees,
        session_filter=args.session,
        min_range=args.min_range,
        no_flip=no_flip,
        nowick_eps=args.nowick_eps,
        market_open=args.market_open,
        market_close=args.market_close,
    )
    print_wick_summary(results)
    print_by_day(results)
    write_outputs(results, args.out_dir)
    (args.out_dir / "scoreboard.json").write_text(
        json.dumps(
            [
                {
                    "row": r.tf,
                    "n_bars": r.n_bars,
                    "n_trades": r.n_trades,
                    "n_long": r.n_long,
                    "n_short": r.n_short,
                    "win_rate": r.win_rate,
                    "gross_pts": r.gross_pts,
                    "after_tax_pnl_inr": r.after_tax_pnl_inr,
                    "max_dd_inr": r.max_dd_inr,
                }
                for r in results
            ],
            indent=2,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
