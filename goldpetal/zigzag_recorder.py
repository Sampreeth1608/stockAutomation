"""Record S8 zigzag observations for future retune / model rebuild.

Stores timestamp, price (ltp), TBQ, TSQ (+ derived NET/IMB), action, and
context so we can re-sweep TP/SL/imb/weaken later without guessing.

Tables (in data/zigzag_retune.db by default):
  - zigzag_events : BUY / SHORT / CLOSE / SNAP
  - zigzag_params : config snapshot at process start
"""

from __future__ import annotations

import csv
import json
import os
import sqlite3
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_DB = Path(__file__).resolve().parent / "data" / "zigzag_retune.db"


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    return con


def init_zigzag_db(db_path: Path = DEFAULT_DB) -> None:
    with _connect(db_path) as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS zigzag_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                exchange_timestamp INTEGER,
                symbol TEXT,
                action TEXT NOT NULL,
                side TEXT,
                ltp REAL NOT NULL,
                tbq REAL,
                tsq REAL,
                net REAL,
                imb_pct REAL,
                position TEXT,
                entry_ltp REAL,
                unrealized_pts REAL,
                reason TEXT,
                params_json TEXT,
                extra_json TEXT
            )
            """
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_zigzag_ts ON zigzag_events(ts)"
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_zigzag_action ON zigzag_events(action)"
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS zigzag_params (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL,
                params_json TEXT NOT NULL
            )
            """
        )
        con.commit()


class ZigzagRecorder:
    """Append-only recorder for retune dataset."""

    def __init__(
        self,
        db_path: Path | str | None = None,
        *,
        symbol: str = "GOLDPETAL",
        snap_every_n: int = 25,
        enabled: bool = True,
    ) -> None:
        env_path = os.getenv("S8_RETUNE_DB", "").strip()
        self.db_path = Path(db_path or env_path or DEFAULT_DB)
        self.symbol = symbol
        self.snap_every_n = max(0, int(snap_every_n))
        self.enabled = enabled
        self._tick_i = 0
        self._params_json = "{}"
        if self.enabled:
            init_zigzag_db(self.db_path)

    def record_params(self, params: Any) -> None:
        if not self.enabled:
            return
        if is_dataclass(params) and not isinstance(params, type):
            payload = asdict(params)
        elif isinstance(params, dict):
            payload = params
        else:
            payload = {"repr": repr(params)}
        self._params_json = json.dumps(payload, default=str)
        with _connect(self.db_path) as con:
            con.execute(
                "INSERT INTO zigzag_params (started_at, params_json) VALUES (?, ?)",
                (datetime.now().isoformat(timespec="seconds"), self._params_json),
            )
            con.commit()

    def log(
        self,
        *,
        ts: str,
        action: str,
        ltp: float,
        tbq: float | None,
        tsq: float | None,
        net: float | None = None,
        imb_pct: float | None = None,
        side: str | None = None,
        position: str | None = None,
        entry_ltp: float | None = None,
        unrealized_pts: float | None = None,
        reason: str | None = None,
        exchange_timestamp: int | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        if not self.enabled:
            return
        if net is None and tbq is not None and tsq is not None:
            net = float(tbq) - float(tsq)
        if imb_pct is None and tbq is not None and tsq is not None:
            denom = max(float(tbq), float(tsq), 1e-9)
            imb_pct = abs(float(net or 0.0)) / denom * 100.0
        with _connect(self.db_path) as con:
            con.execute(
                """
                INSERT INTO zigzag_events (
                    ts, exchange_timestamp, symbol, action, side,
                    ltp, tbq, tsq, net, imb_pct, position, entry_ltp,
                    unrealized_pts, reason, params_json, extra_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ts,
                    exchange_timestamp,
                    self.symbol,
                    action,
                    side,
                    float(ltp),
                    tbq,
                    tsq,
                    net,
                    imb_pct,
                    position,
                    entry_ltp,
                    unrealized_pts,
                    reason,
                    self._params_json,
                    json.dumps(extra or {}, default=str),
                ),
            )
            con.commit()

    def on_tick_snapshot(
        self,
        *,
        ts: str,
        ltp: float,
        tbq: float | None,
        tsq: float | None,
        position: str,
        entry_ltp: float | None,
        exchange_timestamp: int | None = None,
    ) -> None:
        """Periodic SNAP while in a trade (for path / MFE-MAE style retune)."""
        if not self.enabled or self.snap_every_n <= 0:
            return
        if position == "flat":
            return
        self._tick_i += 1
        if self._tick_i % self.snap_every_n != 0:
            return
        unreal = None
        if entry_ltp is not None:
            if position == "long":
                unreal = float(ltp) - float(entry_ltp)
            elif position == "short":
                unreal = float(entry_ltp) - float(ltp)
        self.log(
            ts=ts,
            action="SNAP",
            ltp=ltp,
            tbq=tbq,
            tsq=tsq,
            side=position,
            position=position,
            entry_ltp=entry_ltp,
            unrealized_pts=unreal,
            reason="in_trade_snapshot",
            exchange_timestamp=exchange_timestamp,
        )

    def export_csv(self, out_path: Path | str) -> int:
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with _connect(self.db_path) as con:
            rows = con.execute(
                "SELECT * FROM zigzag_events ORDER BY id ASC"
            ).fetchall()
        if not rows:
            out.write_text("")
            return 0
        fields = rows[0].keys()
        with out.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(fields))
            w.writeheader()
            for r in rows:
                w.writerow({k: r[k] for k in fields})
        return len(rows)


def recorder_from_env(symbol: str = "GOLDPETAL") -> ZigzagRecorder:
    try:
        from dotenv import load_dotenv

        load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
    except Exception:
        pass
    enabled = os.getenv("S8_RECORD_RETUNE", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "y",
    }
    snap = int(os.getenv("S8_SNAP_EVERY_N", "25"))
    return ZigzagRecorder(symbol=symbol, snap_every_n=snap, enabled=enabled)
