#!/usr/bin/env python3
"""Historical paper sim for S8_NET_ZIGZAG (best fixed params)."""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from strategy_net_zigzag import NetZigzagConfig, NetZigzagStrategy

DB = Path("data/ticks.db")
IST = ZoneInfo("Asia/Kolkata")


def fee_rt(ltp: float, lots: float) -> float:
    brokerage = 40.0
    buy = sell = ltp * lots
    txn = 0.000021 * (buy + sell)
    sebi = 0.000001 * (buy + sell)
    stamp = 0.000020 * buy
    ctt = 0.00010 * sell
    gst = 0.18 * (brokerage + txn + sebi)
    return brokerage + txn + sebi + stamp + ctt + gst


def load_ticks(db: Path):
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """
        SELECT received_at, ltp, bp, sp, raw_json
        FROM ticks WHERE ltp IS NOT NULL
        ORDER BY received_at ASC, id ASC
        """
    ).fetchall()
    con.close()
    return rows


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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--lots", type=float, default=100.0)
    args = ap.parse_args()
    db = Path(args.db)
    if not db.exists():
        print(f"DB not found: {db}")
        return
    rows = load_ticks(db)
    cfg = NetZigzagConfig()
    s = NetZigzagStrategy(cfg)
    trades = []
    side = None
    entry = None
    reasons = Counter()
    print(f"ticks={len(rows)} lots={args.lots} cfg={cfg}")
    for row in rows:
        ltp = float(row["ltp"])
        try:
            now = datetime.fromisoformat(str(row["received_at"]))
            if now.tzinfo is None:
                now = now.replace(tzinfo=IST)
        except Exception:
            now = datetime.now(IST)
        sig = s.on_tick(now, ltp, msg_from_row(row))
        if not sig:
            continue
        if sig.action in {"BUY", "SHORT"}:
            side = "long" if sig.action == "BUY" else "short"
            entry = ltp
        elif sig.action == "CLOSE" and side and entry is not None:
            gross = (ltp - entry) if side == "long" else (entry - ltp)
            pnl = gross * args.lots - fee_rt(ltp, args.lots)
            tag = "other"
            r = sig.reason or ""
            if r.startswith("tp"):
                tag = "tp"
            elif r.startswith("sl"):
                tag = "sl"
            elif "weaken" in r:
                tag = "weaken"
            elif "flip" in r:
                tag = "flip"
            reasons[tag] += 1
            trades.append((gross, pnl))
            side = None
            entry = None
    n = len(trades)
    if n == 0:
        print("no trades")
        return
    dir_w = sum(1 for g, _ in trades if g > 0) / n * 100
    net_w = sum(1 for _, p in trades if p > 0) / n * 100
    avg_g = sum(g for g, _ in trades) / n
    sum_r = sum(p for _, p in trades)
    print(
        f"S8_NET_ZIGZAG n={n} dir%={dir_w:.1f} net%={net_w:.1f} "
        f"avgG={avg_g:.2f} sum₹={sum_r:.1f} {dict(reasons)}"
    )


if __name__ == "__main__":
    main()
