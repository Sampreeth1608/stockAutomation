#!/usr/bin/env python3
"""Backtest 3-candle body shrink fade. Research. Not live.

Two same-color bodies, second smaller and lower (greens) / higher (reds),
then fade candle 3. Honest fill = candle 3 open. Near/extreme are look-ahead.

Also scores S16 / S18 / S19 / S20 with a skip-chase overlay (do not buy a
shrinking second green / do not short a shrinking second red). Does not
rewrite those paper formulas. No ENABLE.

  python backtest_body_shrink_fade.py --synthetic --lots 100 --fees
  python backtest_body_shrink_fade.py --db data/ticks.db --lots 100 --fees --session
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

from backtest_hhhl_candles import count_ticks, write_outputs
from backtest_wick_candles import print_wick_summary
from body_shrink_fade import (
    FORMULA,
    LAB_NAME,
    after_charges_inr,
    simulate_body_shrink_fade,
    simulate_close_flip,
    skip_chase,
    synthetic_bars,
    to_candles,
)
from s18_ohlc_vol_htf import (
    BASE_PACK,
    attach_completed_day,
    build_vol_bars,
    load_vol_rows,
    s18_bar_decision,
    simulate_s18,
)
from s19_body_close import s19_bar_decision, simulate_s19
from s16_hhhl_wick import s16_bar_decision, simulate_s16
from s20_fade_hl import fade_bounce_decision, simulate_s20

TFS: tuple[tuple[str, int], ...] = (
    ("5m", 5),
    ("15m", 15),
    ("30m", 30),
    ("1h", 60),
    ("2h", 120),
)


def _line(label: str, result: Any) -> None:
    ac = after_charges_inr(result)
    n = result.n_trades
    wins = sum(1 for t in result.trades if (t.gross_pnl_inr - t.fees_inr) > 0)
    wr = (100.0 * wins / n) if n else 0.0
    extra = ""
    if getattr(result, "n_setup", None) is not None:
        extra = f" setups={result.n_setup} fills={result.n_fill}"
    print(
        f"{label}: trades={n} L/S={result.n_long}/{result.n_short} "
        f"after_charges_win%={wr:.1f} gross₹={result.gross_pnl_inr:.1f} "
        f"fees₹={result.fees_inr:.1f} after_charges₹={ac:.1f}{extra}"
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="Body shrink fade (research, not live).")
    ap.add_argument("--db", type=Path, default=Path("data/ticks.db"))
    ap.add_argument("--lots", type=float, default=100.0)
    ap.add_argument("--fees", action="store_true")
    ap.add_argument("--session", action="store_true")
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--out-dir", type=Path, default=Path("data/backtests/body_shrink_fade"))
    args = ap.parse_args()
    if args.fees:
        os.environ["IGNORE_FEES"] = "false"

    print(LAB_NAME)
    print(FORMULA)
    print("Not S7_HOURLY. Not S16 rewrite. No ENABLE. Stay DRY_RUN.")
    print()

    kw = dict(lots=float(args.lots), fees=bool(args.fees), session_filter=bool(args.session))
    results: list[Any] = []

    if args.synthetic:
        bars = synthetic_bars()
        print(f"synthetic bars={len(bars)} lots={args.lots} fees={args.fees}", flush=True)
        for how in ("open", "near", "extreme"):
            res = simulate_body_shrink_fade(
                bars, tf=f"toy:{how}", fill=how, session_filter=False, lots=float(args.lots), fees=bool(args.fees)
            )
            results.append(res)
            tag = "" if how == "open" else " LOOKAHEAD"
            _line(f"toy fill={how}{tag}", res)
        print()
        print(
            "Toy only proves the formula. Rank the VM --db tape after charges. "
            "Stay DRY_RUN."
        )
        args.out_dir.mkdir(parents=True, exist_ok=True)
        write_outputs(results, args.out_dir)
        return

    if not args.db.exists():
        raise SystemExit(f"missing db: {args.db}")
    n = count_ticks(args.db)
    if n == 0:
        raise SystemExit(
            f"empty ticks in {args.db}. Cloud ticks.db is often empty — "
            "run this on the VM tape. Stay DRY_RUN."
        )
    print(f"ticks={n} db={args.db} lots={args.lots} fees={args.fees} session={args.session}", flush=True)
    print("building bars...", flush=True)
    rows = load_vol_rows(args.db)
    by_tf: dict[str, list[Any]] = {}
    for name, minutes in TFS:
        by_tf[name] = build_vol_bars(rows, minutes)
        print(f"  {name}: {len(by_tf[name])} bars", flush=True)

    hours = by_tf["1h"]
    days = build_vol_bars(rows, 1440)

    print()
    print("=== standalone fade (candle 3, exit close) ===")
    for name, _m in TFS:
        bars = by_tf[name]
        if len(bars) < 4:
            continue
        for how in ("open", "near"):
            res = simulate_body_shrink_fade(
                bars, tf=f"{name}:shrink_{how}", fill=how, **kw
            )
            results.append(res)
            tag = "" if how == "open" else " LOOKAHEAD"
            _line(f"{name} fill={how}{tag}", res)

    print()
    print("=== paper books vs skip-chase overlay (1h, fill at close) ===")
    candles = to_candles(hours)

    s16 = simulate_s16(candles, tf="1h:S16", min_wick_gap=0.0, **kw)
    results.append(s16)
    _line("S16 as-is", s16)

    def s16_skip(i: int, prev: Any, cur: Any) -> str | None:
        del i
        side, _ = s16_bar_decision(prev, cur, min_wick_gap=0.0)
        return None if skip_chase(prev, cur, side) else side

    s16s = simulate_close_flip(candles, s16_skip, tf="1h:S16_skip", **kw)
    results.append(s16s)
    _line("S16 skip shrinking chase", s16s)

    s19 = simulate_s19(hours, tf="1h:S19", **kw)
    results.append(s19)
    _line("S19 as-is", s19)

    def s19_skip(i: int, prev: Any, cur: Any) -> str | None:
        del i
        side, _ = s19_bar_decision(prev, cur)
        return None if skip_chase(prev, cur, side) else side

    s19s = simulate_close_flip(hours, s19_skip, tf="1h:S19_skip", **kw)
    results.append(s19s)
    _line("S19 skip shrinking chase", s19s)

    s20 = simulate_s20(candles, tf="1h:S20", **kw)
    results.append(s20)
    _line("S20 as-is", s20)

    def s20_skip(i: int, prev: Any, cur: Any) -> str | None:
        del i
        side, _ = fade_bounce_decision(prev, cur)
        return None if skip_chase(prev, cur, side) else side

    s20s = simulate_close_flip(candles, s20_skip, tf="1h:S20_skip", **kw)
    results.append(s20s)
    _line("S20 skip shrinking chase", s20s)

    has_vol = any(b.volume > 0 for b in hours)
    if has_vol and days:
        s18 = simulate_s18(hours, days, tf="1h:S18", **kw)
        results.append(s18)
        _line("S18 as-is", s18)
        htf = attach_completed_day(hours, days)

        def s18_skip(i: int, prev: Any, cur: Any) -> str | None:
            day = htf[i] if i < len(htf) else None
            side, _ = s18_bar_decision(prev, cur, day, BASE_PACK)
            return None if skip_chase(prev, cur, side) else side

        s18s = simulate_close_flip(hours, s18_skip, tf="1h:S18_skip", **kw)
        results.append(s18s)
        _line("S18 skip shrinking chase", s18s)
    else:
        print("S18 skipped — need volume + day bars.")

    print()
    print_wick_summary(
        results,
        title=f"body shrink fade  lots={args.lots:g}  fees={args.fees}",
    )
    args.out_dir.mkdir(parents=True, exist_ok=True)
    write_outputs(results, args.out_dir)
    print(f"wrote {args.out_dir}")
    print(
        "Rank after charges, tax excluded. Exact high/low of candle 3 is "
        "look-ahead. Honest row is fill=open. Do not ENABLE. Do not rewrite "
        "S16. Stay DRY_RUN. This is not S7_HOURLY."
    )


if __name__ == "__main__":
    main()
