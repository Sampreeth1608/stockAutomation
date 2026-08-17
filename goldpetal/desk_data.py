"""Light trade history for the operator desk (no S14 / sheets / HTTP imports)."""

from __future__ import annotations

import math
import threading
import time
from pathlib import Path
from typing import Any

from control_state import SLIM_PAPER_STRATEGIES
from live_orders import recent_orders
from paper_report import summarize_trades
from storage import DB_PATH, build_trades, connect, init_db, latest_signals, latest_ticks

_TRADE_CACHE: dict[str, Any] = {"at": 0.0, "rows": [], "error": "", "db": ""}
_TRADE_LOCK = threading.Lock()
_SIGNAL_WINDOW = 1500


def row_to_dict(row: Any) -> dict[str, Any]:
    if hasattr(row, "keys"):
        return {k: row[k] for k in row.keys()}
    return dict(row)


def json_safe(obj: Any) -> Any:
    """JSON that browsers can parse (no NaN / Infinity)."""
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, dict):
        return {str(k): json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [json_safe(v) for v in obj]
    return obj


def desk_db_candidates() -> list[Path]:
    root = Path(__file__).resolve().parent
    home = Path.home()
    out: list[Path] = []
    seen: set[Path] = set()
    for raw in (
        root / "data" / "ticks.db",
        DB_PATH,
        Path.cwd() / "data" / "ticks.db",
        home / "goldpetal-repo" / "goldpetal" / "data" / "ticks.db",
        home / "goldpetal" / "data" / "ticks.db",
    ):
        try:
            key = raw.resolve()
        except OSError:
            key = raw
        if key in seen:
            continue
        seen.add(key)
        out.append(raw)
    return out


def resolve_desk_db(*, candidates: list[Path] | None = None) -> Path:
    """Pick the ticks.db the bot is actually writing (newest nonempty file)."""
    paths = list(candidates) if candidates is not None else desk_db_candidates()
    existing = [p for p in paths if p.is_file()]
    if not existing:
        return paths[0] if paths else DB_PATH

    def mtime(p: Path) -> float:
        try:
            return float(p.stat().st_mtime)
        except OSError:
            return 0.0

    nonempty: list[Path] = []
    for p in existing:
        try:
            if p.stat().st_size > 4096:
                nonempty.append(p)
        except OSError:
            continue
    pool = nonempty or existing
    return max(pool, key=mtime)


def db_info(db_path: Path) -> dict[str, Any]:
    exists = db_path.is_file()
    size = 0
    try:
        size = int(db_path.stat().st_size) if exists else 0
    except OSError:
        exists = False
    return {"db_path": str(db_path), "db_exists": exists, "db_bytes": size}


def reset_trade_cache() -> None:
    _TRADE_CACHE.update({"at": 0.0, "rows": [], "error": "", "db": ""})


def _last_tick_meta(db_path: Path) -> dict[str, Any]:
    init_db(db_path)
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT id, received_at, ltp FROM ticks ORDER BY id DESC LIMIT 1"
        ).fetchone()
    if not row:
        return {"tick_count": 0, "ltp": None, "last_tick_at": ""}
    return {
        "tick_count": int(row["id"] or 0),
        "ltp": row["ltp"],
        "last_tick_at": row["received_at"] or "",
    }


def tape_payload(
    *,
    tick_limit: int = 40,
    signal_limit: int = 40,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Ticks / signals / live orders only — never rebuilds trades."""
    db = db_path or resolve_desk_db()
    payload: dict[str, Any] = {
        **db_info(db),
        "ticks": [],
        "signals": [],
        "live_orders": [],
        "tick_count": 0,
        "ltp": None,
        "last_tick_at": "",
        "error": "",
    }
    try:
        payload["ticks"] = [row_to_dict(r) for r in latest_ticks(limit=tick_limit, db_path=db)]
        payload["signals"] = [
            row_to_dict(r) for r in latest_signals(limit=signal_limit, db_path=db)
        ]
        payload["live_orders"] = recent_orders(limit=40)
        payload.update(_last_tick_meta(db))
    except Exception as exc:
        payload["error"] = str(exc)
    return payload


def all_trades_cached(*, db_path: Path | None = None) -> tuple[list[dict[str, Any]], str]:
    db = db_path or resolve_desk_db()
    now = time.time()
    if (
        _TRADE_CACHE.get("db") == str(db)
        and float(_TRADE_CACHE["at"]) > 0
        and now - float(_TRADE_CACHE["at"]) < 12
    ):
        return list(_TRADE_CACHE["rows"]), str(_TRADE_CACHE.get("error") or "")
    if not _TRADE_LOCK.acquire(blocking=False):
        return list(_TRADE_CACHE["rows"]), str(_TRADE_CACHE.get("error") or "trades still loading")
    try:
        now = time.time()
        if (
            _TRADE_CACHE.get("db") == str(db)
            and float(_TRADE_CACHE["at"]) > 0
            and now - float(_TRADE_CACHE["at"]) < 12
        ):
            return list(_TRADE_CACHE["rows"]), str(_TRADE_CACHE.get("error") or "")
        rows: list[dict[str, Any]] = []
        err = ""
        try:
            for name in SLIM_PAPER_STRATEGIES:
                rows.extend(
                    build_trades(strategy=name, db_path=db, signal_limit=_SIGNAL_WINDOW)
                )
        except Exception as exc:
            err = str(exc)
        _TRADE_CACHE.update({"at": time.time(), "rows": rows, "error": err, "db": str(db)})
        return list(rows), err
    finally:
        _TRADE_LOCK.release()


def recent_trades(limit: int = 8) -> list[dict[str, Any]]:
    rows, _ = all_trades_cached()
    open_t = [t for t in rows if t.get("status") == "OPEN"]
    closed = [t for t in rows if str(t.get("status", "")).startswith("CLOSED")]
    return (open_t + list(reversed(closed)))[:limit]


def history_payload(
    *,
    limit: int = 80,
    strategy: str | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Trade history plus the fast tape so Watch is never an empty placeholder."""
    db = db_path or resolve_desk_db()
    tape = tape_payload(db_path=db)
    rows, trade_err = all_trades_cached(db_path=db)
    if strategy:
        rows = [t for t in rows if t.get("strategy") == strategy]
    open_t = [t for t in rows if t.get("status") == "OPEN"]
    closed = [t for t in rows if str(t.get("status", "")).startswith("CLOSED")]
    closed_rev = list(reversed(closed))
    all_rows, _ = all_trades_cached(db_path=db)
    scoreboard = [summarize_trades(all_rows, s) for s in SLIM_PAPER_STRATEGIES]
    scoreboard.append(summarize_trades(all_rows, None))
    err = str(tape.get("error") or "") or trade_err
    return {
        **tape,
        "strategy": strategy or "",
        "open": open_t,
        "closed": closed_rev[:limit],
        "trades": (open_t + closed_rev)[:limit],
        "total_open": len(open_t),
        "total_closed": len(closed),
        "scoreboard": scoreboard,
        "error": err,
        "trades_loading": trade_err == "trades still loading",
    }
