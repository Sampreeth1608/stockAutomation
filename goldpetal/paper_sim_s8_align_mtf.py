#!/usr/bin/env python3
"""S8 ALIGN hist on tick + time bars + count bars (5t…60t).

Time TFs: 1m,2m,3m,5m,10m,15m,30m,1h
Tick-count TFs: 5t,10t,15t,20t,30t,40t,50t,60t

Uses book-driven SL/TP (price∩TBQ∩TSQ), not bare price-range stops.

  python3 paper_sim_s8_align_mtf.py --db data/ticks.db --lots 100
  python3 paper_sim_s8_align_mtf.py --db data/ticks.db --lots 100 --day 2026-08-10
  python3 paper_sim_s8_align_mtf.py --db data/ticks.db --lots 100 --only ticks
  python3 paper_sim_s8_align_mtf.py --db data/ticks.db --lots 100 --json-out /tmp/s8_align_mtf.json
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from mtf_bars import (
    INTERVALS,
    TICK_INTERVALS,
    build_rich_bars,
    build_rich_bars_by_ticks,
    load_tick_rows,
)
from strategy_net_zigzag import NetZigzagConfig, NetZigzagStrategy
from strategy_s8_align import AlignS8Config, AlignS8Strategy

IST = ZoneInfo("Asia/Kolkata")
DB = Path("data/ticks.db")
TFS = list(INTERVALS)  # 1m..1h
TICK_TFS = list(TICK_INTERVALS)  # 5t..60t


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


def _tag(reason: str) -> str:
    r = reason or ""
    if r.startswith("tp") or "stall" in r:
        return "tp"
    if r.startswith("sl"):
        return "sl"
    if "book_break" in r:
        return "break"
    if "flip" in r:
        return "flip"
    if "weaken" in r:
        return "weaken"
    return "other"


def _pack(trades: list[tuple[float, float]], reasons: Counter, n_in: int) -> dict:
    if not trades:
        return {
            "n_in": n_in,
            "n": 0,
            "dir_pct": 0.0,
            "avgG": 0.0,
            "sumPts": 0.0,
            "sumInr": 0.0,
            "reasons": {},
        }
    gs = [g for g, _ in trades]
    ps = [p for _, p in trades]
    nt = len(trades)
    return {
        "n_in": n_in,
        "n": nt,
        "dir_pct": round(sum(1 for g in gs if g > 0) / nt * 100, 1),
        "avgG": round(sum(gs) / nt, 2),
        "sumPts": round(sum(gs), 1),
        "sumInr": round(sum(ps), 1),
        "reasons": dict(reasons),
    }


def summarize(label: str, pack: dict) -> None:
    if pack["n"] == 0:
        print(f"{label:18} | n_in={pack['n_in']:6} | trades=0")
        return
    print(
        f"{label:18} | n_in={pack['n_in']:6} | trades={pack['n']:3} "
        f"dir%={pack['dir_pct']:5.1f} "
        f"avgG={pack['avgG']:7.2f} sumPts={pack['sumPts']:+8.1f} "
        f"sum₹={pack['sumInr']:9.1f} {pack['reasons']}"
    )


def run_align_ticks(rows, lots: float, cfg: AlignS8Config) -> dict:
    s = AlignS8Strategy(cfg)
    trades: list[tuple[float, float]] = []
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
            reasons[_tag(sig.reason or "")] += 1
            trades.append((gross, pnl))
            side = entry = None
    if side and entry is not None and rows:
        ltp = float(rows[-1]["ltp"])
        gross = (ltp - entry) if side == "long" else (entry - ltp)
        trades.append((gross, gross * lots - fee_rt(ltp, lots)))
        reasons["eod"] += 1
    return _pack(trades, reasons, len(rows))


def run_align_bars(bars: list[dict], lots: float, cfg: AlignS8Config) -> dict:
    s = AlignS8Strategy(cfg)
    s.book_allow_memory = 8
    trades: list[tuple[float, float]] = []
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
            reasons[_tag(sig.reason or "")] += 1
            trades.append((gross, pnl))
            side = entry = None
    if side and entry is not None and bars:
        px = float(bars[-1]["close"])
        gross = (px - entry) if side == "long" else (entry - px)
        trades.append((gross, gross * lots - fee_rt(px, lots)))
        reasons["eod"] += 1
    return _pack(trades, reasons, len(bars))


def run_legacy_always(rows, lots: float, bar_minutes: int) -> dict:
    s = NetZigzagStrategy(
        NetZigzagConfig(
            bar_minutes=bar_minutes,
            entry_mode="always",
            cooldown_ticks=0,
            min_imb_pct=10,
            tp_points=25,
            sl_points=20,
            weaken_pct=10,
        )
    )
    trades: list[tuple[float, float]] = []
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
            reasons[_tag(sig.reason or "")] += 1
            trades.append((gross, pnl))
            side = entry = None
    return _pack(trades, reasons, len(rows))


def print_md_table(rows: list[dict]) -> None:
    print("\n| TF | n_in | trades | dir% | avgG | sumPts | sum₹ | exits |")
    print("|----|-----:|------:|-----:|-----:|-------:|-----:|-------|")
    for r in rows:
        print(
            f"| {r['tf']} | {r['n_in']} | {r['n']} | {r['dir_pct']} | "
            f"{r['avgG']} | {r['sumPts']:+.1f} | {r['sumInr']:.1f} | "
            f"`{r['reasons']}` |"
        )


def align_cfg_for_scale(scale: float | None) -> AlignS8Config:
    """scale=None → raw tick; else minutes or tick-count used to loosen pullbacks."""
    if scale is None:
        return AlignS8Config(
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
    s = float(scale)
    return AlignS8Config(
        min_imb_pct=10.0,
        book_frac_of_net=0.10,
        pullback_points=max(5.0, 4.0 + s * 0.3),
        resume_points=max(3.0, 3.0 + s * 0.15),
        stall_min_profit=20.0,
        stall_bars=2 if s >= 15 else 3,
        sl_min=20.0,
        tp_min=20.0,
        weaken_pct=20.0,
        cooldown_ticks=1,
    )


def run_report(rows, lots: float, only: str = "all") -> dict:
    results: list[dict] = []
    only = (only or "all").strip().lower()

    print("\n=== S8 ALIGN book-driven SL/TP ===")
    if only in {"all", "time", "raw"}:
        tick_pack = run_align_ticks(rows, lots, align_cfg_for_scale(None))
        summarize("TICK", tick_pack)
        results.append({"tf": "tick", "kind": "ALIGN", **tick_pack})

    if only in {"all", "time"}:
        for tf_name, minutes in TFS:
            bars = [b.to_row() for b in build_rich_bars(rows, tf_name, minutes)]
            pack = run_align_bars(bars, lots, align_cfg_for_scale(minutes))
            summarize(f"ALIGN_{tf_name}", pack)
            results.append({"tf": tf_name, "kind": "ALIGN", **pack})

    if only in {"all", "ticks"}:
        print("\n=== S8 ALIGN on tick-count bars (5t…60t) ===")
        for tf_name, n_ticks in TICK_TFS:
            bars = [b.to_row() for b in build_rich_bars_by_ticks(rows, tf_name, n_ticks)]
            # scale pullbacks gently with count (5t≈light, 60t≈heavier)
            scale = max(1.0, n_ticks / 5.0)
            pack = run_align_bars(bars, lots, align_cfg_for_scale(scale))
            summarize(f"ALIGN_{tf_name}", pack)
            results.append({"tf": tf_name, "kind": "ALIGN", **pack})

    if only in {"all", "time"}:
        print("\n=== reference: legacy always (fixed TP25/SL20) ===")
        leg30 = run_legacy_always(rows, lots, bar_minutes=30)
        summarize("LEGACY_30m", leg30)
        results.append({"tf": "30m", "kind": "LEGACY_ALWAYS", **leg30})

        leg_tick = run_legacy_always(rows, lots, bar_minutes=0)
        summarize("LEGACY_tick", leg_tick)
        results.append({"tf": "tick", "kind": "LEGACY_ALWAYS", **leg_tick})

    align_only = [r for r in results if r["kind"] == "ALIGN"]
    print("\n=== ALIGN markdown ===")
    print_md_table(align_only)
    return {"rows": results, "align": align_only}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--lots", type=float, default=100.0)
    ap.add_argument("--day", default="")
    ap.add_argument(
        "--only",
        default="all",
        choices=["all", "time", "ticks", "raw"],
        help="all=tick+time+5t..60t; ticks=5t..60t only; time=tick+1m..1h",
    )
    ap.add_argument("--json-out", default="")
    args = ap.parse_args()
    day = args.day.strip() or None

    db_path = Path(args.db)
    if not db_path.exists() or db_path.stat().st_size == 0:
        raise SystemExit(f"DB missing or empty: {db_path}")

    rows = filter_rows_day(load_tick_rows(db_path), day)
    print(f"db={args.db} day={day or 'ALL'} ticks={len(rows)} lots={args.lots} only={args.only}")
    if len(rows) < 100:
        raise SystemExit("need more ticks")

    report = run_report(rows, args.lots, only=args.only)
    payload = {
        "db": str(args.db),
        "day": day or "ALL",
        "ticks": len(rows),
        "lots": args.lots,
        "only": args.only,
        "results": report["rows"],
    }
    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
