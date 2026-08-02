"""SQLite storage for Gold Petal ticks, bars, and signals."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Optional

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
                bp REAL,
                sp REAL,
                raw_json TEXT NOT NULL
            )
            """
        )
        # Older DBs may not have bp/sp columns yet.
        cols = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(ticks)").fetchall()
        }
        if "bp" not in cols:
            conn.execute("ALTER TABLE ticks ADD COLUMN bp REAL")
        if "sp" not in cols:
            conn.execute("ALTER TABLE ticks ADD COLUMN sp REAL")

        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ticks_received_at ON ticks(received_at)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ticks_token ON ticks(token)")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS bars (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                time_label TEXT NOT NULL,
                symbol TEXT NOT NULL,
                token TEXT NOT NULL,
                cmp REAL NOT NULL,
                bp REAL NOT NULL,
                sp REAL NOT NULL,
                net REAL NOT NULL,
                price_delta REAL,
                net_delta REAL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                time_label TEXT NOT NULL,
                symbol TEXT NOT NULL,
                action TEXT NOT NULL,
                position_after TEXT NOT NULL,
                reason TEXT NOT NULL,
                price_delta REAL,
                net REAL,
                net_delta REAL,
                dry_run INTEGER NOT NULL
            )
            """
        )
        conn.commit()


def _scale_price(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number / 100.0


def save_tick(
    message: dict[str, Any],
    symbol: str,
    token: str,
    received_at: str,
    db_path: Path = DB_PATH,
) -> None:
    bp = message.get("total_buy_quantity")
    sp = message.get("total_sell_quantity")
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO ticks (
                received_at, exchange_timestamp, symbol, token,
                ltp, open, high, low, close, volume, bp, sp, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                float(bp) if bp is not None else None,
                float(sp) if sp is not None else None,
                json.dumps(message, default=str),
            ),
        )
        conn.commit()


def save_bar(
    time_label: str,
    symbol: str,
    token: str,
    cmp: float,
    bp: float,
    sp: float,
    net: float,
    price_delta: Optional[float],
    net_delta: Optional[float],
    db_path: Path = DB_PATH,
) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO bars (
                time_label, symbol, token, cmp, bp, sp, net, price_delta, net_delta
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (time_label, symbol, token, cmp, bp, sp, net, price_delta, net_delta),
        )
        conn.commit()


def save_signal(
    time_label: str,
    symbol: str,
    action: str,
    position_after: str,
    reason: str,
    price_delta: Optional[float],
    net: float,
    net_delta: Optional[float],
    dry_run: bool,
    db_path: Path = DB_PATH,
) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO signals (
                time_label, symbol, action, position_after, reason,
                price_delta, net, net_delta, dry_run
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                time_label,
                symbol,
                action,
                position_after,
                reason,
                price_delta,
                net,
                net_delta,
                1 if dry_run else 0,
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
                SELECT received_at, symbol, token, ltp, volume, bp, sp, exchange_timestamp
                FROM ticks
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            )
        )


def latest_signals(limit: int = 20, db_path: Path = DB_PATH) -> list[sqlite3.Row]:
    init_db(db_path)
    with connect(db_path) as conn:
        return list(
            conn.execute(
                """
                SELECT time_label, symbol, action, position_after, reason,
                       price_delta, net, net_delta, dry_run
                FROM signals
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            )
        )


def count_bars(db_path: Path = DB_PATH) -> int:
    init_db(db_path)
    with connect(db_path) as conn:
        row = conn.execute("SELECT COUNT(*) AS c FROM bars").fetchone()
        return int(row["c"])


def sheet_rows(limit: int | None = None, db_path: Path = DB_PATH) -> list[dict[str, Any]]:
    """Build manual-sheet style rows from bars + signals."""
    init_db(db_path)
    query = """
        SELECT
            b.id,
            b.time_label,
            b.symbol,
            b.cmp,
            b.bp,
            b.sp,
            b.net,
            b.price_delta,
            b.net_delta,
            s.action,
            s.position_after,
            s.reason
        FROM bars b
        LEFT JOIN signals s
          ON s.id = (
            SELECT s2.id FROM signals s2
            WHERE s2.time_label = b.time_label AND s2.symbol = b.symbol
            ORDER BY s2.id DESC
            LIMIT 1
          )
        ORDER BY b.id ASC
    """
    with connect(db_path) as conn:
        rows = list(conn.execute(query))

    if limit is not None:
        rows = rows[-limit:]

    out: list[dict[str, Any]] = []
    prev_bp: float | None = None
    prev_sp: float | None = None
    for row in rows:
        bp = float(row["bp"])
        sp = float(row["sp"])
        bp_delta = None if prev_bp is None else bp - prev_bp
        sp_delta = None if prev_sp is None else sp - prev_sp
        out.append(
            {
                "time": row["time_label"],
                "symbol": row["symbol"],
                "cmp": row["cmp"],
                "price_delta": row["price_delta"],
                "bp": bp,
                "bp_delta": bp_delta,
                "sp": sp,
                "sp_delta": sp_delta,
                "net": row["net"],
                "net_delta": row["net_delta"],
                "signal": row["action"],
                "position": row["position_after"],
                "reason": row["reason"],
            }
        )
        prev_bp = bp
        prev_sp = sp
    return out


def export_sheet_csv(
    path: Path, limit: int | None = None, db_path: Path = DB_PATH
) -> int:
    rows = sheet_rows(limit=limit, db_path=db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "TIME,CMP,PRICE_DELTA,BP,BP_DELTA,SP,SP_DELTA,BP_SP,NET_DELTA,SIGNAL,POSITION,REASON\n"
    )
    with path.open("w", encoding="utf-8") as handle:
        handle.write(header)
        for row in rows:
            handle.write(
                ",".join(
                    [
                        str(row["time"] or ""),
                        str(row["cmp"] if row["cmp"] is not None else ""),
                        str(row["price_delta"] if row["price_delta"] is not None else ""),
                        str(row["bp"] if row["bp"] is not None else ""),
                        str(row["bp_delta"] if row["bp_delta"] is not None else ""),
                        str(row["sp"] if row["sp"] is not None else ""),
                        str(row["sp_delta"] if row["sp_delta"] is not None else ""),
                        str(row["net"] if row["net"] is not None else ""),
                        str(row["net_delta"] if row["net_delta"] is not None else ""),
                        str(row["signal"] or ""),
                        str(row["position"] or ""),
                        '"' + str(row["reason"] or "").replace('"', "'") + '"',
                    ]
                )
                + "\n"
            )
    return len(rows)


def export_csv(path: Path, limit: int | None = None, db_path: Path = DB_PATH) -> int:
    init_db(db_path)
    query = """
        SELECT received_at, exchange_timestamp, symbol, token,
               ltp, open, high, low, close, volume, bp, sp
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
            "received_at,exchange_timestamp,symbol,token,ltp,open,high,low,close,volume,bp,sp\n"
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
                        str(row["bp"] or ""),
                        str(row["sp"] or ""),
                    ]
                )
                + "\n"
            )
    return len(rows)
