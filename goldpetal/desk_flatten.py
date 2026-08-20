"""Per-book Exit on the desk — queue a flatten for the running bot.

One click = one strategy. Paper CLOSE always records. Angel CLOSE only if
that book is already live-armed (same gates as any other CLOSE). This
module never sets DRY_RUN=false, never ENABLE's a book, and never
Emergency-stops the rest of the desk.
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from control_state import (
    CONTROL_DIR,
    all_strategy_names,
    ensure_control_dir,
    paper_strategy_names,
)
from you_trade import bot_is_running

IST = ZoneInfo("Asia/Kolkata")
REQUEST_PATH = CONTROL_DIR / "flatten_requests.json"
STALE_PENDING_SEC = 900.0
STALE_FLIGHT_SEC = 90.0
KEEP = 50
_lock = threading.Lock()

REASON = "desk Exit button"


def _now_iso() -> str:
    return datetime.now(IST).isoformat(timespec="seconds")


def _aware(raw: str) -> datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    return dt.astimezone(IST)


def known_flatten_names() -> set[str]:
    names = set(paper_strategy_names()) | set(all_strategy_names())
    names.add("FLOW_BRAIN")
    names.discard("YOU_MANUAL")
    names.discard("S1_NETDELTA")
    return names


def load_flatten_file(path: Path = REQUEST_PATH) -> dict[str, Any]:
    if not path.is_file():
        return {"requests": []}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"requests": []}
    if not isinstance(raw, dict):
        return {"requests": []}
    rows = raw.get("requests")
    if not isinstance(rows, list):
        return {"requests": []}
    return {"requests": [r for r in rows if isinstance(r, dict)]}


def save_flatten_file(payload: dict[str, Any], path: Path = REQUEST_PATH) -> dict[str, Any]:
    ensure_control_dir(path)
    rows = list(payload.get("requests") or [])
    if len(rows) > KEEP:
        live = [r for r in rows if str(r.get("status") or "") in {"pending", "in_flight"}]
        done = [r for r in rows if str(r.get("status") or "") not in {"pending", "in_flight"}]
        rows = live + done[-(KEEP - len(live)) :]
    out = {"requests": rows, "updated_at_ist": _now_iso()}
    with _lock:
        path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    return out


def _stale(row: dict[str, Any], *, now: datetime | None = None) -> bool:
    status = str(row.get("status") or "")
    ts = _aware(str(row.get("updated_at_ist") or row.get("requested_at_ist") or ""))
    if ts is None:
        return status in {"pending", "in_flight"}
    clock = now or datetime.now(IST)
    age = (clock - ts).total_seconds()
    if status == "pending":
        return age > STALE_PENDING_SEC
    if status == "in_flight":
        return age > STALE_FLIGHT_SEC
    return False


def flatten_desk_status(
    *,
    path: Path = REQUEST_PATH,
    now: datetime | None = None,
) -> dict[str, Any]:
    data = load_flatten_file(path)
    rows = list(data.get("requests") or [])
    pending = [
        r
        for r in rows
        if str(r.get("status") or "") in {"pending", "in_flight"} and not _stale(r, now=now)
    ]
    by_strategy = {str(r.get("strategy") or ""): r for r in pending if r.get("strategy")}
    recent = [r for r in rows if str(r.get("status") or "") in {"done", "error"}][-8:]
    bot_ok = bot_is_running(now=now)
    return {
        "pending": pending,
        "by_strategy": by_strategy,
        "recent": recent,
        "bot_running": bot_ok,
        "note": (
            "Exit flattens that book only. Bot must be ON so CLOSE can fire. "
            "Paper CLOSE always records. Angel CLOSE only if that book is live-armed. "
            "Does not disable the book — uncheck In if you do not want it to re-open."
        ),
    }


def request_flatten(
    strategy: str,
    *,
    path: Path = REQUEST_PATH,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Queue a CLOSE for one book. The running bot claims it on the next tick."""
    name = str(strategy or "").strip()
    if not name:
        return {"ok": False, "queued": False, "error": "strategy required"}
    if name == "YOU_MANUAL":
        return {
            "ok": False,
            "queued": False,
            "error": "use the You tab CLOSE for YOU_MANUAL",
        }
    if name not in known_flatten_names():
        return {"ok": False, "queued": False, "error": f"unknown book {name}"}
    data = load_flatten_file(path)
    rows = list(data.get("requests") or [])
    clock = now or datetime.now(IST)
    stamp = clock.isoformat(timespec="seconds")
    changed = False
    for row in rows:
        if str(row.get("strategy") or "") != name:
            continue
        if str(row.get("status") or "") not in {"pending", "in_flight"}:
            continue
        if _stale(row, now=clock):
            row["status"] = "error"
            row["error"] = "bot did not ack in time"
            row["updated_at_ist"] = stamp
            changed = True
            continue
        if changed:
            save_flatten_file({"requests": rows}, path)
        return {
            "ok": True,
            "queued": False,
            "already_queued": True,
            "request": row,
            "bot_running": bot_is_running(now=clock),
            "note": "Exit already waiting on the bot for this book",
        }
    row = {
        "id": uuid.uuid4().hex[:10],
        "status": "pending",
        "strategy": name,
        "requested_at_ist": stamp,
        "updated_at_ist": stamp,
        "error": "",
        "result": {},
    }
    rows.append(row)
    save_flatten_file({"requests": rows}, path)
    bot_ok = bot_is_running(now=clock)
    note = (
        "queued — bot will CLOSE this book on the next tick"
        if bot_ok
        else "queued — Start bot / type RESTART so CLOSE can fire"
    )
    return {
        "ok": True,
        "queued": True,
        "request": row,
        "bot_running": bot_ok,
        "note": note,
    }


def take_flatten_requests(
    path: Path = REQUEST_PATH,
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Engine: claim every pending Exit (or [])."""
    data = load_flatten_file(path)
    rows = list(data.get("requests") or [])
    clock = now or datetime.now(IST)
    stamp = clock.isoformat(timespec="seconds")
    claimed: list[dict[str, Any]] = []
    changed = False
    for row in rows:
        status = str(row.get("status") or "")
        if status == "pending":
            if _stale(row, now=clock):
                row["status"] = "error"
                row["error"] = "bot did not ack in time"
                row["updated_at_ist"] = stamp
                changed = True
                continue
            row["status"] = "in_flight"
            row["updated_at_ist"] = stamp
            claimed.append(row)
            changed = True
        elif status == "in_flight" and _stale(row, now=clock):
            row["status"] = "error"
            row["error"] = "in_flight timed out"
            row["updated_at_ist"] = stamp
            changed = True
    if changed:
        save_flatten_file({"requests": rows}, path)
    return claimed


def finish_flatten(
    job: dict[str, Any],
    result: dict[str, Any],
    *,
    path: Path = REQUEST_PATH,
) -> dict[str, Any]:
    """Engine: mark one Exit done or error."""
    data = load_flatten_file(path)
    rows = list(data.get("requests") or [])
    jid = str(job.get("id") or "")
    ok = bool(result.get("ok"))
    row_out = {
        **job,
        "status": "done" if ok else "error",
        "error": "" if ok else str(result.get("error") or "flatten failed"),
        "result": result,
        "finished_at_ist": _now_iso(),
        "updated_at_ist": _now_iso(),
    }
    found = False
    for i, row in enumerate(rows):
        if str(row.get("id") or "") == jid:
            rows[i] = row_out
            found = True
            break
    if not found:
        rows.append(row_out)
    save_flatten_file({"requests": rows}, path)
    return row_out


def flatten_ram(obj: Any, *, px: float | None = None, why: str = REASON) -> dict[str, Any]:
    """Set one strategy object flat in RAM (and disk state if it has _flatten/_save_state)."""
    side = str(getattr(obj, "position", "flat") or "flat").lower()
    if side not in {"long", "short"}:
        return {"already_flat": True, "was_side": "flat"}
    fill = 0.0 if px is None else float(px)
    fn = getattr(obj, "_flatten", None)
    used = False
    if callable(fn):
        try:
            fn(fill, why)
            used = True
        except TypeError:
            try:
                fn(why)
                used = True
            except TypeError:
                used = False
    if not used:
        obj.position = "flat"
        if hasattr(obj, "entry_price"):
            obj.entry_price = None
        if hasattr(obj, "entry_cmp"):
            obj.entry_cmp = None
        if hasattr(obj, "entry_net_delta"):
            obj.entry_net_delta = None
        if hasattr(obj, "entry_date"):
            obj.entry_date = None
        save = getattr(obj, "_save_state", None)
        if callable(save):
            try:
                save()
            except Exception:
                pass
    if hasattr(obj, "last_skip"):
        obj.last_skip = "desk_exit"
    if str(getattr(obj, "position", "flat") or "flat").lower() != "flat":
        obj.position = "flat"
        if hasattr(obj, "entry_price"):
            obj.entry_price = None
    return {"already_flat": False, "was_side": side}


def apply_pending_flattens(
    strat_map: dict[str, Any],
    record_close: Callable[..., Any],
    *,
    now: datetime,
    cmp: float | None,
    path: Path = REQUEST_PATH,
) -> list[dict[str, Any]]:
    """Claim pending Exits, flatten RAM, record CLOSE. Used by the runner."""
    jobs = take_flatten_requests(path=path, now=now)
    out: list[dict[str, Any]] = []
    ts = now.isoformat(timespec="seconds")
    for job in jobs:
        name = str(job.get("strategy") or "")
        obj = strat_map.get(name)
        if obj is None:
            row = finish_flatten(
                job,
                {"ok": False, "error": "not in runner RAM — is this book In + Restarted?"},
                path=path,
            )
            out.append(row)
            continue
        info = flatten_ram(obj, px=cmp, why=REASON)
        if info.get("already_flat"):
            row = finish_flatten(
                job,
                {"ok": True, "already_flat": True, "was_side": "flat"},
                path=path,
            )
            out.append(row)
            continue
        try:
            res = record_close(
                time_label=ts,
                action="CLOSE",
                position_after="flat",
                reason=f"{REASON} was_{info.get('was_side')} queued={job.get('id')}",
                price_delta=None,
                net=0.0,
                net_delta=None,
                strategy=name,
                cmp=cmp,
            )
        except Exception as exc:
            row = finish_flatten(
                job,
                {"ok": False, "error": f"record failed: {exc}", "was_side": info.get("was_side")},
                path=path,
            )
            out.append(row)
            continue
        raw = res.to_dict() if hasattr(res, "to_dict") else (dict(res) if isinstance(res, dict) else {"ok": True})
        row = finish_flatten(
            job,
            {
                "ok": True,
                "already_flat": False,
                "was_side": info.get("was_side"),
                "broker": raw,
            },
            path=path,
        )
        out.append(row)
    return out
