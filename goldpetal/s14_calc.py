"""S14 finished-candle calculation for the operator desk.

Same formula as live S14. Built from ticks.db 30m bars. Forming bar is skipped.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from strategy_wick import _parse_ts, s14_bar_decision
from wick_candles import wick_measure

IST = ZoneInfo("Asia/Kolkata")

FORMULA = (
    "Wait for the 30m candle to finish. Same closed candle, in order: "
    "open=high → SHORT | open=low → LONG | both (flat) → wick | "
    "else wick: upper=high−max(open,close)  lower=min(open,close)−low | "
    "L>U LONG, U>L SHORT, equal skip. "
    "FLIP if already the other side. Fill at that bar's close."
)

_CACHE: dict[str, Any] = {"at": 0.0, "db": "", "payload": None}


def floor_bar(ts: datetime, minutes: int = 30) -> datetime:
    ts = ts.astimezone(IST)
    midnight = ts.replace(hour=0, minute=0, second=0, microsecond=0)
    mins = int((ts - midnight).total_seconds() // 60)
    block = (mins // max(1, minutes)) * max(1, minutes)
    return midnight + timedelta(minutes=block)


def explain_bar(
    *,
    time: str,
    o: float,
    h: float,
    l: float,
    c: float,
    pos: str,
    open_hold: bool = True,
    forming: bool = False,
) -> dict[str, Any]:
    m = wick_measure(o, h, l, c)
    oh = float(h) == float(o)
    ol = float(l) == float(o)
    side, why = s14_bar_decision(o, h, l, c, open_hold=open_hold)
    prev = pos
    action = "skip"
    pos_after = pos
    if forming:
        action = "forming"
        why = "still forming — not decided"
        side_out: str = "—"
    elif side is None:
        action = "skip"
        side_out = "skip"
    elif side == pos:
        action = "hold"
        side_out = side
    else:
        action = "FLIP" if pos != "flat" else "enter"
        pos_after = side
        side_out = side
    return {
        "time": time,
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "upper": round(m.upper, 2),
        "lower": round(m.lower, 2),
        "open_eq_high": oh,
        "open_eq_low": ol,
        "rule": why,
        "side": side_out,
        "action": action,
        "pos_before": prev,
        "pos_after": pos_after,
        "forming": forming,
    }


def walk_candles(
    candles: list[dict[str, Any]], *, open_hold: bool = True
) -> list[dict[str, Any]]:
    pos = "flat"
    out: list[dict[str, Any]] = []
    for bar in candles:
        row = explain_bar(
            time=str(bar["time"]),
            o=float(bar["open"]),
            h=float(bar["high"]),
            l=float(bar["low"]),
            c=float(bar["close"]),
            pos=pos,
            open_hold=open_hold,
        )
        pos = str(row["pos_after"])
        out.append(row)
    return out


def ohlc_bars_from_ticks(
    db: Path,
    *,
    minutes: int = 30,
    max_bars: int = 48,
    now: datetime | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Finished 30m OHLC plus the still-forming bar (not decided)."""
    from storage import connect, init_db

    now = (now or datetime.now(IST)).astimezone(IST)
    lookback = timedelta(minutes=max(1, minutes) * (max_bars + 4)) + timedelta(days=1)
    start = (now - lookback).strftime("%Y-%m-%dT%H:%M:%S")
    init_db(db)
    with connect(db) as conn:
        rows = conn.execute(
            "SELECT received_at, ltp FROM ticks "
            "WHERE ltp IS NOT NULL AND received_at >= ? "
            "ORDER BY received_at ASC, id ASC",
            (start,),
        ).fetchall()
    buckets: dict[datetime, dict[str, Any]] = {}
    order: list[datetime] = []
    for received_at, ltp in rows:
        ts = _parse_ts(str(received_at))
        if ts is None:
            continue
        key = floor_bar(ts, minutes)
        px = float(ltp)
        if key not in buckets:
            buckets[key] = {
                "time": key.isoformat(timespec="seconds"),
                "open": px,
                "high": px,
                "low": px,
                "close": px,
            }
            order.append(key)
        else:
            b = buckets[key]
            b["high"] = max(float(b["high"]), px)
            b["low"] = min(float(b["low"]), px)
            b["close"] = px
    finished: list[dict[str, Any]] = []
    forming: dict[str, Any] | None = None
    for key in order:
        b = buckets[key]
        end = key + timedelta(minutes=max(1, minutes))
        if now >= end:
            finished.append(b)
        else:
            forming = b
    return finished[-max_bars:], forming


def _pts(side: str, entry: float, exit_px: float) -> float:
    if side == "BUY":
        return round(exit_px - entry, 2)
    return round(entry - exit_px, 2)


def book_from_walk(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Opened/closed S14 book from the formula walk. Leftover stays OPEN."""
    closed: list[dict[str, Any]] = []
    open_t: dict[str, Any] | None = None
    for row in rows:
        if row["action"] not in {"enter", "FLIP"}:
            continue
        side = "BUY" if str(row["side"]).lower() == "long" else "SHORT"
        px = float(row["close"])
        ts = str(row["time"])
        why = f"{row['action']} {row['rule']} U={row['upper']} L={row['lower']}"
        if open_t is not None:
            pts = _pts(str(open_t["side"]), float(open_t["entry_price"]), px)
            closed.append(
                {
                    **open_t,
                    "status": "CLOSED",
                    "exit_ts": ts,
                    "exit_price": px,
                    "exit_reason": why,
                    "pnl_pts": pts,
                }
            )
        open_t = {
            "strategy": "S14_WICK30_STRICT",
            "side": side,
            "status": "OPEN",
            "entry_ts": ts,
            "entry_price": px,
            "entry_reason": why,
            "exit_ts": "",
            "exit_price": "",
            "exit_reason": "",
            "pnl_pts": "",
            "rule": row["rule"],
            "upper": row["upper"],
            "lower": row["lower"],
        }
    return {"open": open_t, "closed": list(reversed(closed))}


def calc_payload(
    db: Path,
    *,
    minutes: int = 30,
    max_bars: int = 48,
    now: datetime | None = None,
) -> dict[str, Any]:
    finished, forming = ohlc_bars_from_ticks(
        db, minutes=minutes, max_bars=max_bars, now=now
    )
    rows = walk_candles(finished)
    book = book_from_walk(rows)
    forming_row = None
    if forming is not None:
        pos = str(rows[-1]["pos_after"]) if rows else "flat"
        forming_row = explain_bar(
            time=str(forming["time"]),
            o=float(forming["open"]),
            h=float(forming["high"]),
            l=float(forming["low"]),
            c=float(forming["close"]),
            pos=pos,
            forming=True,
        )
    return {
        "formula": FORMULA,
        "tf": f"{minutes}m",
        "db_path": str(db),
        "bars": list(reversed(rows)),
        "forming": forming_row,
        "open": book["open"],
        "closed": book["closed"],
        "total_open": 1 if book["open"] else 0,
        "total_closed": len(book["closed"]),
        "n_finished": len(rows),
        "error": "" if (rows or forming_row) else "no 30m bars in this ticks.db",
    }


def cached_calc_payload(
    db: Path,
    *,
    minutes: int = 30,
    max_bars: int = 48,
) -> dict[str, Any]:
    now = time.time()
    if (
        _CACHE["payload"] is not None
        and _CACHE.get("db") == str(db)
        and now - float(_CACHE["at"]) < 8
    ):
        return dict(_CACHE["payload"])
    payload = calc_payload(db, minutes=minutes, max_bars=max_bars)
    _CACHE.update({"at": now, "db": str(db), "payload": payload})
    return dict(payload)
