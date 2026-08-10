#!/usr/bin/env python3
"""Sweep ALIGN "models" (exit presets) on best TFs to raise stable profit.

Models try to stop book_break from eating trades and push for larger TPs.

  python3 paper_sim_s8_align_models.py --db data/ticks.db --lots 100
  python3 paper_sim_s8_align_models.py --db data/ticks.db --lots 100 --day 2026-08-10
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from mtf_bars import build_rich_bars, build_rich_bars_by_ticks, load_tick_rows
from strategy_net_zigzag import NetZigzagConfig, NetZigzagStrategy
from strategy_s8_align import AlignS8Config, AlignS8Strategy

IST = ZoneInfo("Asia/Kolkata")
DB = Path("data/ticks.db")

# Hist-best TFs from prior runs
TF_SPECS = [
    ("2m", "time", 2),
    ("5m", "time", 5),
    ("30m", "time", 30),
    ("50t", "ticks", 50),
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


def filter_day(rows, day: str | None):
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
    return {"total_buy_quantity": row["bp"], "total_sell_quantity": row["sp"]}


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


def models() -> dict[str, AlignS8Config]:
    """Named exit/TP models to compare (break-gate + flip-gate)."""
    base = AlignS8Config(
        min_imb_pct=10.0,
        book_frac_of_net=0.10,
        pullback_points=8.0,
        resume_points=5.0,
        stall_min_profit=20.0,
        stall_bars=3,
        sl_min=20.0,
        tp_min=20.0,
        weaken_pct=20.0,
        cooldown_ticks=1,
        # noisy: no break/flip gates
        break_min_bars=1,
        break_min_adverse=0.0,
        break_skip_if_supported=False,
        break_price_min=0.0,
        break_persist=1,
        break_need_both=False,
        prefer_fat_tp=False,
        flip_min_bars=1,
        flip_min_adverse=0.0,
        flip_block_in_profit=False,
        flip_persist=1,
        flip_need_widen=False,
    )
    break_gate = dict(
        break_min_bars=2,
        break_min_adverse=8.0,
        break_skip_if_supported=True,
        break_price_min=1.5,
    )
    flip_gate = dict(
        flip_min_bars=2,
        flip_min_adverse=8.0,
        flip_block_in_profit=True,
        flip_persist=1,
        flip_need_widen=False,
    )
    flip_strict = dict(
        flip_min_bars=3,
        flip_min_adverse=12.0,
        flip_block_in_profit=True,
        flip_persist=2,
        flip_need_widen=True,
    )
    return {
        "baseline_noisy": base,
        "break_only": replace(base, **break_gate),
        "flip_gate": replace(base, **break_gate, **flip_gate),
        "flip_strict": replace(base, **break_gate, **flip_strict),
        "fat_tp_flip": replace(
            base,
            **break_gate,
            **flip_gate,
            prefer_fat_tp=True,
            fat_tp_min=35.0,
            fat_stall_min=30.0,
            tp_points=45.0,
            tp_min=35.0,
        ),
        "hold_fat_flip": replace(
            base,
            break_min_bars=3,
            break_min_adverse=12.0,
            break_skip_if_supported=True,
            break_price_min=2.0,
            break_persist=2,
            break_need_both=True,
            prefer_fat_tp=True,
            fat_tp_min=40.0,
            fat_stall_min=35.0,
            tp_points=50.0,
            tp_min=40.0,
            stall_bars=2,
            **flip_strict,
        ),
    }


def run_bars(bars: list[dict], lots: float, cfg: AlignS8Config) -> dict:
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
    if not trades:
        return {"n": 0, "dir%": 0.0, "avgG": 0.0, "sumPts": 0.0, "sum₹": 0.0, "reasons": {}}
    gs = [g for g, _ in trades]
    ps = [p for _, p in trades]
    nt = len(trades)
    return {
        "n": nt,
        "dir%": round(sum(1 for g in gs if g > 0) / nt * 100, 1),
        "avgG": round(sum(gs) / nt, 2),
        "sumPts": round(sum(gs), 1),
        "sum₹": round(sum(ps), 1),
        "reasons": dict(reasons),
    }


def run_legacy_30m(rows, lots: float) -> dict:
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
            reasons[_tag(sig.reason or "")] += 1
            trades.append((gross, pnl))
            side = entry = None
    if not trades:
        return {"n": 0, "dir%": 0.0, "avgG": 0.0, "sumPts": 0.0, "sum₹": 0.0, "reasons": {}}
    gs = [g for g, _ in trades]
    ps = [p for _, p in trades]
    nt = len(trades)
    return {
        "n": nt,
        "dir%": round(sum(1 for g in gs if g > 0) / nt * 100, 1),
        "avgG": round(sum(gs) / nt, 2),
        "sumPts": round(sum(gs), 1),
        "sum₹": round(sum(ps), 1),
        "reasons": dict(reasons),
    }


def build_bars(rows, kind: str, n: int, name: str) -> list[dict]:
    if kind == "time":
        return [b.to_row() for b in build_rich_bars(rows, name, n)]
    return [b.to_row() for b in build_rich_bars_by_ticks(rows, name, n)]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--lots", type=float, default=100.0)
    ap.add_argument("--day", default="")
    args = ap.parse_args()
    day = args.day.strip() or None
    db = Path(args.db)
    if not db.exists() or db.stat().st_size == 0:
        raise SystemExit(f"DB missing/empty: {db}")

    rows = filter_day(load_tick_rows(db), day)
    print(f"db={db} day={day or 'ALL'} ticks={len(rows)} lots={args.lots}")
    if len(rows) < 100:
        raise SystemExit("need more ticks")

    mods = models()
    print(
        f"\n{'model':18} {'TF':5} {'n':>4} {'dir%':>6} {'avgG':>7} "
        f"{'sumPts':>8} {'sum₹':>10}  exits"
    )
    print("-" * 100)

    best = None
    for tf_name, kind, n in TF_SPECS:
        bars = build_bars(rows, kind, n, tf_name)
        for mname, cfg in mods.items():
            # slight pullback scale by TF
            scale = float(n if kind == "time" else max(1.0, n / 5.0))
            cfg_tf = replace(
                cfg,
                pullback_points=max(5.0, 4.0 + scale * 0.3),
                resume_points=max(3.0, 3.0 + scale * 0.15),
                stall_bars=2 if scale >= 15 else cfg.stall_bars,
            )
            r = run_bars(bars, args.lots, cfg_tf)
            print(
                f"{mname:18} {tf_name:5} {r['n']:4} {r['dir%']:6.1f} {r['avgG']:7.2f} "
                f"{r['sumPts']:+8.1f} {r['sum₹']:10.1f}  {r['reasons']}"
            )
            key = (r["sum₹"], r["sumPts"], r["n"])
            if best is None or key > (best[0], best[1], best[2]):
                best = (r["sum₹"], r["sumPts"], r["n"], mname, tf_name, r)

    leg = run_legacy_30m(rows, args.lots)
    print(
        f"\n{'LEGACY_30m':18} {'30m':5} {leg['n']:4} {leg['dir%']:6.1f} {leg['avgG']:7.2f} "
        f"{leg['sumPts']:+8.1f} {leg['sum₹']:10.1f}  {leg['reasons']}"
    )
    if best:
        print(
            f"\nBEST ALIGN model={best[3]} TF={best[4]} "
            f"sum₹={best[0]:.1f} sumPts={best[1]:+.1f} n={best[2]} {best[5]['reasons']}"
        )
        if best[0] > leg["sum₹"]:
            print("→ beats LEGACY_30m on this slice (paper only — confirm other days)")
        else:
            print(
                f"→ still behind LEGACY_30m by ₹{leg['sum₹'] - best[0]:.1f} "
                "(keep legacy on paper until ALIGN wins)"
            )


if __name__ == "__main__":
    main()
