"""S18 paper pack apply for the ML tab. Not live.

Weekly learn_s18.py writes a pending proposal. Approve → paper copies that
pack into data/learn/s18/active.json and keeps DRY_RUN=true. Type RESTART.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from analytics.env_bridge import read_env
from s18_ohlc_vol_htf import PACK_PATH, load_active_pack, pack_from_dict

IST = ZoneInfo("Asia/Kolkata")
ROOT = Path(__file__).resolve().parent


def _now_iso() -> str:
    return datetime.now(IST).isoformat(timespec="seconds")


def write_active_pack(
    pack: dict[str, Any],
    *,
    note: str,
    path: Path | None = None,
) -> Path:
    dest = path or PACK_PATH
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        json.dumps(
            {
                "paper": True,
                "live": False,
                "pack": pack,
                "note": note,
                "updated_at": _now_iso(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return dest


def s18_status(*, root: Path = ROOT, env_path: Path | None = None) -> dict[str, Any]:
    env = read_env(env_path or (root / ".env"))
    enable = str(env.get("ENABLE_S18") or "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "y",
    }
    dry_raw = str(env.get("DRY_RUN", "true")).strip().lower()
    dry_run = dry_raw in {"1", "true", "yes", "y", ""}
    pack_file = root / "data" / "learn" / "s18" / "active.json"
    pack = load_active_pack(pack_file if pack_file.exists() else None)
    rel = "data/learn/s18/active.json"
    hint = f"pack={pack.name} · after charges, exclude tax"
    if not pack_file.exists():
        hint = "no active.json — paper uses base AND until you Approve a pack"
    elif not enable:
        hint = f"pack={pack.name} on disk, ENABLE_S18 is false"
    else:
        hint = (
            f"ENABLE_S18 + pack={pack.name} — type RESTART if RAM still shows the old pack"
        )
    return {
        "enable_s18": enable,
        "dry_run": dry_run,
        "pack_name": pack.name,
        "pack": pack.as_dict(),
        "pack_path": rel if pack_file.exists() else "",
        "load_hint": hint,
        "metric": "after_charges_ex_tax",
        "paper": True,
        "live": False,
    }


def apply_s18_proposal(
    proposal: Any,
    *,
    pack_path: Path | None = None,
    root: Path = ROOT,
) -> dict[str, Any]:
    """Copy the approved pack into active.json. Never live."""
    paper = getattr(proposal, "paper", None)
    extra: dict[str, Any] = {}
    if paper is not None:
        extra = dict(getattr(paper, "extra", None) or {})
    elif isinstance(proposal, dict):
        extra = dict(((proposal.get("paper") or {}).get("extra")) or {})
    pack = extra.get("pack")
    if not isinstance(pack, dict):
        model = str(getattr(proposal, "model_path", "") or "")
        if model:
            raw_path = Path(model)
            if not raw_path.is_absolute():
                raw_path = root / model
            if raw_path.is_file():
                try:
                    blob = json.loads(raw_path.read_text(encoding="utf-8"))
                    if isinstance(blob.get("pack"), dict):
                        pack = blob["pack"]
                except Exception:
                    pack = None
    if not isinstance(pack, dict):
        return {"ok": False, "error": "S18 proposal has no pack"}
    parsed = pack_from_dict(pack)
    note = f"ML approved {getattr(proposal, 'id', '')} pack={parsed.name}"
    dest = write_active_pack(parsed.as_dict(), note=note, path=pack_path)
    return {
        "ok": True,
        "pack": parsed.as_dict(),
        "path": str(dest),
        "paper": True,
        "live": False,
        "restart_needed": True,
    }
