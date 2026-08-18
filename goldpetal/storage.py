"""SQLite storage for Gold Petal ticks, bars, and signals."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Optional

DB_PATH = Path(__file__).resolve().parent / "data" / "ticks.db"


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30)
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
                strategy TEXT NOT NULL DEFAULT 'S1_NETDELTA',
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
        cols = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(signals)").fetchall()
        }
        if "strategy" not in cols:
            conn.execute(
                "ALTER TABLE signals ADD COLUMN strategy TEXT NOT NULL DEFAULT 'S1_NETDELTA'"
            )
        if "cmp" not in cols:
            conn.execute("ALTER TABLE signals ADD COLUMN cmp REAL")
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
    strategy: str = "S1_NETDELTA",
    cmp: Optional[float] = None,
    db_path: Path = DB_PATH,
) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO signals (
                time_label, symbol, strategy, action, position_after, reason,
                price_delta, net, net_delta, dry_run, cmp
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                time_label,
                symbol,
                strategy,
                action,
                position_after,
                reason,
                price_delta,
                net,
                net_delta,
                1 if dry_run else 0,
                cmp,
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


def list_signals(
    strategy: str | None = None,
    db_path: Path = DB_PATH,
    *,
    limit: int | None = None,
) -> list[sqlite3.Row]:
    init_db(db_path)
    where = ""
    params: list[Any] = []
    if strategy:
        where = " WHERE strategy = ?"
        params.append(strategy)
    if limit is not None:
        query = f"""
            SELECT id, time_label, symbol, strategy, action, position_after, reason,
                   price_delta, net, net_delta, dry_run, cmp
            FROM signals
            {where}
            ORDER BY id DESC
            LIMIT ?
        """
        params.append(int(limit))
        with connect(db_path) as conn:
            rows = list(conn.execute(query, params))
        rows.reverse()
        return rows
    query = f"""
        SELECT id, time_label, symbol, strategy, action, position_after, reason,
               price_delta, net, net_delta, dry_run, cmp
        FROM signals
        {where}
        ORDER BY id ASC
    """
    with connect(db_path) as conn:
        return list(conn.execute(query, params))


def latest_signals(
    limit: int = 20,
    strategy: str | None = None,
    db_path: Path = DB_PATH,
) -> list[sqlite3.Row]:
    init_db(db_path)
    if strategy:
        query = """
            SELECT time_label, symbol, strategy, action, position_after, reason,
                   price_delta, net, net_delta, dry_run, cmp
            FROM signals
            WHERE strategy = ?
            ORDER BY id DESC
            LIMIT ?
        """
        params: tuple[Any, ...] = (strategy, limit)
    else:
        query = """
            SELECT time_label, symbol, strategy, action, position_after, reason,
                   price_delta, net, net_delta, dry_run, cmp
            FROM signals
            ORDER BY id DESC
            LIMIT ?
        """
        params = (limit,)
    with connect(db_path) as conn:
        return list(conn.execute(query, params))


def latest_ltp(db_path: Path = DB_PATH) -> float | None:
    init_db(db_path)
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT ltp FROM ticks WHERE ltp IS NOT NULL ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return float(row["ltp"]) if row and row["ltp"] is not None else None


def export_signals_csv(
    path: Path,
    strategy: str | None = None,
    limit: int | None = None,
    db_path: Path = DB_PATH,
) -> int:
    init_db(db_path)
    query = """
        SELECT time_label, symbol, strategy, action, position_after, reason,
               price_delta, net, net_delta, dry_run, cmp
        FROM signals
    """
    params: list[Any] = []
    if strategy:
        query += " WHERE strategy = ?"
        params.append(strategy)
    query += " ORDER BY id ASC"
    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)

    with connect(db_path) as conn:
        rows = list(conn.execute(query, tuple(params)))

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        handle.write(
            "time,symbol,strategy,action,position,cmp,price_delta,net,net_delta,reason,dry_run\n"
        )
        for row in rows:
            handle.write(
                ",".join(
                    [
                        str(row["time_label"] or ""),
                        str(row["symbol"] or ""),
                        str(row["strategy"] or ""),
                        str(row["action"] or ""),
                        str(row["position_after"] or ""),
                        str(row["cmp"] if row["cmp"] is not None else ""),
                        str(row["price_delta"] if row["price_delta"] is not None else ""),
                        str(row["net"] if row["net"] is not None else ""),
                        str(row["net_delta"] if row["net_delta"] is not None else ""),
                        '"' + str(row["reason"] or "").replace('"', "'") + '"',
                        str(row["dry_run"]),
                    ]
                )
                + "\n"
            )
    return len(rows)


def _price_from_signal(row: sqlite3.Row, db_path: Path = DB_PATH) -> float | None:
    """Prefer signal.cmp; fall back to bar CMP or nearest tick LTP for older rows."""
    if row["cmp"] is not None:
        try:
            return float(row["cmp"])
        except (TypeError, ValueError):
            pass

    time_label = row["time_label"]
    symbol = row["symbol"]
    with connect(db_path) as conn:
        bar = conn.execute(
            """
            SELECT cmp FROM bars
            WHERE time_label = ? AND symbol = ?
            ORDER BY id DESC LIMIT 1
            """,
            (time_label, symbol),
        ).fetchone()
        if bar and bar["cmp"] is not None:
            return float(bar["cmp"])

        tick = conn.execute(
            """
            SELECT ltp FROM ticks
            WHERE symbol = ? AND ltp IS NOT NULL
              AND received_at <= ?
            ORDER BY id DESC LIMIT 1
            """,
            (symbol, time_label),
        ).fetchone()
        if tick and tick["ltp"] is not None:
            return float(tick["ltp"])
    return None


def build_trades(
    strategy: str | None = None,
    db_path: Path = DB_PATH,
    *,
    lot_size: float | None = None,
    signal_limit: int | None = None,
) -> list[dict[str, Any]]:
    """Pair BUY/SHORT entries with CLOSE (or flip) into round-trip trades + PnL.

    When strategy is None, builds each strategy separately then concatenates
    so S1 and S2 never cross-pair.

    Post-trade Angel fees + tax are always computed for display (even when
    IGNORE_FEES=true). lot_size overrides LOT_SIZE for fee/PnL scaling.
    signal_limit caps how far back each book is scanned (desk tape).
    """
    if strategy is None:
        with connect(db_path) as conn:
            init_db(db_path)
            names = [
                r[0]
                for r in conn.execute(
                    "SELECT DISTINCT strategy FROM signals ORDER BY strategy"
                ).fetchall()
            ]
        merged: list[dict[str, Any]] = []
        trade_no = 0
        for name in names:
            for t in _build_trades_one(
                name, db_path=db_path, lot_size=lot_size, signal_limit=signal_limit
            ):
                trade_no += 1
                t = dict(t)
                t["trade_no"] = trade_no
                merged.append(t)
        return merged
    return _build_trades_one(
        strategy, db_path=db_path, lot_size=lot_size, signal_limit=signal_limit
    )


def _build_trades_one(
    strategy: str,
    db_path: Path = DB_PATH,
    *,
    lot_size: float | None = None,
    signal_limit: int | None = None,
) -> list[dict[str, Any]]:
    """Pair BUY/SHORT entries with CLOSE (or flip) for one strategy."""
    rows = list_signals(strategy=strategy, db_path=db_path, limit=signal_limit)
    trades: list[dict[str, Any]] = []
    open_trade: dict[str, Any] | None = None
    trade_no = 0

    def _close(trade: dict[str, Any], *, exit_ts: str, exit_price: float | None,
               exit_reason: str, exit_net: Any, exit_net_delta: Any,
               status: str) -> dict[str, Any]:
        from charges import (
            angel_charges_from_env,
            apply_charges_and_tax,
            ignore_fees_enabled,
        )

        entry = trade.get("entry_price")
        side = trade["side"]
        pnl: float | None = None
        pnl_pct: float | None = None
        charge_bits = {
            "gross_pnl": "",
            "charges": "",
            "pnl_after_charges": "",
            "tax": "",
            "pnl_after_tax": "",
        }
        if entry is not None and entry != "" and exit_price is not None:
            entry_f = float(entry)
            if side == "BUY":
                pnl_pts = exit_price - entry_f
            else:
                pnl_pts = entry_f - exit_price
            pnl_pct = (pnl_pts / entry_f * 100.0) if entry_f else 0.0
            # Always Angel schedule for post-trade reporting columns
            report_cfg = angel_charges_from_env(lot_size=lot_size)
            charge_bits = apply_charges_and_tax(
                pnl_pts,
                report_cfg,
                side=side,
                entry_price=entry_f,
                exit_price=float(exit_price),
            )
            # IGNORE_FEES: strategies "ride freely" — net_pnl stays gross.
            # charges / tax / pnl_after_tax still filled for Streamlit/desk.
            if ignore_fees_enabled():
                pnl = float(charge_bits["gross_pnl"])
            else:
                pnl = float(charge_bits["pnl_after_tax"])
        trade.update(
            {
                "exit_ts": exit_ts,
                "exit_price": round(exit_price, 2) if exit_price is not None else "",
                "exit_reason": exit_reason,
                "exit_net": exit_net if exit_net is not None else "",
                "exit_net_delta": exit_net_delta if exit_net_delta is not None else "",
                "gross_pnl": charge_bits["gross_pnl"],
                "charges": charge_bits["charges"],
                "brokerage": charge_bits.get("brokerage", ""),
                "txn": charge_bits.get("txn", ""),
                "sebi": charge_bits.get("sebi", ""),
                "stamp": charge_bits.get("stamp", ""),
                "ctt": charge_bits.get("ctt", ""),
                "gst": charge_bits.get("gst", ""),
                "pnl_after_charges": charge_bits["pnl_after_charges"],
                "tax": charge_bits["tax"],
                "pnl_after_tax": charge_bits["pnl_after_tax"],
                "lots": float(lot_size) if lot_size is not None else "",
                "net_pnl": round(pnl, 2) if pnl is not None else "",
                "net_pnl_pct": round(pnl_pct, 4) if pnl_pct is not None else "",
                "status": status,
            }
        )
        return trade

    for r in rows:
        action = str(r["action"] or "").upper()
        if action not in {"BUY", "SHORT", "CLOSE"}:
            continue

        price = _price_from_signal(r, db_path=db_path)
        ts = str(r["time_label"] or "")
        strat = str(r["strategy"] or "")
        reason = str(r["reason"] or "")
        net_v = r["net"]
        net_d = r["net_delta"]
        symbol = str(r["symbol"] or "")

        if action in {"BUY", "SHORT"}:
            if open_trade is not None:
                trades.append(
                    _close(
                        open_trade,
                        exit_ts=ts,
                        exit_price=price,
                        exit_reason=f"replaced_by_{action}",
                        exit_net=net_v,
                        exit_net_delta=net_d,
                        status="CLOSED_FORCED",
                    )
                )
                open_trade = None

            trade_no += 1
            open_trade = {
                "trade_no": trade_no,
                "strategy": strat,
                "symbol": symbol,
                "side": action,
                "entry_ts": ts,
                "entry_price": round(price, 2) if price is not None else "",
                "entry_reason": reason,
                "entry_net": net_v if net_v is not None else "",
                "entry_net_delta": net_d if net_d is not None else "",
                "exit_ts": "",
                "exit_price": "",
                "exit_reason": "",
                "exit_net": "",
                "exit_net_delta": "",
                "gross_pnl": "",
                "charges": "",
                "brokerage": "",
                "txn": "",
                "sebi": "",
                "stamp": "",
                "ctt": "",
                "gst": "",
                "pnl_after_charges": "",
                "tax": "",
                "pnl_after_tax": "",
                "net_pnl": "",
                "net_pnl_pct": "",
                "status": "OPEN",
            }
        elif action == "CLOSE" and open_trade is not None:
            trades.append(
                _close(
                    open_trade,
                    exit_ts=ts,
                    exit_price=price,
                    exit_reason=reason,
                    exit_net=net_v,
                    exit_net_delta=net_d,
                    status="CLOSED",
                )
            )
            open_trade = None

    if open_trade is not None:
        trades.append(open_trade)

    return trades


TRADE_CSV_FIELDS = [
    "trade_no",
    "strategy",
    "symbol",
    "side",
    "status",
    "entry_ts",
    "entry_price",
    "exit_ts",
    "exit_price",
    "gross_pnl",
    "charges",
    "brokerage",
    "txn",
    "sebi",
    "stamp",
    "ctt",
    "gst",
    "pnl_after_charges",
    "tax",
    "pnl_after_tax",
    "net_pnl",
    "net_pnl_pct",
    "entry_reason",
    "exit_reason",
    "entry_net",
    "entry_net_delta",
    "exit_net",
    "exit_net_delta",
]


def export_trades_csv(
    path: Path,
    strategy: str | None = None,
    db_path: Path = DB_PATH,
) -> tuple[int, int, float]:
    """Write trade journal CSV. Returns (total, closed_count, sum pnl_after_tax)."""
    import csv

    trades = build_trades(strategy=strategy, db_path=db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=TRADE_CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for t in trades:
            writer.writerow({k: t.get(k, "") for k in TRADE_CSV_FIELDS})

    closed = [t for t in trades if str(t.get("status", "")).startswith("CLOSED")]
    pnl_sum = 0.0
    for t in closed:
        val = t.get("pnl_after_tax", t.get("net_pnl"))
        if val != "" and val is not None:
            pnl_sum += float(val)
    return len(trades), len(closed), pnl_sum


def count_bars(db_path: Path = DB_PATH) -> int:
    init_db(db_path)
    with connect(db_path) as conn:
        row = conn.execute("SELECT COUNT(*) AS c FROM bars").fetchone()
        return int(row["c"])


def latest_bar(db_path: Path = DB_PATH):
    init_db(db_path)
    with connect(db_path) as conn:
        return conn.execute(
            """
            SELECT time_label, symbol, token, cmp, bp, sp, net, price_delta, net_delta
            FROM bars
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()


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
            s.strategy,
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
                "strategy": row["strategy"],
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
        "TIME,CMP,PRICE_DELTA,BP,BP_DELTA,SP,SP_DELTA,BP_SP,NET_DELTA,STRATEGY,SIGNAL,POSITION,REASON\n"
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
                        str(row.get("strategy") or ""),
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
