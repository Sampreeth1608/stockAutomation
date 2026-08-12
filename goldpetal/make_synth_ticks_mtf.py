#!/usr/bin/env python3
"""Build a synthetic ticks.db for MTF ALIGN smoke tests (NOT real market data).

Creates clear bull/bear align waves with supported pullbacks so ALIGN fires.

  python3 make_synth_ticks_mtf.py --out data/synth_ticks_mtf.db
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from storage import init_db

IST = ZoneInfo("Asia/Kolkata")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="data/synth_ticks_mtf.db")
    ap.add_argument("--hours", type=float, default=6.0)
    ap.add_argument("--every-sec", type=float, default=2.0)
    args = ap.parse_args()

    out = Path(args.out)
    if out.exists():
        out.unlink()
    init_db(out)

    start = datetime(2026, 8, 10, 9, 0, 0, tzinfo=IST)
    n = int(args.hours * 3600 / args.every_sec)
    px = 10000.0
    tbq = 12000.0
    tsq = 9000.0

    rows = []
    for i in range(n):
        t = start + timedelta(seconds=i * args.every_sec)
        # Cycle ~25 minutes: widen → supported dip → resume (×2 bull, then bear)
        cycle = (i // 30) % 40  # ~1 min blocks in a 40-block theme
        theme = (i // (30 * 40)) % 4

        if theme in (0, 2):  # bull themes
            if cycle < 12:  # widen bull
                px += 1.2
                tbq += 80
                tsq += 5
            elif cycle < 20:  # supported dip
                px -= 1.0
                tbq += 70
                tsq += 8
            elif cycle < 32:  # resume widen
                px += 1.4
                tbq += 90
                tsq += 5
            else:  # mild chop
                px += 0.1 if i % 2 == 0 else -0.1
                tbq += 10
                tsq += 10
        else:  # bear themes
            if cycle < 12:
                px -= 1.2
                tsq += 80
                tbq += 5
            elif cycle < 20:
                px += 1.0
                tsq += 70
                tbq += 8
            elif cycle < 32:
                px -= 1.4
                tsq += 90
                tbq += 5
            else:
                px += 0.1 if i % 2 == 0 else -0.1
                tbq += 10
                tsq += 10

        tbq = max(2000.0, tbq)
        tsq = max(2000.0, tsq)
        raw = {
            "last_traded_price": px,
            "total_buy_quantity": tbq,
            "total_sell_quantity": tsq,
            "last_traded_quantity": 1.0,
            "volume_trade_for_the_day": float(i + 1),
        }
        rows.append(
            (
                t.isoformat(),
                None,
                "GOLDPETAL",
                "SYNTH",
                px,
                None,
                None,
                None,
                None,
                i + 1,
                tbq,
                tsq,
                json.dumps(raw),
            )
        )

    con = sqlite3.connect(out)
    con.executemany(
        """
        INSERT INTO ticks (
            received_at, exchange_timestamp, symbol, token,
            ltp, open, high, low, close, volume, bp, sp, raw_json
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        rows,
    )
    con.commit()
    con.close()
    print(f"wrote {out} ticks={n} hours={args.hours} every_sec={args.every_sec}")


if __name__ == "__main__":
    main()
