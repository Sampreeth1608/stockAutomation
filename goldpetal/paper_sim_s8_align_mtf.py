#!/usr/bin/env python3
"""S8 ALIGN hist on tick + MTF bars: 1m,2m,3m,5m,10m,15m,30m,1h.

Uses book-driven SL/TP (price∩TBQ∩TSQ), not bare price-range stops.

  python3 paper_sim_s8_align_mtf.py --db data/ticks.db --lots 100
  python3 paper_sim_s8_align_mtf.py --db data/ticks.db --lots 100 --day 2026-08-10
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from mtf_bars import INTERVALS, build_rich_bars, load_tick_rows
from strategy_net_zigzag import NetZigzagConfig, NetZigzagStrategy
from strategy_s8_align import AlignS8Config, AlignS8Strategy

IST = ZoneInfo("Asia/Kolkata")
DB = Path("data/ticks.db")
TFS = [(n, m) for n, m in INTERVALS]  # 1m..1h


def fee_rt(ltp: float, lots: float) -> float:
    brokerage = 40.0
    buy = sell = ltp * lots
    txn = 0.000021 * (buy + sell)
    sebi = 0.000001 * (buy + sell)
    stamp = 0.000020 * buy
    ctt = 0.00010 * sell
    gst = 0.18 * (brokerage + txn + sebi)
    return brokerage + txn + sebi + stamp + ctt + gst


def filter_rows_day(rows, day: str | None):
    if not day:
        return rows
    return [r for r in rows if str(r["received_at"]).startswith(day)]


def msg_from_row(row) -> dict:
    if row["raw_json"]:
        try:
            m = json.loads(row["raw_json"])
            if isinstance(m, dict):
                if m.get("total_buy_quantity") is None and row["bp"] is not None:
                    m["total_buy_quantity"] = row["bp"]
                if m.get("total_sell_quantity") is None and row["sp"] is not None:
                    m["total_sell_quantity"] = row["sp"]
                return m
        except (TypeError, json.JSONDecodeError):
            pass
    return {
        "total_buy_quantity": row["bp"],
        "total_sell_quantity": row["sp"],
    }


def parse_ts(raw: str) -> datetime:
    try:
        now = datetime.fromisoformat(str(raw))
        if now.tzinfo is None:
            now = now.replace(tzinfo=IST)
        return now
    except Exception:
        return datetime.now(IST)


def summarize(label: str, trades: list[tuple[float, float]], reasons: Counter, n: int) -> None:
    if not trades:
        print(f"{label:18} | n_in={n:6} | trades=0")
        return
    gs = [g for g, _ in trades]
    ps = [p for _, p in trades]
    nt = len(trades)
    print(
        f"{label:18} | n_in={n:6} | trades={nt:3} "
        f"dir%={sum(1 for g in gs if g > 0)/nt*100:5.1f} "
        f"avgG={sum(gs)/nt:7.2f} sumPts={sum(gs):+8.1f} "
        f"sum₹={sum(ps):9.1f} {dict(reasons)}"
    )


def run_align_ticks(rows, lots: float, cfg: AlignS8Config):
    s = AlignS8Strategy(cfg)
    trades = []
    side = entry = None
    reasons: Counter = Counter()
    for row in rows:
        ltp = float(row["ltp"])
        sig = s.on_tick(parse_ts(row["received_at"]), ltp, msg_from_row(row))
        if not sig:
            continue
        if sig.action in {"BUY", "SHORT"}:
            side = "long" if sig.action == "BUY" else "short"
            entry = ltp
        elif sig.action == "CLOSE" and side and entry is not None:
            gross = (ltp - entry) if side == "long" else (entry - ltp)
            pnl = gross * lots - fee_rt(ltp, lots)
            r = sig.reason or ""
            tag = (
                "tp"
                if r.startswith("tp") or "stall" in r
                else "sl"
                if r.startswith("sl")
                else "break"
                if "book_break" in r
                else "flip"
                if "flip" in r
                else "weaken"
                if "weaken" in r
                else "other"
            )
            reasons[tag] += 1
            trades.append((gross, pnl))
            side = entry = None
    if side and entry is not None and rows:
        ltp = float(rows[-1]["ltp"])
        gross = (ltp - entry) if side == "long" else (entry - ltp)
        trades.append((gross, gross * lots - fee_rt(ltp, lots)))
        reasons["eod"] += 1
    return trades, reasons


def run_align_bars(bars: list[dict], lots: float, cfg: AlignS8Config):
    # Bar path: stall_bars counts bars; loosen allow memory in bar units
    s = AlignS8Strategy(cfg)
    s.book_allow_memory = 8
    trades = []
    side = entry = None
    reasons: Counter = Counter()
    for br in bars:
        sig = s.on_bar_row(br)
        if not sig:
            continue
        px = float(br["close"])
        if sig.action in {"BUY", "SHORT"}:
            side = "long" if sig.action == "BUY" else "short"
            entry = px
        elif sig.action == "CLOSE" and side and entry is not None:
            gross = (px - entry) if side == "long" else (entry - px)
            pnl = gross * lots - fee_rt(px, lots)
            r = sig.reason or ""
            tag = (
                "tp"
                if r.startswith("tp") or "stall" in r
                else "sl"
                if r.startswith("sl")
                else "break"
                if "book_break" in r
                else "flip"
                if "flip" in r
                else "weaken"
                if "weaken" in r
                else "other"
            )
            reasons[tag] += 1
            trades.append((gross, pnl))
            side = entry = None
    if side and entry is not None and bars:
        px = float(bars[-1]["close"])
        gross = (px - entry) if side == "long" else (entry - px)
        trades.append((gross, gross * lots - fee_rt(px, lots)))
        reasons["eod"] += 1
    return trades, reasons


def run_legacy_30m(rows, lots: float):
    s = NetZigzagStrategy(
        NetZigzagConfig(
            bar_minutes=30,
            entry_mode="always",
            cooldown_ticks=0,
            min_imb_pct=10,
            tp_points=25,
            sl_points=20,
            weaken_pct=10,
        )
    )
    trades = []
    side = entry = None
    reasons: Counter = Counter()
    for row in rows:
        ltp = float(row["ltp"])
        sig = s.on_tick(parse_ts(row["received_at"]), ltp, msg_from_row(row))
        if not sig:
            continue
        if sig.action in {"BUY", "SHORT"}:
            side = "long" if sig.action == "BUY" else "short"
            entry = ltp
        elif sig.action == "CLOSE" and side and entry is not None:
            gross = (ltp - entry) if side == "long" else (entry - ltp)
            pnl = gross * lots - fee_rt(ltp, lots)
            r = sig.reason or ""
            tag = "tp" if r.startswith("tp") else "sl" if r.startswith("sl") else "other"
            reasons[tag] += 1
            trades.append((gross, pnl))
            side = entry = None
    return trades, reasons


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--lots", type=float, default=100.0)
    ap.add_argument("--day", default="")
    args = ap.parse_args()
    day = args.day.strip() or None

    rows = filter_rows_day(load_tick_rows(Path(args.db)), day)
    print(f"db={args.db} day={day or 'ALL'} ticks={len(rows)} lots={args.lots}")
    if len(rows) < 100:
        raise SystemExit("need more ticks")

    cfg = AlignS8Config(
        min_imb_pct=10.0,
        book_frac_of_net=0.10,
        pullback_points=8.0,
        resume_points=5.0,
        stall_min_profit=20.0,
        stall_bars=3,
        sl_min=20.0,
        tp_min=20.0,
        weaken_pct=20.0,
    )

    print("\n=== S8 ALIGN book-driven SL/TP ===")
    t, r = run_align_ticks(rows, args.lots, cfg)
    summarize("TICK", t, r, len(rows))

    for tf_name, minutes in TFS:
        bars = [b.to_row() for b in build_rich_bars(rows, tf_name, minutes)]
        # scale pullback to TF a bit
        cfg_b = AlignS8Config(
            min_imb_pct=10.0,
            book_frac_of_net=0.10,
            pullback_points=max(5.0, 4.0 + minutes * 0.3),
            resume_points=max(3.0, 3.0 + minutes * 0.15),
            stall_min_profit=20.0,
            stall_bars=2 if minutes >= 15 else 3,
            sl_min=20.0,
            tp_min=20.0,
            weaken_pct=20.0,
            cooldown_ticks=1,
        )
        t, r = run_align_bars(bars, args.lots, cfg_b)
        summarize(f"ALIGN_{tf_name}", t, r, len(bars))

    print("\n=== reference: legacy 30m always ===")
    t, r = run_legacy_30m(rows, args.lots)
    summarize("LEGACY_30m", t, r, len(rows))


if __name__ == "__main__":
    main()
