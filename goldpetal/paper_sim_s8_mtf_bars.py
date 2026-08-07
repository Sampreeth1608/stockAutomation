#!/usr/bin/env python3
"""Build 1m..1h bars from ticks and paper-sim S8_NET_ZIGZAG on each TF.

Usage:
  python paper_sim_s8_mtf_bars.py --lots 100
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from strategy_net_zigzag import NetZigzagConfig, NetZigzagStrategy

DB = Path("data/ticks.db")
IST = ZoneInfo("Asia/Kolkata")

INTERVALS = [
    ("1m", 1),
    ("2m", 2),
    ("3m", 3),
    ("5m", 5),
    ("10m", 10),
    ("15m", 15),
    ("30m", 30),
    ("1h", 60),
]


def fee_rt(ltp: float, lots: float) -> float:
    brokerage = 40.0
    buy = sell = ltp * lots
    txn = 0.000021 * (buy + sell)
    sebi = 0.000001 * (buy + sell)
    stamp = 0.000020 * buy
    ctt = 0.00010 * sell
    gst = 0.18 * (brokerage + txn + sebi)
    return brokerage + txn + sebi + stamp + ctt + gst


def _parse_ts(raw: str) -> datetime:
    try:
        dt = datetime.fromisoformat(str(raw))
    except Exception:
        return datetime.now(IST)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    return dt.astimezone(IST)


def _floor_bar(ts: datetime, minutes: int) -> datetime:
    # Align to IST wall-clock blocks from midnight
    midnight = ts.replace(hour=0, minute=0, second=0, microsecond=0)
    mins = int((ts - midnight).total_seconds() // 60)
    block = (mins // minutes) * minutes
    return midnight + timedelta(minutes=block)


@dataclass
class Bar:
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    tbq: float
    tsq: float
    n_ticks: int


def load_ticks(db: Path):
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """
        SELECT received_at, ltp, bp, sp, raw_json
        FROM ticks
        WHERE ltp IS NOT NULL AND bp IS NOT NULL AND sp IS NOT NULL
        ORDER BY received_at ASC, id ASC
        """
    ).fetchall()
    con.close()
    return rows


def _tbq_tsq(row) -> tuple[float, float]:
    tbq = row["bp"]
    tsq = row["sp"]
    if row["raw_json"]:
        try:
            m = json.loads(row["raw_json"])
            if isinstance(m, dict):
                if m.get("total_buy_quantity") is not None:
                    tbq = m["total_buy_quantity"]
                if m.get("total_sell_quantity") is not None:
                    tsq = m["total_sell_quantity"]
        except (TypeError, json.JSONDecodeError):
            pass
    return float(tbq), float(tsq)


def build_bars(rows, minutes: int) -> list[Bar]:
    bars: list[Bar] = []
    cur_key: datetime | None = None
    o = h = l = c = None
    tbq = tsq = 0.0
    n = 0

    def flush(key: datetime) -> None:
        nonlocal o, h, l, c, tbq, tsq, n
        if o is None or c is None or h is None or l is None:
            return
        bars.append(
            Bar(
                ts=key,
                open=float(o),
                high=float(h),
                low=float(l),
                close=float(c),
                tbq=float(tbq),
                tsq=float(tsq),
                n_ticks=n,
            )
        )
        o = h = l = c = None
        n = 0

    for row in rows:
        ts = _parse_ts(row["received_at"])
        key = _floor_bar(ts, minutes)
        ltp = float(row["ltp"])
        bq, sq = _tbq_tsq(row)
        if cur_key is None:
            cur_key = key
        if key != cur_key:
            flush(cur_key)
            cur_key = key
        if o is None:
            o = h = l = c = ltp
            n = 0
        h = max(h, ltp)
        l = min(l, ltp)
        c = ltp
        tbq, tsq = bq, sq  # bar-end TBQ/TSQ
        n += 1
    if cur_key is not None:
        flush(cur_key)
    return bars


def run_on_bars(bars: list[Bar], cfg: NetZigzagConfig, lots: float) -> dict:
    s = NetZigzagStrategy(cfg)
    trades = []
    side = None
    entry = None
    reasons: Counter = Counter()

    for bar in bars:
        msg = {
            "total_buy_quantity": bar.tbq,
            "total_sell_quantity": bar.tsq,
        }
        # Drive strategy on bar close (one decision per bar)
        sig = s.on_tick(bar.ts, bar.close, msg)
        if not sig:
            continue
        if sig.action in {"BUY", "SHORT"}:
            side = "long" if sig.action == "BUY" else "short"
            entry = bar.close
        elif sig.action == "CLOSE" and side and entry is not None:
            gross = (bar.close - entry) if side == "long" else (entry - bar.close)
            pnl = gross * lots - fee_rt(bar.close, lots)
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
            trades.append((gross, pnl, side))
            side = None
            entry = None

    n = len(trades)
    if n == 0:
        return {
            "bars": len(bars),
            "n": 0,
            "dir%": 0.0,
            "net%": 0.0,
            "avgG": 0.0,
            "sum₹": 0.0,
            "avg_bar_range": 0.0,
            "reasons": {},
        }
    ranges = [b.high - b.low for b in bars]
    return {
        "bars": len(bars),
        "n": n,
        "dir%": round(sum(1 for g, _, _ in trades if g > 0) / n * 100, 1),
        "net%": round(sum(1 for _, p, _ in trades if p > 0) / n * 100, 1),
        "avgG": round(sum(g for g, _, _ in trades) / n, 2),
        "sum₹": round(sum(p for _, p, _ in trades), 1),
        "avg_bar_range": round(sum(ranges) / max(len(ranges), 1), 2),
        "reasons": dict(reasons),
    }


def bar_stats(bars: list[Bar]) -> dict:
    if not bars:
        return {"bars": 0}
    ranges = [b.high - b.low for b in bars]
    nets = [b.tbq - b.tsq for b in bars]
    closes = [b.close for b in bars]
    day_move = closes[-1] - closes[0] if closes else 0.0
    return {
        "bars": len(bars),
        "avg_range": round(sum(ranges) / len(ranges), 2),
        "med_range": round(sorted(ranges)[len(ranges) // 2], 2),
        "pct_range_ge20": round(sum(1 for r in ranges if r >= 20) / len(ranges) * 100, 1),
        "pct_range_ge25": round(sum(1 for r in ranges if r >= 25) / len(ranges) * 100, 1),
        "pct_range_ge30": round(sum(1 for r in ranges if r >= 30) / len(ranges) * 100, 1),
        "net_pos_%": round(sum(1 for n in nets if n > 0) / len(nets) * 100, 1),
        "session_move": round(day_move, 1),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="MTF bars from ticks + S8 zigzag")
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--lots", type=float, default=100.0)
    ap.add_argument(
        "--mode",
        default="both",
        choices=["always", "edge", "pullback", "both"],
        help="S8 entry mode",
    )
    ap.add_argument("--cooldown", type=int, default=2, help="cooldown in BARS (not ticks)")
    ap.add_argument("--tp", type=float, default=25.0)
    ap.add_argument("--sl", type=float, default=20.0)
    args = ap.parse_args()

    db = Path(args.db)
    if not db.exists():
        print(f"DB not found: {db}")
        return

    print("Loading ticks...")
    rows = load_ticks(db)
    print(f"ticks={len(rows)} lots={args.lots} entry_mode={args.mode} cd_bars={args.cooldown}")
    print()
    print("=== Bar structure (range = high-low pts) ===")
    print(
        f"{'TF':>4} {'bars':>6} {'avgR':>6} {'medR':>6} "
        f"{'≥20%':>6} {'≥25%':>6} {'≥30%':>6} {'NET+%':>6} {'sessΔ':>7}"
    )
    print("-" * 70)

    built: dict[str, list[Bar]] = {}
    for name, mins in INTERVALS:
        bars = build_bars(rows, mins)
        built[name] = bars
        st = bar_stats(bars)
        print(
            f"{name:>4} {st['bars']:6} {st['avg_range']:6.2f} {st['med_range']:6.2f} "
            f"{st['pct_range_ge20']:6.1f} {st['pct_range_ge25']:6.1f} "
            f"{st['pct_range_ge30']:6.1f} {st['net_pos_%']:6.1f} {st['session_move']:7.1f}"
        )

    cfg = NetZigzagConfig(
        min_imb_pct=10.0,
        weaken_pct=10.0,
        tp_points=args.tp,
        sl_points=args.sl,
        cooldown_ticks=args.cooldown,  # bars, since each bar = 1 on_tick
        entry_mode=args.mode,
        pullback_points=8.0,
        resume_points=5.0,
        use_fee_gate=False,
    )

    print()
    print(f"=== S8_NET_ZIGZAG on bar closes (TP={args.tp} SL={args.sl}) ===")
    print(
        f"{'TF':>4} {'bars':>6} {'n':>4} {'dir%':>6} {'net%':>6} "
        f"{'avgG':>7} {'sum₹':>10}  exits"
    )
    print("-" * 88)
    for name, _ in INTERVALS:
        r = run_on_bars(built[name], cfg, args.lots)
        print(
            f"{name:>4} {r['bars']:6} {r['n']:4} {r['dir%']:6.1f} {r['net%']:6.1f} "
            f"{r['avgG']:7.2f} {r['sum₹']:10.1f}  {r['reasons']}"
        )

    # Also try "always" spam on each TF for reference
    print()
    print("=== Reference: entry_mode=always (no gate) ===")
    print(
        f"{'TF':>4} {'n':>4} {'dir%':>6} {'avgG':>7} {'sum₹':>10}  exits"
    )
    print("-" * 60)
    always_cfg = NetZigzagConfig(
        min_imb_pct=10.0,
        weaken_pct=10.0,
        tp_points=args.tp,
        sl_points=args.sl,
        cooldown_ticks=0,
        entry_mode="always",
        use_fee_gate=False,
    )
    for name, _ in INTERVALS:
        r = run_on_bars(built[name], always_cfg, args.lots)
        print(
            f"{name:>4} {r['n']:4} {r['dir%']:6.1f} {r['avgG']:7.2f} "
            f"{r['sum₹']:10.1f}  {r['reasons']}"
        )


if __name__ == "__main__":
    main()
