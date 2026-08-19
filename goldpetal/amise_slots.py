"""AMISE challenger slots S21–S24.

The factory invents genomes. You Approve on Lab. This module names the
next free slot (S21_AMISE … S24_AMISE), writes the genome, and turns
paper ENABLE on. It never sets DRY_RUN=false. Angel still needs Unlock
+ LIVE on the desk. Empty slots do not trade.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from strategy_genome import StrategyGenome, genome_from_dict

IST = ZoneInfo("Asia/Kolkata")
ROOT = Path(__file__).resolve().parent
SLOTS_DIR = ROOT / "data" / "amise" / "slots"
INDEX_NAME = "index.json"

AMISE_SLOT_BOOKS: tuple[str, ...] = (
    "S21_AMISE",
    "S22_AMISE",
    "S23_AMISE",
    "S24_AMISE",
)
AMISE_ENABLE: dict[str, str] = {
    "S21_AMISE": "ENABLE_S21",
    "S22_AMISE": "ENABLE_S22",
    "S23_AMISE": "ENABLE_S23",
    "S24_AMISE": "ENABLE_S24",
}
AMISE_ENABLE_KEYS: frozenset[str] = frozenset(AMISE_ENABLE.values())
SHORT_AMISE = {
    "S21_AMISE": "S21 AMISE",
    "S22_AMISE": "S22 AMISE",
    "S23_AMISE": "S23 AMISE",
    "S24_AMISE": "S24 AMISE",
}


def _now_iso() -> str:
    return datetime.now(IST).isoformat(timespec="seconds")


def is_amise_slot(name: str) -> bool:
    return str(name or "").strip() in AMISE_SLOT_BOOKS


def enable_key(slot: str) -> str:
    key = AMISE_ENABLE.get(str(slot or "").strip())
    if not key:
        raise KeyError(f"not an AMISE slot: {slot}")
    return key


def slots_dir(path: Path | None = None) -> Path:
    dest = path or SLOTS_DIR
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def _index_path(folder: Path) -> Path:
    return folder / INDEX_NAME


def load_index(folder: Path | None = None) -> dict[str, Any]:
    dest = _index_path(slots_dir(folder))
    if not dest.is_file():
        return {"slots": {}, "updated_at_ist": ""}
    try:
        raw = json.loads(dest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"slots": {}, "updated_at_ist": ""}
    if not isinstance(raw, dict):
        return {"slots": {}, "updated_at_ist": ""}
    slots = raw.get("slots") if isinstance(raw.get("slots"), dict) else {}
    return {"slots": dict(slots), "updated_at_ist": str(raw.get("updated_at_ist") or "")}


def save_index(payload: dict[str, Any], folder: Path | None = None) -> Path:
    dest = _index_path(slots_dir(folder))
    body = {
        "slots": dict(payload.get("slots") or {}),
        "updated_at_ist": payload.get("updated_at_ist") or _now_iso(),
    }
    dest.write_text(json.dumps(body, indent=2), encoding="utf-8")
    return dest


def slot_file(slot: str, folder: Path | None = None) -> Path:
    return slots_dir(folder) / f"{slot}.json"


def load_slot(slot: str, folder: Path | None = None) -> dict[str, Any]:
    path = slot_file(slot, folder)
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def load_slot_genome(slot: str, folder: Path | None = None) -> StrategyGenome | None:
    raw = load_slot(slot, folder)
    g = raw.get("genome") if isinstance(raw.get("genome"), dict) else None
    if not g:
        return None
    return genome_from_dict(g)


def allocated_slots(folder: Path | None = None) -> list[str]:
    idx = load_index(folder)
    out: list[str] = []
    for name in AMISE_SLOT_BOOKS:
        row = (idx.get("slots") or {}).get(name) or {}
        if row.get("genome_id") or load_slot_genome(name, folder) is not None:
            out.append(name)
    return out


def slot_is_fade(slot: str, folder: Path | None = None) -> bool:
    g = load_slot_genome(slot, folder)
    if g is None:
        return False
    if "fade" in str(g.recipe or "").lower() or "fade" in str(g.name or "").lower():
        return True
    longs = {str(x) for x in g.entry_long}
    return bool(longs & {"ll", "lh", "bear", "below_vwap"})


def next_free_slot(folder: Path | None = None, *, genome_id: str = "") -> str | None:
    idx = load_index(folder)
    slots = idx.get("slots") or {}
    want = str(genome_id or "").strip()
    if want:
        for name in AMISE_SLOT_BOOKS:
            row = slots.get(name) or {}
            if str(row.get("genome_id") or "") == want:
                return name
    for name in AMISE_SLOT_BOOKS:
        row = slots.get(name) or {}
        if not row.get("genome_id") and load_slot_genome(name, folder) is None:
            return name
    return None


def assign_slot(
    genome: StrategyGenome,
    *,
    proposal_id: str = "",
    folder: Path | None = None,
    note: str = "",
) -> dict[str, Any]:
    """Write genome into the next free S21–S24 slot (or the same genome_id)."""
    g = genome.normalized()
    folder = slots_dir(folder)
    slot = next_free_slot(folder, genome_id=g.genome_id)
    if slot is None:
        return {
            "ok": False,
            "error": "S21–S24 are full. Reject an old challenger before assigning another.",
            "slots": load_index(folder).get("slots") or {},
        }
    payload = {
        "slot": slot,
        "assigned_at_ist": _now_iso(),
        "proposal_id": str(proposal_id or ""),
        "note": str(note or ""),
        "genome": g.to_dict(),
        "paper": True,
        "live": False,
        "dry_run_required": True,
    }
    slot_file(slot, folder).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    idx = load_index(folder)
    slots = dict(idx.get("slots") or {})
    slots[slot] = {
        "genome_id": g.genome_id,
        "name": g.name,
        "proposal_id": str(proposal_id or ""),
        "assigned_at_ist": payload["assigned_at_ist"],
        "direction": g.direction,
        "recipe": g.recipe,
    }
    save_index({"slots": slots, "updated_at_ist": payload["assigned_at_ist"]}, folder)
    return {
        "ok": True,
        "slot": slot,
        "enable_key": enable_key(slot),
        "genome_id": g.genome_id,
        "name": g.name,
        "env_patch": slot_env_patch(slot),
        "assigned_at_ist": payload["assigned_at_ist"],
    }


def slot_env_patch(slot: str) -> dict[str, str]:
    """Paper ENABLE for this slot. Never DRY_RUN=false."""
    return {"DRY_RUN": "true", enable_key(slot): "true"}


def apply_slot_enable(
    slot: str,
    *,
    env_path: Path | None = None,
    sync_environ: bool = True,
) -> dict[str, Any]:
    from analytics.env_bridge import write_env_updates

    patch = slot_env_patch(slot)
    res = write_env_updates(patch, path=env_path)
    if res.get("ok") and sync_environ:
        os.environ["DRY_RUN"] = "true"
        os.environ[enable_key(slot)] = "true"
    return res


def desk_slots_payload(folder: Path | None = None) -> dict[str, Any]:
    idx = load_index(folder)
    rows = []
    for name in AMISE_SLOT_BOOKS:
        row = dict((idx.get("slots") or {}).get(name) or {})
        g = load_slot_genome(name, folder)
        rows.append(
            {
                "slot": name,
                "short": SHORT_AMISE[name],
                "filled": g is not None,
                "genome_id": row.get("genome_id") or (g.genome_id if g else ""),
                "name": row.get("name") or (g.name if g else ""),
                "direction": row.get("direction") or (g.direction if g else ""),
                "assigned_at_ist": row.get("assigned_at_ist") or "",
            }
        )
    return {
        "ok": True,
        "slots": rows,
        "allocated": allocated_slots(folder),
        "free": [r["slot"] for r in rows if not r["filled"]],
        "updated_at_ist": idx.get("updated_at_ist") or "",
    }
