#!/usr/bin/env python3
"""Paper-trade performance report with charges + tax.

Shows gross PnL, charges, after-charges, tax (default 30%), after-tax.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from charges import charges_from_env
from storage import build_trades, export_trades_csv


def _summarize(strategy: str | None) -> dict:
    trades = build_trades(strategy=strategy)
    closed = [t for t in trades if str(t.get("status", "")).startswith("CLOSED")]
    open_n = sum(1 for t in trades if t.get("status") == "OPEN")

    def _f(t: dict, key: str) -> float:
        v = t.get(key, "")
        if v == "" or v is None:
            # fallback
            if key == "pnl_after_tax":
                v = t.get("net_pnl", 0) or 0
            else:
                return 0.0
        return float(v)

    gross = sum(_f(t, "gross_pnl") for t in closed)
    charges = sum(_f(t, "charges") for t in closed)
    after_ch = sum(_f(t, "pnl_after_charges") for t in closed)
    tax = sum(_f(t, "tax") for t in closed)
    after_tax = sum(_f(t, "pnl_after_tax") for t in closed)

    wins = [t for t in closed if _f(t, "pnl_after_tax") > 0]
    losses = [t for t in closed if _f(t, "pnl_after_tax") < 0]
    return {
        "strategy": strategy or "ALL",
        "trades": len(trades),
        "closed": len(closed),
        "open": open_n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": (len(wins) / len(closed) * 100.0) if closed else 0.0,
        "gross_pnl": gross,
        "charges": charges,
        "pnl_after_charges": after_ch,
        "tax": tax,
        "pnl_after_tax": after_tax,
        "avg_win": (sum(_f(t, "pnl_after_tax") for t in wins) / len(wins)) if wins else 0.0,
        "avg_loss": (sum(_f(t, "pnl_after_tax") for t in losses) / len(losses)) if losses else 0.0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Paper trading report (after charges+tax)")
    parser.add_argument("--out-dir", default="data")
    args = parser.parse_args()
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    cfg = charges_from_env()
    print("=== PAPER TRADE REPORT (Angel MCX fees + tax) ===")
    print(
        f"Brokerage ₹{cfg.brokerage_per_order}/order "
        f"{'(PROMO ₹0)' if cfg.brokerage_promo else ''} | "
        f"MCX txn {cfg.mcx_txn_rate*100:.4f}% | CTT sell {cfg.ctt_sell_rate*100:.3f}% | "
        f"stamp buy {cfg.stamp_buy_rate*100:.4f}% | GST {cfg.gst_rate*100:.0f}% | "
        f"SEBI {cfg.sebi_rate*100:.4f}% | TAX {cfg.tax_rate*100:.0f}% | "
        f"lot={cfg.lot_size} turnover_mult={cfg.turnover_mult}"
    )
    print("Mode: paper only until DRY_RUN=false + live order module")
    print()
    print(
        f"{'strategy':12} {'n':>4} {'cl':>4} {'win%':>6} "
        f"{'gross':>9} {'fees':>8} {'aftFee':>9} {'tax':>8} {'aftTax':>9}"
    )
    print("-" * 90)
    for strat in (
        "S1_NETDELTA",
        "S2_BALANCE",
        "S3_ML",
        "S4_OVERNIGHT",
        "S5_MINEDGE",
        "S6_MIN30",
        "S8_NET_ZIGZAG",
        "S9_STATE30",
        None,
    ):
        s = _summarize(strat)
        print(
            f"{s['strategy']:12} {s['trades']:4} {s['closed']:4} {s['win_rate']:6.1f} "
            f"{s['gross_pnl']:+9.2f} {s['charges']:8.2f} {s['pnl_after_charges']:+9.2f} "
            f"{s['tax']:8.2f} {s['pnl_after_tax']:+9.2f}"
        )
        name = "all" if strat is None else strat.lower()
        export_trades_csv(out / f"trades_{name}.csv", strategy=strat)

    print()
    print("CSVs include Angel fee breakup + pnl_after_tax (net_pnl).")
    print("Env: BROKERAGE_PER_ORDER, BROKERAGE_PROMO, TAX_RATE, LOT_SIZE, TURNOVER_MULT")


if __name__ == "__main__":
    main()
