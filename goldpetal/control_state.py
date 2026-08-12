"""Shared control-panel state: emergency stop, trading master switch, live gate.

Persisted as JSON so the runner and the web panel share one source of truth
without restarting the process (runner reloads on each tick / bar).
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
CONTROL_DIR = Path(__file__).resolve().parent / "data" / "control"
STATE_PATH = CONTROL_DIR / "state.json"

_lock = threading.Lock()


@dataclass
class ControlState:
    """Operator switches for the Gold Petal bot."""

    # Hard kill: blocks ALL new entries + tick-driven strategy emits.
    emergency_off: bool = False
    # Soft master: when false, strategies hold but ticks still archive.
    trading_enabled: bool = True
    # Live path stays locked until operator explicitly unlocks AND DRY_RUN=false.
    live_unlocked: bool = False
    # Strategies the operator approved for paper after weekend review.
    paper_approved: list[str] = field(default_factory=list)
    # Strategies approved to go live (still need live_unlocked + DRY_RUN=false).
    live_approved: list[str] = field(default_factory=list)
    # Strategies force-disabled from the panel (overrides ENABLE_* for entries).
    force_disabled: list[str] = field(default_factory=list)
    updated_at_ist: str = ""
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _now_iso() -> str:
    return datetime.now(IST).isoformat(timespec="seconds")


def default_state() -> ControlState:
    return ControlState(updated_at_ist=_now_iso(), note="defaults")


def ensure_control_dir(path: Path | None = None) -> Path:
    target = (path.parent if path is not None else CONTROL_DIR)
    target.mkdir(parents=True, exist_ok=True)
    return target


def _state_path(path: Path | None) -> Path:
    return path if path is not None else STATE_PATH


def load_state(path: Path | None = None) -> ControlState:
    path = _state_path(path)
    ensure_control_dir(path)
    if not path.exists():
        st = default_state()
        save_state(st, path=path)
        return st
    with _lock:
        raw = json.loads(path.read_text(encoding="utf-8"))
    return ControlState(
        emergency_off=bool(raw.get("emergency_off", False)),
        trading_enabled=bool(raw.get("trading_enabled", True)),
        live_unlocked=bool(raw.get("live_unlocked", False)),
        paper_approved=list(raw.get("paper_approved") or []),
        live_approved=list(raw.get("live_approved") or []),
        force_disabled=list(raw.get("force_disabled") or []),
        updated_at_ist=str(raw.get("updated_at_ist") or ""),
        note=str(raw.get("note") or ""),
    )


def save_state(state: ControlState, path: Path | None = None) -> ControlState:
    path = _state_path(path)
    ensure_control_dir(path)
    state.updated_at_ist = _now_iso()
    with _lock:
        path.write_text(json.dumps(state.to_dict(), indent=2), encoding="utf-8")
    return state


def set_emergency(off: bool, note: str = "", path: Path | None = None) -> ControlState:
    st = load_state(path)
    st.emergency_off = bool(off)
    st.note = note or ("EMERGENCY OFF" if off else "emergency cleared")
    return save_state(st, path=path)


def set_trading_enabled(enabled: bool, note: str = "", path: Path | None = None) -> ControlState:
    st = load_state(path)
    st.trading_enabled = bool(enabled)
    st.note = note or ("trading ON" if enabled else "trading OFF")
    return save_state(st, path=path)


def set_live_unlocked(unlocked: bool, note: str = "", path: Path | None = None) -> ControlState:
    st = load_state(path)
    st.live_unlocked = bool(unlocked)
    st.note = note or ("live unlocked" if unlocked else "live locked")
    return save_state(st, path=path)


def entries_blocked(path: Path | None = None) -> tuple[bool, str]:
    """True when new BUY/SHORT must be skipped."""
    st = load_state(path)
    if st.emergency_off:
        return True, "emergency_off"
    if not st.trading_enabled:
        return True, "trading_disabled"
    return False, ""


def strategy_entries_allowed(strategy: str, path: Path | None = None) -> tuple[bool, str]:
    blocked, reason = entries_blocked(path)
    if blocked:
        return False, reason
    st = load_state(path)
    if strategy in st.force_disabled:
        return False, "force_disabled"
    return True, ""


def is_live_mode_allowed(path: Path | None = None) -> tuple[bool, str]:
    """Live orders require: not emergency, trading on, live unlocked, DRY_RUN=false."""
    st = load_state(path)
    if st.emergency_off:
        return False, "emergency_off"
    if not st.trading_enabled:
        return False, "trading_disabled"
    if not st.live_unlocked:
        return False, "live_locked"
    dry = os.getenv("DRY_RUN", "true").strip().lower() in {"1", "true", "yes", "y"}
    if dry:
        return False, "DRY_RUN=true"
    return True, "ok"


def approve_strategy_paper(strategy: str, path: Path | None = None) -> ControlState:
    st = load_state(path)
    if strategy not in st.paper_approved:
        st.paper_approved.append(strategy)
    if strategy in st.force_disabled:
        st.force_disabled = [s for s in st.force_disabled if s != strategy]
    st.note = f"paper approved: {strategy}"
    return save_state(st, path=path)


def approve_strategy_live(strategy: str, path: Path | None = None) -> ControlState:
    st = approve_strategy_paper(strategy, path=path)
    if strategy not in st.live_approved:
        st.live_approved.append(strategy)
    st.note = f"live approved (still needs live_unlocked + DRY_RUN=false): {strategy}"
    return save_state(st, path=path)


def set_live_approved(
    strategies: list[str],
    *,
    path: Path | None = None,
    note: str = "",
) -> ControlState:
    """Replace the live-approved list (exact set for real-money strategies).

    Still requires live_unlocked + DRY_RUN=false before Angel orders fire.
    """
    st = load_state(path)
    seen: list[str] = []
    for raw in strategies:
        name = str(raw or "").strip()
        if name and name not in seen:
            seen.append(name)
    st.live_approved = seen
    for name in seen:
        if name not in st.paper_approved:
            st.paper_approved.append(name)
        if name in st.force_disabled:
            st.force_disabled = [s for s in st.force_disabled if s != name]
    st.note = note or (
        f"live_approved set: {', '.join(seen)}" if seen else "live_approved cleared"
    )
    return save_state(st, path=path)


# Known strategy ids (paper + live). Used for allowlist lock.
ALL_STRATEGY_NAMES: tuple[str, ...] = (
    "S1_NETDELTA",
    "S2_BALANCE",
    "S3_ML",
    "S4_OVERNIGHT",
    "S5_MINEDGE",
    "S6_MIN30",
    "S8_NET_ZIGZAG",
    "S9_STATE30",
    "S10_LEGACY30",
    "S11_DISCOVERED",
    "S12_HHHL30",
)

SLIM_PAPER_STRATEGIES: tuple[str, ...] = (
    "S4_OVERNIGHT",
    "S5_MINEDGE",
    "S8_NET_ZIGZAG",
    "S11_DISCOVERED",
    "S12_HHHL30",
)


def set_paper_allowlist(
    allowed: list[str],
    *,
    path: Path | None = None,
    known: tuple[str, ...] | None = None,
    note: str = "",
) -> ControlState:
    """Only ``allowed`` strategies may open new paper/live entries.

    Everyone else is force-disabled (blocks BUY/SHORT even if ENABLE_S*=true).
    Also keep ENABLE_S9=false etc. in .env and restart so disabled strategies
    are not loaded / do not emit.
    """
    st = load_state(path)
    allow: list[str] = []
    for raw in allowed:
        name = str(raw or "").strip()
        if name and name not in allow:
            allow.append(name)
    universe = known or ALL_STRATEGY_NAMES
    disabled = [name for name in universe if name not in set(allow)]
    st.force_disabled = disabled
    # Allowed strategies must not remain force-disabled.
    for name in allow:
        if name not in st.paper_approved:
            st.paper_approved.append(name)
    st.note = note or (
        f"paper allowlist: {', '.join(allow) or '(none)'}; "
        f"force_disabled: {', '.join(disabled) or '(none)'}"
    )
    return save_state(st, path=path)


def reject_strategy(strategy: str, path: Path | None = None) -> ControlState:
    st = load_state(path)
    st.paper_approved = [s for s in st.paper_approved if s != strategy]
    st.live_approved = [s for s in st.live_approved if s != strategy]
    if strategy not in st.force_disabled:
        st.force_disabled.append(strategy)
    st.note = f"rejected / force-disabled: {strategy}"
    return save_state(st, path=path)
