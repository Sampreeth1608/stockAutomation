#!/usr/bin/env python3
"""Compare nested-trend variants with LTQ + bid1-5/sell1-5 filters.

Usage on VM:
  python paper_sim_s8_nested_micro.py --lots 100
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from strategy_nested_trend import NestedTrendConfig, NestedTrendStrategy

DB = Path("data/ticks.db")
IST = ZoneInfo("Asia/Kolkata")


def fee_be_pts(ltp: float, lots: float) -> float:
    brokerage = 40.0
    buy = sell = ltp * lots
    txn = 0.000021 * (buy + sell)
    sebi = 0.000001 * (buy + sell)
    stamp = 0.000020 * buy
    ctt = 0.00010 * sell
    gst = 0.18 * (brokerage + txn + sebi)
    return (brokerage + txn + sebi + stamp + ctt + gst) / max(lots, 1e-9)


def load_ticks(db: Path = DB):
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


def _message_from_row(row) -> dict:
    raw = row["raw_json"]
    if raw:
        try:
            msg = json.loads(raw)
            if isinstance(msg, dict):
                if msg.get("total_buy_quantity") is None and row["bp"] is not None:
                    msg["total_buy_quantity"] = row["bp"]
                if msg.get("total_sell_quantity") is None and row["sp"] is not None:
                    msg["total_sell_quantity"] = row["sp"]
                return msg
        except (TypeError, json.JSONDecodeError):
            pass
    return {
        "total_buy_quantity": row["bp"],
        "total_sell_quantity": row["sp"],
        "last_traded_price": (row["ltp"] * 100.0) if row["ltp"] is not None else None,
        "last_traded_quantity": 0,
        "best_5_buy_data": [],
        "best_5_sell_data": [],
    }


def reason_tag(r: str) -> str:
    if r.startswith("tp ") or r.startswith("tp+"):
        return "tp"
    if r.startswith("sl "):
        return "sl"
    if "weaken" in r:
        return "weaken"
    if "flip" in r:
        return "flip"
    if "depth_exit" in r:
        return "depth_exit"
    return "other"


def run_sim(rows, cfg: NestedTrendConfig, lots: float) -> dict:
    strat = NestedTrendStrategy(cfg)
    trades: list[dict] = []
    open_side = None
    entry_px = None
    reasons: Counter = Counter()
    grades: Counter = Counter()

    for row in rows:
        ltp = float(row["ltp"])
        msg = _message_from_row(row)
        try:
            now = datetime.fromisoformat(str(row["received_at"]))
            if now.tzinfo is None:
                now = now.replace(tzinfo=IST)
        except Exception:
            now = datetime.now(IST)

        sig = strat.on_tick(now, ltp, msg)
        if sig is None:
            continue
        if sig.action in {"BUY", "SHORT"}:
            open_side = "long" if sig.action == "BUY" else "short"
            entry_px = ltp
            grades[strat.session_bias] += 1
        elif sig.action == "CLOSE" and open_side and entry_px is not None:
            gross = (ltp - entry_px) if open_side == "long" else (entry_px - ltp)
            fee = fee_be_pts(ltp, lots) * lots
            rupees = gross * lots - fee
            reasons[reason_tag(sig.reason or "")] += 1
            trades.append({"gross": gross, "rupees": rupees})
            open_side = None
            entry_px = None

    n = len(trades)
    if n == 0:
        return {
            "n": 0,
            "dir%": 0.0,
            "net%": 0.0,
            "avgG": 0.0,
            "sum₹": 0.0,
            "reasons": {},
            "grades": {},
        }
    return {
        "n": n,
        "dir%": round(sum(1 for t in trades if t["gross"] > 0) / n * 100, 1),
        "net%": round(sum(1 for t in trades if t["rupees"] > 0) / n * 100, 1),
        "avgG": round(sum(t["gross"] for t in trades) / n, 2),
        "sum₹": round(sum(t["rupees"] for t in trades), 1),
        "reasons": dict(reasons),
        "grades": dict(grades),
    }


def variants() -> list[tuple[str, NestedTrendConfig]]:
    base = NestedTrendConfig(
        min_imb_pct=10.0,
        weaken_pct=10.0,
        tp_points=25.0,
        sl_points=20.0,
        pullback_points=8.0,
        resume_points=5.0,
        use_fee_gate=False,
        require_depth=False,
        require_ltq=False,
        depth_exit=False,
    )
    return [
        ("baseline", base),
        ("depth", replace(base, require_depth=True, depth_ratio=1.15)),
        ("depth_1.25", replace(base, require_depth=True, depth_ratio=1.25)),
        ("ltq", replace(base, require_ltq=True, ltq_min=1.0, ltq_vs_median=1.0)),
        ("ltq_1.5med", replace(base, require_ltq=True, ltq_min=1.0, ltq_vs_median=1.5)),
        (
            "depth+ltq",
            replace(
                base,
                require_depth=True,
                depth_ratio=1.15,
                require_ltq=True,
                ltq_min=1.0,
                ltq_vs_median=1.0,
            ),
        ),
        (
            "depth+ltq+dexit",
            replace(
                base,
                require_depth=True,
                depth_ratio=1.15,
                require_ltq=True,
                ltq_min=1.0,
                ltq_vs_median=1.0,
                depth_exit=True,
            ),
        ),
        (
            "strict",
            replace(
                base,
                require_depth=True,
                depth_ratio=1.25,
                require_ltq=True,
                ltq_vs_median=1.5,
                pullback_points=12.0,
                resume_points=6.0,
            ),
        ),
    ]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--lots", type=float, default=100.0)
    args = ap.parse_args()
    db = Path(args.db)
    if not db.exists():
        print(f"DB not found: {db}")
        return

    print("Loading ticks...")
    rows = load_ticks(db)
    print(f"ticks={len(rows)}  lots={args.lots}")
    print(
        f"{'mode':18} {'n':>4} {'dir%':>6} {'net%':>6} {'avgG':>7} {'sum₹':>10}  exits"
    )
    print("-" * 88)
    for name, cfg in variants():
        r = run_sim(rows, cfg, lots=args.lots)
        print(
            f"{name:18} {r['n']:4} {r['dir%']:6.1f} {r['net%']:6.1f} "
            f"{r['avgG']:7.2f} {r['sum₹']:10.1f}  {r['reasons']}"
        )


if __name__ == "__main__":
    main()
