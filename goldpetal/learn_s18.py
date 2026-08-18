#!/usr/bin/env python3
"""Improve S18 from stored ticks. Promotes a better pack for paper (not live).

Uses 1h OHLC, previous 1h OHLC, volume, yesterday, wick, n_ticks, and TBQ/TSQ
net from ticks.db. Time-split OOS after-tax. Writes data/learn/s18/active.json
only if the winner beats the current pack on later bars after fees.
Paper S18 loads that pack on RESTART. Never live. Never changes S13/S16.

  ./venv/bin/python learn_s18.py --db data/ticks.db --lots 100 --session --fees
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from s18_ohlc_vol_htf import (
    BASE_PACK,
    S18Pack,
    build_vol_bars,
    load_active_pack,
    load_vol_rows,
    simulate_s18,
)

IST = ZoneInfo("Asia/Kolkata")


def _clone(name: str, **kw: Any) -> S18Pack:
    d = BASE_PACK.as_dict()
    d.update(kw)
    d["name"] = name
    return S18Pack(**d)


def candidate_packs() -> list[S18Pack]:
    """Base AND, drop one core leg, or add tick/prev-OHLC confirms. One book."""
    out: list[S18Pack] = [BASE_PACK]
    drops = (
        ("drop_vol", {"require_vol_up": False}),
        ("drop_day", {"require_vs_day": False}),
        ("drop_hhll", {"require_hh_ll": False}),
        ("drop_c_prev", {"require_c_vs_prev": False}),
        ("drop_body", {"require_green_red": False}),
    )
    for name, kw in drops:
        out.append(_clone(name, **kw))
    extras = (
        ("plus_wick", {"require_wick_agree": True}),
        ("plus_net", {"require_net_confirm": True}),
        ("plus_ticks", {"require_ticks_up": True}),
        ("plus_beyond_hl", {"require_beyond_prev_hl": True}),
        ("plus_range", {"require_range_up": True}),
        ("plus_tbq", {"require_tbq_lead": True}),
    )
    for name, kw in extras:
        out.append(_clone(name, **kw))
    pairs = (
        (
            "plus_wick_net",
            {"require_wick_agree": True, "require_net_confirm": True},
        ),
        (
            "plus_net_ticks",
            {"require_net_confirm": True, "require_ticks_up": True},
        ),
        (
            "tick_full",
            {
                "require_wick_agree": True,
                "require_net_confirm": True,
                "require_ticks_up": True,
                "require_tbq_lead": True,
            },
        ),
        (
            "drop_vol_plus_net",
            {"require_vol_up": False, "require_net_confirm": True},
        ),
        (
            "drop_day_plus_beyond",
            {"require_vs_day": False, "require_beyond_prev_hl": True},
        ),
    )
    for name, kw in pairs:
        out.append(_clone(name, **kw))
    out.append(
        S18Pack(
            name="price_only",
            require_green_red=True,
            require_c_vs_prev=True,
            require_hh_ll=True,
            require_vol_up=False,
            require_vs_day=False,
        )
    )
    out.append(
        S18Pack(
            name="price_vol",
            require_green_red=True,
            require_c_vs_prev=True,
            require_hh_ll=True,
            require_vol_up=True,
            require_vs_day=False,
        )
    )
    out.append(
        S18Pack(
            name="price_day",
            require_green_red=True,
            require_c_vs_prev=True,
            require_hh_ll=True,
            require_vol_up=False,
            require_vs_day=True,
        )
    )
    seen: set[str] = set()
    uniq: list[S18Pack] = []
    for p in out:
        if p.name in seen:
            continue
        seen.add(p.name)
        uniq.append(p)
    return uniq


def _score(result: Any) -> dict[str, Any]:
    return {
        "n_trades": int(getattr(result, "n_trades", 0) or 0),
        "win_rate": round(100.0 * float(getattr(result, "win_rate", 0) or 0), 1),
        "after_tax_inr": round(float(getattr(result, "after_tax_pnl_inr", 0) or 0), 1),
        "gross_pts": round(float(getattr(result, "gross_pts", 0) or 0), 1),
        "max_dd_inr": round(float(getattr(result, "max_dd_inr", 0) or 0), 1),
    }


def _run_pack(
    pack: S18Pack,
    train_h: list,
    test_h: list,
    days: list,
    *,
    lots: float,
    fees: bool,
    session: bool,
) -> dict[str, Any]:
    tr = simulate_s18(
        train_h, days, lots=lots, fees=fees, session_filter=session, pack=pack
    )
    te = simulate_s18(
        test_h, days, lots=lots, fees=fees, session_filter=session, pack=pack
    )
    return {
        "pack": pack.as_dict(),
        "paper": True,
        "live": False,
        "train": _score(tr),
        "test": _score(te),
    }


def learn(
    db: Path,
    *,
    lots: float,
    fees: bool,
    session: bool,
    train_frac: float,
    min_test_trades: int,
    out_dir: Path,
    pack_path: Path | None = None,
) -> dict[str, Any]:
    dest = pack_path or (out_dir / "active.json")
    rows = load_vol_rows(db)
    hours = build_vol_bars(rows, 60)
    days = build_vol_bars(rows, 1440)
    cut = max(2, int(len(hours) * train_frac))
    if cut >= len(hours):
        cut = max(1, len(hours) - 1)
    train_h, test_h = hours[:cut], hours[cut:]
    current = load_active_pack(dest)
    packs = candidate_packs()
    if current.name not in {p.name for p in packs}:
        packs.insert(0, current)
    ranked: list[dict[str, Any]] = []
    for pack in packs:
        ranked.append(
            _run_pack(
                pack,
                train_h,
                test_h,
                days,
                lots=lots,
                fees=fees,
                session=session,
            )
        )
    ranked.sort(
        key=lambda r: (
            -float(r["test"]["after_tax_inr"]),
            -int(r["test"]["n_trades"]),
        )
    )
    eligible = [
        r for r in ranked if int(r["test"]["n_trades"]) >= min_test_trades
    ]
    winner = eligible[0] if eligible else None
    cur_te = next((r for r in ranked if r["pack"]["name"] == current.name), None)
    if cur_te is None:
        cur_te = _run_pack(
            current,
            train_h,
            test_h,
            days,
            lots=lots,
            fees=fees,
            session=session,
        )
        ranked.append(cur_te)
    promoted = False
    active = current.as_dict()
    note = "kept current pack"
    if winner is not None:
        win_pnl = float(winner["test"]["after_tax_inr"])
        cur_pnl = float(cur_te["test"]["after_tax_inr"])
        if win_pnl > cur_pnl:
            active = winner["pack"]
            promoted = winner["pack"]["name"] != current.name
            note = (
                f"promoted {winner['pack']['name']} "
                f"test_pnl={winner['test']['after_tax_inr']} "
                f"(was {current.name} {cur_pnl})"
            )
    payload = {
        "paper": True,
        "live": False,
        "ticks": len(rows),
        "hours": len(hours),
        "days": len(days),
        "train_bars": len(train_h),
        "test_bars": len(test_h),
        "pack": active,
        "promoted": promoted,
        "note": note,
        "updated_at": datetime.now(IST).isoformat(timespec="seconds"),
        "ranked": ranked[:16],
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    latest = out_dir / "latest.json"
    latest.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        json.dumps(
            {
                "paper": True,
                "live": False,
                "pack": active,
                "note": note,
                "updated_at": payload["updated_at"],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    payload["wrote"] = [str(latest), str(dest)]
    return payload


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default="data/ticks.db")
    ap.add_argument("--lots", type=float, default=100.0)
    ap.add_argument("--fees", action="store_true")
    ap.add_argument("--session", action="store_true")
    ap.add_argument("--train-frac", type=float, default=0.7)
    ap.add_argument("--min-test-trades", type=int, default=2)
    ap.add_argument("--out-dir", default="data/learn/s18")
    args = ap.parse_args()
    print("S18 learner (paper packs only, not live)")
    out_dir = Path(args.out_dir)
    rep = learn(
        Path(args.db),
        lots=float(args.lots),
        fees=bool(args.fees),
        session=bool(args.session),
        train_frac=float(args.train_frac),
        min_test_trades=int(args.min_test_trades),
        out_dir=out_dir,
        pack_path=out_dir / "active.json",
    )
    print(
        f"ticks={rep.get('ticks')} hours={rep.get('hours')} "
        f"promoted={rep.get('promoted')} pack={rep.get('pack', {}).get('name')} "
        f"note={rep.get('note')}"
    )
    for row in (rep.get("ranked") or [])[:8]:
        te = row["test"]
        print(
            f"  {row['pack']['name']:<18} test_n={te['n_trades']:<3} "
            f"win={te['win_rate']:<5} after₹={te['after_tax_inr']}"
        )
    print("wrote", ", ".join(rep.get("wrote") or []))
    print("Type RESTART on the station to load a promoted pack. Keep DRY_RUN=true.")


if __name__ == "__main__":
    main()
