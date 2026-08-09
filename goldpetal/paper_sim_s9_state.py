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
from s9_bar_ml import make_entry_filter, walk_forward_p_up
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
    require_bias_align: bool = False,
    bias_mode: str = "net",
    exit_on_bias_flip: bool = True,
    require_net_sign: bool = True,
    use_range_stops: bool = False,
    range_window: int = 20,
    tp_range_mult: float = 0.85,
    sl_range_mult: float = 0.55,
    range_fee_be: float = 0.0,
    ml_p_by_time: dict | None = None,
    ml_min_proba: float = 0.55,
    ml_allow_if_missing: bool = False,
    ml_filter_stats: dict | None = None,
) -> tuple[list[tuple[float, float, str, str, str]], Counter]:
    cfg = StateS9Config(
        bar_minutes=bar_minutes,
        tp_points=tp,
        sl_points=sl,
        allow_short=allow_short,
        require_net_sign=require_net_sign,
        min_imb_pct=min_imb_pct,
        require_hlv_confirm=require_hlv,
        hlv_mode=hlv_mode,
        require_vol_expansion=require_vol_expansion,
        vol_exp_require_up=vol_exp_require_up,
        vol_exp_require_h_plus=vol_exp_require_h_plus,
        enable_flip_reverse=enable_flip_reverse,
        flip_large_pts=flip_large_pts,
        require_bias_align=require_bias_align,
        bias_mode=bias_mode,
        exit_on_bias_flip=exit_on_bias_flip,
        use_range_stops=use_range_stops,
        range_window=range_window,
        tp_range_mult=tp_range_mult,
        sl_range_mult=sl_range_mult,
        range_fee_be=range_fee_be,
    )
    s = StateS9Strategy(cfg)
    if ml_p_by_time is not None:
        stats = ml_filter_stats if ml_filter_stats is not None else {}
        s.extra_entry_filters.append(
            make_entry_filter(
                ml_p_by_time,
                min_proba=ml_min_proba,
                allow_if_missing=ml_allow_if_missing,
                stats=stats,
            )
        )
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
        elif "bias_flip" in reason:
            tag = "bias_flip"
        reasons[tag] += 1
        stack = f"{s.bias}/{s.last_px_trend}|{s.last_vol_stack}"
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
        "--bias-align",
        action="store_true",
        help="S8-style: BULL→long only, BEAR→short only",
    )
    ap.add_argument("--bias-mode", choices=("net", "dnet", "px"), default="net")
    ap.add_argument(
        "--compare",
        action="store_true",
        help="Print BASE vs BIAS_* vs BIAS_NET_RANGE (expected-range TP/SL)",
    )
    ap.add_argument(
        "--range-stops",
        action="store_true",
        help="Map rolling median bar-range → TP/SL (vs fixed --tp/--sl)",
    )
    ap.add_argument(
        "--ml-min-proba",
        type=float,
        default=0.55,
        help="Walk-forward ML filter threshold for BIAS_NET_ML*",
    )
    ap.add_argument(
        "--ml-model",
        choices=("logreg", "random_forest", "grad_boost", "lightgbm"),
        default="logreg",
    )
    ap.add_argument("--ml-min-train", type=int, default=40)
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
        off = dict(
            require_hlv=False,
            require_vol_expansion=False,
            enable_flip_reverse=False,
            require_bias_align=False,
        )
        variants = [
            ("BASE", {**off, "allow_short": False}),
            # S8 mirror: absolute NET (TBQ-TSQ) sign + IMB
            (
                "BIAS_NET",
                {
                    **off,
                    "allow_short": True,
                    "require_bias_align": True,
                    "bias_mode": "net",
                },
            ),
            # Current NET vs previous NET (netΔ) — what you asked next
            (
                "BIAS_DNET",
                {
                    **off,
                    "allow_short": True,
                    "require_bias_align": True,
                    "bias_mode": "dnet",
                    "require_net_sign": True,
                },
            ),
            (
                "BIAS_DNET_L",
                {
                    **off,
                    "allow_short": False,
                    "require_bias_align": True,
                    "bias_mode": "dnet",
                    "require_net_sign": True,
                },
            ),
            # Price-trend align (UP→long, DOWN→short)
            (
                "BIAS_PX",
                {
                    **off,
                    "allow_short": True,
                    "require_bias_align": True,
                    "bias_mode": "px",
                },
            ),
            # Expected fluctuation → adaptive TP/SL (default OFF until it beats FIXED)
            (
                "BIAS_NET_RANGE",
                {
                    **off,
                    "allow_short": True,
                    "require_bias_align": True,
                    "bias_mode": "net",
                    "use_range_stops": True,
                },
            ),
            # Walk-forward bar ML filter on BIAS_NET (no look-ahead)
            (
                "BIAS_NET_ML",
                {
                    **off,
                    "allow_short": True,
                    "require_bias_align": True,
                    "bias_mode": "net",
                    "_use_ml": True,
                },
            ),
        ]
        print(
            f"ML walk-forward model={args.ml_model} min_train={args.ml_min_train} "
            f"min_proba={args.ml_min_proba} …"
        )
        try:
            ml_p = walk_forward_p_up(
                bars,
                lags=3,
                min_train=args.ml_min_train,
                model_kind=args.ml_model,  # type: ignore[arg-type]
                retrain_every=5,
            )
            ps = list(ml_p.values())
            if ps:
                import numpy as np

                print(
                    f"ML OOS scores ready for {len(ml_p)}/{len(bars)} bars | "
                    f"p_up mean={np.mean(ps):.3f} p50={np.median(ps):.3f} "
                    f"p10={np.quantile(ps,0.1):.3f} p90={np.quantile(ps,0.9):.3f}"
                )
            else:
                print("ML OOS scores empty")
        except Exception as e:
            print(f"ML walk-forward failed: {e}")
            ml_p = {}
        for label, kw in variants:
            c = dict(common)
            use_ml = bool(kw.pop("_use_ml", False))
            c.update(kw)
            filt_stats: dict = {}
            if use_ml:
                c["ml_p_by_time"] = ml_p
                c["ml_min_proba"] = args.ml_min_proba
                c["ml_allow_if_missing"] = False  # fail closed — expose key bugs
                c["ml_filter_stats"] = filt_stats
            t, r = run_once(bars, **c)
            summarize(label, t, r, len(bars), args.tf)
            if use_ml and filt_stats:
                print(f"  ML filter stats: {dict(filt_stats)}")
        return

    use_hlv = bool(args.hlv) and not args.no_hlv
    trades, reasons = run_once(
        bars,
        require_hlv=use_hlv,
        require_vol_expansion=bool(args.vol_exp),
        vol_exp_require_h_plus=bool(args.vol_exp_h_plus),
        enable_flip_reverse=bool(args.flip_rev),
        require_bias_align=bool(args.bias_align),
        bias_mode=args.bias_mode,
        use_range_stops=bool(args.range_stops),
        **common,
    )
    bits = [
        "HLV_ON" if use_hlv else "HLV_OFF",
        "VOLX_ON" if args.vol_exp else "VOLX_OFF",
        "FLIP_ON" if args.flip_rev else "FLIP_OFF",
        f"BIAS_{args.bias_mode.upper()}" if args.bias_align else "BIAS_OFF",
        "RANGE_ON" if args.range_stops else "RANGE_OFF",
    ]
    summarize("+".join(bits), trades, reasons, len(bars), args.tf)


if __name__ == "__main__":
    main()
