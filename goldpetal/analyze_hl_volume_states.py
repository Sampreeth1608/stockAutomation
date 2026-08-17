#!/usr/bin/env python3
"""High/Low × Volume state study + almost-equal (=) bands.

Combos (9 + 9):
  H vs P.H  ×  V vs P.V
  L vs P.L  ×  V vs P.V

'=' means almost-equal:
  volume: within equal_pct (default 5%, use 3–9%)
  high/low: within equal_px_pct (default 0.05%) or equal_pts

Also refreshes TBQ/TSQ/Price 27-states with the same almost-equal rules.

Usage:
  python analyze_hl_volume_states.py --tf 30m,1h
  python analyze_hl_volume_states.py --equal-pct 0.05 --equal-px-pct 0.0005
"""

from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from almost_equal import clamp, sign_px, sign_rel, triple_state
from mtf_bars import INTERVALS, DB, build_rich_bars, load_tick_rows

OUT = Path("data/hl_vol_study")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("")
        return
    fields: list[str] = []
    seen = set()
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


def bar_rows_with_volume(raw_bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach per-bar volume = Δ cumulative volume_close."""
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


def analyze(
    bars: list[dict[str, Any]],
    *,
    tf: str,
    equal_pct: float,
    equal_px_pct: float,
    equal_pts: float | None,
    horizon: int,
    min_n: int,
) -> dict[str, Any]:
    if len(bars) < 3:
        return {"tf": tf, "n": 0}

    # sequences of states
    hv_states: list[str] = []
    lv_states: list[str] = []
    tbp_states: list[str] = []
    closes: list[float] = []

    for i, b in enumerate(bars):
        closes.append(float(b["close"]))
        if i == 0:
            hv_states.append("START")
            lv_states.append("START")
            tbp_states.append("START")
            continue
        p = bars[i - 1]
        hv = triple_state(
            float(b["high"]),
            float(p["high"]),
            float(b["bar_volume"]),
            float(p["bar_volume"]),
            a_is_px=True,
            b_is_px=False,
            equal_pct=equal_pct,
            equal_px_pct=equal_px_pct,
            equal_pts=equal_pts,
            prefix_a="H",
            prefix_b="V",
        )
        lv = triple_state(
            float(b["low"]),
            float(p["low"]),
            float(b["bar_volume"]),
            float(p["bar_volume"]),
            a_is_px=True,
            b_is_px=False,
            equal_pct=equal_pct,
            equal_px_pct=equal_px_pct,
            equal_pts=equal_pts,
            prefix_a="L",
            prefix_b="V",
        )
        # TBQ/TSQ/Price with almost-equal
        tbq_s = sign_rel(float(b["tbq_close"]), float(p["tbq_close"]), equal_pct)
        tsq_s = sign_rel(float(b["tsq_close"]), float(p["tsq_close"]), equal_pct)
        px_s = sign_px(
            float(b["close"]),
            float(p["close"]),
            equal_px_pct=equal_px_pct,
            equal_pts=equal_pts,
        )
        tbp = f"TBQ{tbq_s}_TSQ{tsq_s}_P{px_s}"
        hv_states.append(hv)
        lv_states.append(lv)
        tbp_states.append(tbp)

    def impact_table(states: list[str], family: str) -> list[dict[str, Any]]:
        buckets: dict[str, list[float]] = defaultdict(list)
        for i in range(len(states) - 1):
            if states[i] == "START":
                continue
            buckets[states[i]].append(closes[i + 1] - closes[i])
        rows = []
        for st, ds in sorted(buckets.items(), key=lambda kv: -len(kv[1])):
            n = len(ds)
            ups = sum(1 for d in ds if d > 0)
            rows.append(
                {
                    "tf": tf,
                    "family": family,
                    "state": st,
                    "n": n,
                    "next_up%": round(ups / n * 100, 1),
                    "avg_next_Δ": round(sum(ds) / n, 3),
                    "med_next_Δ": round(pct(ds, 50), 3),
                    "p25": round(pct(ds, 25), 3),
                    "p75": round(pct(ds, 75), 3),
                    "role": _hv_lv_role(st, family, ups / n, sum(ds) / n),
                }
            )
        return rows

    def transition_table(states: list[str], family: str) -> list[dict[str, Any]]:
        cnt: Counter = Counter()
        dmap: dict[tuple[str, str], list[float]] = defaultdict(list)
        for i in range(len(states) - 1):
            a, b = states[i], states[i + 1]
            if a == "START" or b == "START":
                continue
            cnt[(a, b)] += 1
            dmap[(a, b)].append(closes[i + 1] - closes[i])
        rows = []
        for (a, b), n in sorted(cnt.items(), key=lambda kv: -kv[1]):
            ds = dmap[(a, b)]
            denom = sum(v for (x, _), v in cnt.items() if x == a)
            rows.append(
                {
                    "tf": tf,
                    "family": family,
                    "from": a,
                    "to": b,
                    "n": n,
                    "pct_of_from": round(n / max(1, denom) * 100, 1),
                    "avg_Δ": round(sum(ds) / n, 3),
                }
            )
        return rows

    def path_entries(states: list[str], family: str) -> list[dict[str, Any]]:
        rows = []
        for st in sorted({s for s in states if s != "START"}):
            ends_long = []
            mfes = []
            maes = []
            for i in range(len(states) - horizon):
                if states[i] != st:
                    continue
                path = [closes[i + k] - closes[i] for k in range(1, horizon + 1)]
                ends_long.append(path[-1])
                mfes.append(max(0.0, max(path)))
                maes.append(max(0.0, -min(path)))
            n = len(ends_long)
            if n < min_n:
                continue
            win = sum(1 for e in ends_long if e > 0) / n * 100
            avg = sum(ends_long) / n
            tp = pct(mfes, 60)
            sl = pct(maes, 60)
            rr = (tp / sl) if sl and sl > 1e-9 else None
            side = "long" if avg >= 0 else "short"
            if side == "short":
                # flip metrics
                ends_s = [-e for e in ends_long]
                win = sum(1 for e in ends_s if e > 0) / n * 100
                avg = sum(ends_s) / n
                # for short MFE/MAE swap
                mfes_s = maes
                maes_s = mfes
                tp = pct(mfes_s, 60)
                sl = pct(maes_s, 60)
                rr = (tp / sl) if sl and sl > 1e-9 else None
            rows.append(
                {
                    "tf": tf,
                    "family": family,
                    "state": st,
                    "n": n,
                    "best_side": side,
                    "win%": round(win, 1),
                    "avg_end_Δ": round(avg, 3),
                    "suggest_TP": round(max(5.0, tp), 1) if tp == tp else 5.0,
                    "suggest_SL": round(max(5.0, sl), 1) if sl == sl else 5.0,
                    "RR": round(rr, 2) if rr else None,
                    "action": (
                        f"CANDIDATE_ENTER_{side.upper()}"
                        if n >= min_n and win >= 55 and avg > 0 and (rr or 0) >= 1.0
                        else "HOLD_or_SKIP"
                    ),
                }
            )
        rows.sort(key=lambda r: r["avg_end_Δ"], reverse=True)
        return rows

    hv_impact = impact_table(hv_states, "HIGH_VOL")
    lv_impact = impact_table(lv_states, "LOW_VOL")
    tbp_impact = impact_table(tbp_states, "TBQ_TSQ_P")

    return {
        "tf": tf,
        "n_bars": len(bars) - 1,
        "hv_impact": hv_impact,
        "lv_impact": lv_impact,
        "tbp_impact": tbp_impact,
        "hv_trans": transition_table(hv_states, "HIGH_VOL"),
        "lv_trans": transition_table(lv_states, "LOW_VOL"),
        "tbp_trans": transition_table(tbp_states, "TBQ_TSQ_P"),
        "hv_entries": path_entries(hv_states, "HIGH_VOL"),
        "lv_entries": path_entries(lv_states, "LOW_VOL"),
        "tbp_entries": path_entries(tbp_states, "TBQ_TSQ_P"),
        "equal_pct": equal_pct,
        "equal_px_pct": equal_px_pct,
        "equal_pts": equal_pts,
    }


def _hv_lv_role(st: str, family: str, up_frac: float, avg: float) -> str:
    # H+/V+ breakout with volume; H+/V- weak rally; L-/V+ breakdown volume, etc.
    if family == "HIGH_VOL":
        if st == "H+_V+":
            return "BREAKOUT_UP_vol_confirm"
        if st == "H+_V-":
            return "HIGH_NO_VOL (weak/trap risk)"
        if st == "H+_V=":
            return "HIGH_flat_vol"
        if st == "H-_V+":
            return "FAILED_HIGH_or_supply (vol up)"
        if st == "H-_V-":
            return "SOFT_PULLBACK"
        if st == "H=_V+":
            return "RANGE_HIGH_vol_build"
        if st == "H=_V-":
            return "RANGE_HIGH_quiet"
        if st == "H=_V=":
            return "COIL"
    if family == "LOW_VOL":
        if st == "L-_V+":
            return "BREAKDOWN_vol_confirm"
        if st == "L-_V-":
            return "LOW_NO_VOL (weak drain)"
        if st == "L+_V+":
            return "HIGHER_LOW_vol (bull cont)"
        if st == "L+_V-":
            return "HIGHER_LOW_weak"
        if st == "L=_V+":
            return "RANGE_LOW_vol_build"
        if st == "L=_V=":
            return "COIL"
    if avg > 0.5 and up_frac >= 0.55:
        return "EMPIRICAL_BULLISH"
    if avg < -0.5 and up_frac <= 0.45:
        return "EMPIRICAL_BEARISH"
    return "CHOP/NOISE"


def print_block(title: str, rows: list[dict[str, Any]], top: int = 12) -> None:
    print(f"\n--- {title} ---")
    if not rows:
        print("(none)")
        return
    if "from" in rows[0]:
        print(f"{'from':10} → {'to':10} {'n':>4} {'%':>6} {'avgΔ':>8}")
        for r in rows[:top]:
            print(
                f"{r['from']:10} → {r['to']:10} {r['n']:4} "
                f"{r['pct_of_from']:6.1f} {r['avg_Δ']:8.2f}"
            )
        return
    if "best_side" in rows[0]:
        print(
            f"{'state':12} {'side':5} {'n':>4} {'win%':>6} {'avgEnd':>8} "
            f"{'TP':>6} {'SL':>6}  action"
        )
        for r in rows[:top]:
            print(
                f"{r['state']:12} {r['best_side']:5} {r['n']:4} {r['win%']:6.1f} "
                f"{r['avg_end_Δ']:8.2f} {r['suggest_TP']:6.1f} {r['suggest_SL']:6.1f}  "
                f"{r['action']}"
            )
        return
    print(f"{'state':16} {'n':>4} {'up%':>6} {'avgΔ':>8}  role")
    for r in sorted(rows, key=lambda x: -x["n"])[:top]:
        print(
            f"{r['state']:16} {r['n']:4} {r['next_up%']:6.1f} "
            f"{r['avg_next_Δ']:8.2f}  {r['role']}"
        )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--tf", default="15m,30m,1h")
    ap.add_argument(
        "--equal-pct",
        type=float,
        default=0.05,
        help="Almost-equal band for qty/volume (0.05=5%; typical 0.03–0.09)",
    )
    ap.add_argument(
        "--equal-px-pct",
        type=float,
        default=0.0005,
        help="Almost-equal for high/low/close as fraction of price (0.0005=0.05%)",
    )
    ap.add_argument(
        "--equal-pts",
        type=float,
        default=None,
        help="If set, high/low/close '=' uses absolute points instead of %%",
    )
    ap.add_argument("--horizon", type=int, default=3)
    ap.add_argument("--min-n", type=int, default=8)
    args = ap.parse_args()

    eq = clamp(args.equal_pct, 0.03, 0.09)
    if abs(eq - args.equal_pct) > 1e-12:
        print(f"note: equal-pct clamped to {eq:.0%} (allowed 3–9%)")

    want = {x.strip() for x in args.tf.split(",") if x.strip()}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    print("Loading ticks...")
    ticks = load_tick_rows(Path(args.db))
    print(
        f"ticks={len(ticks)} equal_pct={eq:.0%} equal_px_pct={args.equal_px_pct} "
        f"equal_pts={args.equal_pts}"
    )

    all_impact: list[dict] = []
    all_trans: list[dict] = []
    all_entries: list[dict] = []

    for tf, mins in INTERVALS:
        if tf not in want:
            continue
        raw = [b.to_row() for b in build_rich_bars(ticks, tf, mins)]
        bars = bar_rows_with_volume(raw)
        result = analyze(
            bars,
            tf=tf,
            equal_pct=eq,
            equal_px_pct=args.equal_px_pct,
            equal_pts=args.equal_pts,
            horizon=args.horizon,
            min_n=args.min_n,
        )
        print("\n" + "=" * 88)
        print(f"TF={tf} bars={result.get('n_bars', 0)}")
        print("=" * 88)
        print_block("HIGH × VOLUME — next-bar impact", result["hv_impact"])
        print_block("LOW × VOLUME — next-bar impact", result["lv_impact"])
        print_block("TBQ/TSQ/P (almost-equal) — next-bar impact", result["tbp_impact"])
        print_block("HIGH×VOL transitions", result["hv_trans"], top=10)
        print_block("LOW×VOL transitions", result["lv_trans"], top=10)
        print_block("HIGH×VOL entry candidates", result["hv_entries"])
        print_block("LOW×VOL entry candidates", result["lv_entries"])
        print_block("TBQ/TSQ/P entry candidates (almost-equal)", result["tbp_entries"])

        write_csv(out / f"{tf}_hv_impact.csv", result["hv_impact"])
        write_csv(out / f"{tf}_lv_impact.csv", result["lv_impact"])
        write_csv(out / f"{tf}_tbp_impact.csv", result["tbp_impact"])
        write_csv(out / f"{tf}_hv_trans.csv", result["hv_trans"])
        write_csv(out / f"{tf}_lv_trans.csv", result["lv_trans"])
        write_csv(out / f"{tf}_hv_entries.csv", result["hv_entries"])
        write_csv(out / f"{tf}_lv_entries.csv", result["lv_entries"])
        write_csv(out / f"{tf}_tbp_entries.csv", result["tbp_entries"])

        all_impact.extend(result["hv_impact"] + result["lv_impact"] + result["tbp_impact"])
        all_trans.extend(result["hv_trans"] + result["lv_trans"] + result["tbp_trans"])
        all_entries.extend(
            result["hv_entries"] + result["lv_entries"] + result["tbp_entries"]
        )

    write_csv(out / "all_impact.csv", all_impact)
    write_csv(out / "all_transitions.csv", all_trans)
    write_csv(out / "all_entries.csv", all_entries)
    (out / "README.txt").write_text(
        f"""HL × Volume + TBQ/TSQ/P state study
equal_pct (qty/vol almost-equal) = {eq:.0%}  (clamped 3–9%)
equal_px_pct (H/L/P almost-equal) = {args.equal_px_pct}
equal_pts override = {args.equal_pts}

Note: C.L+P.L in the sheet is treated as C.L≈P.L (almost equal lows).

Bar volume = Δ of cumulative day volume between bars.
Import CSVs from {out} into Google Sheets for manual review.
""",
        encoding="utf-8",
    )
    print(f"\nCSVs → {out.resolve()}")


if __name__ == "__main__":
    main()
