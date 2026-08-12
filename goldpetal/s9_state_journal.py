"""Append-only journal for S9 states / signals (future retune & rule adds)."""

from __future__ import annotations

import csv
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_DB = Path(__file__).resolve().parent / "data" / "s9_state_journal.db"


def init_s9_db(db_path: Path = DEFAULT_DB) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS s9_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                event TEXT NOT NULL,
                state TEXT,
                label TEXT,
                prev_state TEXT,
                ltp REAL,
                tbq REAL,
                tsq REAL,
                net REAL,
                imb_pct REAL,
                position TEXT,
                entry_ltp REAL,
                reason TEXT,
                extra_json TEXT
            )
            """
        )
        con.execute("CREATE INDEX IF NOT EXISTS idx_s9_ts ON s9_events(ts)")
        con.commit()


class S9Journal:
    def __init__(self, db_path: Path | str | None = None, enabled: bool = True) -> None:
        env = os.getenv("S9_JOURNAL_DB", "").strip()
        self.db_path = Path(db_path or env or DEFAULT_DB)
        self.enabled = enabled
        if self.enabled:
            init_s9_db(self.db_path)

    def log(
        self,
        *,
        ts: str,
        event: str,
        state: str | None = None,
        label: str | None = None,
        prev_state: str | None = None,
        ltp: float | None = None,
        tbq: float | None = None,
        tsq: float | None = None,
        net: float | None = None,
        imb_pct: float | None = None,
        position: str | None = None,
        entry_ltp: float | None = None,
        reason: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        if not self.enabled:
            return
        with sqlite3.connect(self.db_path) as con:
            con.execute(
                """
                INSERT INTO s9_events (
                    ts, event, state, label, prev_state, ltp, tbq, tsq, net,
                    imb_pct, position, entry_ltp, reason, extra_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ts,
                    event,
                    state,
                    label,
                    prev_state,
                    ltp,
                    tbq,
                    tsq,
                    net,
                    imb_pct,
                    position,
                    entry_ltp,
                    reason,
                    json.dumps(extra or {}, default=str),
                ),
            )
            con.commit()

    def export_csv(self, out: Path | str) -> int:
        out_p = Path(out)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute("SELECT * FROM s9_events ORDER BY id").fetchall()
        if not rows:
            out_p.write_text("")
            return 0
        fields = rows[0].keys()
        with out_p.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(fields))
            w.writeheader()
            for r in rows:
                w.writerow({k: r[k] for k in fields})
        return len(rows)


def s9_journal_from_env() -> S9Journal:
    try:
        from dotenv import load_dotenv

        load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
    except Exception:
        pass
    enabled = os.getenv("S9_JOURNAL", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "y",
    }
    return S9Journal(enabled=enabled)
