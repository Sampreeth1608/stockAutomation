#!/usr/bin/env python3
"""Historical paper sim for S8_NESTED_TREND (session bias + nested zigzags).

Usage (on VM with ticks.db):
  python paper_sim_s8_nested_trend.py
  python paper_sim_s8_nested_trend.py --lots 100
  python paper_sim_s8_nested_trend.py --compare-fixed
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from strategy_nested_trend import NestedTrendConfig, NestedTrendStrategy

DB = Path("data/ticks.db")


def fee_be_pts(ltp: float, lots: float) -> float:
    brokerage = 40.0  # round-trip ₹20×2
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
                # Ensure TBQ/TSQ present even if raw is sparse
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
    }


def run_sim(
    rows,
    cfg: NestedTrendConfig,
    lots: float = 1.0,
) -> dict:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    IST = ZoneInfo("Asia/Kolkata")
    strat = NestedTrendStrategy(cfg)
    trades: list[dict] = []
    open_side = None
    entry_px = None
    entry_t = None
    grades = Counter()
    reasons = Counter()

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
            entry_t = str(row["received_at"])
            grades[strat.session_bias] += 1
        elif sig.action == "CLOSE" and open_side and entry_px is not None:
            gross = (ltp - entry_px) if open_side == "long" else (entry_px - ltp)
            rupees = gross * lots  # 1 pt ≈ ₹1 / lot
            fee = fee_be_pts(ltp, lots) * lots
            # reason tag
            tag = "other"
            r = sig.reason or ""
            for key in ("tp ", "sl ", "tbq_weaken", "tsq_weaken", "session_flip"):
                if r.startswith(key.strip()) or key.strip() in r:
                    tag = key.strip().replace(" ", "")
                    break
            if "tp" in r[:3]:
                tag = "tp"
            elif "sl" in r[:3]:
                tag = "sl"
            elif "weaken" in r:
                tag = "weaken"
            elif "flip" in r:
                tag = "flip"
            reasons[tag] += 1
            trades.append(
                {
                    "side": open_side,
                    "entry": entry_px,
                    "exit": ltp,
                    "gross": gross,
                    "rupees": rupees - fee,
                    "gross_rupees": rupees,
                    "fee": fee,
                    "reason": r,
                    "entry_t": entry_t,
                    "exit_t": str(row["received_at"]),
                    "bias": strat.session_bias,
                }
            )
            open_side = None
            entry_px = None
            entry_t = None

    n = len(trades)
    if n == 0:
        return {
            "n": 0,
            "dir_win%": 0.0,
            "net_win%": 0.0,
            "avg_gross": 0.0,
            "avg_tp": cfg.tp_points,
            "avg_sl": cfg.sl_points,
            "sum_₹": 0.0,
            "avg_₹": 0.0,
            "reasons": dict(reasons),
            "grades": dict(grades),
            "note": "no_trades",
        }

    dir_wins = sum(1 for t in trades if t["gross"] > 0)
    net_wins = sum(1 for t in trades if t["rupees"] > 0)
    return {
        "n": n,
        "dir_win%": round(dir_wins / n * 100.0, 1),
        "net_win%": round(net_wins / n * 100.0, 1),
        "avg_gross": round(sum(t["gross"] for t in trades) / n, 2),
        "avg_tp": cfg.tp_points,
        "avg_sl": cfg.sl_points,
        "sum_₹": round(sum(t["rupees"] for t in trades), 1),
        "avg_₹": round(sum(t["rupees"] for t in trades) / n, 1),
        "reasons": dict(reasons),
        "grades": dict(grades),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="S8 nested-trend paper sim")
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--lots", type=float, default=1.0)
    ap.add_argument("--tp", type=float, default=25.0)
    ap.add_argument("--sl", type=float, default=20.0)
    ap.add_argument("--imb", type=float, default=10.0)
    ap.add_argument("--weaken", type=float, default=10.0)
    ap.add_argument("--pullback", type=float, default=8.0)
    ap.add_argument("--resume", type=float, default=5.0)
    ap.add_argument("--fee-gate", action="store_true")
    ap.add_argument(
        "--compare-fixed",
        action="store_true",
        help="Also print a no-pullback immediate-bias baseline note",
    )
    args = ap.parse_args()

    db = Path(args.db)
    if not db.exists():
        print(f"DB not found: {db} — copy ticks.db or run on the goldpetal VM")
        return

    print("Loading ticks...")
    rows = load_ticks(db)
    print(f"ticks={len(rows)}")

    cfg = NestedTrendConfig(
        min_imb_pct=args.imb,
        weaken_pct=args.weaken,
        tp_points=args.tp,
        sl_points=args.sl,
        pullback_points=args.pullback,
        resume_points=args.resume,
        use_fee_gate=args.fee_gate,
    )
    print("config:", {k: v for k, v in asdict(cfg).items() if k != "fee_be_points"})

    for lots in ([args.lots] if args.lots != 1 else [1.0, 100.0]):
        r = run_sim(rows, cfg, lots=lots)
        if r["n"] == 0 and cfg.use_fee_gate:
            print(f"nested_trend  lots={lots:>4}   0  (fee gate / no entries)")
            continue
        print(
            f"nested_trend  lots={lots:>4}  n={r['n']:>3}  "
            f"dir%={r['dir_win%']:>5}  net%={r['net_win%']:>5}  "
            f"avgG={r['avg_gross']:>6}  sum₹={r['sum_₹']:>10}  "
            f"{r['grades']} {r['reasons']}"
        )

    if args.compare_fixed:
        print(
            "\nNote: prior fixed zigzag research best was imb10/weaken10/TP25/SL20 "
            "@100 lots (~+₹3.5–5.5k). This nested sim adds pullback-resume gating."
        )


if __name__ == "__main__":
    main()
