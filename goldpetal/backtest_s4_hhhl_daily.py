#!/usr/bin/env python3
"""Day-by-day HH/LL notes + backtest (S12 rules on daily bars, S4-style overnight).

S12 rules (same as strategy_hhhl / backtest_hhhl_candles):
  LONG  entry: high > prev_high AND close > open
        exit:  high < prev_high AND close < open
  SHORT entry: low  < prev_low  AND close < open
        exit:  low  > prev_low  AND close > open

Modes:
  swing     — hold across days until opposite exit (pure daily S12)
  overnight — enter at day CLOSE on signal; exit at NEXT day OPEN (S4-style)

Examples (on VM with real ticks):

  python3 backtest_s4_hhhl_daily.py --db data/ticks.db --lots 1 --fees --min-range 5 --no-flip
  python3 backtest_s4_hhhl_daily.py --db data/ticks.db --lots 100 --fees --min-range 5 --no-flip
  python3 backtest_s4_hhhl_daily.py --db data/ticks.db --mode overnight --lots 1 --fees
"""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from charges import ChargeConfig, apply_charges_and_tax, zero_charge_config
from mtf_bars import build_rich_bars, load_tick_rows, parse_ts


@dataclass
class DayNote:
    date: str
    open: float
    high: float
    low: float
    close: float
    prev_high: float | None
    prev_low: float | None
    range_pts: float
    green: bool
    hh: bool
    ll: bool
    long_entry: bool
    short_entry: bool
    long_exit: bool
    short_exit: bool
    signal: str


@dataclass
class Trade:
    mode: str
    side: str
    entry_time: str
    entry_px: float
    exit_time: str
    exit_px: float
    gross_pts: float
    gross_pnl_inr: float
    after_tax_pnl_inr: float
    fees_inr: float
    lots: float
    reason: str = ""


def make_charge_cfg(*, fees: bool, lots: float) -> ChargeConfig:
    if not fees:
        return zero_charge_config(lot_size=float(lots))
    return ChargeConfig(
        brokerage_per_order=20.0,
        brokerage_promo=False,
        mcx_txn_rate=0.0000210,
        ctt_sell_rate=0.0001,
        sebi_rate=0.000001,
        stamp_buy_rate=0.00002,
        gst_rate=0.18,
        tax_rate=0.30,
        lot_size=float(lots),
        turnover_mult=1.0,
    )


def settle(side: str, entry: float, exit_px: float, *, lots: float, cfg: ChargeConfig) -> dict[str, float]:
    pts = (exit_px - entry) if side == "long" else (entry - exit_px)
    order_side = "BUY" if side == "long" else "SELL"
    settled = apply_charges_and_tax(
        pts,
        cfg,
        side=order_side,
        entry_price=entry,
        exit_price=exit_px,
    )
    return {
        "gross_pts": float(pts),
        "gross_pnl_inr": float(settled["gross_pnl"]),
        "after_tax_pnl_inr": float(settled["pnl_after_tax"]),
        "fees_inr": float(settled["charges"]) + float(settled.get("tax", 0.0)),
    }


def day_candles_from_ticks(db: Path) -> list[dict[str, Any]]:
    rows = load_tick_rows(db)
    bars = build_rich_bars(rows, "1d", 1440)
    out: list[dict[str, Any]] = []
    for b in bars:
        out.append(
            {
                "time": str(b.time),
                "date": str(b.time)[:10],
                "open": float(b.open),
                "high": float(b.high),
                "low": float(b.low),
                "close": float(b.close),
                "range_pts": float(b.high - b.low),
                "n_ticks": int(b.n_ticks),
            }
        )
    return out


def build_day_notes(days: list[dict[str, Any]], *, min_range: float) -> list[DayNote]:
    notes: list[DayNote] = []
    for i, cur in enumerate(days):
        prev = days[i - 1] if i else None
        ph = float(prev["high"]) if prev else None
        pl = float(prev["low"]) if prev else None
        o, h, l, c = cur["open"], cur["high"], cur["low"], cur["close"]
        green = c > o
        hh = ph is not None and h > ph
        ll = pl is not None and l < pl
        long_e = bool(prev) and hh and green and (h - l) >= min_range
        short_e = bool(prev) and ll and (not green) and (h - l) >= min_range
        # exit uses same candle vs prev (S12)
        long_x = bool(prev) and ph is not None and h < ph and (not green)
        short_x = bool(prev) and pl is not None and l > pl and green
        if not prev:
            sig = "warmup"
        elif long_e and short_e:
            sig = "conflict"
        elif long_e:
            sig = "LONG_ENTRY"
        elif short_e:
            sig = "SHORT_ENTRY"
        elif long_x:
            sig = "LONG_EXIT"
        elif short_x:
            sig = "SHORT_EXIT"
        else:
            sig = "hold/none"
        notes.append(
            DayNote(
                date=cur["date"],
                open=o,
                high=h,
                low=l,
                close=c,
                prev_high=ph,
                prev_low=pl,
                range_pts=float(h - l),
                green=green,
                hh=bool(hh),
                ll=bool(ll),
                long_entry=long_e,
                short_entry=short_e,
                long_exit=long_x,
                short_exit=short_x,
                signal=sig,
            )
        )
    return notes


def simulate_swing(
    days: list[dict[str, Any]],
    *,
    lots: float,
    fees: bool,
    min_range: float,
    no_flip: bool,
) -> list[Trade]:
    cfg = make_charge_cfg(fees=fees, lots=lots)
    trades: list[Trade] = []
    side: str | None = None
    entry_px = entry_t = None
    for i in range(1, len(days)):
        prev, cur = days[i - 1], days[i]
        o, h, l, c = cur["open"], cur["high"], cur["low"], cur["close"]
        ph, pl = float(prev["high"]), float(prev["low"])
        want_long = h > ph and c > o and (h - l) >= min_range
        want_short = l < pl and c < o and (h - l) >= min_range
        exit_long = h < ph and c < o
        exit_short = l > pl and c > o

        def close_trade(px: float, reason: str) -> None:
            nonlocal side, entry_px, entry_t
            assert side and entry_px is not None and entry_t is not None
            s = settle(side, entry_px, px, lots=lots, cfg=cfg)
            trades.append(
                Trade(
                    mode="swing",
                    side=side,
                    entry_time=entry_t,
                    entry_px=entry_px,
                    exit_time=cur["time"],
                    exit_px=px,
                    reason=reason,
                    lots=lots,
                    **s,
                )
            )
            side = None
            entry_px = entry_t = None

        if side == "long":
            if exit_long or (want_short and not no_flip):
                close_trade(c, "exit_long" if exit_long else "flip")
                if want_short and not want_long:
                    side, entry_px, entry_t = "short", c, cur["time"]
                elif want_long and not want_short:
                    side, entry_px, entry_t = "long", c, cur["time"]
            continue
        if side == "short":
            if exit_short or (want_long and not no_flip):
                close_trade(c, "exit_short" if exit_short else "flip")
                if want_long and not want_short:
                    side, entry_px, entry_t = "long", c, cur["time"]
                elif want_short and not want_long:
                    side, entry_px, entry_t = "short", c, cur["time"]
            continue
        if want_long and not want_short:
            side, entry_px, entry_t = "long", c, cur["time"]
        elif want_short and not want_long:
            side, entry_px, entry_t = "short", c, cur["time"]
    return trades


def simulate_overnight(
    days: list[dict[str, Any]],
    *,
    lots: float,
    fees: bool,
    min_range: float,
) -> list[Trade]:
    """S4-style: signal on day i vs day i-1 → enter at day i close → exit day i+1 open."""
    cfg = make_charge_cfg(fees=fees, lots=lots)
    trades: list[Trade] = []
    for i in range(1, len(days) - 1):
        prev, cur, nxt = days[i - 1], days[i], days[i + 1]
        o, h, l, c = cur["open"], cur["high"], cur["low"], cur["close"]
        ph, pl = float(prev["high"]), float(prev["low"])
        want_long = h > ph and c > o and (h - l) >= min_range
        want_short = l < pl and c < o and (h - l) >= min_range
        if want_long == want_short:
            continue
        side = "long" if want_long else "short"
        exit_px = float(nxt["open"])
        s = settle(side, c, exit_px, lots=lots, cfg=cfg)
        trades.append(
            Trade(
                mode="overnight",
                side=side,
                entry_time=cur["time"],
                entry_px=c,
                exit_time=nxt["time"],
                exit_px=exit_px,
                reason=f"hhll_day close→next_open ({'HH+green' if want_long else 'LL+red'})",
                lots=lots,
                **s,
            )
        )
    return trades


def summarize(trades: list[Trade]) -> dict[str, Any]:
    if not trades:
        return {
            "n_trades": 0,
            "win_rate": 0.0,
            "gross_pnl_inr": 0.0,
            "after_tax_pnl_inr": 0.0,
            "fees_inr": 0.0,
        }
    wins = sum(1 for t in trades if t.gross_pts > 0)
    return {
        "n_trades": len(trades),
        "n_long": sum(1 for t in trades if t.side == "long"),
        "n_short": sum(1 for t in trades if t.side == "short"),
        "win_rate": round(100.0 * wins / len(trades), 1),
        "gross_pnl_inr": round(sum(t.gross_pnl_inr for t in trades), 2),
        "after_tax_pnl_inr": round(sum(t.after_tax_pnl_inr for t in trades), 2),
        "fees_inr": round(sum(t.fees_inr for t in trades), 2),
        "avg_gross_pts": round(sum(t.gross_pts for t in trades) / len(trades), 2),
    }


def print_notes(notes: list[DayNote]) -> None:
    print("\n=== Day-by-day HH/LL notes (vs previous day) ===")
    hdr = (
        f"{'date':10} {'O':>8} {'H':>8} {'L':>8} {'C':>8} "
        f"{'prevH':>8} {'prevL':>8} {'rng':>6} {'HH':>3} {'LL':>3} {'sig':12}"
    )
    print(hdr)
    for n in notes:
        print(
            f"{n.date:10} {n.open:8.1f} {n.high:8.1f} {n.low:8.1f} {n.close:8.1f} "
            f"{(n.prev_high if n.prev_high is not None else float('nan')):8.1f} "
            f"{(n.prev_low if n.prev_low is not None else float('nan')):8.1f} "
            f"{n.range_pts:6.1f} "
            f"{'Y' if n.hh else '.':>3} {'Y' if n.ll else '.':>3} {n.signal:12}"
        )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=Path("data/ticks.db"))
    ap.add_argument("--mode", choices=("both", "swing", "overnight"), default="both")
    ap.add_argument("--lots", type=float, default=1.0)
    ap.add_argument("--fees", action="store_true")
    ap.add_argument("--min-range", type=float, default=5.0)
    ap.add_argument("--no-flip", action="store_true", default=True)
    ap.add_argument("--allow-flip", action="store_true", help="Disable no-flip for swing mode")
    ap.add_argument("--out", type=Path, default=Path("data/backtests/s4_hhhl_daily"))
    args = ap.parse_args()
    no_flip = not args.allow_flip

    if not args.db.exists():
        raise SystemExit(f"DB not found: {args.db}")

    days = day_candles_from_ticks(args.db)
    if len(days) < 2:
        raise SystemExit(
            f"Need ≥2 daily bars; got {len(days)} from {args.db}. "
            "Run on the VM with the live ticks.db."
        )

    notes = build_day_notes(days, min_range=args.min_range)
    print_notes(notes)
    print(
        f"\nDays={len(days)}  first={days[0]['date']}  last={days[-1]['date']}  "
        f"lots={args.lots} fees={args.fees} min_range={args.min_range} no_flip={no_flip}"
    )

    results: dict[str, Any] = {
        "db": str(args.db),
        "n_days": len(days),
        "first": days[0]["date"],
        "last": days[-1]["date"],
        "lots": args.lots,
        "fees": args.fees,
        "min_range": args.min_range,
        "notes": [asdict(n) for n in notes],
        "modes": {},
    }

    modes: list[str] = []
    if args.mode in {"both", "swing"}:
        modes.append("swing")
    if args.mode in {"both", "overnight"}:
        modes.append("overnight")

    args.out.mkdir(parents=True, exist_ok=True)
    for mode in modes:
        if mode == "swing":
            trades = simulate_swing(
                days,
                lots=args.lots,
                fees=args.fees,
                min_range=args.min_range,
                no_flip=no_flip,
            )
        else:
            trades = simulate_overnight(
                days,
                lots=args.lots,
                fees=args.fees,
                min_range=args.min_range,
            )
        summary = summarize(trades)
        results["modes"][mode] = {"summary": summary, "trades": [asdict(t) for t in trades]}
        print(f"\n=== {mode.upper()} summary ===")
        for k, v in summary.items():
            print(f"  {k}: {v}")
        csv_path = args.out / f"trades_{mode}_lots{int(args.lots)}.csv"
        with csv_path.open("w", newline="", encoding="utf-8") as fh:
            if trades:
                w = csv.DictWriter(fh, fieldnames=list(asdict(trades[0]).keys()))
                w.writeheader()
                for t in trades:
                    w.writerow(asdict(t))
        print(f"  wrote {csv_path}")

    notes_path = args.out / "day_notes.csv"
    with notes_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(asdict(notes[0]).keys()))
        w.writeheader()
        for n in notes:
            w.writerow(asdict(n))
    report = args.out / "report.json"
    report.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nWrote {notes_path}")
    print(f"Wrote {report}")
    print(
        "\nInterpretation:\n"
        "  swing     = S12 rules on daily candles (hold for days).\n"
        "  overnight = S4-style: enter day CLOSE on HH/LL signal, exit NEXT OPEN.\n"
        "If overnight after-tax is weak at 1 lot, fees dominate (same lesson as S12)."
    )


if __name__ == "__main__":
    main()
