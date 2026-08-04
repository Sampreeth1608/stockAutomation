#!/usr/bin/env python3
"""Paper-trade performance report from stored signals / trade journal."""

from __future__ import annotations

import argparse
from pathlib import Path

from storage import build_trades, export_trades_csv


def _summarize(strategy: str | None) -> dict:
    trades = build_trades(strategy=strategy)
    closed = [t for t in trades if str(t.get("status", "")).startswith("CLOSED")]
    wins = [t for t in closed if t.get("net_pnl") != "" and float(t["net_pnl"]) > 0]
    losses = [t for t in closed if t.get("net_pnl") != "" and float(t["net_pnl"]) < 0]
    pnl = sum(float(t["net_pnl"]) for t in closed if t.get("net_pnl") != "")
    open_n = sum(1 for t in trades if t.get("status") == "OPEN")
    return {
        "strategy": strategy or "ALL",
        "trades": len(trades),
        "closed": len(closed),
        "open": open_n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": (len(wins) / len(closed) * 100.0) if closed else 0.0,
        "net_pnl": pnl,
        "avg_win": (sum(float(t["net_pnl"]) for t in wins) / len(wins)) if wins else 0.0,
        "avg_loss": (sum(float(t["net_pnl"]) for t in losses) / len(losses)) if losses else 0.0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Paper trading report")
    parser.add_argument("--out-dir", default="data")
    args = parser.parse_args()
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    print("=== PAPER TRADE REPORT (DRY_RUN journal) ===")
    print("Mode: paper only until DRY_RUN=false + live order module")
    print()
    rows = []
    for strat in ("S1_NETDELTA", "S2_BALANCE", "S3_ML", "S4_OVERNIGHT", None):
        s = _summarize(strat)
        rows.append(s)
        print(
            f"{s['strategy']:12} trades={s['trades']:4} closed={s['closed']:4} "
            f"open={s['open']} win%={s['win_rate']:.1f} "
            f"pnl={s['net_pnl']:+.2f} avgW={s['avg_win']:+.2f} avgL={s['avg_loss']:+.2f}"
        )
        name = "all" if strat is None else strat.lower()
        export_trades_csv(out / f"trades_{name}.csv", strategy=strat)

    print()
    print("CSVs refreshed under", out)
    print("Strategies only open when regime allows them (see portfolio.py).")
    print("Keep DRY_RUN=true until paper pnl is consistently positive.")


if __name__ == "__main__":
    main()
