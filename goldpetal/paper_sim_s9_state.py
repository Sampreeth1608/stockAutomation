#!/usr/bin/env python3
"""Paper sim for S9_STATE30 on built bars (default 30m).

Compares baseline (no HLV) vs H/L×Volume confirm+veto when --compare is set.
"""

from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path

from mtf_bars import DB, build_rich_bars, load_tick_rows
from strategy_state_s9 import StateS9Config, StateS9Strategy


def load_bars_csv(path: Path) -> list[dict]:
    """Load offline bar rows (manual_pack / fixture). Numeric fields coerced."""
    rows: list[dict] = []
    with path.open(newline="") as f:
        for raw in csv.DictReader(f):
            br: dict = dict(raw)
            for k, v in list(br.items()):
                if v is None or v == "":
                    continue
                if k in {"time", "tf", "state", "label", "hv", "lv"}:
                    continue
                try:
                    br[k] = float(v) if ("." in v or "e" in v.lower()) else int(v)
                except ValueError:
                    pass
            rows.append(br)
    return enrich_bar_volume(rows)


def fee_rt(ltp: float, lots: float) -> float:
    brokerage = 40.0
    buy = sell = ltp * lots
    txn = 0.000021 * (buy + sell)
    sebi = 0.000001 * (buy + sell)
    stamp = 0.000020 * buy
    ctt = 0.00010 * sell
    gst = 0.18 * (brokerage + txn + sebi)
    return brokerage + txn + sebi + stamp + ctt + gst


def enrich_bar_volume(bars: list[dict]) -> list[dict]:
    """Attach per-bar volume from cumulative volume_close deltas when missing."""
    prev = None
    for br in bars:
        if br.get("bar_volume") is not None:
            try:
                vc = br.get("volume_close")
                if vc is not None:
                    prev = float(vc)
            except (TypeError, ValueError):
                pass
            continue
        vc = br.get("volume_close")
        try:
            vc_f = float(vc) if vc is not None else None
        except (TypeError, ValueError):
            vc_f = None
        if vc_f is not None and prev is not None:
            br["bar_volume"] = max(0.0, vc_f - prev)
        else:
            br["bar_volume"] = 0.0
        if vc_f is not None:
            prev = vc_f
    return bars


def run_once(
    bars: list[dict],
    *,
    bar_minutes: int,
    lots: float,
    tp: float,
    sl: float,
    allow_short: bool,
    require_hlv: bool,
    hlv_mode: str,
    min_imb_pct: float,
) -> tuple[list[tuple[float, float, str, str, str]], Counter]:
    cfg = StateS9Config(
        bar_minutes=bar_minutes,
        tp_points=tp,
        sl_points=sl,
        allow_short=allow_short,
        require_net_sign=True,
        min_imb_pct=min_imb_pct,
        require_hlv_confirm=require_hlv,
        hlv_mode=hlv_mode,
    )
    s = StateS9Strategy(cfg)
    trades: list[tuple[float, float, str, str, str]] = []
    side = None
    entry = None
    reasons: Counter = Counter()

    for br in bars:
        sig = s.on_bar_row(br)
        if not sig:
            continue
        if sig.action in {"BUY", "SHORT"}:
            side = "long" if sig.action == "BUY" else "short"
            entry = float(br["close"])
        elif sig.action == "CLOSE" and side and entry is not None:
            close = float(br["close"])
            gross = (close - entry) if side == "long" else (entry - close)
            pnl = gross * lots - fee_rt(close, lots)
            r = sig.reason or ""
            tag = "other"
            if r.startswith("tp"):
                tag = "tp"
            elif r.startswith("sl"):
                tag = "sl"
            elif "net_flip" in r:
                tag = "net_flip"
            elif "state_break" in r:
                tag = "state_break"
            reasons[tag] += 1
            trades.append((gross, pnl, s.last_label, s.last_hv, s.last_lv))
            side = None
            entry = None
    return trades, reasons


def summarize(label: str, trades: list, reasons: Counter, n_bars: int, tf: int) -> None:
    n = len(trades)
    if n == 0:
        print(f"{label} | bars={n_bars} TF={tf}m | no trades")
        return
    print(
        f"{label} | bars={n_bars} TF={tf}m | n={n} "
        f"dir%={sum(1 for g, *_ in trades if g > 0) / n * 100:.1f} "
        f"avgG={sum(g for g, *_ in trades) / n:.2f} "
        f"sum₹={sum(p for _, p, *_ in trades):.1f} {dict(reasons)}"
    )
    for g, p, lab, hv, lv in trades:
        print(f"  pts={g:+.2f} ₹={p:+.1f} state={lab} hv={hv} lv={lv}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--lots", type=float, default=100.0)
    ap.add_argument("--tf", type=int, default=30)
    ap.add_argument("--tp", type=float, default=26.0)
    ap.add_argument("--sl", type=float, default=16.0)
    ap.add_argument("--min-imb", type=float, default=5.0)
    ap.add_argument("--allow-short", action="store_true")
    ap.add_argument(
        "--hlv",
        action="store_true",
        help="Enable H/L×V confirm+veto (default off; lost edge on VM sample)",
    )
    ap.add_argument("--no-hlv", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--hlv-mode", choices=("any", "both"), default="any")
    ap.add_argument(
        "--compare",
        action="store_true",
        help="Print baseline (no HLV) vs HLV-gated side by side",
    )
    ap.add_argument(
        "--bars-csv",
        default="",
        help="Offline bars CSV (skips ticks.db). Use for fixtures / manual_pack.",
    )
    args = ap.parse_args()

    if args.bars_csv:
        bars = load_bars_csv(Path(args.bars_csv))
        print(
            f"bars_csv={args.bars_csv} bars={len(bars)} "
            f"TF={args.tf}m TP={args.tp} SL={args.sl}"
        )
    else:
        rows = load_tick_rows(Path(args.db))
        bars = enrich_bar_volume(
            [b.to_row() for b in build_rich_bars(rows, f"{args.tf}m", args.tf)]
        )
        print(f"ticks={len(rows)} bars={len(bars)} TF={args.tf}m TP={args.tp} SL={args.sl}")

    common = dict(
        bar_minutes=args.tf,
        lots=args.lots,
        tp=args.tp,
        sl=args.sl,
        allow_short=args.allow_short,
        hlv_mode=args.hlv_mode,
        min_imb_pct=args.min_imb,
    )

    if args.compare:
        base_t, base_r = run_once(bars, require_hlv=False, **common)
        gate_t, gate_r = run_once(bars, require_hlv=True, **common)
        summarize("NO_HLV", base_t, base_r, len(bars), args.tf)
        summarize("HLV_GATE", gate_t, gate_r, len(bars), args.tf)
        return

    use_hlv = bool(args.hlv) and not args.no_hlv
    trades, reasons = run_once(bars, require_hlv=use_hlv, **common)
    label = "HLV_ON" if use_hlv else "HLV_OFF"
    summarize(label, trades, reasons, len(bars), args.tf)


if __name__ == "__main__":
    main()
