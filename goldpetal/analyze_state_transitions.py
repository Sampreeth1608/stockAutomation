#!/usr/bin/env python3
"""27-state TBQ/TSQ/Price transition study → entry/hold/exit + TP/SL hints.

States (each of TBQ, TSQ, Price vs previous):
  +  increase
  -  decrease
  =  flat (within eps)

For each bar TF (default 15m/30m/1h):
  1) state sequence
  2) transition matrix (from → to) with count + avg next price Δ
  3) per-state next-bar impact
  4) which states start / continue / stop trends
  5) MFE/MAE after start states → suggested TP/SL percentiles

Usage (VM):
  python analyze_state_transitions.py
  python analyze_state_transitions.py --tf 30m --horizon 3
  python analyze_state_transitions.py --from-csv data/manual_pack/bars_30m.csv
"""

from __future__ import annotations

import argparse
import csv
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mtf_bars import INTERVALS, DB, build_rich_bars, load_tick_rows

OUT = Path("data/state_study")


def _sign(delta: float, eps: float) -> str:
    if delta > eps:
        return "+"
    if delta < -eps:
        return "-"
    return "="


def state_code(dtbq: float, dtsq: float, dpx: float, eps_qty: float, eps_px: float) -> str:
    return f"TBQ{_sign(dtbq, eps_qty)}_TSQ{_sign(dtsq, eps_qty)}_P{_sign(dpx, eps_px)}"


def short_label(code: str) -> str:
    # TBQ+_TSQ-_P+ → B+S-P+
    parts = code.split("_")
    b = parts[0].replace("TBQ", "B")
    s = parts[1].replace("TSQ", "S")
    p = parts[2]
    return f"{b}{s}{p}"


@dataclass
class BarView:
    time: str
    close: float
    tbq: float
    tsq: float
    net: float
    imb_pct: float
    state: str


def bars_from_csv(path: Path) -> list[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def to_views(
    rows: list[dict[str, Any]],
    *,
    eps_qty: float,
    eps_px: float,
) -> list[BarView]:
    views: list[BarView] = []
    prev_tbq = prev_tsq = prev_px = None
    for r in rows:
        close = float(r["close"])
        tbq = float(r.get("tbq_close", r.get("tbq", 0)) or 0)
        tsq = float(r.get("tsq_close", r.get("tsq", 0)) or 0)
        net = tbq - tsq
        imb = abs(net) / max(tbq, tsq, 1e-9) * 100.0
        if prev_tbq is None:
            st = "START"
        else:
            st = state_code(tbq - prev_tbq, tsq - prev_tsq, close - prev_px, eps_qty, eps_px)
        views.append(
            BarView(
                time=str(r.get("time", "")),
                close=close,
                tbq=tbq,
                tsq=tsq,
                net=net,
                imb_pct=imb,
                state=st,
            )
        )
        prev_tbq, prev_tsq, prev_px = tbq, tsq, close
    return views


def pct(xs: list[float], p: float) -> float:
    if not xs:
        return float("nan")
    ys = sorted(xs)
    i = min(len(ys) - 1, max(0, int(round((p / 100.0) * (len(ys) - 1)))))
    return ys[i]


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


def analyze_tf(
    views: list[BarView],
    *,
    tf: str,
    horizon: int,
    min_n: int,
) -> dict[str, Any]:
    # drop START
    seq = [v for v in views if v.state != "START"]
    if len(seq) < 5:
        return {"tf": tf, "n": 0}

    # --- next-bar impact per state ---
    state_next: dict[str, list[float]] = defaultdict(list)
    for i in range(len(seq) - 1):
        d = seq[i + 1].close - seq[i].close
        state_next[seq[i].state].append(d)

    state_rows = []
    for st, deltas in sorted(state_next.items(), key=lambda kv: -len(kv[1])):
        n = len(deltas)
        ups = sum(1 for d in deltas if d > 0)
        dns = sum(1 for d in deltas if d < 0)
        state_rows.append(
            {
                "tf": tf,
                "state": st,
                "label": short_label(st),
                "n": n,
                "next_up%": round(ups / n * 100, 1),
                "next_dn%": round(dns / n * 100, 1),
                "avg_next_Δ": round(sum(deltas) / n, 3),
                "med_next_Δ": round(pct(deltas, 50), 3),
                "p25_next_Δ": round(pct(deltas, 25), 3),
                "p75_next_Δ": round(pct(deltas, 75), 3),
                "role_hint": _role_hint(st, ups / n, sum(deltas) / n),
            }
        )

    # --- transitions from → to ---
    trans_count: Counter = Counter()
    trans_dpx: dict[tuple[str, str], list[float]] = defaultdict(list)
    for i in range(len(seq) - 1):
        a, b = seq[i].state, seq[i + 1].state
        d = seq[i + 1].close - seq[i].close
        trans_count[(a, b)] += 1
        trans_dpx[(a, b)].append(d)

    trans_rows = []
    for (a, b), n in sorted(trans_count.items(), key=lambda kv: -kv[1]):
        ds = trans_dpx[(a, b)]
        trans_rows.append(
            {
                "tf": tf,
                "from_state": a,
                "to_state": b,
                "from_label": short_label(a),
                "to_label": short_label(b),
                "n": n,
                "pct_of_from": round(
                    n / max(1, sum(v for (x, _), v in trans_count.items() if x == a)) * 100,
                    1,
                ),
                "avg_price_Δ_on_transition": round(sum(ds) / n, 3),
            }
        )

    # --- trend start / continue / stop using multi-bar path ---
    # Define "up path" as cumulative Δ over horizon > 0, etc.
    start_long, start_short = [], []
    cont_long, cont_short = [], []
    stop_up, stop_dn = [], []

    for i in range(len(seq) - horizon):
        st = seq[i].state
        path = [seq[i + k].close - seq[i].close for k in range(1, horizon + 1)]
        end = path[-1]
        mfe = max(path)  # max favorable if long
        mae = min(path)  # max adverse if long
        # classify state theoretically
        bull_confirm = st == "TBQ+_TSQ-_P+"
        bear_confirm = st == "TBQ-_TSQ+_P-"
        bull_cont = st in {"TBQ+_TSQ-_P+", "TBQ+_TSQ=_P+", "TBQ=_TSQ-_P+", "TBQ+_TSQ+_P+"}
        bear_cont = st in {"TBQ-_TSQ+_P-", "TBQ-_TSQ=_P-", "TBQ=_TSQ+_P-", "TBQ-_TSQ-_P-"}
        # stop / reverse candidates
        stop_longish = st in {
            "TBQ-_TSQ+_P-",
            "TBQ-_TSQ+_P=",
            "TBQ-_TSQ=_P-",
            "TBQ+_TSQ+_P-",
            "TBQ-_TSQ-_P-",
        }
        stop_shortish = st in {
            "TBQ+_TSQ-_P+",
            "TBQ+_TSQ-_P=",
            "TBQ+_TSQ=_P+",
            "TBQ+_TSQ+_P+",
            "TBQ-_TSQ-_P+",
        }

        rec = {
            "state": st,
            "end_Δ": end,
            "mfe": mfe,
            "mae": mae,
            "mfe_short": -mae,  # favorable for short
            "mae_short": -mfe,
        }
        if bull_confirm:
            start_long.append(rec)
        if bear_confirm:
            start_short.append(rec)
        if bull_cont:
            cont_long.append(rec)
        if bear_cont:
            cont_short.append(rec)
        if stop_longish:
            stop_up.append(rec)
        if stop_shortish:
            stop_dn.append(rec)

    def _agg(name: str, recs: list[dict], side: str) -> dict[str, Any]:
        if not recs:
            return {
                "tf": tf,
                "bucket": name,
                "side": side,
                "n": 0,
                "win%": 0,
                "avg_end_Δ": 0,
                "med_MFE": 0,
                "med_MAE": 0,
                "suggest_TP": 0,
                "suggest_SL": 0,
            }
        if side == "long":
            ends = [r["end_Δ"] for r in recs]
            mfes = [max(0.0, r["mfe"]) for r in recs]
            maes = [max(0.0, -r["mae"]) for r in recs]  # adverse distance
        else:
            # short: profit when price falls
            ends = [-r["end_Δ"] for r in recs]
            mfes = [max(0.0, -r["mae"]) for r in recs]  # down excursion
            maes = [max(0.0, r["mfe"]) for r in recs]  # up excursion against short
        wins = sum(1 for e in ends if e > 0)
        # Scientific TP/SL: TP near p60 of MFE, SL near p60 of MAE (asymmetric OK)
        sug_tp = pct(mfes, 60)
        sug_sl = pct(maes, 60)
        # clamp sanity
        if math.isnan(sug_tp):
            sug_tp = 0.0
        if math.isnan(sug_sl):
            sug_sl = 0.0
        return {
            "tf": tf,
            "bucket": name,
            "side": side,
            "n": len(recs),
            "win%": round(wins / len(recs) * 100, 1),
            "avg_end_Δ": round(sum(ends) / len(ends), 3),
            "med_MFE": round(pct(mfes, 50), 2),
            "med_MAE": round(pct(maes, 50), 2),
            "p60_MFE_TP": round(sug_tp, 2),
            "p60_MAE_SL": round(sug_sl, 2),
            "suggest_TP": round(max(5.0, sug_tp), 1),
            "suggest_SL": round(max(5.0, sug_sl), 1),
            "RR": round(sug_tp / sug_sl, 2) if sug_sl > 1e-9 else None,
        }

    role_rows = [
        _agg("START_confirm_B+S-P+", start_long, "long"),
        _agg("START_confirm_B-S+P-", start_short, "short"),
        _agg("CONTINUE_bullish_family", cont_long, "long"),
        _agg("CONTINUE_bearish_family", cont_short, "short"),
        _agg("STOP_uptrend_family", stop_up, "long"),
        _agg("STOP_downtrend_family", stop_dn, "short"),
    ]

    # Per-state excursion for ALL states with enough samples (entry candidates)
    entry_rows = []
    for st, _ in sorted(state_next.items(), key=lambda kv: -len(kv[1])):
        recs = []
        for i in range(len(seq) - horizon):
            if seq[i].state != st:
                continue
            path = [seq[i + k].close - seq[i].close for k in range(1, horizon + 1)]
            recs.append(
                {
                    "end_Δ": path[-1],
                    "mfe": max(path),
                    "mae": min(path),
                    "mfe_short": -min(path),
                    "mae_short": -max(path),
                }
            )
        if len(recs) < min_n:
            continue
        long_a = _agg(st, recs, "long")
        short_a = _agg(st, recs, "short")
        # pick better side by avg_end
        best = long_a if long_a["avg_end_Δ"] >= short_a["avg_end_Δ"] else short_a
        entry_rows.append(
            {
                "tf": tf,
                "state": st,
                "label": short_label(st),
                "n": best["n"],
                "best_side": best["side"],
                "win%": best["win%"],
                "avg_end_Δ": best["avg_end_Δ"],
                "med_MFE": best["med_MFE"],
                "med_MAE": best["med_MAE"],
                "suggest_TP": best["suggest_TP"],
                "suggest_SL": best["suggest_SL"],
                "RR": best["RR"],
                "action": _action_from_stats(best),
            }
        )

    entry_rows.sort(key=lambda r: (r["avg_end_Δ"], r["win%"]), reverse=True)

    return {
        "tf": tf,
        "n_bars": len(seq),
        "state_rows": state_rows,
        "trans_rows": trans_rows,
        "role_rows": role_rows,
        "entry_rows": entry_rows,
    }


def _role_hint(st: str, up_frac: float, avg: float) -> str:
    if st == "TBQ+_TSQ-_P+":
        return "TRUE_UP / start_or_continue_LONG"
    if st == "TBQ-_TSQ+_P-":
        return "TRUE_DOWN / start_or_continue_SHORT"
    if st == "TBQ+_TSQ-_P-":
        return "BULL_PULLBACK (buy dips if NET+)"
    if st == "TBQ-_TSQ+_P+":
        return "BEAR_BOUNCE (sell rallies if NET-)"
    if st.endswith("P=") and "TBQ+" in st and "TSQ-" in st:
        return "COIL_BULL"
    if st.endswith("P=") and "TBQ-" in st and "TSQ+" in st:
        return "COIL_BEAR"
    if avg > 0.5 and up_frac >= 0.55:
        return "EMPIRICAL_BULLISH"
    if avg < -0.5 and up_frac <= 0.45:
        return "EMPIRICAL_BEARISH"
    return "CHOP/NOISE"


def _action_from_stats(best: dict[str, Any]) -> str:
    if best["n"] < 8:
        return "IGNORE_low_sample"
    if best["win%"] >= 55 and best["avg_end_Δ"] > 0 and (best["RR"] or 0) >= 1.0:
        return f"CANDIDATE_ENTER_{best['side'].upper()}"
    if best["win%"] <= 42 and best["avg_end_Δ"] < 0:
        return f"CANDIDATE_EXIT_or_FADE_{'long' if best['side']=='short' else 'short'}"
    return "HOLD_or_SKIP"


def print_report(result: dict[str, Any], top: int = 12) -> None:
    tf = result["tf"]
    print()
    print("=" * 88)
    print(f"TF={tf}  bars={result.get('n_bars', 0)}")
    print("=" * 88)

    print("\n--- Role buckets (horizon path) ---")
    print(
        f"{'bucket':32} {'side':5} {'n':>4} {'win%':>6} {'avgEnd':>8} "
        f"{'TP':>6} {'SL':>6} {'RR':>5}"
    )
    for r in result["role_rows"]:
        print(
            f"{r['bucket'][:32]:32} {r['side']:5} {r['n']:4} {r['win%']:6.1f} "
            f"{r['avg_end_Δ']:8.2f} {r['suggest_TP']:6.1f} {r['suggest_SL']:6.1f} "
            f"{str(r['RR']):>5}"
        )

    print("\n--- Best entry states (by avg end Δ) ---")
    print(
        f"{'label':14} {'side':5} {'n':>4} {'win%':>6} {'avgEnd':>8} "
        f"{'TP':>6} {'SL':>6} {'RR':>5}  action"
    )
    for r in result["entry_rows"][:top]:
        print(
            f"{r['label']:14} {r['best_side']:5} {r['n']:4} {r['win%']:6.1f} "
            f"{r['avg_end_Δ']:8.2f} {r['suggest_TP']:6.1f} {r['suggest_SL']:6.1f} "
            f"{str(r['RR']):>5}  {r['action']}"
        )

    print("\n--- Top transitions (what comes after) ---")
    print(f"{'from':14} → {'to':14} {'n':>4} {'%from':>6} {'avgΔ':>8}")
    for r in result["trans_rows"][:top]:
        print(
            f"{r['from_label']:14} → {r['to_label']:14} {r['n']:4} "
            f"{r['pct_of_from']:6.1f} {r['avg_price_Δ_on_transition']:8.2f}"
        )

    print("\n--- Per-state next-bar impact (top by n) ---")
    print(f"{'label':14} {'n':>4} {'up%':>6} {'avgΔ':>8}  role")
    for r in sorted(result["state_rows"], key=lambda x: -x["n"])[:top]:
        print(
            f"{r['label']:14} {r['n']:4} {r['next_up%']:6.1f} "
            f"{r['avg_next_Δ']:8.2f}  {r['role_hint']}"
        )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument(
        "--tf",
        default="15m,30m,1h",
        help="Comma list among 1m,2m,3m,5m,10m,15m,30m,1h",
    )
    ap.add_argument("--from-csv", default="", help="Optional single bars_*.csv")
    ap.add_argument("--horizon", type=int, default=3, help="Bars ahead for MFE/MAE")
    ap.add_argument("--eps-qty", type=float, default=1.0, help="TBQ/TSQ flat band")
    ap.add_argument("--eps-px", type=float, default=0.5, help="Price flat band (pts)")
    ap.add_argument("--min-n", type=int, default=8)
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    want = {x.strip() for x in args.tf.split(",") if x.strip()}

    if args.from_csv:
        path = Path(args.from_csv)
        rows = bars_from_csv(path)
        tf = path.stem.replace("bars_", "") or "csv"
        views = to_views(rows, eps_qty=args.eps_qty, eps_px=args.eps_px)
        result = analyze_tf(views, tf=tf, horizon=args.horizon, min_n=args.min_n)
        print_report(result)
        write_csv(out / f"{tf}_states.csv", result["state_rows"])
        write_csv(out / f"{tf}_transitions.csv", result["trans_rows"])
        write_csv(out / f"{tf}_roles.csv", result["role_rows"])
        write_csv(out / f"{tf}_entries.csv", result["entry_rows"])
        print(f"\nCSVs → {out.resolve()}")
        return

    print("Loading ticks & building bars...")
    tick_rows = load_tick_rows(Path(args.db))
    print(f"ticks={len(tick_rows)}")

    all_states, all_trans, all_roles, all_entries = [], [], [], []
    for tf, mins in INTERVALS:
        if tf not in want:
            continue
        brows = [b.to_row() for b in build_rich_bars(tick_rows, tf, mins)]
        views = to_views(brows, eps_qty=args.eps_qty, eps_px=args.eps_px)
        result = analyze_tf(views, tf=tf, horizon=args.horizon, min_n=args.min_n)
        if not result.get("n_bars"):
            continue
        print_report(result)
        write_csv(out / f"{tf}_states.csv", result["state_rows"])
        write_csv(out / f"{tf}_transitions.csv", result["trans_rows"])
        write_csv(out / f"{tf}_roles.csv", result["role_rows"])
        write_csv(out / f"{tf}_entries.csv", result["entry_rows"])
        all_states.extend(result["state_rows"])
        all_trans.extend(result["trans_rows"])
        all_roles.extend(result["role_rows"])
        all_entries.extend(result["entry_rows"])

    write_csv(out / "all_states.csv", all_states)
    write_csv(out / "all_transitions.csv", all_trans)
    write_csv(out / "all_roles.csv", all_roles)
    write_csv(out / "all_entries.csv", all_entries)

    # README
    (out / "README.txt").write_text(
        f"""State transition study
horizon={args.horizon} bars | eps_qty={args.eps_qty} | eps_px={args.eps_px}

Files per TF:
  *_states.csv       — each of 27 states → next bar up%/avg Δ
  *_transitions.csv  — from_state → to_state frequency + price impact
  *_roles.csv        — start/continue/stop families + suggested TP/SL from MFE/MAE
  *_entries.csv      — ranked states for enter long/short with data-driven TP/SL

How to read suggest_TP / suggest_SL:
  After a state prints, look horizon bars ahead.
  MFE = max favorable excursion, MAE = max adverse.
  TP ≈ 60th pct of MFE, SL ≈ 60th pct of MAE (not fixed 20/30).

Import CSVs to Google Sheets same as manual_pack.
""",
        encoding="utf-8",
    )
    print(f"\nAll CSVs → {out.resolve()}")
    print("Import data/state_study/*.csv to Google Sheets for manual review.")


if __name__ == "__main__":
    main()
