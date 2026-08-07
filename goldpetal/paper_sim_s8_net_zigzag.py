#!/usr/bin/env python3
"""Historical paper sim for S8_NET_ZIGZAG — sweep entry gates."""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from dataclasses import replace
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


def run(rows, cfg: NetZigzagConfig, lots: float) -> dict:
    s = NetZigzagStrategy(cfg)
    trades = []
    side = None
    entry = None
    reasons: Counter = Counter()
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
            pnl = gross * lots - fee_rt(ltp, lots)
            r = sig.reason or ""
            tag = "other"
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
        return {"n": 0, "dir%": 0, "net%": 0, "avgG": 0, "sum₹": 0, "reasons": {}}
    return {
        "n": n,
        "dir%": round(sum(1 for g, _ in trades if g > 0) / n * 100, 1),
        "net%": round(sum(1 for _, p in trades if p > 0) / n * 100, 1),
        "avgG": round(sum(g for g, _ in trades) / n, 2),
        "sum₹": round(sum(p for _, p in trades), 1),
        "reasons": dict(reasons),
    }


def variants() -> list[tuple[str, NetZigzagConfig]]:
    base = NetZigzagConfig(
        min_imb_pct=10.0,
        weaken_pct=10.0,
        tp_points=25.0,
        sl_points=20.0,
        use_fee_gate=False,
    )
    return [
        ("spam_old", replace(base, cooldown_ticks=0, entry_mode="always")),
        ("edge_cd40", replace(base, cooldown_ticks=40, entry_mode="edge")),
        ("edge_cd80", replace(base, cooldown_ticks=80, entry_mode="edge")),
        ("pullback", replace(base, cooldown_ticks=20, entry_mode="pullback")),
        ("both_cd40", replace(base, cooldown_ticks=40, entry_mode="both")),
        ("both_cd80", replace(base, cooldown_ticks=80, entry_mode="both")),
        ("strict_pb12", replace(base, cooldown_ticks=40, entry_mode="pullback", pullback_points=12, resume_points=6)),
    ]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--lots", type=float, default=100.0)
    ap.add_argument("--sweep", action="store_true", help="compare entry gates")
    args = ap.parse_args()
    db = Path(args.db)
    if not db.exists():
        print(f"DB not found: {db}")
        return
    rows = load_ticks(db)
    print(f"ticks={len(rows)} lots={args.lots}")
    if not args.sweep:
        # default = gated both/cd40
        r = run(rows, NetZigzagConfig(), args.lots)
        print(
            f"S8_NET_ZIGZAG n={r['n']} dir%={r['dir%']} net%={r['net%']} "
            f"avgG={r['avgG']} sum₹={r['sum₹']} {r['reasons']}"
        )
        print("Tip: re-run with --sweep to compare entry gates")
        return

    print(f"{'mode':14} {'n':>4} {'dir%':>6} {'net%':>6} {'avgG':>7} {'sum₹':>10}  exits")
    print("-" * 80)
    for name, cfg in variants():
        r = run(rows, cfg, args.lots)
        print(
            f"{name:14} {r['n']:4} {r['dir%']:6.1f} {r['net%']:6.1f} "
            f"{r['avgG']:7.2f} {r['sum₹']:10.1f}  {r['reasons']}"
        )


if __name__ == "__main__":
    main()
