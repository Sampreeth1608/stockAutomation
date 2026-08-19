#!/usr/bin/env python3
"""AI Strategy Research Factory CLI — research only. Not paper. Not live.

Discovers relationship atoms, composes genomes, backtests them on the
same engine as flow_lab, walk-forward + robustness, then REJECT or
write a pending Lab proposal. Never ENABLE_*. Never DRY_RUN=false.

  ./venv/bin/python backtest_research_factory.py --db data/ticks.db --minutes 60 --lots 100 --fees
  ./venv/bin/python backtest_research_factory.py --csv /tmp/goldpetal_1h_oi.csv --lots 100 --fees
  ./venv/bin/python backtest_research_factory.py --db data/ticks.db --propose

Run from a git worktree on the VM. Do not checkout over the paper bot.
Two weeks of ticks will usually yield 0 proposals — that is the gate working.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from backtest_flow_lab import _load_bars
from flow_lab import FlowParams, session_bars, tape_flags
from mtf_bars import load_tick_rows
from research_factory import LAB_NAME, run_research_lab, run_research_lab_multi


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default="data/ticks.db")
    ap.add_argument("--csv", type=Path, default=None)
    ap.add_argument("--csv-30m", type=Path, default=None)
    ap.add_argument("--ticks", type=Path, default=None)
    ap.add_argument("--upstox-json", type=Path, default=None)
    ap.add_argument("--upstox-json-30m", type=Path, default=None)
    ap.add_argument("--cache-csv", type=Path, default=None)
    ap.add_argument("--minutes", type=int, default=60)
    ap.add_argument("--lookback", type=int, default=20)
    ap.add_argument("--atr-n", type=int, default=14)
    ap.add_argument("--lots", type=float, default=100.0)
    ap.add_argument("--fees", action="store_true", default=True)
    ap.add_argument("--no-fees", action="store_true")
    ap.add_argument("--session", action="store_true", default=True)
    ap.add_argument("--no-session", action="store_true")
    ap.add_argument("--folds", type=int, default=3)
    ap.add_argument("--max-compose", type=int, default=24)
    ap.add_argument("--skip-recipes", action="store_true")
    ap.add_argument("--no-robustness", action="store_true")
    ap.add_argument("--fast", action="store_true", help="smaller compose list; same strong gates")
    ap.add_argument("--all-tf", action="store_true", help="every lab rung 3m…daily vs same-TF S16/S18")
    ap.add_argument("--propose", action="store_true", help="write pending Lab rows (not ENABLE)")
    args = ap.parse_args()
    fees = bool(args.fees) and not bool(args.no_fees)
    session_filter = bool(args.session) and not bool(args.no_session)
    params = FlowParams(lookback=int(args.lookback), atr_n=int(args.atr_n))

    hours, _m30, source = _load_bars(args)
    if session_filter:
        hours = session_bars(hours)
    flags = tape_flags(hours)
    print(LAB_NAME, "(research laboratory — not a paper book, not live)")
    print(
        "Discover → compose → backtest → walk-forward → robustness → "
        "REJECT or pending approval. LLM does not BUY."
    )
    if args.all_tf:
        print("Multi-TF: 3m…3h45 + daily. Same-TF vs S16/S18; daily vs S13.", flush=True)
    print(
        f"tape book={flags['book']} l1={flags['l1']} oi={flags['oi']} "
        f"volume={flags['volume']} source={source}",
        flush=True,
    )
    lab_kw = dict(
        lots=float(args.lots),
        fees=fees,
        n_folds=int(args.folds),
        params=params,
        include_recipes=not bool(args.skip_recipes),
        max_compose=8 if args.fast else int(args.max_compose),
        robustness=not bool(args.no_robustness),
        sklearn=True,
        strong=True,
        screen_first=True,
        propose=bool(args.propose),
    )
    if args.all_tf:
        dbp = Path(args.db)
        if not dbp.exists():
            raise SystemExit(f"--all-tf needs ticks.db at {dbp}")
        tick_rows = list(load_tick_rows(dbp))
        result = run_research_lab_multi(tick_rows, **lab_kw)
    else:
        need = max(int(args.lookback), int(args.atr_n)) + 5
        if len(hours) < need:
            raise SystemExit(
                f"need ≥{need} bars (lookback={args.lookback}), got {len(hours)} from {source}"
            )
        print(
            f"lots={args.lots:g} fees={fees} session={session_filter} "
            f"bars={len(hours)} {hours[0].time}→{hours[-1].time} folds={args.folds}",
            flush=True,
        )
        result = run_research_lab(hours, **lab_kw)
    counts = result.get("counts") or {}
    champs = result.get("champions") or {}
    s16 = champs.get("S16") or {}
    s18 = champs.get("S18") or {}
    print()
    print("AI RESEARCH LAB")
    print(f"New strategies found this run:     {counts.get('found', 0)}")
    print(f"Passed validation:                 {counts.get('passed_validation', 0)}")
    print(f"Awaiting approval:                 {counts.get('awaiting_approval', 0)}")
    print(f"Rejected:                          {counts.get('rejected', 0)}")
    print()
    print(
        f"CHAMPION  S16  n={s16.get('n_trades', 0)}  AC₹={float(s16.get('after_charges') or 0):.1f}"
    )
    if s18:
        print(
            f"CHAMPION  S18  n={s18.get('n_trades', 0)}  AC₹={float(s18.get('after_charges') or 0):.1f}"
        )
    print()
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    for row in (result.get("challengers") or [])[:8]:
        g = row.get("genome") or {}
        m = row.get("metrics") or {}
        print(f"{g.get('name')}")
        print(
            f"  OOS AC₹={float(m.get('after_charges') or 0):.1f}  "
            f"PF={float(m.get('profit_factor') or 0):.2f}  "
            f"n={m.get('n_trades', 0)}  wf={m.get('stability')}  "
            f"Status 🟡 APPROVAL"
        )
        print(f"  {(row.get('supervisor') or '')[:200]}")
        print()
    if not (result.get("challengers") or []):
        print("No challenger beat S16 and S18 after charges with ≥20 trades,")
        print("walk-forward, 2×/3× costs, and holdout. Nothing proposed. That is correct.")
        print()
        rejected = result.get("rejected") or []
        if rejected:
            print("Top rejected (after charges):")
            for row in rejected[:8]:
                g = row.get("genome") or {}
                m = row.get("metrics") or {}
                fails = row.get("fails") or []
                print(
                    f"  {g.get('name')}: n={m.get('n_trades', 0)} "
                    f"AC₹={float(m.get('after_charges') or 0):.1f} "
                    f"wf={m.get('stability')}  {'; '.join(fails)[:120]}"
                )
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    if args.propose:
        ids = result.get("proposed_ids") or []
        print(f"--propose wrote {len(ids)} pending Lab row(s). Approve on the station Lab tab.")
        print("Approve does not ENABLE the book. Keep DRY_RUN=true.")
    else:
        print("Re-run with --propose to write pending Lab rows (still not ENABLE).")
    print("data/research/library.json updated. Do not paper. Do not live-unlock.")


if __name__ == "__main__":
    main()
