#!/usr/bin/env python3
"""3-bar volume stacks vs trend halt / flip / expansion (manual observation check).

Hypothesis (your sheet):
  Decreasing candle volume on a trend often ends on the *next* candle
  with a huge price move.

Volume = candle volume = Δ cumulative day volume (same as HLV C.V).
P.P.V = volume two bars ago.

Also scores extra co-signals that often mark exhaustion / expansion:
  - range shrink then expand
  - close location in bar (near high/low)
  - NET / imbalance
  - TBQ/TSQ/P state
  - LTQ sum surge
  - volume climax (C.V >> P.V after a decline)

Usage (on VM with ticks.db):
  python3 analyze_vol_trend_exhaustion.py --tf 30m,1h
  python3 analyze_vol_trend_exhaustion.py --tf 30m --huge-pct 75
"""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path
from typing import Any

from almost_equal import clamp, sign_px, sign_rel
from mtf_bars import DB, INTERVALS, build_rich_bars, load_tick_rows

OUT = Path("data/vol_trend_study")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("")
        return
    fields: list[str] = []
    seen: set[str] = set()
    for r in rows:
        for k in r:
            if k not in seen:
                seen.add(k)
                fields.append(k)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def pct(xs: list[float], p: float) -> float:
    if not xs:
        return float("nan")
    ys = sorted(xs)
    i = min(len(ys) - 1, max(0, int(round((p / 100.0) * (len(ys) - 1)))))
    return ys[i]


def mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else float("nan")


def bar_rows_with_volume(raw_bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    prev_vol = None
    for r in raw_bars:
        vc = r.get("volume_close")
        try:
            vc_f = float(vc) if vc is not None else None
        except (TypeError, ValueError):
            vc_f = None
        if vc_f is None or prev_vol is None:
            bar_vol = 0.0
        else:
            bar_vol = max(0.0, vc_f - prev_vol)
        if vc_f is not None:
            prev_vol = vc_f
        row = dict(r)
        row["bar_volume"] = bar_vol
        out.append(row)
    return out


def vol_stack_label(cv: float, pv: float, ppv: float, equal_pct: float) -> str:
    """C.V ? P.V ? P.P.V → DEC_DEC / INC_INC / DEC_INC / … / FLAT_FLAT."""
    s1 = sign_rel(cv, pv, equal_pct)  # C vs P
    s2 = sign_rel(pv, ppv, equal_pct)  # P vs PP

    def word(s: str) -> str:
        return {"+": "INC", "-": "DEC", "=": "FLAT"}[s]

    # strict monotone helpers for your sheet
    if s1 == "-" and s2 == "-":
        return "DEC_DEC"  # C < P < PP (approx)
    if s1 == "+" and s2 == "+":
        return "INC_INC"  # C > P > PP
    if s1 == "=" and s2 == "=":
        return "FLAT_FLAT"
    return f"{word(s1)}_{word(s2)}"  # e.g. DEC_INC, INC_FLAT


def price_trend(closes: list[float], i: int, lookback: int, equal_px_pct: float) -> str:
    """Trend from closes[i-lookback] → closes[i]."""
    if i < lookback:
        return "NA"
    a = closes[i - lookback]
    b = closes[i]
    s = sign_px(b, a, equal_px_pct=equal_px_pct)
    return {"+": "UP", "-": "DOWN", "=": "FLAT"}[s]


def structure_hl(
    bars: list[dict[str, Any]], i: int, equal_px_pct: float
) -> tuple[str, str]:
    """H vs P.H and L vs P.L labels."""
    if i < 1:
        return "H?", "L?"
    h = sign_px(
        float(bars[i]["high"]),
        float(bars[i - 1]["high"]),
        equal_px_pct=equal_px_pct,
    )
    l = sign_px(
        float(bars[i]["low"]),
        float(bars[i - 1]["low"]),
        equal_px_pct=equal_px_pct,
    )
    return f"H{h}", f"L{l}"


def close_loc(o: float, h: float, l: float, c: float) -> float:
    """0 = close at low, 1 = close at high."""
    span = max(h - l, 1e-9)
    return (c - l) / span


def outcome_label(
    *,
    trend: str,
    next_d: float,
    next_range: float,
    huge_move: bool,
    halt_pts: float,
) -> str:
    """CONTINUE / HALT / FLIP (+ _HUGE if next move is large)."""
    if trend == "UP":
        if abs(next_d) <= halt_pts:
            base = "HALT"
        elif next_d > 0:
            base = "CONTINUE"
        else:
            base = "FLIP"
    elif trend == "DOWN":
        if abs(next_d) <= halt_pts:
            base = "HALT"
        elif next_d < 0:
            base = "CONTINUE"
        else:
            base = "FLIP"
    else:
        if abs(next_d) <= halt_pts:
            base = "HALT"
        else:
            base = "EXPAND" if huge_move else "MOVE"
    if huge_move and base in {"CONTINUE", "FLIP", "EXPAND", "MOVE"}:
        return f"{base}_HUGE"
    if huge_move and base == "HALT":
        # large range but small closeΔ = wide inside / rejection
        return "HALT_WIDE" if next_range > halt_pts * 2 else "HALT"
    return base


def analyze(
    bars: list[dict[str, Any]],
    *,
    tf: str,
    equal_pct: float,
    equal_px_pct: float,
    huge_pct: float,
    halt_pts: float,
    trend_lookback: int,
    min_n: int,
) -> dict[str, Any]:
    if len(bars) < 4:
        return {"tf": tf, "n": 0}

    closes = [float(b["close"]) for b in bars]
    ranges = [
        float(b["high"]) - float(b["low"]) for b in bars
    ]
    next_abs = [abs(closes[i + 1] - closes[i]) for i in range(len(closes) - 1)]
    huge_thr = pct(next_abs, huge_pct) if next_abs else 20.0
    med_range = pct(ranges, 50)

    events: list[dict[str, Any]] = []

    for i in range(2, len(bars) - 1):
        b, p, pp = bars[i], bars[i - 1], bars[i - 2]
        cv = float(b["bar_volume"])
        pv = float(p["bar_volume"])
        ppv = float(pp["bar_volume"])
        if cv <= 0 and pv <= 0:
            continue

        stack = vol_stack_label(cv, pv, ppv, equal_pct)
        trend = price_trend(closes, i, trend_lookback, equal_px_pct)
        hs, ls = structure_hl(bars, i, equal_px_pct)

        # next candle
        nxt = bars[i + 1]
        next_d = float(nxt["close"]) - closes[i]
        next_range = float(nxt["high"]) - float(nxt["low"])
        next_abs_d = abs(next_d)
        huge = next_abs_d >= huge_thr
        out = outcome_label(
            trend=trend,
            next_d=next_d,
            next_range=next_range,
            huge_move=huge,
            halt_pts=halt_pts,
        )

        # extras
        rng = ranges[i]
        prev_rng = ranges[i - 1]
        rng_s = sign_rel(rng, max(prev_rng, 1e-9), equal_pct)
        cl = close_loc(
            float(b["open"]), float(b["high"]), float(b["low"]), float(b["close"])
        )
        net = float(b.get("net") or 0.0)
        imb = float(b.get("imb_pct") or 0.0)
        tbq_s = sign_rel(float(b["tbq_close"]), float(p["tbq_close"]), equal_pct)
        tsq_s = sign_rel(float(b["tsq_close"]), float(p["tsq_close"]), equal_pct)
        px_s = sign_px(closes[i], closes[i - 1], equal_px_pct=equal_px_pct)
        tbp = f"B{tbq_s}S{tsq_s}P{px_s}"
        ltq = float(b.get("ltq_sum") or 0.0)
        prev_ltq = float(p.get("ltq_sum") or 0.0)
        ltq_s = sign_rel(ltq, max(prev_ltq, 1e-9), equal_pct)

        # volume climax: C much larger than P after P < PP
        climax = sign_rel(cv, pv, equal_pct) == "+" and sign_rel(pv, ppv, equal_pct) == "-"
        # dry-up into trend: DEC_DEC while trending
        dry_up = stack == "DEC_DEC" and trend in {"UP", "DOWN"}

        events.append(
            {
                "tf": tf,
                "time": b.get("time"),
                "vol_stack": stack,
                "trend": trend,
                "hs": hs,
                "ls": ls,
                "combo": f"{stack}|{trend}|{hs}|{ls}",
                "cv": round(cv, 1),
                "pv": round(pv, 1),
                "ppv": round(ppv, 1),
                "close": closes[i],
                "range": round(rng, 2),
                "range_sign": f"R{rng_s}",
                "close_loc": round(cl, 3),
                "net": round(net, 1),
                "imb_pct": round(imb, 2),
                "tbp": tbp,
                "ltq_sign": f"LTQ{ltq_s}",
                "climax_after_dry": climax,
                "dry_up_trend": dry_up,
                "next_Δ": round(next_d, 2),
                "next_|Δ|": round(next_abs_d, 2),
                "next_range": round(next_range, 2),
                "huge": huge,
                "outcome": out,
            }
        )

    # --- aggregate tables ---
    def agg(key_fn, name: str) -> list[dict[str, Any]]:
        buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for e in events:
            buckets[key_fn(e)].append(e)
        rows = []
        for k, xs in sorted(buckets.items(), key=lambda kv: -len(kv[1])):
            if len(xs) < min_n:
                continue
            abs_d = [float(x["next_|Δ|"]) for x in xs]
            rngs = [float(x["next_range"]) for x in xs]
            huge_n = sum(1 for x in xs if x["huge"])
            flip_n = sum(1 for x in xs if str(x["outcome"]).startswith("FLIP"))
            cont_n = sum(1 for x in xs if str(x["outcome"]).startswith("CONTINUE"))
            halt_n = sum(1 for x in xs if str(x["outcome"]).startswith("HALT"))
            rows.append(
                {
                    "tf": tf,
                    "family": name,
                    "key": k,
                    "n": len(xs),
                    "huge%": round(huge_n / len(xs) * 100, 1),
                    "flip%": round(flip_n / len(xs) * 100, 1),
                    "continue%": round(cont_n / len(xs) * 100, 1),
                    "halt%": round(halt_n / len(xs) * 100, 1),
                    "avg_next_|Δ|": round(mean(abs_d), 2),
                    "med_next_|Δ|": round(pct(abs_d, 50), 2),
                    "p75_next_|Δ|": round(pct(abs_d, 75), 2),
                    "avg_next_range": round(mean(rngs), 2),
                    "lift_vs_base_|Δ|": round(
                        mean(abs_d) / max(pct(next_abs, 50), 1e-9), 2
                    ),
                    "note": _note(k, name, huge_n / len(xs), flip_n / len(xs), mean(abs_d)),
                }
            )
        rows.sort(key=lambda r: (r["huge%"], r["avg_next_|Δ|"]), reverse=True)
        return rows

    base_huge = sum(1 for e in events if e["huge"]) / max(len(events), 1) * 100

    hyp_rows = []
    # core hypothesis slices
    for label, pred in [
        ("DEC_DEC_any_trend", lambda e: e["vol_stack"] == "DEC_DEC"),
        ("DEC_DEC_UP", lambda e: e["vol_stack"] == "DEC_DEC" and e["trend"] == "UP"),
        ("DEC_DEC_DOWN", lambda e: e["vol_stack"] == "DEC_DEC" and e["trend"] == "DOWN"),
        ("DEC_DEC_UP_H+", lambda e: e["vol_stack"] == "DEC_DEC" and e["trend"] == "UP" and e["hs"] == "H+"),
        ("DEC_DEC_DOWN_L-", lambda e: e["vol_stack"] == "DEC_DEC" and e["trend"] == "DOWN" and e["ls"] == "L-"),
        ("INC_INC_any", lambda e: e["vol_stack"] == "INC_INC"),
        ("FLAT_FLAT_any", lambda e: e["vol_stack"] == "FLAT_FLAT"),
        ("climax_after_dry", lambda e: e["climax_after_dry"]),
        ("dry_up_trend", lambda e: e["dry_up_trend"]),
        ("DEC_DEC_range_shrink", lambda e: e["vol_stack"] == "DEC_DEC" and e["range_sign"] == "R-"),
        ("DEC_DEC_close_high", lambda e: e["vol_stack"] == "DEC_DEC" and e["close_loc"] >= 0.7),
        ("DEC_DEC_close_low", lambda e: e["vol_stack"] == "DEC_DEC" and e["close_loc"] <= 0.3),
        ("DEC_DEC_imb>=10", lambda e: e["vol_stack"] == "DEC_DEC" and float(e["imb_pct"]) >= 10),
    ]:
        xs = [e for e in events if pred(e)]
        if len(xs) < min_n:
            hyp_rows.append(
                {
                    "tf": tf,
                    "slice": label,
                    "n": len(xs),
                    "note": "too_few",
                }
            )
            continue
        abs_d = [float(x["next_|Δ|"]) for x in xs]
        huge_n = sum(1 for x in xs if x["huge"])
        flip_n = sum(1 for x in xs if str(x["outcome"]).startswith("FLIP"))
        cont_n = sum(1 for x in xs if str(x["outcome"]).startswith("CONTINUE"))
        hyp_rows.append(
            {
                "tf": tf,
                "slice": label,
                "n": len(xs),
                "huge%": round(huge_n / len(xs) * 100, 1),
                "base_huge%": round(base_huge, 1),
                "huge_lift": round((huge_n / len(xs) * 100) - base_huge, 1),
                "flip%": round(flip_n / len(xs) * 100, 1),
                "continue%": round(cont_n / len(xs) * 100, 1),
                "avg_next_|Δ|": round(mean(abs_d), 2),
                "med_next_|Δ|": round(pct(abs_d, 50), 2),
                "p75_next_|Δ|": round(pct(abs_d, 75), 2),
                "lift_vs_med_|Δ|": round(mean(abs_d) / max(pct(next_abs, 50), 1e-9), 2),
                "supports_hypothesis": (
                    (huge_n / len(xs) * 100) >= base_huge + 10
                    and mean(abs_d) >= pct(next_abs, 50) * 1.25
                ),
            }
        )
    hyp_rows.sort(key=lambda r: r.get("huge_lift", -999), reverse=True)

    return {
        "tf": tf,
        "n_events": len(events),
        "huge_thr_pts": round(huge_thr, 2),
        "med_next_|Δ|": round(pct(next_abs, 50), 2),
        "med_range": round(med_range, 2),
        "base_huge%": round(base_huge, 1),
        "events": events,
        "by_stack": agg(lambda e: e["vol_stack"], "VOL_STACK"),
        "by_stack_trend": agg(lambda e: f"{e['vol_stack']}|{e['trend']}", "STACK_TREND"),
        "by_combo": agg(lambda e: e["combo"], "STACK_TREND_HL"),
        "by_tbp": agg(lambda e: f"{e['vol_stack']}|{e['tbp']}", "STACK_TBP"),
        "hypothesis": hyp_rows,
    }


def _note(k: str, family: str, huge_frac: float, flip_frac: float, avg_abs: float) -> str:
    bits = []
    if "DEC_DEC" in k:
        bits.append("dry_up")
    if "INC_INC" in k:
        bits.append("vol_thrust")
    if huge_frac >= 0.35:
        bits.append("HIGH_HUGE_RATE")
    if flip_frac >= 0.4:
        bits.append("FLIP_RISK")
    if avg_abs >= 20:
        bits.append("BIG_AVG_MOVE")
    return ",".join(bits) if bits else ""


def print_hyp(tf: str, meta: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    print("\n" + "=" * 96)
    print(
        f"TF={tf} events={meta.get('n_events')} "
        f"huge_thr≈{meta.get('huge_thr_pts')}pts "
        f"med_next_|Δ|={meta.get('med_next_|Δ|')} "
        f"base_huge%={meta.get('base_huge%')}"
    )
    print("=" * 96)
    print(
        f"{'slice':28} {'n':>4} {'huge%':>7} {'lift':>6} "
        f"{'flip%':>6} {'cont%':>6} {'avg|Δ|':>8} {'ok?':>5}"
    )
    for r in rows:
        if r.get("note") == "too_few":
            print(f"{r['slice']:28} {r['n']:4}  (too few)")
            continue
        ok = "YES" if r.get("supports_hypothesis") else "no"
        print(
            f"{r['slice']:28} {r['n']:4} {r.get('huge%', 0):7.1f} "
            f"{r.get('huge_lift', 0):6.1f} {r.get('flip%', 0):6.1f} "
            f"{r.get('continue%', 0):6.1f} {r.get('avg_next_|Δ|', 0):8.2f} {ok:>5}"
        )


def print_top(title: str, rows: list[dict[str, Any]], n: int = 12) -> None:
    print(f"\n--- {title} (top {n} by huge%) ---")
    if not rows:
        print("(none)")
        return
    print(
        f"{'key':40} {'n':>4} {'huge%':>7} {'flip%':>6} "
        f"{'cont%':>6} {'avg|Δ|':>8} {'lift':>6}"
    )
    for r in rows[:n]:
        print(
            f"{str(r['key'])[:40]:40} {r['n']:4} {r['huge%']:7.1f} "
            f"{r['flip%']:6.1f} {r['continue%']:6.1f} "
            f"{r['avg_next_|Δ|']:8.2f} {r['lift_vs_base_|Δ|']:6.2f}"
        )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--tf", default="30m,1h")
    ap.add_argument("--equal-pct", type=float, default=0.05)
    ap.add_argument("--equal-px-pct", type=float, default=0.0005)
    ap.add_argument(
        "--huge-pct",
        type=float,
        default=75.0,
        help="Next |Δclose| percentile that counts as huge (default p75)",
    )
    ap.add_argument(
        "--halt-pts",
        type=float,
        default=5.0,
        help="|next Δclose| <= this → HALT",
    )
    ap.add_argument("--trend-lookback", type=int, default=2)
    ap.add_argument("--min-n", type=int, default=5)
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()

    eq = clamp(args.equal_pct, 0.03, 0.09)
    wanted = {x.strip() for x in args.tf.split(",") if x.strip()}
    tfs = [(n, m) for n, m in INTERVALS if n in wanted]
    if not tfs:
        raise SystemExit(f"No matching TF in {wanted}")

    print("Loading ticks...")
    rows = load_tick_rows(Path(args.db))
    print(f"ticks={len(rows)}")
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    for name, minutes in tfs:
        raw = [b.to_row() for b in build_rich_bars(rows, name, minutes)]
        bars = bar_rows_with_volume(raw)
        result = analyze(
            bars,
            tf=name,
            equal_pct=eq,
            equal_px_pct=args.equal_px_pct,
            huge_pct=args.huge_pct,
            halt_pts=args.halt_pts,
            trend_lookback=args.trend_lookback,
            min_n=args.min_n,
        )
        if not result.get("n_events"):
            print(f"TF={name}: not enough bars")
            continue
        print_hyp(name, result, result["hypothesis"])
        print_top("VOL_STACK", result["by_stack"])
        print_top("STACK|TREND", result["by_stack_trend"])
        print_top("STACK|TREND|H|L", result["by_combo"])
        print_top("STACK|TBP", result["by_tbp"])

        write_csv(out_dir / f"events_{name}.csv", result["events"])
        write_csv(out_dir / f"hypothesis_{name}.csv", result["hypothesis"])
        write_csv(out_dir / f"by_stack_{name}.csv", result["by_stack"])
        write_csv(out_dir / f"by_stack_trend_{name}.csv", result["by_stack_trend"])
        write_csv(out_dir / f"by_combo_{name}.csv", result["by_combo"])
        write_csv(out_dir / f"by_tbp_{name}.csv", result["by_tbp"])

    print(f"\nCSVs → {out_dir.resolve()}")


if __name__ == "__main__":
    main()
