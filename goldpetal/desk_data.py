"""Light trade history for the operator desk (no S14 / sheets / HTTP imports)."""

from __future__ import annotations

import math
import sqlite3
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from control_state import paper_strategy_names
from live_orders import (
    angel_cache_fresh,
    angel_net_cache_is_flat,
    live_lots_for,
    lots_on_fill_for_trade,
    net_open_from_fills,
    recent_orders,
)
from paper_report import summarize_trades
from storage import build_trades, latest_signals, latest_ticks, set_db_path
import storage as _storage
from charges import paper_lots

IST = ZoneInfo("Asia/Kolkata")
TAPE_LIVE_SEC = 30

_TRADE_CACHE: dict[str, Any] = {"at": 0.0, "rows": [], "error": "", "db": ""}
_TRADE_LOCK = threading.Lock()
_LIVE_PNL_CACHE: dict[str, Any] = {"at": 0.0, "payload": None, "db": ""}
_LIVE_PNL_LOCK = threading.Lock()
_PGREP_CACHE: dict[str, Any] = {"at": 0.0, "path": ""}
_SIGNAL_WINDOW = 1500
_LIVE_PNL_BOOKS_EXTRA = frozenset()


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


def bot_live_ticks_db() -> Path | None:
    """ticks.db beside the running run_strategy.py / collect_ticks.py process."""
    now = time.time()
    if now - float(_PGREP_CACHE.get("at") or 0) < 3.0:
        raw = str(_PGREP_CACHE.get("path") or "")
        if not raw:
            return None
        p = Path(raw)
        return p if p.is_file() else None
    found = _bot_live_ticks_db_scan()
    _PGREP_CACHE["at"] = now
    _PGREP_CACHE["path"] = str(found) if found is not None else ""
    return found


def _bot_live_ticks_db_scan() -> Path | None:
    try:
        proc = subprocess.run(
            ["pgrep", "-af", "run_strategy.py|collect_ticks.py"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return None
    for line in (proc.stdout or "").splitlines():
        if "pgrep" in line or "control_panel" in line:
            continue
        parts = line.split(None, 1)
        if len(parts) < 2:
            continue
        cmd = parts[1]
        if "run_strategy.py" not in cmd and "collect_ticks.py" not in cmd:
            continue
        try:
            pid = int(parts[0])
            cwd = Path(f"/proc/{pid}/cwd").resolve()
        except (ValueError, OSError):
            continue
        db = cwd / "data" / "ticks.db"
        if db.is_file():
            return db
    return None


def desk_db_candidates() -> list[Path]:
    root = Path(__file__).resolve().parent
    home = Path.home()
    out: list[Path] = []
    seen: set[Path] = set()
    bot_db = bot_live_ticks_db()
    for raw in (
        *((bot_db,) if bot_db is not None else ()),
        root / "data" / "ticks.db",
        _storage.DB_PATH,
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


_RESOLVE_CACHE: dict[str, Any] = {"at": 0.0, "path": ""}


def _file_mtime(p: Path) -> float:
    try:
        return float(p.stat().st_mtime)
    except OSError:
        return 0.0


def _last_tick_epoch(p: Path) -> float:
    """Newest tick clock in this file. 0 if unreadable / empty (mtime must not win)."""
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(str(p), timeout=1.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=1000")
        row = conn.execute(
            "SELECT received_at FROM ticks ORDER BY id DESC LIMIT 1"
        ).fetchone()
    except Exception:
        return 0.0
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    if not row:
        return 0.0
    ts = parse_tick_at(str(row["received_at"] or ""))
    if ts is None:
        return 0.0
    return float(ts.timestamp())


def resolve_desk_db(*, candidates: list[Path] | None = None) -> Path:
    """Live bot ticks.db if the runner is up; else the file with the freshest tick."""
    paths = list(candidates) if candidates is not None else desk_db_candidates()
    use_cache = candidates is None
    now = time.time()
    bot_db = None if candidates is not None else bot_live_ticks_db()
    if use_cache and bot_db is not None and bot_db.is_file():
        _RESOLVE_CACHE.update({"at": now, "path": str(bot_db)})
        return bot_db
    if use_cache and _RESOLVE_CACHE.get("path") and now - float(_RESOLVE_CACHE.get("at") or 0) < 2.0:
        cached = Path(str(_RESOLVE_CACHE["path"]))
        if cached.is_file():
            return cached
    existing = [p for p in paths if p.is_file()]
    if not existing:
        chosen = paths[0] if paths else _storage.DB_PATH
        if use_cache:
            _RESOLVE_CACHE.update({"at": now, "path": str(chosen)})
        return chosen

    nonempty: list[Path] = []
    for p in existing:
        try:
            if p.stat().st_size > 4096:
                nonempty.append(p)
        except OSError:
            continue
    pool = nonempty or existing
    chosen = max(pool, key=lambda p: (_last_tick_epoch(p), _file_mtime(p)))
    if use_cache:
        _RESOLVE_CACHE.update({"at": now, "path": str(chosen)})
    return chosen


def _same_db(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return str(a) == str(b)


def quarantine_stale_ticks_dbs(
    live: Path, *, candidates: list[Path] | None = None
) -> list[str]:
    """Rename leftover ticks.db files so the desk cannot read them. Live file stays."""
    moved: list[str] = []
    live_ep = _last_tick_epoch(live) if live.is_file() else 0.0
    for raw in list(candidates) if candidates is not None else desk_db_candidates():
        p = Path(raw)
        if not p.is_file() or _same_db(p, live):
            continue
        other_ep = _last_tick_epoch(p)
        if live_ep > 0 and other_ep > live_ep:
            continue
        dest = p.with_name(p.name + ".stale")
        if dest.exists():
            dest = p.with_name(p.name + f".stale-{int(time.time())}")
        try:
            p.rename(dest)
            moved.append(f"{p} -> {dest}")
        except OSError:
            continue
        for side in (p.with_name(p.name + "-wal"), p.with_name(p.name + "-shm")):
            if not side.is_file():
                continue
            side_dest = Path(str(dest) + side.name[len(p.name) :])
            try:
                side.rename(side_dest)
                moved.append(f"{side} -> {side_dest}")
            except OSError:
                pass
    if moved:
        _RESOLVE_CACHE.update({"at": 0.0, "path": ""})
        reset_trade_cache()
    return moved


def bind_live_ticks_db(*, candidates: list[Path] | None = None) -> dict[str, Any]:
    """Desk + storage use the bot db. Stale copies are renamed off the candidate list."""
    live = resolve_desk_db(candidates=candidates)
    moved = quarantine_stale_ticks_dbs(live, candidates=candidates)
    set_db_path(live)
    _RESOLVE_CACHE.update({"at": time.time(), "path": str(live)})
    return {
        "ok": True,
        "live": str(live),
        "bot_cwd_db": str(bot_live_ticks_db() or ""),
        "quarantined": moved,
    }


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
    _LIVE_PNL_CACHE.update({"at": 0.0, "payload": None, "db": ""})


def _public_live_order(row: dict[str, Any]) -> dict[str, Any]:
    skipped = bool(row.get("skipped"))
    dry = bool(row.get("dry_run"))
    ok = bool(row.get("ok"))
    return {
        "ts": str(row.get("ts_ist") or row.get("ts") or row.get("time") or ""),
        "strategy": str(row.get("strategy") or ""),
        "transaction": str(row.get("transaction") or ""),
        "quantity": row.get("quantity") or 0,
        "ok": ok,
        "skipped": skipped,
        "dry_run": dry,
        "order_id": str(row.get("order_id") or ""),
        "reason": str(row.get("reason") or ""),
        "placed": ok and not skipped and not dry,
    }


def _kick_live_pnl(db: Path) -> None:
    if _LIVE_PNL_LOCK.locked():
        return

    def run() -> None:
        try:
            live_pnl_payload(db_path=db, wait=True)
        except Exception:
            pass

    threading.Thread(target=run, name="gp-live-pnl", daemon=True).start()


def invalidate_live_pnl_cache() -> None:
    _LIVE_PNL_CACHE["at"] = 0.0
    _LIVE_PNL_CACHE["payload"] = None


def live_pnl_payload(*, db_path: Path | None = None, wait: bool = True) -> dict[str, Any]:
    """Angel-sized P&L from dry_run=0 signals on live-eligible books.

    Paper Positions / Blotter / Paper P&L stay paper 100 lots. Desk/tape polls
    pass wait=False so GOLD LTP is not blocked by a trade rebuild.
    """
    from control_state import load_state
    from live_readiness import LIVE_ELIGIBLE_BOOKS, NEVER_LIVE_BOOKS, book_may_go_live

    db = db_path or resolve_desk_db()
    now = time.time()
    cached = _LIVE_PNL_CACHE.get("payload")
    same = _LIVE_PNL_CACHE.get("db") == str(db) and cached is not None
    fresh = same and float(_LIVE_PNL_CACHE.get("at") or 0) > 0 and now - float(_LIVE_PNL_CACHE["at"]) < 8
    if fresh:
        return _apply_angel_snapshot(dict(cached))
    if not wait:
        if same:
            _kick_live_pnl(db)
            return _apply_angel_snapshot(dict(cached))
        _kick_live_pnl(db)
        return _leftover_live_pnl()
    if not _LIVE_PNL_LOCK.acquire(blocking=False):
        return _apply_angel_snapshot(dict(cached)) if same else _leftover_live_pnl()
    try:
        now = time.time()
        cached = _LIVE_PNL_CACHE.get("payload")
        same = _LIVE_PNL_CACHE.get("db") == str(db) and cached is not None
        fresh = same and float(_LIVE_PNL_CACHE.get("at") or 0) > 0 and now - float(_LIVE_PNL_CACHE["at"]) < 8
        if fresh:
            return _apply_angel_snapshot(dict(cached))
        approved = [
            n
            for n in (load_state().live_approved or [])
            if n not in NEVER_LIVE_BOOKS and book_may_go_live(n)
        ]
        payload = _build_live_pnl(db, frozenset(LIVE_ELIGIBLE_BOOKS) | frozenset(approved))
        _LIVE_PNL_CACHE.update({"at": time.time(), "payload": payload, "db": str(db)})
        return _apply_angel_snapshot(dict(payload))
    finally:
        _LIVE_PNL_LOCK.release()


def _empty_live_pnl() -> dict[str, Any]:
    return {
        "trades": [],
        "open": [],
        "positions": [],
        "closed": [],
        "scoreboard": [],
        "orders": [],
        "placed_count": 0,
        "lots": 1,
        "summary": {
            "strategy": "LIVE",
            "closed": 0,
            "open": 0,
            "gross_pnl": 0.0,
            "charges": 0.0,
            "pnl_after_charges": 0.0,
            "win_rate_after_charges": 0.0,
        },
        "note": "Angel Gold Petal after charges (tax excluded). Open contracts are on Live Open Positions. Closed round-trips are on Blotter.",
    }


def _live_positions_by_book(
    open_t: list[dict[str, Any]],
    *,
    summaries: dict[str, dict[str, Any]] | None = None,
    live_gate: bool = True,
) -> list[dict[str, Any]]:
    """One row per live-picked book: OPEN trade if any, else Angel fill leftover, else FLAT.

    If Angel Gold Petal is already flat, leftover OPEN is a ghost — show FLAT.
    """
    from control_state import load_state
    from live_readiness import NEVER_LIVE_BOOKS, book_may_go_live

    open_by: dict[str, dict[str, Any]] = {}
    fill_open = net_open_from_fills()
    if angel_net_cache_is_flat():
        fill_open = {}
    else:
        for trade in open_t:
            name = str(trade.get("strategy") or "").strip()
            if name:
                open_by[name] = trade
    for name, row in fill_open.items():
        if name not in open_by:
            open_by[name] = dict(row)
    names: list[str] = []
    seen: set[str] = set()
    approved = list(load_state().live_approved or [])
    for raw in list(approved) + list(open_by.keys()):
        name = str(raw or "").strip()
        if not name or name in seen or name in NEVER_LIVE_BOOKS:
            continue
        if live_gate and name not in open_by and not book_may_go_live(name, summaries=summaries):
            continue
        seen.add(name)
        names.append(name)
    rows: list[dict[str, Any]] = []
    for name in names:
        trade = open_by.get(name)
        if trade:
            row = dict(trade)
            if row.get("lots") in (None, ""):
                row["lots"] = live_lots_for(name)
            if str(row.get("status") or "").upper() != "OPEN":
                row["status"] = "OPEN"
            rows.append(row)
            continue
        rows.append(
            {
                "strategy": name,
                "side": "FLAT",
                "status": "FLAT",
                "lots": live_lots_for(name),
                "entry_price": "",
                "exit_price": "",
                "pnl_after_charges": "",
                "entry_reason": "",
                "exit_reason": "",
            }
        )
    return rows


def _leftover_live_pnl() -> dict[str, Any]:
    """Fill-log leftover only. Used when the full Live P&L rebuild must not block LTP."""
    out = _empty_live_pnl()
    try:
        # Skip the 40% WR% paper rebuild — S19 FLAT still lists from live_approved.
        positions = _live_positions_by_book([], live_gate=False)
    except Exception:
        return out
    angel_open = [p for p in positions if str(p.get("status") or "") == "OPEN"]
    out["positions"] = positions
    out["open"] = list(angel_open)
    summary = dict(out["summary"])
    summary["open"] = len(angel_open)
    out["summary"] = summary
    if angel_open:
        out["note"] = (
            f"Angel still has {len(angel_open)} open book(s) on the fill log. "
            "Live AC ₹ is still loading. Exit on that OPEN row squares leftover "
            "Angel even in Paper. If you already squared in the Angel app, the "
            "bot clears this OPEN when Angel is flat — it does not send a new order. "
            "Does not Arm live."
        )
    return _apply_angel_snapshot(out)


def _apply_angel_snapshot(out: dict[str, Any]) -> dict[str, Any]:
    """Live open + Live AC follow Angel Gold Petal, not a stale fill log."""
    snap = angel_cache_fresh()
    if not snap:
        return out
    try:
        net = int(snap.get("net") or 0)
    except (TypeError, ValueError):
        net = 0
    pnl = snap.get("pnl")
    if pnl is None:
        pnl = snap.get("pnl_after_charges")
    p: float | None = None
    if pnl is not None:
        try:
            p = round(float(pnl), 2)
        except (TypeError, ValueError):
            p = None
    summary = dict(out.get("summary") or {})
    summary["angel_net"] = net
    summary["angel_ok"] = True
    flat = angel_net_cache_is_flat()
    if flat:
        flat_rows: list[dict[str, Any]] = []
        for row in list(out.get("positions") or []):
            r = dict(row)
            r["status"] = "FLAT"
            r["side"] = "FLAT"
            r["lots"] = 0
            if str(r.get("source") or "") in {"angel_fill", "angel_book"}:
                r["entry_reason"] = ""
            flat_rows.append(r)
        out["positions"] = flat_rows
        out["open"] = []
        summary["open"] = 0
        boards: list[dict[str, Any]] = []
        for board in list(out.get("scoreboard") or []):
            b = dict(board)
            b["open"] = 0
            boards.append(b)
        out["scoreboard"] = boards
        if p is not None:
            summary["pnl_after_charges"] = p
            summary["pnl_after_tax"] = p
            summary["angel_pnl"] = p
            for b in boards:
                if str(b.get("strategy") or "") == "LIVE":
                    b["pnl_after_charges"] = p
                    b["pnl_after_tax"] = p
            out["note"] = (
                "Angel Gold Petal is flat. Live AC ₹ is Angel after charges (tax excluded), "
                "not the old fill-tape total. Does not Arm live."
            )
        else:
            summary["pnl_after_charges"] = None
            summary["pnl_after_tax"] = None
            summary["angel_pnl"] = None
            out["note"] = (
                "Angel Gold Petal is flat — no open live contracts. "
                "Live AC ₹ is Angel (not the old tape). Waiting for Angel P&L. Does not Arm live."
            )
    elif p is not None:
        summary["pnl_after_charges"] = p
        summary["pnl_after_tax"] = p
        summary["angel_pnl"] = p
        out["note"] = (
            f"Angel Gold Petal net {net} lot(s). Live open and Live AC ₹ are Angel, "
            "not the fill log. Does not Arm live."
        )
    out["summary"] = summary
    return out


def _live_fill_lot_size(info: dict[str, Any], *, orders: list[dict[str, Any]]) -> float | None:
    return lots_on_fill_for_trade(info, orders=orders)


def _build_live_pnl(db: Path, eligible: frozenset[str]) -> dict[str, Any]:
    books = sorted(set(eligible) | set(_LIVE_PNL_BOOKS_EXTRA))
    rows: list[dict[str, Any]] = []
    from live_orders import iter_placed_orders

    fills = iter_placed_orders()
    try:
        for name in books:
            rows.extend(
                build_trades(
                    strategy=name,
                    db_path=db,
                    signal_limit=_SIGNAL_WINDOW,
                    lot_size=1.0,
                    live_only=True,
                    lot_size_for=lambda info, _orders=fills: _live_fill_lot_size(
                        info, orders=_orders
                    ),
                )
            )
    except Exception:
        rows = []
    lot_used = 1
    for trade in rows:
        try:
            n = int(float(trade.get("lots") or 1))
        except (TypeError, ValueError):
            n = 1
        if n > lot_used:
            lot_used = n
    open_t = [t for t in rows if t.get("status") == "OPEN"]
    closed = [t for t in rows if str(t.get("status", "")).startswith("CLOSED")]
    closed_rev = list(reversed(closed))
    positions = _live_positions_by_book(open_t)
    angel_open = [p for p in positions if str(p.get("status") or "") == "OPEN"]
    if angel_open:
        seen_open = {str(t.get("strategy") or "") for t in open_t}
        for row in angel_open:
            name = str(row.get("strategy") or "")
            if name and name not in seen_open:
                open_t.append(row)
                seen_open.add(name)
    board_names: list[str] = []
    seen: set[str] = set()
    for raw in [str(r.get("strategy") or "") for r in positions] + sorted(
        {str(t.get("strategy") or "") for t in rows if t.get("strategy")}
    ):
        name = str(raw or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        board_names.append(name)
    scoreboard = [summarize_trades(rows, s) for s in board_names]
    summary = summarize_trades(rows, None)
    summary["strategy"] = "LIVE"
    summary["open"] = len(angel_open)
    open_names = {
        str(p.get("strategy") or "")
        for p in angel_open
        if p.get("strategy")
    }
    for board in scoreboard:
        name = str(board.get("strategy") or "")
        if name in open_names:
            try:
                board["open"] = max(int(board.get("open") or 0), 1)
            except (TypeError, ValueError):
                board["open"] = 1
    orders = [_public_live_order(o) for o in recent_orders(limit=40)]
    placed = [o for o in orders if o.get("placed")]
    out = _empty_live_pnl()
    out.update(
        {
            "trades": (open_t + closed_rev)[:80],
            "open": list(angel_open),
            "positions": positions,
            "closed": closed_rev[:80],
            "scoreboard": scoreboard + [summary],
            "orders": orders,
            "placed_count": len(placed),
            "lots": lot_used,
            "summary": summary,
        }
    )
    skip_reasons = {
        str(o.get("reason") or "")
        for o in orders
        if o.get("skipped") or o.get("dry_run")
    }
    leftover = [p for p in angel_open if p.get("source") == "angel_fill"]
    dry = False
    try:
        from live_readiness import read_live_env

        dry = bool(read_live_env().get("dry_run"))
    except Exception:
        dry = False
    if leftover and dry:
        n_open = len(angel_open)
        lots_n = 0
        for p in angel_open:
            try:
                lots_n += int(float(p.get("lots") or 0) or 0)
            except (TypeError, ValueError):
                pass
        out["note"] = (
            f"Angel still has {n_open} open book(s), {lots_n} lots on the fill log. "
            "Paper mode does not send new Angel orders. Exit on that OPEN row "
            "squares leftover Angel even in Paper. If you already squared in the "
            "Angel app, the bot clears this OPEN when Angel is flat — it does not "
            "open a new book. Does not Arm live. "
            "Save strategies does not switch you to Paper."
        )
    elif "bot_still_paper_restart_required" in skip_reasons:
        out["note"] = (
            "Paper can fill while Angel does not: this bot started in paper. "
            "Type RESTART after Arm live. Live AC ₹ stays empty until an Angel order id appears."
        )
    elif not closed and not open_t and not placed:
        out["note"] = (
            "No Angel round-trips yet. Paper opens stay paper — they do not become live. "
            "Live is FLAT until the next live-picked entry. All live ₹ is Live AC; each book is "
            "Live P&L by book. After RESTART, the next emit shows on Angel orders (order id or Why)."
        )
    else:
        out["note"] = (
            "Live AC ₹ uses lots on each Angel fill, not today's Live Lots. "
            "A 3-lot close stays 3 lots after you arm 25. Angel app is the fill confirmation. "
            "Top KPIs are Angel after charges (tax excluded)."
        )
    return _apply_angel_snapshot(out)


def parse_tick_at(raw: str) -> datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    return dt.astimezone(IST)


def tape_freshness(*, last_tick_at: str = "", now: datetime | None = None) -> dict[str, Any]:
    """True when the last saved tick is within TAPE_LIVE_SEC of now (IST)."""
    clock = now or datetime.now(IST)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=IST)
    else:
        clock = clock.astimezone(IST)
    ts = parse_tick_at(last_tick_at)
    if ts is None:
        return {"last_tick_at": last_tick_at or "", "tape_age_sec": None, "tape_live": False}
    age = max(0.0, (clock - ts).total_seconds())
    return {
        "last_tick_at": last_tick_at or "",
        "tape_age_sec": round(age, 1),
        "tape_live": age <= TAPE_LIVE_SEC,
    }


def tick_feed_stale(
    *,
    idle_sec: float,
    market_open: bool,
    got_tick: bool,
    watchdog_sec: float = 45.0,
    first_tick_grace_sec: float = 90.0,
) -> bool:
    """Hung Angel socket: process alive, no ticks during the Gold Petal session."""
    if not market_open:
        return False
    limit = float(first_tick_grace_sec if not got_tick else watchdog_sec)
    return float(idle_sec) >= limit


def last_tick_snapshot(*, db_path: Path | None = None, now: datetime | None = None) -> dict[str, Any]:
    db = db_path or resolve_desk_db()
    try:
        meta = _last_tick_meta(db)
    except Exception:
        meta = {"tick_count": 0, "ltp": None, "last_tick_at": ""}
    fresh = tape_freshness(last_tick_at=str(meta.get("last_tick_at") or ""), now=now)
    return {**meta, **fresh}


def _last_tick_meta(db_path: Path) -> dict[str, Any]:
    empty = {"tick_count": 0, "ltp": None, "last_tick_at": ""}
    if not Path(db_path).is_file():
        return empty
    conn = sqlite3.connect(str(db_path), timeout=2.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout=2000")
        row = conn.execute(
            "SELECT id, received_at, ltp FROM ticks ORDER BY id DESC LIMIT 1"
        ).fetchone()
    except Exception:
        return empty
    finally:
        conn.close()
    if not row:
        return empty
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
    """Ticks / signals / live orders — paper trades stay on /api/history."""
    db = db_path or resolve_desk_db()
    payload: dict[str, Any] = {
        **db_info(db),
        "ticks": [],
        "signals": [],
        "live_orders": [],
        "live_pnl": _empty_live_pnl(),
        "tick_count": 0,
        "ltp": None,
        "last_tick_at": "",
        "tape_live": False,
        "tape_age_sec": None,
        "error": "",
    }
    try:
        payload["ticks"] = [row_to_dict(r) for r in latest_ticks(limit=tick_limit, db_path=db)]
        payload["signals"] = [
            row_to_dict(r) for r in latest_signals(limit=signal_limit, db_path=db)
        ]
        payload["live_orders"] = recent_orders(limit=40)
        payload.update(last_tick_snapshot(db_path=db))
    except Exception as exc:
        payload["error"] = str(exc)
    try:
        payload["live_pnl"] = live_pnl_payload(db_path=db, wait=False)
    except Exception as exc:
        payload["live_pnl"] = {**_empty_live_pnl(), "note": str(exc)}
    return payload


def _kick_paper_trades(db: Path) -> None:
    if _TRADE_LOCK.locked():
        return

    def run() -> None:
        try:
            all_trades_cached(db_path=db, wait=True)
        except Exception:
            pass

    threading.Thread(target=run, name="gp-paper-trades", daemon=True).start()


def all_trades_cached(*, db_path: Path | None = None, wait: bool = True) -> tuple[list[dict[str, Any]], str]:
    db = db_path or resolve_desk_db()
    now = time.time()
    same = _TRADE_CACHE.get("db") == str(db) and float(_TRADE_CACHE.get("at") or 0) > 0
    fresh = same and now - float(_TRADE_CACHE["at"]) < 12
    if fresh:
        return list(_TRADE_CACHE["rows"]), str(_TRADE_CACHE.get("error") or "")
    if not wait:
        if same:
            _kick_paper_trades(db)
            return list(_TRADE_CACHE["rows"]), str(_TRADE_CACHE.get("error") or "")
        _kick_paper_trades(db)
        return [], "trades still loading"
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
            for name in paper_strategy_names():
                rows.extend(
                    t
                    for t in build_trades(
                        strategy=name,
                        db_path=db,
                        signal_limit=_SIGNAL_WINDOW,
                        lot_size=paper_lots(),
                    )
                    if t.get("tape") != "live"
                )
        except Exception as exc:
            err = str(exc)
        _TRADE_CACHE.update({"at": time.time(), "rows": rows, "error": err, "db": str(db)})
        return list(rows), err
    finally:
        _TRADE_LOCK.release()


def paper_strategy_summaries(*, db_path: Path | None = None, wait: bool = True) -> dict[str, dict[str, Any]]:
    """Closed-trade WR% AC per paper book. Shared with the Live-tab 40% gate."""
    rows, _ = all_trades_cached(db_path=db_path, wait=wait)
    return {name: summarize_trades(rows, name) for name in paper_strategy_names()}


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
    scoreboard = [summarize_trades(all_rows, s) for s in paper_strategy_names()]
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
        "lots": paper_lots(),
    }
