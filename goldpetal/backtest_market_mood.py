#!/usr/bin/env python3
"""Replay mood from ticks.db. Observe only. Not a paper book.

  python3 backtest_market_mood.py --db ~/goldpetal/data/ticks.db --asof 2026-08-18T10:30:00+05:30
"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from market_mood import classify_samples
from storage import connect, init_db

IST = ZoneInfo("Asia/Kolkata")


def _parse_asof(raw: str) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    if "T" not in text and " " in text:
        text = text.replace(" ", "T", 1)
    return text


def main() -> int:
    ap = argparse.ArgumentParser(description="Classify Gold Petal mood at a time (observe only)")
    ap.add_argument("--db", type=Path, required=True)
    ap.add_argument("--asof", default="", help="IST timestamp; default = last tick")
    ap.add_argument("--limit", type=int, default=240)
    args = ap.parse_args()
    init_db(args.db)
    asof = _parse_asof(args.asof)
    with connect(args.db) as conn:
        if asof:
            rows = list(
                conn.execute(
                    """
                    SELECT received_at, ltp, bp, sp FROM ticks
                    WHERE ltp IS NOT NULL AND received_at <= ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (asof, int(args.limit)),
                )
            )
        else:
            rows = list(
                conn.execute(
                    """
                    SELECT received_at, ltp, bp, sp FROM ticks
                    WHERE ltp IS NOT NULL
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (int(args.limit),),
                )
            )
    rows.reverse()
    samples = [
        (float(r["ltp"]), float(r["bp"] or 0.0), float(r["sp"] or 0.0)) for r in rows
    ]
    st = classify_samples(samples, gate=False, flatten=False)
    last_ts = rows[-1]["received_at"] if rows else "—"
    print(f"asof={asof or last_ts}  n={st.n_samples}  ltp={samples[-1][0] if samples else '—'}")
    print(f"mood={st.mood}  dir={st.direction}  heat={st.heat}")
    print(f"label={st.label}")
    print(f"reason={st.reason}")
    print(
        f"allow_long={st.allow_long} allow_short={st.allow_short} "
        f"(observe only — MOOD_GATE not applied here)"
    )
    print(f"now={datetime.now(IST).isoformat(timespec='seconds')}")
    print("Does not ENABLE. Keep DRY_RUN=true.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
