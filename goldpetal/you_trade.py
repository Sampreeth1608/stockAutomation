"""You-tab live orders — queue for the running bot. Not a paper book.

BUY / SHORT / CLOSE from the You tab always record the tape first.
Angel only fires when live is already armed (Paper off + type LIVE +
Unlock) AND you type YOU on the click AND the engine is running.
This module never sets DRY_RUN=false. YOU_MANUAL is not ENABLE'd.
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from control_state import CONTROL_DIR, ensure_control_dir, is_live_mode_allowed, load_state
from live_readiness import bot_age_seconds, read_live_env
from live_orders import live_lots
from position_safety import read_bot_health

IST = ZoneInfo("Asia/Kolkata")
YOU_BOOK = "YOU_MANUAL"
CONFIRM_WORD = "YOU"
ORDER_PATH = CONTROL_DIR / "you_order.json"
POS_PATH = Path(__file__).resolve().parent / "data" / "human_capture" / "you_position.json"
STALE_SEC = 90.0
_lock = threading.Lock()

ACTIONS = frozenset({"BUY", "SHORT", "CLOSE"})


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


def bot_is_running(*, now: datetime | None = None) -> bool:
    """True when the engine has a recent heartbeat (You live goes through it)."""
    health = read_bot_health()
    age = bot_age_seconds(health, now=now)
    if age is not None and age <= 180.0:
        return True
    try:
        from desk_data import bot_live_ticks_db

        return bot_live_ticks_db() is not None
    except Exception:
        return False


def load_you_position(path: Path = POS_PATH) -> dict[str, Any]:
    if not path.is_file():
        return {"side": "flat", "entry_px": None, "updated_at_ist": "", "example_id": ""}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"side": "flat", "entry_px": None, "updated_at_ist": "", "example_id": ""}
    side = str(raw.get("side") or "flat").lower()
    if side not in {"long", "short", "flat"}:
        side = "flat"
    return {
        "side": side,
        "entry_px": raw.get("entry_px"),
        "updated_at_ist": str(raw.get("updated_at_ist") or ""),
        "example_id": str(raw.get("example_id") or ""),
        "order_id": str(raw.get("order_id") or ""),
    }


def save_you_position(
    side: str,
    *,
    entry_px: float | None = None,
    example_id: str = "",
    order_id: str = "",
    path: Path = POS_PATH,
) -> dict[str, Any]:
    pos = {
        "side": str(side or "flat").lower(),
        "entry_px": entry_px,
        "example_id": str(example_id or ""),
        "order_id": str(order_id or ""),
        "updated_at_ist": _now_iso(),
    }
    if pos["side"] not in {"long", "short", "flat"}:
        pos["side"] = "flat"
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        path.write_text(json.dumps(pos, indent=2), encoding="utf-8")
    return pos


def load_you_order(path: Path = ORDER_PATH) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def save_you_order(row: dict[str, Any], path: Path = ORDER_PATH) -> dict[str, Any]:
    ensure_control_dir(path)
    payload = dict(row)
    payload["updated_at_ist"] = _now_iso()
    with _lock:
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def _stale(row: dict[str, Any] | None, *, now: datetime | None = None) -> bool:
    if not row:
        return False
    status = str(row.get("status") or "")
    if status not in {"pending", "in_flight"}:
        return False
    ts = _aware(str(row.get("updated_at_ist") or row.get("requested_at_ist") or ""))
    if ts is None:
        return True
    clock = now or datetime.now(IST)
    return (clock - ts).total_seconds() > STALE_SEC


def you_live_status(
    *,
    order_path: Path = ORDER_PATH,
    pos_path: Path = POS_PATH,
    now: datetime | None = None,
) -> dict[str, Any]:
    live_ok, live_why = is_live_mode_allowed()
    env = read_live_env()
    st = load_state()
    bot_ok = bot_is_running(now=now)
    pending = load_you_order(path=order_path)
    if _stale(pending, now=now) and pending is not None:
        pending = save_you_order(
            {**pending, "status": "error", "error": "bot did not ack in time"},
            path=order_path,
        )
    pos = load_you_position(path=pos_path)
    dry = bool(env["dry_run"])
    would = bool(live_ok and bot_ok)
    if dry:
        why = "DRY_RUN=true — uncheck Paper only, type LIVE, Save, Unlock, Restart"
    elif not st.live_unlocked:
        why = "live locked — Unlock live on Live money"
    elif not live_ok:
        why = live_why
    elif not bot_ok:
        why = "bot not running — Start bot / RESTART so Angel can fire"
    else:
        why = "armed — type YOU on BUY/SHORT/CLOSE to send 1 lot (LIVE_MAX cap)"
    return {
        "live_ok": live_ok,
        "live_why": live_why,
        "dry_run": dry,
        "live_unlocked": bool(st.live_unlocked),
        "emergency_off": bool(st.emergency_off),
        "trading_enabled": bool(st.trading_enabled),
        "bot_running": bot_ok,
        "lots": live_lots(),
        "would_place": would,
        "why": why,
        "position": pos,
        "pending": pending,
        "confirm_word": CONFIRM_WORD,
        "book": YOU_BOOK,
        "note": (
            "You tab always records LTP/TBQ/TSQ/ticks/candles. "
            "Angel only when live is armed and you type YOU. "
            "Never ENABLE. This tab does not set DRY_RUN=false."
        ),
    }


def request_you_order(
    action: str,
    *,
    confirm: str = "",
    example_id: str = "",
    entry_px: float | None = None,
    order_path: Path = ORDER_PATH,
    pos_path: Path = POS_PATH,
    now: datetime | None = None,
    require_bot: bool = True,
) -> dict[str, Any]:
    """Queue one MARKET order for the engine. Record-only if gates fail."""
    act = str(action or "").strip().upper()
    if act in {"BUY_LONG", "LONG"}:
        act = "BUY"
    if act in {"SELL", "SHORT_SELL"}:
        act = "SHORT"
    if act not in ACTIONS:
        return {"ok": False, "queued": False, "error": "action must be BUY, SHORT, or CLOSE"}
    if str(confirm or "").strip() != CONFIRM_WORD:
        return {
            "ok": False,
            "queued": False,
            "error": f"type {CONFIRM_WORD} to send this click to Angel",
        }
    live_ok, live_why = is_live_mode_allowed()
    if not live_ok:
        return {
            "ok": False,
            "queued": False,
            "error": f"live blocked ({live_why}) — recorded only",
            "live_why": live_why,
        }
    if require_bot and not bot_is_running(now=now):
        return {
            "ok": False,
            "queued": False,
            "error": "bot not running — recorded only. Start bot so Angel can fire",
        }
    existing = load_you_order(path=order_path)
    if existing and str(existing.get("status") or "") in {"pending", "in_flight"}:
        if not _stale(existing, now=now):
            return {
                "ok": False,
                "queued": False,
                "error": "a You order is already waiting on the bot",
                "pending": existing,
            }
    pos = load_you_position(path=pos_path)
    side = str(pos.get("side") or "flat")
    if act == "CLOSE" and side == "flat":
        return {"ok": False, "queued": False, "error": "already flat — nothing to close"}
    if act == "BUY" and side == "long":
        return {"ok": False, "queued": False, "error": "already long"}
    if act == "SHORT" and side == "short":
        return {"ok": False, "queued": False, "error": "already short"}
    row = {
        "id": uuid.uuid4().hex[:10],
        "status": "pending",
        "action": act,
        "example_id": str(example_id or ""),
        "entry_px": entry_px,
        "requested_at_ist": _now_iso(),
        "lots": live_lots(),
        "book": YOU_BOOK,
        "error": "",
        "result": {},
    }
    save_you_order(row, path=order_path)
    return {"ok": True, "queued": True, "order": row}


def take_pending_you_order(
    path: Path = ORDER_PATH,
    *,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Engine: claim one pending You order (or None)."""
    row = load_you_order(path=path)
    if not row:
        return None
    if str(row.get("status") or "") != "pending":
        if _stale(row, now=now) and str(row.get("status") or "") == "in_flight":
            save_you_order(
                {**row, "status": "error", "error": "in_flight timed out"},
                path=path,
            )
        return None
    row["status"] = "in_flight"
    save_you_order(row, path=path)
    return row


def finish_you_order(
    job: dict[str, Any],
    result: Any,
    *,
    order_path: Path = ORDER_PATH,
    pos_path: Path = POS_PATH,
) -> dict[str, Any]:
    """Engine: mark Angel result and update the You position file."""
    raw = result.to_dict() if hasattr(result, "to_dict") else dict(result or {})
    ok = bool(raw.get("ok")) and not bool(raw.get("skipped")) and not bool(raw.get("dry_run"))
    act = str(job.get("action") or "").upper()
    if ok:
        if act == "BUY":
            side = "long"
        elif act == "SHORT":
            side = "short"
        else:
            side = "flat"
        px = job.get("entry_px")
        save_you_position(
            side,
            entry_px=None if side == "flat" else px,
            example_id=str(job.get("example_id") or ""),
            order_id=str(raw.get("order_id") or ""),
            path=pos_path,
        )
        status = "done"
        error = ""
    else:
        status = "error"
        error = str(raw.get("reason") or "angel skipped")
    row = {
        **job,
        "status": status,
        "error": error,
        "result": raw,
        "finished_at_ist": _now_iso(),
    }
    return save_you_order(row, path=order_path)


def map_capture_action(action: str) -> str | None:
    act = str(action or "").strip().lower().replace("-", "_")
    if act in {"buy", "long"}:
        return "BUY"
    if act in {"short", "sell"}:
        return "SHORT"
    if act == "close":
        return "CLOSE"
    return None
