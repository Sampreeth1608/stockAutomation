#!/usr/bin/env python3
"""HH/LL structure + wick length on the *same* candle (backtest only).

Not live. Do not paper until a 100-lot + fees row is picked.

  Structure (S12):
    LONG  = higher high AND green (H > prevH and C > O)
    SHORT = lower low  AND red   (L < prevL and C < O)

  Wick (S14):
    LONG  = lower wick > upper wick (or bald green)
    SHORT = upper wick > lower wick (or bald red)

  AND (the combined idea): both must agree on that candle.
    Long:  HH+green *and* demand wick. Skips a breakout that got slapped
           at the high (long upper wick).
    Short: LL+red *and* supply wick. Skips a breakdown that wicked up.

  OR: either agrees (more trades, noisier). If they disagree, skip.

HOLD: opposite → CLOSE, no reverse on that candle.
Exit `either`: flatten if structure exit OR wick opposite.
Exit `both`:   flatten only when both say opposite (holds longer).

  python3 backtest_hhhl_wick.py --db data/ticks.db --lots 100 --session --fees
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Literal

from backtest_hhhl_candles import (
    Candle,
    TfResult,
    Trade,
    in_session,
    long_entry,
    long_exit,
    make_charge_cfg,
    print_by_day,
    short_entry,
    short_exit,
    write_outputs,
)
from backtest_wick_candles import (
    _tf_result_from_trades,
    build_ohlc_candles,
    load_ltp_rows,
    print_wick_summary,
)
from charges import ChargeConfig, apply_charges_and_tax
from wick_candles import wick_exit_strict, wick_side

TIMEFRAMES: list[tuple[str, int]] = [
    ("15m", 15),
    ("30m", 30),
    ("45m", 45),
    ("1h", 60),
    ("3h", 180),
]

Join = Literal["and", "or", "hhhl", "wick"]
ExitMode = Literal["either", "both"]


def _wick(
    cur: Candle,
    *,
    min_diff: float,
    min_frac: float,
    min_body_ratio: float,
    nowick_eps: float,
    nowick_body: bool,
    nowick_only: bool,
    min_range: float,
) -> str | None:
    return wick_side(
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


def simulate_combo(
    candles: list[Candle],
    *,
    tf: str,
    lots: float = 1.0,
    allow_short: bool = True,
    allow_long: bool = True,
    fees: bool = False,
    session_filter: bool = False,
    min_range: float = 5.0,
    join: Join = "and",
    exit_mode: ExitMode = "either",
    exit_strict: bool = False,
    nowick_only: bool = False,
    nowick_body: bool = True,
    nowick_eps: float = 1.0,
    min_diff: float = 0.0,
    min_frac: float = 0.0,
    min_body_ratio: float = 0.0,
    market_open: str = "09:00",
    market_close: str = "23:30",
    charge_cfg: ChargeConfig | None = None,
) -> TfResult:
    """HOLD combo. Fill at signal-bar close. No same-candle reverse."""
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

    def wick_entry(cur: Candle) -> str | None:
        want = _wick(
            cur,
            min_diff=min_diff,
            min_frac=min_frac,
            min_body_ratio=min_body_ratio,
            nowick_eps=nowick_eps,
            nowick_body=nowick_body,
            nowick_only=nowick_only,
            min_range=0.0,
        )
        if want == "long" and not allow_long:
            return None
        if want == "short" and not allow_short:
            return None
        return want

    def wick_x(cur: Candle) -> str | None:
        if exit_strict:
            want = wick_exit_strict(
                cur.open,
                cur.high,
                cur.low,
                cur.close,
                min_range=0.0,
                nowick_eps=nowick_eps,
                nowick_body=nowick_body,
            )
        else:
            want = wick_entry(cur)
        if want == "long" and not allow_long:
            return None
        if want == "short" and not allow_short:
            return None
        return want

    def signals(prev: Candle, cur: Candle) -> tuple[str | None, bool, bool]:
        hhhl_long = allow_long and long_entry(cur, prev)
        hhhl_short = allow_short and short_entry(cur, prev)
        w = wick_entry(cur)
        if join == "hhhl":
            if hhhl_long and not hhhl_short:
                return "long", long_exit(cur, prev), short_exit(cur, prev)
            if hhhl_short and not hhhl_long:
                return "short", long_exit(cur, prev), short_exit(cur, prev)
            return None, long_exit(cur, prev), short_exit(cur, prev)
        if join == "wick":
            wx = wick_x(cur)
            return w, wx == "short", wx == "long"
        # and / or
        if join == "and":
            want_long = hhhl_long and w == "long"
            want_short = hhhl_short and w == "short"
        else:
            want_long = hhhl_long or w == "long"
            want_short = hhhl_short or w == "short"
            if want_long and want_short:
                want_long = want_short = False
        struct_x_long = long_exit(cur, prev)
        struct_x_short = short_exit(cur, prev)
        wx = wick_x(cur)
        wick_x_long = wx == "short"
        wick_x_short = wx == "long"
        if exit_mode == "both":
            x_long = struct_x_long and wick_x_long
            x_short = struct_x_short and wick_x_short
        else:
            x_long = struct_x_long or wick_x_long
            x_short = struct_x_short or wick_x_short
        if want_long and not want_short:
            return "long", x_long, x_short
        if want_short and not want_long:
            return "short", x_long, x_short
        return None, x_long, x_short

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
        if side is not None and session_filter and not sess_ok:
            close_trade(cur)
            continue

        want, x_long, x_short = signals(prev, cur)

        if side == "LONG":
            if x_long:
                close_trade(cur)
            continue
        if side == "SHORT":
            if x_short:
                close_trade(cur)
            continue
        if not can_enter(cur) or want is None:
            continue
        if want == "long":
            side = "LONG"
            entry_px = cur.close
            entry_time = cur.time
        elif want == "short":
            side = "SHORT"
            entry_px = cur.close
            entry_time = cur.time

    if side is not None and candles:
        close_trade(candles[-1])
    return _tf_result_from_trades(tf, len(candles), trades)


PRESETS: list[tuple[str, dict[str, Any]]] = [
    ("hhhl", {"join": "hhhl", "exit_mode": "either"}),
    ("wick_raw_strict", {"join": "wick", "exit_strict": True, "exit_mode": "either"}),
    ("wick_nowick", {"join": "wick", "nowick_only": True, "exit_mode": "either"}),
    (
        "and_either_raw_strict",
        {"join": "and", "exit_mode": "either", "exit_strict": True},
    ),
    (
        "and_both_raw_strict",
        {"join": "and", "exit_mode": "both", "exit_strict": True},
    ),
    (
        "and_either_nowick",
        {"join": "and", "exit_mode": "either", "nowick_only": True},
    ),
    (
        "or_either_raw_strict",
        {"join": "or", "exit_mode": "either", "exit_strict": True},
    ),
]


def run_all(
    db: Path,
    *,
    lots: float = 100.0,
    tfs: list[tuple[str, int]] | None = None,
    presets: list[tuple[str, dict[str, Any]]] | None = None,
    fees: bool = True,
    session_filter: bool = True,
    min_range: float = 5.0,
    nowick_eps: float = 1.0,
    market_open: str = "09:00",
    market_close: str = "23:30",
) -> list[TfResult]:
    rows = load_ltp_rows(db)
    if not rows:
        raise SystemExit(f"no ticks in {db}")
    cfg = make_charge_cfg(fees=fees, lots=lots)
    results: list[TfResult] = []
    for name, minutes in tfs or TIMEFRAMES:
        candles = build_ohlc_candles(rows, minutes)
        use_session = session_filter and name != "1d"
        for preset_name, filt in presets or PRESETS:
            r = simulate_combo(
                candles,
                tf=f"{name}:{preset_name}",
                lots=lots,
                fees=fees,
                session_filter=use_session,
                min_range=min_range,
                nowick_eps=nowick_eps,
                market_open=market_open,
                market_close=market_close,
                charge_cfg=cfg,
                **filt,
            )
            results.append(r)
            print(
                f"{r.tf:>28}  bars={r.n_bars:5d}  trades={r.n_trades:4d}  "
                f"{r.n_long}/{r.n_short}  win={100 * r.win_rate:5.1f}%  "
                f"pts={r.gross_pts:8.1f}  pnl={r.after_tax_pnl_inr:9.1f}  "
                f"dd={r.max_dd_inr:9.1f}",
                flush=True,
            )
    return results


def main() -> None:
    ap = argparse.ArgumentParser(
        description="HH/LL + wick same-candle combo backtest (not live)."
    )
    ap.add_argument("--db", type=Path, default=Path("data/ticks.db"))
    ap.add_argument("--lots", type=float, default=100.0)
    ap.add_argument("--fees", action="store_true", default=True)
    ap.add_argument("--no-fees", action="store_true")
    ap.add_argument("--session", action="store_true", default=True)
    ap.add_argument("--no-session", action="store_true")
    ap.add_argument("--min-range", type=float, default=5.0)
    ap.add_argument("--nowick-eps", type=float, default=1.0)
    ap.add_argument("--out", type=Path, default=Path("data/backtests/hhhl_wick"))
    args = ap.parse_args()
    fees = False if args.no_fees else True
    session_filter = False if args.no_session else True
    print(
        f"HH/LL + wick AND/OR HOLD  lots={args.lots} fees={fees} "
        f"session={session_filter} min_range={args.min_range}",
        flush=True,
    )
    results = run_all(
        args.db,
        lots=args.lots,
        fees=fees,
        session_filter=session_filter,
        min_range=args.min_range,
        nowick_eps=args.nowick_eps,
    )
    print_wick_summary(results, "HH/LL + wick HOLD (100-lot tape unless --lots set)")
    print_by_day(results)
    write_outputs(results, args.out)


if __name__ == "__main__":
    main()
