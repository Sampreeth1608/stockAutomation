"""SQLite storage for Gold Petal ticks."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

DB_PATH = Path(__file__).resolve().parent / "data" / "ticks.db"


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Path = DB_PATH) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ticks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                received_at TEXT NOT NULL,
                exchange_timestamp INTEGER,
                symbol TEXT NOT NULL,
                token TEXT NOT NULL,
                ltp REAL,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume INTEGER,
                raw_json TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ticks_received_at ON ticks(received_at)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ticks_token ON ticks(token)")
        conn.commit()


def _scale_price(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    # Angel websocket prices are typically in paise.
    return number / 100.0


def save_tick(
    message: dict[str, Any],
    symbol: str,
    token: str,
    received_at: str,
    db_path: Path = DB_PATH,
) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO ticks (
                received_at, exchange_timestamp, symbol, token,
                ltp, open, high, low, close, volume, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                received_at,
                message.get("exchange_timestamp"),
                symbol,
                token,
                _scale_price(message.get("last_traded_price")),
                _scale_price(message.get("open_price_of_the_day")),
                _scale_price(message.get("high_price_of_the_day")),
                _scale_price(message.get("low_price_of_the_day")),
                _scale_price(message.get("closed_price")),
                message.get("volume_trade_for_the_day"),
                json.dumps(message, default=str),
            ),
        )
        conn.commit()


def count_ticks(db_path: Path = DB_PATH) -> int:
    init_db(db_path)
    with connect(db_path) as conn:
        row = conn.execute("SELECT COUNT(*) AS c FROM ticks").fetchone()
        return int(row["c"])


def latest_ticks(limit: int = 20, db_path: Path = DB_PATH) -> list[sqlite3.Row]:
    init_db(db_path)
    with connect(db_path) as conn:
        return list(
            conn.execute(
                """
                SELECT received_at, symbol, token, ltp, volume, exchange_timestamp
                FROM ticks
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            )
        )


def export_csv(path: Path, limit: int | None = None, db_path: Path = DB_PATH) -> int:
    init_db(db_path)
    query = """
        SELECT received_at, exchange_timestamp, symbol, token,
               ltp, open, high, low, close, volume
        FROM ticks
        ORDER BY id ASC
    """
    params: tuple[Any, ...] = ()
    if limit is not None:
        query += " LIMIT ?"
        params = (limit,)

    with connect(db_path) as conn:
        rows = list(conn.execute(query, params))

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        handle.write(
            "received_at,exchange_timestamp,symbol,token,ltp,open,high,low,close,volume\n"
        )
        for row in rows:
            handle.write(
                ",".join(
                    [
                        str(row["received_at"] or ""),
                        str(row["exchange_timestamp"] or ""),
                        str(row["symbol"] or ""),
                        str(row["token"] or ""),
                        str(row["ltp"] or ""),
                        str(row["open"] or ""),
                        str(row["high"] or ""),
                        str(row["low"] or ""),
                        str(row["close"] or ""),
                        str(row["volume"] or ""),
                    ]
                )
                + "\n"
            )
    return len(rows)
