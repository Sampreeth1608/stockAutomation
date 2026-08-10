#!/usr/bin/env python3
"""S8 ALIGN learning loop: real trades + ticks → better entry/hold/exit.

================================================================================
HOW THIS WAS BUILT (inputs → outputs)
================================================================================
DATA SOURCES
  1. data/ticks.db          live Angel ticks (ltp, bp/sp=TBQ/TSQ, raw_json)
  2. ticks.db.signals       live paper S8 BUY/SHORT/CLOSE (strategy=S8_NET_ZIGZAG)
  3. data/zigzag_retune.db  optional SNAP path (entry params / mid-trade book)
  4. AlignS8Config          current E/H/X reasoning models (imb_sign_rise, book_rise, …)

ENGINE PIECES (existing Gold Petal code reused)
  5. strategy_s8_align.py   live ALIGN entry/hold/exit formulas
  6. storage.build_trades   pairs real signals into round-trip trades + charges
  7. paper fee model        fee_rt() same as paper_sim_s8_align*.py
  8. mtf / bar TF knobs     2m / 10m / 30m / 50t decision cadence

LEARNING STEPS
  9. diagnose   replay baseline on ticks → MFE/MAE / exit-early / drop stats
 10. train      sweep E×H×X×TF (+ nudges); can run minutes→hours (--budget-min)
 11. report     TODAY real trades ₹  vs  improved counterfactual ₹ (all BUY/SHORT)
 12. apply      write S8_MODEL=learned + preset JSON for paper runner

OUTPUT
 13. data/s8_presets/learned_latest.json   improved formula pack
 14. Console report: real vs improved with every trade listed

================================================================================

Examples (VM):
  python3 learn_s8_align.py report --db data/ticks.db --lots 100 --day 2026-08-10
  python3 learn_s8_align.py train  --db data/ticks.db --lots 100 --day 2026-08-10 --budget-min 30
  python3 learn_s8_align.py apply
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from collections import Counter
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from strategy_s8_align import (
    AlignS8Config,
    AlignS8Strategy,
    _apply_entry_model,
    _apply_exit_model,
    _apply_hold_model,
)

IST = ZoneInfo("Asia/Kolkata")
DB = Path("data/ticks.db")
RETUNE_DB = Path("data/zigzag_retune.db")
PRESET_DIR = Path("data/s8_presets")


def fee_rt(ltp: float, lots: float) -> float:
    brokerage = 40.0
    buy = sell = ltp * lots
    txn = 0.000021 * (buy + sell)
    sebi = 0.000001 * (buy + sell)
    stamp = 0.000020 * buy
    ctt = 0.00010 * sell
    gst = 0.18 * (brokerage + txn + sebi)
    return brokerage + txn + sebi + stamp + ctt + gst


def load_ticks(db: Path, day: str | None = None) -> list[sqlite3.Row]:
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    if day:
        rows = con.execute(
            """
            SELECT id, received_at, ltp, bp, sp, raw_json
            FROM ticks WHERE ltp IS NOT NULL AND received_at LIKE ?
            ORDER BY received_at ASC, id ASC
            """,
            (f"{day}%",),
        ).fetchall()
    else:
        rows = con.execute(
            """
            SELECT id, received_at, ltp, bp, sp, raw_json
            FROM ticks WHERE ltp IS NOT NULL
            ORDER BY received_at ASC, id ASC
            """
        ).fetchall()
    con.close()
    return list(rows)


def msg_from_row(row: sqlite3.Row) -> dict[str, Any]:
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


def exit_tag(reason: str) -> str:
    r = reason or ""
    if "tbq_drop" in r or "tsq_drop" in r:
        return "drop"
    if "stall" in r or "tp +" in r:
        return "tp"
    if "book_break" in r:
        return "break"
    if "flip" in r:
        return "flip"
    if "weaken" in r:
        return "weaken"
    if "<=-" in r or " sl " in f" {r}" or r.startswith("sl"):
        return "sl"
    if "EOD" in r:
        return "eod"
    return "other"


def baseline_cfg(
    *,
    bar_minutes: int = 30,
    bar_ticks: int = 0,
    entry: str = "imb_sign_rise",
    hold: str = "book_rise",
    exit_m: str = "fat_tp_flip",
) -> AlignS8Config:
    cfg = AlignS8Config(min_imb_pct=3.0, cooldown_ticks=1)
    cfg = _apply_entry_model(entry, cfg)
    cfg = _apply_hold_model(hold, cfg)
    cfg = _apply_exit_model(exit_m, cfg)
    cfg.bar_minutes = int(bar_minutes)
    cfg.bar_ticks = int(bar_ticks)
    if cfg.bar_minutes > 0:
        cfg.bar_ticks = 0
    if cfg.bar_ticks > 0:
        cfg.bar_minutes = 0
    cfg.model_name = "baseline"
    cfg.close_on_book_drop = True
    cfg.book_drop_min_pct = 0.25
    return cfg


def run_align_labeled(
    rows: list[sqlite3.Row], cfg: AlignS8Config, lots: float
) -> dict[str, Any]:
    """Replay ALIGN; track MFE/MAE path and entry-time book features."""
    s = AlignS8Strategy(cfg)
    trades: list[dict[str, Any]] = []
    reasons: Counter = Counter()

    side: str | None = None
    entry_px: float | None = None
    entry_ts: str | None = None
    entry_reason = ""
    entry_imb = 0.0
    entry_net = 0.0
    entry_tbq = 0.0
    entry_tsq = 0.0
    mfe = 0.0
    mae = 0.0

    for row in rows:
        ltp = float(row["ltp"])
        now = parse_ts(row["received_at"])
        # path extremes while in trade (every tick)
        if side and entry_px is not None:
            move = (ltp - entry_px) if side == "long" else (entry_px - ltp)
            mfe = max(mfe, move)
            mae = min(mae, move)

        sig = s.on_tick(now, ltp, msg_from_row(row))
        if not sig:
            continue

        if sig.action in {"BUY", "SHORT"}:
            side = "long" if sig.action == "BUY" else "short"
            entry_px = ltp
            entry_ts = str(row["received_at"])
            entry_reason = sig.reason or ""
            entry_imb = float(s.last_imb)
            entry_net = float(s.last_net)
            entry_tbq = float(s.last_tbq)
            entry_tsq = float(s.last_tsq)
            mfe = 0.0
            mae = 0.0
        elif sig.action == "CLOSE" and side and entry_px is not None:
            move = (ltp - entry_px) if side == "long" else (entry_px - ltp)
            mfe = max(mfe, move)
            mae = min(mae, move)
            pnl = move * lots - fee_rt(ltp, lots)
            r = sig.reason or ""
            tag = exit_tag(r)
            reasons[tag] += 1
            left = mfe - move  # pts left on table
            giveback = mfe - move if mfe > move else 0.0
            trades.append(
                {
                    "side": side,
                    "entry_ts": entry_ts,
                    "exit_ts": str(row["received_at"]),
                    "entry_px": entry_px,
                    "exit_px": ltp,
                    "gross_pts": round(move, 2),
                    "pnl_inr": round(pnl, 2),
                    "mfe": round(mfe, 2),
                    "mae": round(mae, 2),
                    "left_on_table": round(left, 2),
                    "giveback": round(giveback, 2),
                    "exit_tag": tag,
                    "exit_reason": r[:120],
                    "entry_reason": entry_reason[:120],
                    "entry_imb": round(entry_imb, 2),
                    "entry_net": round(entry_net, 1),
                    "entry_tbq": round(entry_tbq, 1),
                    "entry_tsq": round(entry_tsq, 1),
                    "entry_good": bool(mfe >= 20 and mae > -35),
                    "exit_early": bool(left >= 15 and move > 0),
                    "exit_late": bool(giveback >= 12 and mfe >= 15),
                    "win": bool(move > 0),
                }
            )
            side = None
            entry_px = None

    if side and entry_px is not None and rows:
        ltp = float(rows[-1]["ltp"])
        move = (ltp - entry_px) if side == "long" else (entry_px - ltp)
        mfe = max(mfe, move)
        mae = min(mae, move)
        pnl = move * lots - fee_rt(ltp, lots)
        reasons["eod"] += 1
        trades.append(
            {
                "side": side,
                "entry_ts": entry_ts,
                "exit_ts": str(rows[-1]["received_at"]),
                "entry_px": entry_px,
                "exit_px": ltp,
                "gross_pts": round(move, 2),
                "pnl_inr": round(pnl, 2),
                "mfe": round(mfe, 2),
                "mae": round(mae, 2),
                "left_on_table": round(mfe - move, 2),
                "giveback": round(max(0.0, mfe - move), 2),
                "exit_tag": "eod",
                "exit_reason": "EOD_FLAT",
                "entry_reason": entry_reason[:120],
                "entry_imb": round(entry_imb, 2),
                "entry_net": round(entry_net, 1),
                "entry_tbq": round(entry_tbq, 1),
                "entry_tsq": round(entry_tsq, 1),
                "entry_good": bool(mfe >= 20 and mae > -35),
                "exit_early": False,
                "exit_late": False,
                "win": bool(move > 0),
            }
        )

    return {
        "trades": trades,
        "reasons": dict(reasons),
        "n_ticks": len(rows),
        "nn_skipped": int(getattr(s, "nn_skip_count", 0) or 0),
    }


def summarize(label: str, result: dict[str, Any]) -> dict[str, Any]:
    trades = result["trades"]
    n = len(trades)
    if n == 0:
        out = {
            "label": label,
            "n": 0,
            "dir_pct": 0.0,
            "sum_pts": 0.0,
            "sum_inr": 0.0,
            "avg_mfe": 0.0,
            "avg_left": 0.0,
            "reasons": result.get("reasons", {}),
        }
        print(f"{label} | ticks={result['n_ticks']} | n=0")
        return out
    wins = sum(1 for t in trades if t["win"])
    out = {
        "label": label,
        "n": n,
        "dir_pct": round(wins / n * 100.0, 1),
        "sum_pts": round(sum(t["gross_pts"] for t in trades), 1),
        "sum_inr": round(sum(t["pnl_inr"] for t in trades), 1),
        "avg_mfe": round(sum(t["mfe"] for t in trades) / n, 2),
        "avg_left": round(sum(t["left_on_table"] for t in trades) / n, 2),
        "exit_early_n": sum(1 for t in trades if t["exit_early"]),
        "exit_late_n": sum(1 for t in trades if t["exit_late"]),
        "entry_good_n": sum(1 for t in trades if t["entry_good"]),
        "reasons": result.get("reasons", {}),
    }
    print(
        f"{label} | ticks={result['n_ticks']} | n={n} "
        f"dir%={out['dir_pct']} sumPts={out['sum_pts']:+.1f} "
        f"sum₹={out['sum_inr']:+.1f} avgMFE={out['avg_mfe']} "
        f"left={out['avg_left']} early={out['exit_early_n']} "
        f"late={out['exit_late_n']} {out['reasons']}"
    )
    return out


def diagnose_rules(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """Turn labeled trades into concrete formula nudges."""
    n = len(trades) or 1
    drop_n = sum(1 for t in trades if t["exit_tag"] == "drop")
    early_n = sum(1 for t in trades if t["exit_early"])
    late_n = sum(1 for t in trades if t["exit_late"])
    lose = [t for t in trades if not t["win"]]
    win = [t for t in trades if t["win"]]
    lose_imb = sum(t["entry_imb"] for t in lose) / max(len(lose), 1)
    win_imb = sum(t["entry_imb"] for t in win) / max(len(win), 1)

    tips: list[str] = []
    nudges: dict[str, Any] = {}

    if drop_n / n >= 0.45:
        tips.append(
            "Many book-drop exits — raise S8_BOOK_DROP_MIN_PCT and/or use longer TF"
        )
        nudges["book_drop_min_pct"] = 0.50
        nudges["book_drop_persist"] = 2
    if early_n / n >= 0.35:
        tips.append("Exiting early vs MFE — prefer fatter TP / stronger hold")
        nudges["prefer_fat_tp"] = True
        nudges["fat_tp_min"] = 40.0
        nudges["tp_points"] = 50.0
        nudges["hold_model"] = "book_or_support"
    if late_n / n >= 0.30:
        tips.append("Giving back from peak — tighten flip/break adverse gates")
        nudges["flip_min_adverse"] = 6.0
        nudges["break_min_adverse"] = 6.0
    if lose and win and lose_imb + 2.0 < win_imb:
        tips.append(
            f"Losers enter at softer IMB ({lose_imb:.1f}% vs winners {win_imb:.1f}%) "
            "— raise min_imb"
        )
        nudges["min_imb_pct"] = max(5.0, round(win_imb * 0.6, 1))
    if not tips:
        tips.append("No strong pathology — sweep E/H/X × TF for best paper ₹")

    return {
        "tips": tips,
        "nudges": nudges,
        "stats": {
            "drop_frac": round(drop_n / n, 3),
            "early_frac": round(early_n / n, 3),
            "late_frac": round(late_n / n, 3),
            "lose_imb": round(lose_imb, 2),
            "win_imb": round(win_imb, 2),
        },
    }


def load_live_s8_signals(db: Path, day: str | None = None) -> list[dict[str, Any]]:
    if not db.exists():
        return []
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    q = """
        SELECT time_label, action, position_after, reason, cmp, dry_run
        FROM signals
        WHERE strategy = 'S8_NET_ZIGZAG'
    """
    args: tuple = ()
    if day:
        q += " AND time_label LIKE ?"
        args = (f"{day}%",)
    q += " ORDER BY time_label ASC, id ASC"
    try:
        rows = con.execute(q, args).fetchall()
    except sqlite3.Error:
        rows = []
    con.close()
    return [dict(r) for r in rows]


def search_space(diag: dict[str, Any]) -> list[tuple[str, AlignS8Config]]:
    """Candidate reasoning models to paper-sim."""
    nudges = diag.get("nudges", {})
    entries = ["imb_sign_rise", "imb_sign", "align_widen"]
    holds = ["book_rise", "book_or_support", "off"]
    exits = ["fat_tp_flip", "break_flip", "hold_fat_flip", "book_drop"]
    tfs = [
        ("10m", 10, 0),
        ("30m", 30, 0),
        ("50t", 0, 50),
        ("2m", 2, 0),
    ]
    cands: list[tuple[str, AlignS8Config]] = []
    for e in entries:
        for h in holds:
            for x in exits:
                for tf_name, mins, ticks in tfs:
                    cfg = baseline_cfg(
                        bar_minutes=mins, bar_ticks=ticks, entry=e, hold=h, exit_m=x
                    )
                    # apply diagnosis nudges on a parallel "nudged" variant
                    name = f"{e}|{h}|{x}|{tf_name}"
                    cands.append((name, cfg))
                    if nudges:
                        c2 = replace(cfg)
                        for k, v in nudges.items():
                            if k == "hold_model":
                                c2 = _apply_hold_model(str(v), c2)
                            elif hasattr(c2, k):
                                setattr(c2, k, v)
                        c2.bar_minutes = mins
                        c2.bar_ticks = ticks
                        if mins > 0:
                            c2.bar_ticks = 0
                        if ticks > 0:
                            c2.bar_minutes = 0
                        cands.append((name + "+nudge", c2))
    # de-dupe by (entry,hold,exit,tf,min_imb,drop)
    seen: set[tuple] = set()
    uniq: list[tuple[str, AlignS8Config]] = []
    for name, cfg in cands:
        key = (
            cfg.entry_model,
            cfg.hold_model,
            cfg.exit_model,
            cfg.bar_minutes,
            cfg.bar_ticks,
            round(cfg.min_imb_pct, 2),
            round(cfg.book_drop_min_pct, 3),
            cfg.prefer_fat_tp,
            round(cfg.tp_points, 1),
        )
        if key in seen:
            continue
        seen.add(key)
        uniq.append((name, cfg))
    return uniq


def score_result(summary: dict[str, Any], *, min_trades: int = 2) -> float:
    """Primary objective: paper ₹ with light regularity penalties."""
    if summary["n"] < min_trades:
        return -1e12
    score = float(summary["sum_inr"])
    if summary["dir_pct"] < 40:
        score -= 2000.0
    # mild penalty for overtrading
    if summary["n"] > 80:
        score -= (summary["n"] - 80) * 20.0
    return score


def cfg_to_preset(name: str, cfg: AlignS8Config, meta: dict[str, Any]) -> dict[str, Any]:
    d = asdict(cfg)
    d["preset_name"] = name
    d["meta"] = meta
    d["created_at"] = datetime.now(timezone.utc).isoformat()
    return d


def apply_preset_dict(d: dict[str, Any]) -> AlignS8Config:
    """Rebuild AlignS8Config from learned JSON (full field dump)."""
    cfg = AlignS8Config()
    skip = {"preset_name", "meta", "created_at"}
    for k, v in d.items():
        if k in skip:
            continue
        if hasattr(cfg, k):
            setattr(cfg, k, v)
    cfg.model_name = str(d.get("preset_name") or d.get("model_name") or "learned")
    if int(cfg.bar_minutes or 0) > 0:
        cfg.bar_ticks = 0
    if int(cfg.bar_ticks or 0) > 0:
        cfg.bar_minutes = 0
    return cfg


def cmd_diagnose(args: argparse.Namespace) -> int:
    rows = load_ticks(Path(args.db), day=args.day or None)
    print(f"db={args.db} day={args.day or 'ALL'} ticks={len(rows)} lots={args.lots}")
    if not rows:
        print("no ticks — collect via run_strategy / collect_ticks first")
        return 1
    cfg = baseline_cfg(
        bar_minutes=args.bar_minutes,
        bar_ticks=args.bar_ticks,
        entry=args.entry,
        hold=args.hold,
        exit_m=args.exit,
    )
    result = run_align_labeled(rows, cfg, args.lots)
    summary = summarize("BASELINE", result)
    diag = diagnose_rules(result["trades"])
    print("--- diagnosis ---")
    for tip in diag["tips"]:
        print(f"  • {tip}")
    print(f"  nudges={diag['nudges']}")
    print(f"  stats={diag['stats']}")

    live = load_live_s8_signals(Path(args.db), day=args.day or None)
    if live:
        print(f"--- live signals in ticks.db: {len(live)} ---")
        acts = Counter(x["action"] for x in live)
        print(f"  actions={dict(acts)}")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "summary": summary,
        "diagnosis": diag,
        "trades": result["trades"][:200],
        "cfg": asdict(cfg),
    }
    path = out_dir / "diagnose_latest.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"wrote {path}")
    return 0


def cmd_train(args: argparse.Namespace) -> int:
    rows = load_ticks(Path(args.db), day=args.day or None)
    print(f"db={args.db} day={args.day or 'ALL'} ticks={len(rows)} lots={args.lots}")
    if len(rows) < 50:
        print("need more ticks to train")
        return 1

    base = baseline_cfg(
        bar_minutes=args.bar_minutes,
        bar_ticks=args.bar_ticks,
        entry=args.entry,
        hold=args.hold,
        exit_m=args.exit,
    )
    base_res = run_align_labeled(rows, base, args.lots)
    base_sum = summarize("BASELINE", base_res)
    diag = diagnose_rules(base_res["trades"])
    print("--- diagnosis ---")
    for tip in diag["tips"]:
        print(f"  • {tip}")

    cands = search_space(diag)
    budget_s = max(0.0, float(getattr(args, "budget_min", 0) or 0)) * 60.0
    if args.quick and budget_s <= 0:
        cands = cands[:24]
    print(
        f"sweeping up to {len(cands)} candidates "
        f"(budget={getattr(args, 'budget_min', 0)} min)…"
    )

    best_name = "baseline"
    best_cfg = base
    best_sum = base_sum
    best_score = score_result(base_sum)
    ranked: list[tuple[float, str, dict]] = []
    t0 = time.time()

    for i, (name, cfg) in enumerate(cands, 1):
        if budget_s > 0 and (time.time() - t0) >= budget_s:
            print(f"budget reached after {i - 1} candidates")
            break
        res = run_align_labeled(rows, cfg, args.lots)
        sm = summarize(f"[{i}/{len(cands)}] {name}", res)
        sc = score_result(sm)
        ranked.append((sc, name, sm))
        if sc > best_score:
            best_score = sc
            best_name = name
            best_cfg = cfg
            best_sum = sm

    ranked.sort(key=lambda x: x[0], reverse=True)
    print("--- top 5 ---")
    for sc, name, sm in ranked[:5]:
        print(
            f"  score={sc:.1f} {name} n={sm['n']} dir%={sm['dir_pct']} "
            f"sum₹={sm['sum_inr']:+.1f}"
        )

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "baseline": base_sum,
        "learned": best_sum,
        "delta_inr": round(best_sum["sum_inr"] - base_sum["sum_inr"], 1),
        "diagnosis": diag,
        "search_n": len(ranked),
        "day": args.day or "ALL",
        "lots": args.lots,
        "elapsed_sec": round(time.time() - t0, 1),
    }
    preset = cfg_to_preset(best_name, best_cfg, meta)
    latest = out_dir / "learned_latest.json"
    stamped = out_dir / f"learned_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    text = json.dumps(preset, indent=2)
    latest.write_text(text, encoding="utf-8")
    stamped.write_text(text, encoding="utf-8")
    print(
        f"BEST {best_name} sum₹={best_sum['sum_inr']:+.1f} "
        f"(baseline {base_sum['sum_inr']:+.1f}, "
        f"Δ={meta['delta_inr']:+.1f}) elapsed={meta['elapsed_sec']}s"
    )
    print(f"wrote {latest}")
    print(f"wrote {stamped}")
    _print_env(preset)
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    rows = load_ticks(Path(args.db), day=args.day or None)
    if not rows:
        print("no ticks")
        return 1
    preset_path = Path(args.preset)
    if not preset_path.exists():
        print(f"missing preset {preset_path} — run train first")
        return 1
    d = json.loads(preset_path.read_text(encoding="utf-8"))
    learned = apply_preset_dict(d)
    base = baseline_cfg(
        bar_minutes=args.bar_minutes,
        bar_ticks=args.bar_ticks,
        entry=args.entry,
        hold=args.hold,
        exit_m=args.exit,
    )
    b = summarize("BASELINE", run_align_labeled(rows, base, args.lots))
    l = summarize("LEARNED", run_align_labeled(rows, learned, args.lots))
    print(
        f"Δ sum₹={l['sum_inr'] - b['sum_inr']:+.1f} "
        f"Δn={l['n'] - b['n']:+d} Δdir%={l['dir_pct'] - b['dir_pct']:+.1f}"
    )
    return 0


def _print_env(preset: dict[str, Any]) -> None:
    print("--- paper .env (paste) ---")
    print("DRY_RUN=true")
    print("ENABLE_S8=true")
    print("S8_LOGIC=align")
    print("S8_MODEL=learned")
    print(f"S8_ENTRY_MODEL={preset.get('entry_model', 'imb_sign_rise')}")
    print(f"S8_HOLD_MODEL={preset.get('hold_model', 'book_rise')}")
    print(f"S8_EXIT_MODEL={preset.get('exit_model', 'fat_tp_flip')}")
    print(f"S8_BAR_MINUTES={int(preset.get('bar_minutes') or 0)}")
    print(f"S8_BAR_TICKS={int(preset.get('bar_ticks') or 0)}")
    print(f"S8_MIN_IMB_PCT={preset.get('min_imb_pct', 3)}")
    print(f"S8_BOOK_DROP_MIN_PCT={preset.get('book_drop_min_pct', 0.25)}")
    print(f"S8_BOOK_DROP_PERSIST={int(preset.get('book_drop_persist') or 1)}")
    print("S8_LEARNED_PRESET=data/s8_presets/learned_latest.json")


def _print_trades_table(title: str, trades: list[dict[str, Any]], *, sim: bool) -> float:
    print(f"\n=== {title} ===")
    if not trades:
        print("(no trades)")
        return 0.0
    total = 0.0
    print(
        f"{'#':>3} {'side':5} {'entry_ts':22} {'exit_ts':22} "
        f"{'entry':>8} {'exit':>8} {'pts':>7} {'₹':>10} reason"
    )
    print("-" * 120)
    for i, t in enumerate(trades, 1):
        if sim:
            side = t.get("side", "")
            pts = float(t.get("gross_pts") or 0)
            pnl = float(t.get("pnl_inr") or 0)
            et = str(t.get("entry_ts") or "")[:22]
            xt = str(t.get("exit_ts") or "")[:22]
            ep = t.get("entry_px")
            xp = t.get("exit_px")
            reason = (t.get("exit_reason") or "")[:40]
        else:
            side = "long" if t.get("side") == "BUY" else "short"
            pts = ""
            pnl_raw = t.get("pnl_after_tax", t.get("net_pnl", t.get("gross_pnl", 0)))
            try:
                pnl = float(pnl_raw) if pnl_raw not in ("", None) else 0.0
            except (TypeError, ValueError):
                pnl = 0.0
            try:
                ep_f = float(t.get("entry_price") or 0)
                xp_f = float(t.get("exit_price") or 0) if t.get("exit_price") not in ("", None) else 0.0
                if ep_f and xp_f:
                    pts = (xp_f - ep_f) if side == "long" else (ep_f - xp_f)
            except (TypeError, ValueError):
                pts = 0.0
            et = str(t.get("entry_ts") or "")[:22]
            xt = str(t.get("exit_ts") or "")[:22]
            ep = t.get("entry_price")
            xp = t.get("exit_price") or "-"
            reason = (str(t.get("exit_reason") or t.get("status") or ""))[:40]
        total += float(pnl)
        pts_s = f"{float(pts):+.1f}" if pts != "" else "-"
        print(
            f"{i:3d} {side:5} {et:22} {xt:22} "
            f"{ep!s:>8} {xp!s:>8} {pts_s:>7} {float(pnl):>+10.1f} {reason}"
        )
    print(f"TOTAL ₹ = {total:+.1f}  (n={len(trades)})")
    return total


def load_real_s8_trades(db: Path, day: str | None) -> list[dict[str, Any]]:
    """Load closed S8 paper trades. Prefer storage.build_trades; fallback SQL."""
    try:
        from storage import build_trades

        trades = build_trades(strategy="S8_NET_ZIGZAG", db_path=db)
        if day:
            trades = [t for t in trades if str(t.get("entry_ts") or "").startswith(day)]
        return trades
    except Exception:
        pass

    # Fallback: pair BUY/SHORT ↔ CLOSE from signals without init_db
    if not db.exists():
        return []
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        q = """
            SELECT time_label, action, position_after, reason, cmp, dry_run
            FROM signals WHERE strategy='S8_NET_ZIGZAG'
        """
        args: tuple = ()
        if day:
            q += " AND time_label LIKE ?"
            args = (f"{day}%",)
        q += " ORDER BY time_label ASC, id ASC"
        rows = con.execute(q, args).fetchall()
    except sqlite3.Error:
        con.close()
        return []
    con.close()

    trades: list[dict[str, Any]] = []
    open_t: dict[str, Any] | None = None
    for r in rows:
        act = r["action"]
        if act in {"BUY", "SHORT"}:
            open_t = {
                "side": act,
                "entry_ts": r["time_label"],
                "entry_price": r["cmp"],
                "status": "OPEN",
            }
        elif act == "CLOSE" and open_t is not None:
            ep = float(open_t["entry_price"] or 0)
            xp = float(r["cmp"] or 0)
            side = "long" if open_t["side"] == "BUY" else "short"
            pts = (xp - ep) if side == "long" else (ep - xp)
            # rough paper ₹ using fee_rt
            pnl = pts * 100.0 - fee_rt(xp or ep, 100.0)
            open_t.update(
                {
                    "exit_ts": r["time_label"],
                    "exit_price": xp,
                    "exit_reason": r["reason"],
                    "status": "CLOSED",
                    "gross_pnl": pts * 100.0,
                    "net_pnl": pnl,
                    "pnl_after_tax": pnl,
                }
            )
            trades.append(open_t)
            open_t = None
    return trades


def cmd_report(args: argparse.Namespace) -> int:
    """Today's REAL S8 trades vs IMPROVED counterfactual on the same ticks."""
    day = args.day.strip() or datetime.now(IST).strftime("%Y-%m-%d")
    db = Path(args.db)
    rows = load_ticks(db, day=day)
    print("=" * 72)
    print(f"S8 LEARNING REPORT  day={day}  ticks={len(rows)}  lots={args.lots}")
    print("=" * 72)
    if not rows:
        print("No ticks for this day — cannot compare.")
        return 1

    # --- REAL trades from live paper signals ---
    real = load_real_s8_trades(db, day)
    real_inr = _print_trades_table(
        f"REAL paper trades (signals → S8_NET_ZIGZAG) day={day}",
        real,
        sim=False,
    )

    # --- Baseline replay (current E/H/X) on same ticks ---
    base_cfg = baseline_cfg(
        bar_minutes=args.bar_minutes,
        bar_ticks=args.bar_ticks,
        entry=args.entry,
        hold=args.hold,
        exit_m=args.exit,
    )
    base_res = run_align_labeled(rows, base_cfg, args.lots)
    base_inr = _print_trades_table(
        f"BASELINE replay on ticks  E={base_cfg.entry_model} "
        f"H={base_cfg.hold_model} X={base_cfg.exit_model} "
        f"TF={base_cfg.bar_minutes}m/{base_cfg.bar_ticks}t",
        base_res["trades"],
        sim=True,
    )
    diag = diagnose_rules(base_res["trades"])
    print("\n--- diagnosis from tick-path trades ---")
    for tip in diag["tips"]:
        print(f"  • {tip}")

    # --- Train / load improved ---
    preset_path = Path(args.preset)
    if args.retrain or not preset_path.exists():
        print(
            f"\nTraining improved model (budget={args.budget_min} min, "
            f"quick={args.quick})…"
        )
        # reuse train with same day
        ns = argparse.Namespace(
            db=str(db),
            lots=args.lots,
            day=day,
            bar_minutes=args.bar_minutes,
            bar_ticks=args.bar_ticks,
            entry=args.entry,
            hold=args.hold,
            exit=args.exit,
            out=str(Path(args.out)),
            quick=args.quick,
            budget_min=args.budget_min,
        )
        rc = cmd_train(ns)
        if rc != 0:
            return rc
        preset_path = Path(args.out) / "learned_latest.json"

    if not preset_path.exists():
        print(f"No learned preset at {preset_path}")
        return 1
    d = json.loads(preset_path.read_text(encoding="utf-8"))
    learned = apply_preset_dict(d)
    imp_res = run_align_labeled(rows, learned, args.lots)
    imp_inr = _print_trades_table(
        f"IMPROVED counterfactual  E={learned.entry_model} "
        f"H={learned.hold_model} X={learned.exit_model} "
        f"TF={learned.bar_minutes}m/{learned.bar_ticks}t "
        f"preset={d.get('preset_name')}",
        imp_res["trades"],
        sim=True,
    )

    print("\n" + "=" * 72)
    print("SUMMARY")
    print(f"  REAL paper S8 day ₹     : {real_inr:+.1f}   (n={len(real)})")
    print(f"  BASELINE replay ₹       : {base_inr:+.1f}   (n={len(base_res['trades'])})")
    print(f"  IMPROVED counterfactual : {imp_inr:+.1f}   (n={len(imp_res['trades'])})")
    print(f"  Δ improved − real       : {imp_inr - real_inr:+.1f}")
    print(f"  Δ improved − baseline   : {imp_inr - base_inr:+.1f}")
    print("=" * 72)
    print("Note: IMPROVED is a what-if on the same ticks (paper), not filled fills.")
    print("Enable with: python3 learn_s8_align.py apply   then S8_MODEL=learned")
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    path = Path(args.preset)
    if not path.exists():
        print(f"missing {path}")
        return 1
    d = json.loads(path.read_text(encoding="utf-8"))
    meta = d.get("meta") or {}
    print(
        f"preset={d.get('preset_name')} "
        f"E={d.get('entry_model')} H={d.get('hold_model')} X={d.get('exit_model')} "
        f"TF={d.get('bar_minutes')}m/{d.get('bar_ticks')}t"
    )
    if meta.get("delta_inr") is not None:
        print(
            f"hist Δ₹={meta['delta_inr']:+} "
            f"(learned {meta.get('learned', {}).get('sum_inr')} vs "
            f"base {meta.get('baseline', {}).get('sum_inr')})"
        )
    _print_env(d)
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--db", default=str(DB))
        p.add_argument("--lots", type=float, default=100.0)
        p.add_argument("--day", default="", help="YYYY-MM-DD filter")
        p.add_argument("--bar-minutes", type=int, default=30)
        p.add_argument("--bar-ticks", type=int, default=0)
        p.add_argument("--entry", default="imb_sign_rise")
        p.add_argument("--hold", default="book_rise")
        p.add_argument("--exit", default="fat_tp_flip")
        p.add_argument("--out", default=str(PRESET_DIR))

    p1 = sub.add_parser("diagnose", help="Label baseline trades + tips")
    add_common(p1)
    p1.set_defaults(func=cmd_diagnose)

    p2 = sub.add_parser("train", help="Sweep + write learned preset")
    add_common(p2)
    p2.add_argument(
        "--quick",
        action="store_true",
        help="Fewer candidates (faster on VM)",
    )
    p2.add_argument(
        "--budget-min",
        type=float,
        default=0.0,
        help="Stop sweep after N minutes (0 = no limit)",
    )
    p2.set_defaults(func=cmd_train)

    p3 = sub.add_parser("compare", help="Baseline vs learned preset")
    add_common(p3)
    p3.add_argument(
        "--preset",
        default=str(PRESET_DIR / "learned_latest.json"),
    )
    p3.set_defaults(func=cmd_compare)

    p4 = sub.add_parser("apply", help="Print .env for paper trading learned")
    p4.add_argument(
        "--preset",
        default=str(PRESET_DIR / "learned_latest.json"),
    )
    p4.set_defaults(func=cmd_apply)

    p5 = sub.add_parser(
        "report",
        help="REAL today trades vs IMPROVED counterfactual (full BUY/SHORT list)",
    )
    add_common(p5)
    p5.add_argument(
        "--preset",
        default=str(PRESET_DIR / "learned_latest.json"),
    )
    p5.add_argument(
        "--retrain",
        action="store_true",
        help="Train before reporting (uses --budget-min / --quick)",
    )
    p5.add_argument(
        "--quick",
        action="store_true",
        help="With --retrain: smaller candidate set",
    )
    p5.add_argument(
        "--budget-min",
        type=float,
        default=5.0,
        help="With --retrain: minutes to search (default 5; use 30–120 for deeper)",
    )
    p5.set_defaults(func=cmd_report)
    return ap


def main() -> None:
    ap = build_parser()
    args = ap.parse_args()
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
