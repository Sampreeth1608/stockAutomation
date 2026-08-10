#!/usr/bin/env python3
"""Historical paper sim for S8 ALIGN (price∩TBQ/TSQ) on ticks.db.

Examples:
  python3 paper_sim_s8_align.py --db data/ticks.db --lots 100
  python3 paper_sim_s8_align.py --db data/ticks.db --lots 100 --day 2026-08-10
  python3 paper_sim_s8_align.py --db data/ticks.db --lots 100 --compare
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from strategy_net_zigzag import NetZigzagConfig, NetZigzagStrategy
from strategy_s8_align import AlignS8Config, AlignS8Strategy

IST = ZoneInfo("Asia/Kolkata")
DB = Path("data/ticks.db")


def fee_rt(ltp: float, lots: float) -> float:
    brokerage = 40.0
    buy = sell = ltp * lots
    txn = 0.000021 * (buy + sell)
    sebi = 0.000001 * (buy + sell)
    stamp = 0.000020 * buy
    ctt = 0.00010 * sell
    gst = 0.18 * (brokerage + txn + sebi)
    return brokerage + txn + sebi + stamp + ctt + gst


def load_ticks(db: Path, day: str | None = None):
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    if day:
        rows = con.execute(
            """
            SELECT received_at, ltp, bp, sp, raw_json
            FROM ticks
            WHERE ltp IS NOT NULL
              AND received_at LIKE ?
            ORDER BY received_at ASC, id ASC
            """,
            (f"{day}%",),
        ).fetchall()
    else:
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


def parse_ts(raw: str) -> datetime:
    try:
        now = datetime.fromisoformat(str(raw))
        if now.tzinfo is None:
            now = now.replace(tzinfo=IST)
        return now
    except Exception:
        return datetime.now(IST)


def run_align(rows, cfg: AlignS8Config, lots: float, *, detail: bool):
    s = AlignS8Strategy(cfg)
    trades: list[tuple] = []
    side = None
    entry = None
    entry_ts = None
    reasons: Counter = Counter()

    for row in rows:
        ltp = float(row["ltp"])
        now = parse_ts(row["received_at"])
        sig = s.on_tick(now, ltp, msg_from_row(row))
        if not sig:
            continue
        if sig.action in {"BUY", "SHORT"}:
            side = "long" if sig.action == "BUY" else "short"
            entry = ltp
            entry_ts = str(row["received_at"])
            if detail:
                print(
                    f"  ENTER {sig.action:5} {entry_ts} px={ltp:.1f} "
                    f"{(sig.reason or '')[:70]}"
                )
        elif sig.action == "CLOSE" and side and entry is not None:
            gross = (ltp - entry) if side == "long" else (entry - ltp)
            pnl = gross * lots - fee_rt(ltp, lots)
            r = sig.reason or ""
            tag = "other"
            if r.startswith("tp") or "stall" in r:
                tag = "tp"
            elif r.startswith("sl"):
                tag = "sl"
            elif "book_break" in r:
                tag = "break"
            elif "weaken" in r:
                tag = "weaken"
            elif "flip" in r or "align_flip" in r:
                tag = "flip"
            reasons[tag] += 1
            trades.append((gross, pnl, side, entry_ts, str(row["received_at"]), r[:56]))
            if detail:
                print(
                    f"  EXIT  CLOSE {row['received_at']} px={ltp:.1f} "
                    f"pts={gross:+.1f} ₹={pnl:+.0f} {r[:50]}"
                )
            side = None
            entry = None
            entry_ts = None

    if side and entry is not None and rows:
        ltp = float(rows[-1]["ltp"])
        gross = (ltp - entry) if side == "long" else (entry - ltp)
        pnl = gross * lots - fee_rt(ltp, lots)
        reasons["eod"] += 1
        trades.append(
            (gross, pnl, side, entry_ts, str(rows[-1]["received_at"]), "EOD_FLAT")
        )
    return trades, reasons


def run_legacy(rows, cfg: NetZigzagConfig, lots: float):
    s = NetZigzagStrategy(cfg)
    trades = []
    side = None
    entry = None
    reasons: Counter = Counter()
    for row in rows:
        ltp = float(row["ltp"])
        now = parse_ts(row["received_at"])
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
            tag = (
                "tp"
                if r.startswith("tp")
                else "sl"
                if r.startswith("sl")
                else "flip"
                if "flip" in r
                else "weaken"
                if "weaken" in r
                else "other"
            )
            reasons[tag] += 1
            trades.append((gross, pnl))
            side = None
            entry = None
    return trades, reasons


def summarize(label: str, trades: list, reasons: Counter, n_ticks: int) -> None:
    n = len(trades)
    if n == 0:
        print(f"{label} | ticks={n_ticks} | n=0 (no trades)")
        return
    # trades may be 2-tuple or 6-tuple
    gs = [t[0] for t in trades]
    ps = [t[1] for t in trades]
    print(
        f"{label} | ticks={n_ticks} | n={n} "
        f"dir%={sum(1 for g in gs if g > 0) / n * 100:.1f} "
        f"avgG={sum(gs) / n:.2f} sumPts={sum(gs):+.1f} "
        f"sum₹={sum(ps):.1f} {dict(reasons)}"
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--lots", type=float, default=100.0)
    ap.add_argument("--day", default="", help="Filter YYYY-MM-DD (IST date prefix)")
    ap.add_argument("--detail", action="store_true", help="Print each ALIGN entry/exit")
    ap.add_argument(
        "--compare",
        action="store_true",
        help="Also run legacy 30m always + tick both for reference",
    )
    ap.add_argument("--min-imb", type=float, default=10.0)
    args = ap.parse_args()

    day = args.day.strip() or None
    rows = load_ticks(Path(args.db), day=day)
    print(f"db={args.db} day={day or 'ALL'} ticks={len(rows)} lots={args.lots}")
    if not rows:
        raise SystemExit("no ticks")

    cfg = AlignS8Config(
        min_imb_pct=args.min_imb,
        book_frac_of_net=0.10,
        pullback_points=8.0,
        resume_points=5.0,
        cooldown_ticks=5,
        weaken_pct=20.0,
        stall_min_profit=20.0,
        sl_min=20.0,
        tp_min=20.0,
    )
    trades, reasons = run_align(rows, cfg, args.lots, detail=args.detail)
    summarize("ALIGN", trades, reasons, len(rows))
    if args.detail and trades:
        print("--- trade list (pts) ---")
        for t in trades:
            g, p = t[0], t[1]
            extra = f" {t[2]} {t[3]}→{t[4]}" if len(t) > 2 else ""
            print(f"  pts={g:+.1f} ₹={p:+.0f}{extra} {(t[5] if len(t) > 5 else '')}")

    if args.compare:
        legacy_always, r1 = run_legacy(
            rows,
            NetZigzagConfig(
                bar_minutes=30,
                entry_mode="always",
                cooldown_ticks=0,
                min_imb_pct=args.min_imb,
                tp_points=25,
                sl_points=20,
                weaken_pct=10,
            ),
            args.lots,
        )
        summarize("LEGACY_30m_ALWAYS", legacy_always, r1, len(rows))
        legacy_tick, r2 = run_legacy(
            rows,
            NetZigzagConfig(
                bar_minutes=0,
                entry_mode="both",
                cooldown_ticks=40,
                min_imb_pct=args.min_imb,
                tp_points=25,
                sl_points=20,
                weaken_pct=10,
            ),
            args.lots,
        )
        summarize("LEGACY_TICK_BOTH", legacy_tick, r2, len(rows))


if __name__ == "__main__":
    main()
