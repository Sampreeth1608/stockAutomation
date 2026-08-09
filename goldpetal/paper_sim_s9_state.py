#!/usr/bin/env python3
"""Paper sim for S9_STATE30 on built bars (default 30m)."""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from mtf_bars import DB, build_rich_bars, load_tick_rows
from strategy_state_s9 import StateS9Config, StateS9Strategy


def fee_rt(ltp: float, lots: float) -> float:
    brokerage = 40.0
    buy = sell = ltp * lots
    txn = 0.000021 * (buy + sell)
    sebi = 0.000001 * (buy + sell)
    stamp = 0.000020 * buy
    ctt = 0.00010 * sell
    gst = 0.18 * (brokerage + txn + sebi)
    return brokerage + txn + sebi + stamp + ctt + gst


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--lots", type=float, default=100.0)
    ap.add_argument("--tf", type=int, default=30)
    ap.add_argument("--tp", type=float, default=26.0)
    ap.add_argument("--sl", type=float, default=16.0)
    ap.add_argument("--allow-short", action="store_true")
    args = ap.parse_args()

    rows = load_tick_rows(Path(args.db))
    bars = [b.to_row() for b in build_rich_bars(rows, f"{args.tf}m", args.tf)]
    cfg = StateS9Config(
        bar_minutes=args.tf,
        tp_points=args.tp,
        sl_points=args.sl,
        allow_short=args.allow_short,
        require_net_sign=True,
        min_imb_pct=5.0,
    )
    s = StateS9Strategy(cfg)
    trades = []
    side = None
    entry = None
    reasons = Counter()
    print(f"ticks={len(rows)} bars={len(bars)} TF={args.tf}m TP={args.tp} SL={args.sl}")

    for br in bars:
        sig = s.on_bar_row(br)
        if not sig:
            continue
        if sig.action in {"BUY", "SHORT"}:
            side = "long" if sig.action == "BUY" else "short"
            entry = float(br["close"])
        elif sig.action == "CLOSE" and side and entry is not None:
            close = float(br["close"])
            gross = (close - entry) if side == "long" else (entry - close)
            pnl = gross * args.lots - fee_rt(close, args.lots)
            r = sig.reason or ""
            tag = "other"
            if r.startswith("tp"):
                tag = "tp"
            elif r.startswith("sl"):
                tag = "sl"
            elif "net_flip" in r:
                tag = "net_flip"
            elif "state_break" in r:
                tag = "state_break"
            reasons[tag] += 1
            trades.append((gross, pnl, s.last_label))
            side = None
            entry = None

    n = len(trades)
    if n == 0:
        print("no trades")
        return
    print(
        f"S9_STATE30 n={n} dir%={sum(1 for g,_,_ in trades if g>0)/n*100:.1f} "
        f"avgG={sum(g for g,_,_ in trades)/n:.2f} "
        f"sum₹={sum(p for _,p,_ in trades):.1f} {dict(reasons)}"
    )


if __name__ == "__main__":
    main()
