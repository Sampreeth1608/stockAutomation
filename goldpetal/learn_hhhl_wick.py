#!/usr/bin/env python3
"""Walk-forward gates for S14 (30m wick raw_strict) and 1h HH/LL+wick AND.

This is not a neural net. Two weeks of Gold Petal ticks cannot train one.
A net would memorize Aug 4–14 and fail next week.

What this does instead:
  For each test day, pick the gate combo that won on *earlier* days only,
  then score that gate on the test day (out of sample).
  If OOS is worse than the current paper defaults, do not change S14.

Grid (small on purpose):
  min_range  5 / 15 / 30
  min_frac   0 / 0.5          (wick must be ≥ half the bar)
  min_diff   0 / 10           (winning wick minus losing wick)
  skip open  no / after 10:00
  skip late  no / before 21:00

  python3 learn_hhhl_wick.py --db data/ticks.db --lots 100 --session --fees

Not live. Do not paper a learned gate unless OOS beats the default
and you still have ≥ ~20 OOS trades.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from itertools import product
from pathlib import Path
from typing import Any

from backtest_hhhl_candles import Candle, Trade, make_charge_cfg
from backtest_hhhl_wick import simulate_combo
from backtest_wick_candles import build_ohlc_candles, load_ltp_rows

BOOKS: list[dict[str, Any]] = [
    {
        "name": "30m:wick_raw_strict",
        "minutes": 30,
        "join": "wick",
        "exit_strict": True,
        "exit_mode": "either",
        "nowick_only": False,
    },
    {
        "name": "1h:and_either_raw_strict",
        "minutes": 60,
        "join": "and",
        "exit_strict": True,
        "exit_mode": "either",
        "nowick_only": False,
    },
]


@dataclass(frozen=True)
class Gate:
    min_range: float
    min_frac: float
    min_diff: float
    entry_after: str | None
    entry_before: str | None

    @property
    def label(self) -> str:
        after = self.entry_after or "-"
        before = self.entry_before or "-"
        return (
            f"rng={self.min_range:.0f} frac={self.min_frac:.1f} "
            f"diff={self.min_diff:.0f} after={after} before={before}"
        )


def gate_grid() -> list[Gate]:
    rows: list[Gate] = []
    for rng, frac, diff, skip_open, skip_late in product(
        (5.0, 15.0, 30.0),
        (0.0, 0.5),
        (0.0, 10.0),
        (False, True),
        (False, True),
    ):
        rows.append(
            Gate(
                min_range=rng,
                min_frac=frac,
                min_diff=diff,
                entry_after="10:00" if skip_open else None,
                entry_before="21:00" if skip_late else None,
            )
        )
    return rows


def default_gate() -> Gate:
    return Gate(5.0, 0.0, 0.0, None, None)


def unique_days(candles: list[Candle]) -> list[str]:
    out: list[str] = []
    for c in candles:
        if c.day not in out:
            out.append(c.day)
    return out


def trades_on_days(trades: list[Trade], days: set[str]) -> list[Trade]:
    return [t for t in trades if t.entry_time[:10] in days]


def score_trades(trades: list[Trade]) -> dict[str, float]:
    pnl = sum(t.after_tax_pnl_inr for t in trades)
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in trades:
        equity += t.after_tax_pnl_inr
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    n = len(trades)
    wins = sum(1 for t in trades if t.gross_pnl_inr > 0)
    return {
        "n": float(n),
        "pnl": float(pnl),
        "dd": float(max_dd),
        "win": (wins / n) if n else 0.0,
    }


def pick_gate(
    by_gate: dict[Gate, list[Trade]],
    train_days: set[str],
    *,
    min_trades: int = 4,
) -> Gate:
    """Highest train PnL; require a few trades; tie-break lower DD then default."""
    ranked: list[tuple[float, float, int, Gate]] = []
    for i, gate in enumerate(by_gate):
        sc = score_trades(trades_on_days(by_gate[gate], train_days))
        if sc["n"] < min_trades:
            continue
        # sort key: -pnl, +dd, default-first
        is_default = 0 if gate == default_gate() else 1
        ranked.append((-sc["pnl"], sc["dd"], is_default, gate))
    if not ranked:
        return default_gate()
    ranked.sort()
    return ranked[0][3]


def run_book(
    candles: list[Candle],
    book: dict[str, Any],
    gate: Gate,
    *,
    lots: float,
    fees: bool,
    session_filter: bool,
) -> list[Trade]:
    cfg = make_charge_cfg(fees=fees, lots=lots)
    r = simulate_combo(
        candles,
        tf=str(book["name"]),
        lots=lots,
        fees=fees,
        session_filter=session_filter,
        min_range=gate.min_range,
        min_frac=gate.min_frac,
        min_diff=gate.min_diff,
        entry_after=gate.entry_after,
        entry_before=gate.entry_before,
        join=book["join"],
        exit_strict=book["exit_strict"],
        exit_mode=book["exit_mode"],
        nowick_only=book["nowick_only"],
        charge_cfg=cfg,
    )
    return r.trades


def walk_forward(
    candles: list[Candle],
    book: dict[str, Any],
    *,
    lots: float,
    fees: bool,
    session_filter: bool,
    min_train_days: int = 4,
) -> dict[str, Any]:
    days = unique_days(candles)
    gates = gate_grid()
    by_gate: dict[Gate, list[Trade]] = {}
    for g in gates:
        by_gate[g] = run_book(
            candles, book, g, lots=lots, fees=fees, session_filter=session_filter
        )

    baseline = by_gate[default_gate()]
    folds: list[dict[str, Any]] = []
    oos_learned: list[Trade] = []
    oos_base: list[Trade] = []
    votes: dict[str, int] = {}

    for i in range(min_train_days, len(days)):
        train = set(days[:i])
        test_day = days[i]
        chosen = pick_gate(by_gate, train)
        votes[chosen.label] = votes.get(chosen.label, 0) + 1
        learned = trades_on_days(by_gate[chosen], {test_day})
        base = trades_on_days(baseline, {test_day})
        oos_learned.extend(learned)
        oos_base.extend(base)
        ls = score_trades(learned)
        bs = score_trades(base)
        folds.append(
            {
                "test_day": test_day,
                "chosen": chosen.label,
                "oos_n": int(ls["n"]),
                "oos_pnl": round(ls["pnl"], 1),
                "base_n": int(bs["n"]),
                "base_pnl": round(bs["pnl"], 1),
            }
        )

    learned_sc = score_trades(oos_learned)
    base_sc = score_trades(oos_base)
    in_sample = pick_gate(by_gate, set(days), min_trades=4)
    return {
        "book": book["name"],
        "n_days": len(days),
        "n_folds": len(folds),
        "in_sample_winner": in_sample.label,
        "in_sample_full": score_trades(by_gate[in_sample]),
        "default_full": score_trades(baseline),
        "oos_learned": learned_sc,
        "oos_default": base_sc,
        "beats_default_oos": learned_sc["pnl"] > base_sc["pnl"]
        and learned_sc["n"] >= 8,
        "votes": votes,
        "folds": folds,
    }


def print_report(rep: dict[str, Any]) -> None:
    print()
    print(f"=== {rep['book']}  walk-forward ({rep['n_folds']} test days) ===")
    print(f"in-sample winner (ignore for live): {rep['in_sample_winner']}")
    print(
        f"full default    n={rep['default_full']['n']:.0f}  "
        f"pnl={rep['default_full']['pnl']:+.0f}  dd={rep['default_full']['dd']:.0f}"
    )
    print(
        f"full in-sample  n={rep['in_sample_full']['n']:.0f}  "
        f"pnl={rep['in_sample_full']['pnl']:+.0f}  dd={rep['in_sample_full']['dd']:.0f}"
        "  ← this number lies"
    )
    print(
        f"OOS default     n={rep['oos_default']['n']:.0f}  "
        f"pnl={rep['oos_default']['pnl']:+.0f}  dd={rep['oos_default']['dd']:.0f}"
    )
    print(
        f"OOS learned     n={rep['oos_learned']['n']:.0f}  "
        f"pnl={rep['oos_learned']['pnl']:+.0f}  dd={rep['oos_learned']['dd']:.0f}"
    )
    print(
        "verdict: "
        + (
            "learned gate beats default OOS — discuss, do not auto-paper"
            if rep["beats_default_oos"]
            else "do NOT change paper — learned OOS does not beat default"
        )
    )
    print("folds:")
    for f in rep["folds"]:
        print(
            f"  {f['test_day']}  pick={f['chosen']}  "
            f"oos={f['oos_pnl']:+.0f}({f['oos_n']}t)  "
            f"base={f['base_pnl']:+.0f}({f['base_n']}t)"
        )
    print()


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Walk-forward gates for wick / HH/LL+wick (not a neural net)."
    )
    ap.add_argument("--db", type=Path, default=Path("data/ticks.db"))
    ap.add_argument("--lots", type=float, default=100.0)
    ap.add_argument("--no-fees", action="store_true")
    ap.add_argument("--no-session", action="store_true")
    ap.add_argument("--min-train-days", type=int, default=4)
    ap.add_argument("--out", type=Path, default=Path("data/backtests/hhhl_wick_learn.json"))
    args = ap.parse_args()
    fees = not args.no_fees
    session_filter = not args.no_session
    rows = load_ltp_rows(args.db)
    if not rows:
        raise SystemExit(f"no ticks in {args.db}")
    print(
        f"walk-forward gates  lots={args.lots} fees={fees} "
        f"session={session_filter}  (not a neural net)",
        flush=True,
    )
    reports = []
    cache: dict[int, list[Candle]] = {}
    for book in BOOKS:
        mins = int(book["minutes"])
        if mins not in cache:
            cache[mins] = build_ohlc_candles(rows, mins)
            print(f"built {book['name'].split(':')[0]} bars={len(cache[mins])}", flush=True)
        rep = walk_forward(
            cache[mins],
            book,
            lots=args.lots,
            fees=fees,
            session_filter=session_filter,
            min_train_days=args.min_train_days,
        )
        print_report(rep)
        reports.append(rep)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(reports, indent=2, default=str), encoding="utf-8")
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
