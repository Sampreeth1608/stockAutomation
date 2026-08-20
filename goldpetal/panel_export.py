"""One-click date-range exports for the control panel (ticks + all-strategy trades).

Produces CSV (laptop download) and TSV (copy → paste into Google Sheets).
"""

from __future__ import annotations

import csv
import io
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from charges import paper_lots
from storage import (
    TRADE_CSV_FIELDS,
    DB_PATH,
    build_trades,
    connect,
    init_db,
)

IST = ZoneInfo("Asia/Kolkata")

TICK_CSV_FIELDS = [
    "received_at",
    "symbol",
    "token",
    "ltp",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "bp",
    "sp",
    "exchange_timestamp",
]

SIGNAL_CSV_FIELDS = [
    "time_label",
    "symbol",
    "strategy",
    "action",
    "position_after",
    "reason",
    "price_delta",
    "net",
    "net_delta",
    "dry_run",
    "cmp",
]


def _parse_day(day: str, *, end: bool = False) -> str:
    """Return ISO bound in IST for SQL string compare on received_at / time_label."""
    day = (day or "").strip()
    if not day:
        raise ValueError("date required (YYYY-MM-DD)")
    dt = datetime.strptime(day[:10], "%Y-%m-%d").replace(tzinfo=IST)
    if end:
        # inclusive end-of-day
        dt = dt + timedelta(days=1)
        return dt.isoformat(timespec="seconds")
    return dt.isoformat(timespec="seconds")


def default_date_range() -> tuple[str, str]:
    """Default: last 7 calendar days through today (IST)."""
    today = datetime.now(IST).date()
    start = today - timedelta(days=6)
    return start.isoformat(), today.isoformat()


def trades_in_range(
    date_from: str,
    date_to: str,
    *,
    db_path: Path = DB_PATH,
    strategy: str | None = None,
) -> list[dict[str, Any]]:
    """Filter trades whose entry day (IST date prefix) is in [from, to] inclusive."""
    d0 = (date_from or "").strip()[:10]
    d1 = (date_to or "").strip()[:10]
    if not d0 or not d1:
        raise ValueError("date_from and date_to required (YYYY-MM-DD)")
    trades = build_trades(strategy=strategy, db_path=db_path, lot_size=paper_lots())
    out: list[dict[str, Any]] = []
    for t in trades:
        entry = str(t.get("entry_ts") or "")
        day = entry[:10]
        if len(day) < 10:
            continue
        if day < d0 or day > d1:
            continue
        out.append(t)
    return out


def ticks_in_range(
    date_from: str,
    date_to: str,
    *,
    db_path: Path = DB_PATH,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    init_db(db_path)
    d0 = (date_from or "").strip()[:10]
    d1 = (date_to or "").strip()[:10]
    if not d0 or not d1:
        raise ValueError("date_from and date_to required (YYYY-MM-DD)")
    # Inclusive calendar days via prefix compare on received_at
    start = d0
    end_exclusive = (
        datetime.strptime(d1, "%Y-%m-%d").date() + timedelta(days=1)
    ).isoformat()
    sql = f"""
        SELECT received_at, symbol, token, ltp, open, high, low, close,
               volume, bp, sp, exchange_timestamp
        FROM ticks
        WHERE substr(received_at, 1, 10) >= ? AND substr(received_at, 1, 10) < ?
        ORDER BY id ASC
        {"LIMIT ?" if limit else ""}
    """
    params: list[Any] = [start, end_exclusive]
    if limit:
        params.append(int(limit))
    with connect(db_path) as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
    return [{k: r[k] for k in r.keys()} for r in rows]


def signals_in_range(
    date_from: str,
    date_to: str,
    *,
    db_path: Path = DB_PATH,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    init_db(db_path)
    d0 = (date_from or "").strip()[:10]
    d1 = (date_to or "").strip()[:10]
    if not d0 or not d1:
        raise ValueError("date_from and date_to required (YYYY-MM-DD)")
    end_exclusive = (
        datetime.strptime(d1, "%Y-%m-%d").date() + timedelta(days=1)
    ).isoformat()
    sql = f"""
        SELECT time_label, symbol, strategy, action, position_after, reason,
               price_delta, net, net_delta, dry_run, cmp
        FROM signals
        WHERE substr(time_label, 1, 10) >= ? AND substr(time_label, 1, 10) < ?
        ORDER BY id ASC
        {"LIMIT ?" if limit else ""}
    """
    params: list[Any] = [d0, end_exclusive]
    if limit:
        params.append(int(limit))
    with connect(db_path) as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
    return [{k: r[k] for k in r.keys()} for r in rows]


def rows_to_csv(rows: list[dict[str, Any]], fields: list[str]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({k: r.get(k, "") for k in fields})
    return buf.getvalue()


def rows_to_tsv(rows: list[dict[str, Any]], fields: list[str]) -> str:
    """Tab-separated — pastes cleanly into Google Sheets."""
    lines = ["\t".join(fields)]
    for r in rows:
        cells = []
        for k in fields:
            v = r.get(k, "")
            if v is None:
                v = ""
            s = str(v).replace("\t", " ").replace("\n", " ").replace("\r", "")
            cells.append(s)
        lines.append("\t".join(cells))
    return "\n".join(lines) + ("\n" if lines else "")


def export_ticks_csv(date_from: str, date_to: str, *, db_path: Path = DB_PATH) -> str:
    rows = ticks_in_range(date_from, date_to, db_path=db_path)
    return rows_to_csv(rows, TICK_CSV_FIELDS)


def export_signals_csv(
    date_from: str,
    date_to: str,
    *,
    db_path: Path = DB_PATH,
) -> str:
    rows = signals_in_range(date_from, date_to, db_path=db_path)
    return rows_to_csv(rows, SIGNAL_CSV_FIELDS)


def export_trades_csv(
    date_from: str,
    date_to: str,
    *,
    db_path: Path = DB_PATH,
    strategy: str | None = None,
) -> str:
    rows = trades_in_range(date_from, date_to, db_path=db_path, strategy=strategy)
    return rows_to_csv(rows, TRADE_CSV_FIELDS)


def export_pack_zip(
    date_from: str,
    date_to: str,
    *,
    db_path: Path = DB_PATH,
) -> bytes:
    """ZIP: ticks.csv + trades_all.csv + trades_<strategy>.csv for each strategy."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        ticks = ticks_in_range(date_from, date_to, db_path=db_path)
        zf.writestr("ticks.csv", rows_to_csv(ticks, TICK_CSV_FIELDS))
        signals = signals_in_range(date_from, date_to, db_path=db_path)
        zf.writestr("signals.csv", rows_to_csv(signals, SIGNAL_CSV_FIELDS))
        all_trades = trades_in_range(date_from, date_to, db_path=db_path)
        zf.writestr("trades_all.csv", rows_to_csv(all_trades, TRADE_CSV_FIELDS))
        by_strat: dict[str, list[dict[str, Any]]] = {}
        for t in all_trades:
            name = str(t.get("strategy") or "UNKNOWN")
            by_strat.setdefault(name, []).append(t)
        for name, rows in sorted(by_strat.items()):
            safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
            zf.writestr(f"trades_{safe}.csv", rows_to_csv(rows, TRADE_CSV_FIELDS))
        readme = (
            f"Gold Petal export {date_from} → {date_to} (IST inclusive)\n"
            f"ticks={len(ticks)} signals={len(signals)} trades={len(all_trades)}\n"
            "Import any CSV into Google Sheets: File → Import → Upload\n"
            "Or paste TSV from the control panel Copy button.\n"
        )
        zf.writestr("README.txt", readme)
    return buf.getvalue()


def export_summary(date_from: str, date_to: str, *, db_path: Path = DB_PATH) -> dict[str, Any]:
    ticks = ticks_in_range(date_from, date_to, db_path=db_path)
    signals = signals_in_range(date_from, date_to, db_path=db_path)
    trades = trades_in_range(date_from, date_to, db_path=db_path)
    by_strat: dict[str, int] = {}
    for t in trades:
        name = str(t.get("strategy") or "UNKNOWN")
        by_strat[name] = by_strat.get(name, 0) + 1
    return {
        "date_from": date_from,
        "date_to": date_to,
        "tick_count": len(ticks),
        "signal_count": len(signals),
        "trade_count": len(trades),
        "trades_by_strategy": by_strat,
    }
