#!/usr/bin/env python3
"""Paper sim for S9_STATE30 on built bars (default 30m).

Compares BASE vs VOL_EXP vs HLV vs FLIP_REV gates.
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
    require_vol_expansion: bool = False,
    vol_exp_require_up: bool = True,
    vol_exp_require_h_plus: bool = False,
    enable_flip_reverse: bool = False,
    flip_large_pts: float = 23.0,
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
        require_vol_expansion=require_vol_expansion,
        vol_exp_require_up=vol_exp_require_up,
        vol_exp_require_h_plus=vol_exp_require_h_plus,
        enable_flip_reverse=enable_flip_reverse,
        flip_large_pts=flip_large_pts,
    )
    s = StateS9Strategy(cfg)
    trades: list[tuple[float, float, str, str, str]] = []
    side = None
    entry = None
    reasons: Counter = Counter()

    def close_trade(close: float, reason: str) -> None:
        nonlocal side, entry
        assert side and entry is not None
        gross = (close - entry) if side == "long" else (entry - close)
        pnl = gross * lots - fee_rt(close, lots)
        tag = "other"
        if reason.startswith("tp"):
            tag = "tp"
        elif reason.startswith("sl"):
            tag = "sl"
        elif "net_flip" in reason:
            tag = "net_flip"
        elif "state_break" in reason:
            tag = "state_break"
        elif reason.startswith("flip_reverse"):
            tag = "flip_rev"
        reasons[tag] += 1
        stack = f"{s.last_vol_stack}|{s.last_px_trend}"
        trades.append((gross, pnl, s.last_label, stack, reason[:48]))
        side = None
        entry = None

    for br in bars:
        sig = s.on_bar_row(br)
        if not sig:
            continue
        px = float(br["close"])
        if sig.action == "CLOSE":
            if side and entry is not None:
                close_trade(px, sig.reason or "CLOSE")
        elif sig.action in {"REVERSE_SHORT", "REVERSE_LONG"}:
            if side and entry is not None:
                close_trade(px, sig.reason or sig.action)
            side = "short" if sig.action == "REVERSE_SHORT" else "long"
            entry = px
        elif sig.action in {"BUY", "SHORT"}:
            if side and entry is not None:
                # unexpected re-entry while open — flatten first
                close_trade(px, "reentry_flat")
            side = "long" if sig.action == "BUY" else "short"
            entry = px

    if side and entry is not None and bars:
        close_trade(float(bars[-1]["close"]), "EOD_FLAT")
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
    for g, p, lab, stack, why in trades:
        print(f"  pts={g:+.2f} ₹={p:+.1f} state={lab} stack={stack} why={why}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--lots", type=float, default=100.0)
    ap.add_argument("--tf", type=int, default=30)
    ap.add_argument("--tp", type=float, default=26.0)
    ap.add_argument("--sl", type=float, default=16.0)
    ap.add_argument("--min-imb", type=float, default=5.0)
    ap.add_argument("--allow-short", action="store_true")
    ap.add_argument("--hlv", action="store_true")
    ap.add_argument("--no-hlv", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--hlv-mode", choices=("any", "both"), default="any")
    ap.add_argument("--vol-exp", action="store_true")
    ap.add_argument("--vol-exp-h-plus", action="store_true")
    ap.add_argument(
        "--flip-rev",
        action="store_true",
        help="Enable large-bar flip reverse (long↔short)",
    )
    ap.add_argument("--flip-large", type=float, default=23.0)
    ap.add_argument(
        "--compare",
        action="store_true",
        help="Print BASE vs VOL_EXP vs HLV vs FLIP_REV",
    )
    ap.add_argument("--bars-csv", default="")
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
        flip_large_pts=args.flip_large,
    )

    if args.compare:
        variants = [
            ("BASE", dict(require_hlv=False, require_vol_expansion=False, enable_flip_reverse=False)),
            ("VOL_EXP", dict(require_hlv=False, require_vol_expansion=True, enable_flip_reverse=False)),
            ("HLV_GATE", dict(require_hlv=True, require_vol_expansion=False, enable_flip_reverse=False)),
            ("FLIP_REV", dict(require_hlv=False, require_vol_expansion=False, enable_flip_reverse=True)),
        ]
        for label, kw in variants:
            t, r = run_once(bars, **common, **kw)
            summarize(label, t, r, len(bars), args.tf)
        return

    use_hlv = bool(args.hlv) and not args.no_hlv
    trades, reasons = run_once(
        bars,
        require_hlv=use_hlv,
        require_vol_expansion=bool(args.vol_exp),
        vol_exp_require_h_plus=bool(args.vol_exp_h_plus),
        enable_flip_reverse=bool(args.flip_rev),
        **common,
    )
    bits = [
        "HLV_ON" if use_hlv else "HLV_OFF",
        "VOLX_ON" if args.vol_exp else "VOLX_OFF",
        "FLIP_ON" if args.flip_rev else "FLIP_OFF",
    ]
    summarize("+".join(bits), trades, reasons, len(bars), args.tf)


if __name__ == "__main__":
    main()
