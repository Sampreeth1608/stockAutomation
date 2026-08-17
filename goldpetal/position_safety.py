"""Restart / orphan / EOD safety for intraday strategies (S5/S8/…).

Institutional minimum:
  - Restore RAM position from last DB signal after restart (so exits can fire)
  - Or auto-CLOSE orphans when restore is impossible / mode=close
  - Flatten intraday books in the last N minutes before MARKET_CLOSE
    (S12/S13 skipped — they only exit on same-candle HH/LL confirm)
  - Write data/control/bot_health.json for the desk
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
ROOT = Path(__file__).resolve().parent
HEALTH_PATH = ROOT / "data" / "control" / "bot_health.json"

# Intraday strategies that lose RAM state on restart.
INTRADAY_RESTORE = (
    "S5_MINEDGE",
    "S8_NET_ZIGZAG",
    "S12_HHHL30",
    "S6_MIN30",
    "S2_BALANCE",
    "S3_ML",
    "S9_STATE30",
    "S10_LEGACY30",
    "S11_DISCOVERED",
)

# S12 must only flatten on same-candle last-minute HH/LL (confirm window
# overlaps the last 5m before MARKET_CLOSE). S13 is a multi-day hold.
EOD_FLATTEN_SKIP = frozenset({"S12_HHHL30", "S13_HHHL_DAY"})


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


def restart_mode() -> str:
    """restore | close | off — what to do with open DB positions at startup."""
    raw = (os.getenv("POSITION_ON_RESTART") or "restore").strip().lower()
    if raw in {"restore", "close", "off"}:
        return raw
    if raw in {"0", "false", "no"}:
        return "off"
    return "restore"


def eod_flatten_enabled() -> bool:
    return _env_flag("EOD_FLATTEN_INTRADAY", True)


def eod_flatten_minutes() -> int:
    try:
        return max(1, int(float(os.getenv("EOD_FLATTEN_MINUTES", "5"))))
    except ValueError:
        return 5


@dataclass
class OpenPosition:
    strategy: str
    side: str  # long | short
    entry_price: float | None
    time_label: str
    action: str
    cmp: float | None


def last_open_position(strategy: str) -> OpenPosition | None:
    """If the latest signal leaves the strategy in a trade, return it."""
    from storage import list_signals

    rows = list(list_signals(strategy=strategy))
    if not rows:
        return None
    last = rows[-1]
    action = str(last["action"] or "").upper()
    pos = str(last["position_after"] or "").lower()
    if action == "CLOSE" or pos == "flat":
        return None
    side: str | None = None
    if pos in {"long", "short"}:
        side = pos
    elif action in {"BUY", "REVERSE_LONG"}:
        side = "long"
    elif action in {"SHORT", "REVERSE_SHORT"}:
        side = "short"
    if side is None:
        return None
    cmp = last["cmp"]
    try:
        entry = float(cmp) if cmp is not None else None
    except (TypeError, ValueError):
        entry = None
    return OpenPosition(
        strategy=strategy,
        side=side,
        entry_price=entry,
        time_label=str(last["time_label"] or ""),
        action=action,
        cmp=entry,
    )


def apply_position_to_strategy(strategy_obj: Any, open_pos: OpenPosition) -> bool:
    """Set RAM position/entry on a live strategy object. Returns True if applied."""
    if strategy_obj is None:
        return False
    name = getattr(strategy_obj, "name", "") or ""
    cls = type(strategy_obj).__name__
    if cls == "DisabledStrategy" or "DISABLED" in str(
        getattr(strategy_obj, "status_line", "")
    ).upper():
        return False
    if not hasattr(strategy_obj, "position"):
        return False
    strategy_obj.position = open_pos.side  # type: ignore[assignment]
    if hasattr(strategy_obj, "entry_price"):
        strategy_obj.entry_price = open_pos.entry_price
    # Best-effort: give S5 something to manage against until ATR warms
    if name == "S5_MINEDGE" and open_pos.entry_price is not None:
        if getattr(strategy_obj, "target_points", None) is None:
            strategy_obj.target_points = float(
                getattr(strategy_obj, "required_points", 50) or 50
            )
        if getattr(strategy_obj, "stop_points", None) is None:
            strategy_obj.stop_points = float(strategy_obj.target_points) * 0.45
    return True


def startup_reconcile(
    strategies: dict[str, Any],
    *,
    mode: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Restore or close open DB positions for known strategies.

    Returns:
      {
        "mode": ...,
        "restored": [{"strategy", "side", "entry_price", "time_label"}, ...],
        "closes": [{"strategy", "side", "entry_price", "time_label", "reason"}, ...],
        "messages": [str, ...],
      }
    """
    del now  # reserved for future stale-age rules
    mode = (mode or restart_mode()).lower()
    restored: list[dict[str, Any]] = []
    closes: list[dict[str, Any]] = []
    messages: list[str] = []

    if mode == "off":
        messages.append("POSITION_ON_RESTART=off — leaving DB opens unmanaged")
        return {"mode": mode, "restored": restored, "closes": closes, "messages": messages}

    names = sorted(set(strategies) | set(INTRADAY_RESTORE))
    for name in names:
        open_pos = last_open_position(name)
        if open_pos is None:
            continue
        obj = strategies.get(name)

        if mode == "close":
            closes.append(
                {
                    "strategy": name,
                    "side": open_pos.side,
                    "entry_price": open_pos.entry_price,
                    "time_label": open_pos.time_label,
                    "cmp": open_pos.cmp,
                    "reason": "startup orphan auto-CLOSE (POSITION_ON_RESTART=close)",
                }
            )
            messages.append(
                f"ORPHAN CLOSE {name} was_{open_pos.side} since {open_pos.time_label}"
            )
            continue

        # mode == restore
        if obj is not None and apply_position_to_strategy(obj, open_pos):
            restored.append(
                {
                    "strategy": name,
                    "side": open_pos.side,
                    "entry_price": open_pos.entry_price,
                    "time_label": open_pos.time_label,
                }
            )
            messages.append(
                f"RESTORED {name} {open_pos.side} @ {open_pos.entry_price} "
                f"from {open_pos.time_label}"
            )
            continue

        closes.append(
            {
                "strategy": name,
                "side": open_pos.side,
                "entry_price": open_pos.entry_price,
                "time_label": open_pos.time_label,
                "cmp": open_pos.cmp,
                "reason": "startup orphan auto-CLOSE (strategy not restorable / disabled)",
            }
        )
        messages.append(
            f"ORPHAN CLOSE {name} was_{open_pos.side} since {open_pos.time_label} "
            f"(not restorable)"
        )

    return {"mode": mode, "restored": restored, "closes": closes, "messages": messages}


def in_eod_flatten_window(
    now: datetime,
    *,
    market_close: str = "23:30",
    minutes: int | None = None,
) -> bool:
    """True in the last N minutes before MARKET_CLOSE (same calendar day)."""
    if not eod_flatten_enabled():
        return False
    now = now.astimezone(IST)
    if now.weekday() >= 5:
        return False
    minutes = eod_flatten_minutes() if minutes is None else minutes
    try:
        hh, mm = market_close.strip().split(":")
        close_dt = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
    except ValueError:
        return False
    start = close_dt - timedelta(minutes=max(1, minutes))
    return start <= now <= close_dt


def intraday_open_for_flatten(strategies: dict[str, Any]) -> list[dict[str, Any]]:
    """Which loaded intraday strategies currently hold a RAM position."""
    out: list[dict[str, Any]] = []
    for name, obj in strategies.items():
        if name not in INTRADAY_RESTORE:
            continue
        if name in EOD_FLATTEN_SKIP:
            continue
        pos = getattr(obj, "position", "flat")
        if pos in {"long", "short"}:
            out.append(
                {
                    "strategy": name,
                    "side": pos,
                    "entry_price": getattr(obj, "entry_price", None),
                }
            )
    return out


def write_bot_health(payload: dict[str, Any], path: Path | None = None) -> Path:
    path = path or HEALTH_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    body = dict(payload)
    body["ts_ist"] = datetime.now(IST).isoformat(timespec="seconds")
    path.write_text(json.dumps(body, indent=2, default=str), encoding="utf-8")
    return path


def read_bot_health(path: Path | None = None) -> dict[str, Any]:
    path = path or HEALTH_PATH
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def emit_startup_closes(
    closes: list[dict[str, Any]],
    *,
    record: Callable[..., None],
    symbol: str = "GOLDM",
) -> None:
    """Write CLOSE signals for startup orphan closes via the runner's recorder."""
    now = datetime.now(IST).isoformat(timespec="seconds")
    for row in closes:
        record(
            time_label=now,
            action="CLOSE",
            position_after="flat",
            reason=str(row.get("reason") or "startup orphan auto-CLOSE"),
            price_delta=None,
            net=0.0,
            net_delta=None,
            strategy=str(row["strategy"]),
            cmp=row.get("cmp"),
        )
