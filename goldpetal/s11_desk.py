"""S11 ML pack inspector + paper-safe proposal apply for the HTML desk.

Streamlit used to approve weekend packs (S11_PACK_PATH + ENABLE_S11). The
station on 8501 now owns that flow: inspect packs, compare the loaded pack
to a proposal, write whitelist .env keys, and never live-unlock.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from proposals import decide_proposal, get_proposal, proposals_snapshot
from position_safety import read_bot_health
from s18_desk import apply_s18_proposal, s18_status

IST = ZoneInfo("Asia/Kolkata")
ROOT = Path(__file__).resolve().parent

LIVE_BLOCKED_REASON = (
    "Live approval is not on the ML tab. Approve → paper first, then Unlock live "
    "on Live money after the pack looks good. This tab never sets DRY_RUN=false."
)

# Paper books that weekly ML must not turn back on.
RETIRED_ENABLE_TRUE: dict[str, str] = {
    "ENABLE_S4": "S4 stays off (research). Paper daily swing is S13.",
    "ENABLE_S12": "S12 is retired from paper.",
    "ENABLE_S14": "S14 is retired from paper.",
    "ENABLE_S15": "S15 is retired from paper.",
}

DROP_KEYS = frozenset({"LIVE_UNLOCK", "LIVE_UNLOCKED", "LIVE_APPROVED"})


def _now_iso() -> str:
    return datetime.now(IST).isoformat(timespec="seconds")


def _mtime_ist(path: Path) -> str:
    if not path.exists():
        return ""
    return datetime.fromtimestamp(path.stat().st_mtime, IST).isoformat(timespec="seconds")


def _tail_file(path: Path, n: int = 12) -> str:
    if not path.exists():
        return ""
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            fh.seek(max(0, size - 65536))
            data = fh.read().decode("utf-8", errors="replace")
        return "\n".join(data.splitlines()[-n:])
    except OSError:
        return ""


def data_root(root: Path = ROOT) -> Path:
    return (root / "data").resolve()


def resolve_data_path(raw: str, *, root: Path = ROOT) -> Path:
    """Resolve a pack/model path. Must sit under <root>/data (no .. / ~)."""
    value = str(raw or "").strip().replace("\\", "/")
    if not value:
        raise ValueError("path is empty")
    if ".." in value or value.startswith("~"):
        raise ValueError("path must not contain .. or ~")
    p = Path(value)
    base = data_root(root)
    if p.is_absolute():
        resolved = p.resolve()
    else:
        if not value.startswith("data/"):
            raise ValueError("path must start with data/ or be under data/")
        resolved = (root / value).resolve()
    try:
        resolved.relative_to(base)
    except ValueError as exc:
        raise ValueError("path must be under data/") from exc
    return resolved


def to_env_pack_path(path: Path, *, root: Path = ROOT) -> str:
    rel = path.resolve().relative_to(data_root(root))
    return f"data/{rel.as_posix()}"


def normalize_pack_key(raw: str, *, root: Path = ROOT) -> str:
    value = str(raw or "").strip()
    if not value:
        return ""
    try:
        return to_env_pack_path(resolve_data_path(value, root=root), root=root)
    except (ValueError, OSError):
        return value.replace("\\", "/")


def paper_safe_env_patch(
    patch: dict[str, Any] | None,
) -> tuple[dict[str, str], dict[str, str]]:
    """Force DRY_RUN=true. Drop live-unlock and retired ENABLE_*=true keys."""
    skipped: dict[str, str] = {}
    clean: dict[str, str] = {}
    for key_raw, raw in (patch or {}).items():
        key = str(key_raw).strip()
        val = str(raw).strip()
        if not key:
            continue
        if key == "DRY_RUN":
            if val.lower() not in {"true", "1", "yes", "y"}:
                skipped[key] = "ML tab keeps DRY_RUN=true"
            continue
        if key in DROP_KEYS:
            skipped[key] = "live unlock is not allowed from ML"
            continue
        if key in RETIRED_ENABLE_TRUE and val.lower() in {"true", "1", "yes", "y"}:
            skipped[key] = RETIRED_ENABLE_TRUE[key]
            continue
        clean[key] = val
    clean["DRY_RUN"] = "true"
    return clean, skipped


def apply_paper_env_patch(
    patch: dict[str, Any] | None,
    *,
    env_path: Path | None = None,
) -> dict[str, Any]:
    from analytics.env_bridge import apply_env_patch

    clean, skipped = paper_safe_env_patch(patch)
    result = apply_env_patch(clean, path=env_path)
    merged_skip = dict(skipped)
    merged_skip.update(result.get("skipped") or {})
    out = dict(result)
    out["skipped"] = merged_skip
    out["paper_safe"] = True
    out["forced_dry_run"] = True
    return out


def _read_json_object(path: Path) -> tuple[dict[str, Any] | None, str]:
    """Load a JSON object. Never raise on joblib/pickle (starts with 0x80)."""
    try:
        with path.open("rb") as fh:
            head = fh.read(8)
            if head.startswith(b"\x80"):
                return None, "binary pickle/joblib, not pack JSON"
            blob = head + fh.read()
        text = blob.decode("utf-8")
        data = json.loads(text)
    except UnicodeDecodeError:
        return None, "not utf-8 (binary file)"
    except (json.JSONDecodeError, OSError) as exc:
        return None, f"read failed: {exc}"
    if not isinstance(data, dict):
        return None, "pack is not an object"
    return data, ""


def _json_pack_path(raw: str) -> str:
    """Prefer a .json pack path; skip joblib model_path fallbacks."""
    value = str(raw or "").strip()
    if not value:
        return ""
    if value.lower().endswith((".joblib", ".pkl", ".pickle", ".bin")):
        return ""
    return value


def pack_summary(raw: str | Path | None, *, root: Path = ROOT) -> dict[str, Any]:
    """Read a discovery pack JSON without loading the joblib model."""
    value = str(raw or "").strip()
    if not value:
        return {
            "path": "",
            "exists": False,
            "error": "empty",
        }
    try:
        path = resolve_data_path(value, root=root) if not isinstance(raw, Path) else raw
        if isinstance(raw, Path):
            try:
                path.relative_to(data_root(root))
            except ValueError:
                path = resolve_data_path(str(raw), root=root)
    except ValueError as exc:
        return {"path": value, "exists": False, "error": str(exc)}

    env_path = ""
    try:
        env_path = to_env_pack_path(path, root=root)
    except ValueError:
        env_path = str(path)

    if not path.is_file():
        return {
            "path": env_path or value,
            "exists": False,
            "error": "missing",
        }
    if path.suffix.lower() in {".joblib", ".pkl", ".pickle", ".bin"}:
        return {
            "path": env_path,
            "exists": True,
            "error": "model file, not pack JSON",
            "model_path": env_path,
            "model_exists": True,
        }
    data, err = _read_json_object(path)
    if err:
        return {
            "path": env_path,
            "exists": True,
            "error": err,
        }
    if not isinstance(data, dict):
        return {"path": env_path, "exists": True, "error": "pack is not an object"}

    features = list(data.get("features") or [])
    paper = data.get("paper") if isinstance(data.get("paper"), dict) else {}
    model_raw = str(data.get("model_path") or "")
    model_exists = False
    if model_raw:
        try:
            model_exists = resolve_data_path(model_raw, root=root).is_file()
        except ValueError:
            model_exists = Path(model_raw).is_file()

    safety_reasons = list(data.get("safety_reasons") or [])
    return {
        "path": env_path,
        "exists": True,
        "error": "",
        "id": str(data.get("id") or path.stem),
        "week_id": str(data.get("week_id") or ""),
        "strategy": str(data.get("strategy") or "S11_DISCOVERED"),
        "model_name": str(data.get("model_name") or ""),
        "model_path": model_raw,
        "model_exists": model_exists,
        "family": str(data.get("family") or ""),
        "rationale": str(data.get("rationale") or ""),
        "auc": data.get("auc"),
        "buy_prob": data.get("buy_prob"),
        "short_prob": data.get("short_prob"),
        "min_hold_sec": data.get("min_hold_sec"),
        "every_n_ticks": data.get("every_n_ticks"),
        "min_imb": data.get("min_imb"),
        "safety_ok": bool(data.get("safety_ok", False)),
        "safety_reasons": safety_reasons[:12],
        "n_features": len(features),
        "features_head": features[:12],
        "paper": {
            "n_trades": paper.get("n_trades"),
            "win_rate": paper.get("win_rate"),
            "gross_pnl": paper.get("gross_pnl"),
            "after_tax_pnl": paper.get("after_tax_pnl"),
        },
        "behavior_summary": str(data.get("behavior_summary") or "")[:400],
        "created_at_ist": str(data.get("created_at_ist") or ""),
        "mtime_ist": _mtime_ist(path),
        "bytes": path.stat().st_size,
    }


def list_s11_packs(*, root: Path = ROOT, limit: int = 24) -> list[dict[str, Any]]:
    packs_dir = root / "data" / "discover" / "packs"
    if not packs_dir.is_dir():
        return []
    files = sorted(
        (p for p in packs_dir.glob("*.json") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    out: list[dict[str, Any]] = []
    for path in files[: max(1, int(limit))]:
        row = pack_summary(path, root=root)
        out.append(row)
    return out


def s11_status(*, root: Path = ROOT, env_path: Path | None = None) -> dict[str, Any]:
    from analytics.env_bridge import read_env

    env_file = env_path or (root / ".env")
    env = read_env(env_file)
    pack_raw = str(env.get("S11_PACK_PATH") or "").strip()
    enable_raw = str(env.get("ENABLE_S11") or "").strip().lower()
    enable = enable_raw in {"1", "true", "yes", "y"}
    dry_raw = str(env.get("DRY_RUN", "true")).strip().lower()
    dry_run = dry_raw in {"1", "true", "yes", "y", ""}
    pack = pack_summary(pack_raw, root=root) if pack_raw else pack_summary("", root=root)
    hint = "ready"
    if not pack_raw:
        hint = "S11_PACK_PATH not set — S11 loads disabled"
    elif not pack.get("exists"):
        hint = f"pack missing: {pack.get('path') or pack_raw}"
    elif pack.get("error"):
        hint = str(pack.get("error"))
    elif not pack.get("model_exists"):
        hint = "pack JSON ok but joblib model file is missing"
    elif not enable:
        hint = "pack on disk, ENABLE_S11 is false"
    else:
        hint = "ENABLE_S11 + pack set — type RESTART if the bot still shows pack not set"
    health = read_bot_health()
    positions = health.get("positions") if isinstance(health, dict) else {}
    s11_pos = None
    if isinstance(positions, dict):
        s11_pos = positions.get("S11_DISCOVERED") or positions.get("S11")
    return {
        "enable_s11": enable,
        "pack_path": pack_raw,
        "pack_key": normalize_pack_key(pack_raw, root=root),
        "pack": pack,
        "dry_run": dry_run,
        "load_hint": hint,
        "bot_health_ts": (health or {}).get("ts_ist") if isinstance(health, dict) else "",
        "s11_position": s11_pos,
    }


def discover_status(*, root: Path = ROOT) -> dict[str, Any]:
    discover = root / "data" / "discover"
    report_path = discover / "latest_report.json"
    cron_path = discover / "cron.log"
    candidates: list[dict[str, Any]] = []
    summary = ""
    week_id = ""
    if report_path.is_file():
        raw_obj, err = _read_json_object(report_path)
        raw = raw_obj or {}
        if err and not raw:
            summary = err
        if isinstance(raw, dict):
            summary = str(raw.get("behavior_summary") or raw.get("summary") or "")[:500]
            week_id = str(raw.get("week_id") or "")
            rows = raw.get("candidates") or []
            if isinstance(rows, list):
                for row in rows[:8]:
                    if not isinstance(row, dict):
                        continue
                    pack_path = _json_pack_path(str(row.get("pack_path") or ""))
                    item = {
                        "model": row.get("model") or row.get("model_name"),
                        "template": row.get("template"),
                        "family": row.get("family"),
                        "auc": row.get("auc"),
                        "n_trades": row.get("n_trades"),
                        "win_rate": row.get("win_rate"),
                        "gross_pnl": row.get("gross_pnl"),
                        "after_tax_pnl": row.get("after_tax_pnl"),
                        "safety_ok": row.get("safety_ok"),
                        "pack_path": pack_path,
                    }
                    if pack_path:
                        item["pack"] = pack_summary(pack_path, root=root)
                    candidates.append(item)
    return {
        "report_path": str(report_path.relative_to(root)) if report_path.exists() else "",
        "report_mtime_ist": _mtime_ist(report_path),
        "week_id": week_id,
        "behavior_summary": summary,
        "candidates": candidates,
        "cron_tail": _tail_file(cron_path, 10),
        "packs_dir": "data/discover/packs",
    }


def _annotate_proposal(
    row: dict[str, Any],
    *,
    loaded_key: str,
    root: Path,
    s18_pack_name: str = "",
) -> dict[str, Any]:
    out = dict(row)
    paper = out.get("paper") if isinstance(out.get("paper"), dict) else {}
    extra = paper.get("extra") if isinstance(paper.get("extra"), dict) else {}
    patch = out.get("env_patch") if isinstance(out.get("env_patch"), dict) else {}
    if str(out.get("strategy") or "") == "S18_OHLC_VOL_HTF":
        pack = extra.get("pack") if isinstance(extra.get("pack"), dict) else {}
        name = str(pack.get("name") or "")
        after_ch = extra.get("after_charges_inr")
        if after_ch is None:
            after_ch = paper.get("after_tax_pnl_inr")
        out["proposed_pack"] = {
            "path": str(out.get("model_path") or "data/learn/s18/proposed.json"),
            "exists": True,
            "error": "",
            "rationale": f"pack={name or '—'}",
            "behavior_summary": "S18 AND overlay. Ranked on after charges, tax excluded.",
        }
        out["same_as_loaded"] = bool(name and s18_pack_name and name == s18_pack_name)
        out["would_disable_s11"] = False
        out["extra"] = extra
        out["pnl_label"] = "After charges ₹"
        out["pnl_value"] = after_ch
        return out
    pack_raw = _json_pack_path(
        str(patch.get("S11_PACK_PATH") or "").strip()
        or str(out.get("model_path") or "").strip()
    )
    proposed = pack_summary(pack_raw, root=root) if pack_raw else pack_summary("", root=root)
    proposed_key = str(proposed.get("path") or "")
    out["proposed_pack"] = proposed
    out["same_as_loaded"] = bool(loaded_key and proposed_key and loaded_key == proposed_key)
    out["would_disable_s11"] = str(patch.get("ENABLE_S11") or "").strip().lower() in {
        "false",
        "0",
        "no",
        "n",
    }
    out["extra"] = extra
    out["pnl_label"] = "After-tax ₹"
    out["pnl_value"] = paper.get("after_tax_pnl_inr")
    return out


def ml_desk_payload(
    *,
    root: Path = ROOT,
    env_path: Path | None = None,
    proposals_path: Path | None = None,
) -> dict[str, Any]:
    try:
        s11 = s11_status(root=root, env_path=env_path)
    except Exception as exc:
        s11 = {
            "enable_s11": False,
            "pack_path": "",
            "pack_key": "",
            "pack": {"exists": False, "error": str(exc)},
            "dry_run": True,
            "load_hint": f"status failed: {exc}",
            "bot_health_ts": "",
            "s11_position": None,
        }
    try:
        s18 = s18_status(root=root, env_path=env_path)
    except Exception as exc:
        s18 = {
            "enable_s18": False,
            "pack_name": "base",
            "pack": {},
            "pack_path": "",
            "dry_run": True,
            "load_hint": f"status failed: {exc}",
            "metric": "after_charges_ex_tax",
            "paper": True,
            "live": False,
        }
    loaded_key = str(s11.get("pack_key") or "")
    s18_name = str(s18.get("pack_name") or "")
    try:
        snap = (
            proposals_snapshot(path=proposals_path)
            if proposals_path is not None
            else proposals_snapshot()
        )
    except Exception:
        snap = {
            "pending": [],
            "decided": [],
            "counts": {"pending": 0, "total": 0},
        }
    pending = [
        _annotate_proposal(
            p, loaded_key=loaded_key, root=root, s18_pack_name=s18_name
        )
        for p in (snap.get("pending") or [])
    ]
    decided = [
        _annotate_proposal(
            p, loaded_key=loaded_key, root=root, s18_pack_name=s18_name
        )
        for p in (snap.get("decided") or [])
    ]
    snap = dict(snap)
    snap["pending"] = pending
    snap["decided"] = decided
    try:
        packs = list_s11_packs(root=root)
    except Exception:
        packs = []
    for row in packs:
        row["is_loaded"] = bool(loaded_key and row.get("path") == loaded_key)
    return {
        "ok": True,
        "ts_ist": _now_iso(),
        "proposals": snap,
        "s11": s11,
        "s18": s18,
        "packs": packs,
        "discover": discover_status(root=root),
        "live_blocked": True,
        "live_blocked_reason": LIVE_BLOCKED_REASON,
        "note": (
            "Approve → paper writes S11_PACK_PATH / ENABLE_S11, or S18 active.json "
            "+ ENABLE_S18, and keeps DRY_RUN=true. S18 ranks after charges (tax excluded). "
            "Type RESTART on Engine to load RAM. This tab never arms Angel."
        ),
    }


def decide_proposal_for_desk(
    proposal_id: str,
    decision: str,
    note: str = "",
    *,
    apply_env: bool = True,
    accept_unsafe: bool = False,
    proposals_path: Path | None = None,
    state_path: Path | None = None,
    env_path: Path | None = None,
    root: Path = ROOT,
    s18_pack_path: Path | None = None,
) -> dict[str, Any]:
    """Approve → paper / reject from the station. Never approved_live."""
    if decision == "approved_live":
        return {
            "ok": False,
            "error": LIVE_BLOCKED_REASON,
            "live_blocked": True,
        }
    kwargs: dict[str, Any] = {}
    if proposals_path is not None:
        kwargs["path"] = proposals_path
    if state_path is not None:
        kwargs["state_path"] = state_path
    found = get_proposal(proposal_id, path=proposals_path) if proposals_path else get_proposal(
        proposal_id
    )
    if found is None:
        raise KeyError(f"proposal not found: {proposal_id}")
    if decision == "approved_paper" and found.safety_ok is False and not accept_unsafe:
        return {
            "ok": False,
            "error": (
                "safety_ok=False — tick Accept risk to approve this pack into paper. "
                "Unsafe S11 proposals usually write ENABLE_S11=false."
            ),
            "safety_ok": False,
            "proposal": found.to_dict(),
        }
    pack_applied: dict[str, Any] | None = None
    if (
        apply_env
        and decision == "approved_paper"
        and found.strategy == "S18_OHLC_VOL_HTF"
    ):
        pack_applied = apply_s18_proposal(
            found, pack_path=s18_pack_path, root=root
        )
        if not pack_applied.get("ok"):
            return {
                "ok": False,
                "error": pack_applied.get("error") or "S18 pack write failed",
                "proposal": found.to_dict(),
            }
    # Rejecting an S18 pack must not force-disable paper S18 — keep the loaded pack.
    if found.strategy == "S18_OHLC_VOL_HTF" and decision == "rejected":
        kwargs["touch_control"] = False
    p = decide_proposal(proposal_id, decision, note=note, **kwargs)
    env_result = None
    if apply_env and decision == "approved_paper" and p.env_patch:
        env_result = apply_paper_env_patch(p.env_patch, env_path=env_path)
    restart_needed = bool(env_result and env_result.get("ok"))
    if pack_applied and pack_applied.get("ok"):
        restart_needed = True
    reminder = ""
    if env_result and not env_result.get("ok"):
        reminder = (
            "Approved in the book, but .env write failed: "
            + str(env_result.get("error") or "unknown")
        )
    elif restart_needed:
        reminder = "Type RESTART on Engine to load the pack into RAM. Desk restart is not enough."
    skipped = (env_result or {}).get("skipped") or {}
    if skipped:
        bits = ", ".join(f"{k} ({v})" for k, v in skipped.items())
        reminder = (reminder + " Skipped " + bits).strip()
    return {
        "ok": True,
        "id": p.id,
        "strategy": p.strategy,
        "status": p.status,
        "safety_ok": p.safety_ok,
        "env_patch": p.env_patch,
        "env_applied": env_result,
        "s18_applied": pack_applied,
        "title": p.title,
        "proposal": p.to_dict(),
        "restart_needed": restart_needed,
        "reminder": reminder,
        "ml": ml_desk_payload(root=root, env_path=env_path, proposals_path=proposals_path),
    }


def activate_s11_pack(
    pack_path: str,
    *,
    accept_unsafe: bool = False,
    env_path: Path | None = None,
    root: Path = ROOT,
) -> dict[str, Any]:
    """Load a pack from disk into paper .env without a proposal row."""
    path = resolve_data_path(pack_path, root=root)
    if not path.is_file():
        raise FileNotFoundError(f"pack missing: {to_env_pack_path(path, root=root)}")
    packs_dir = (root / "data" / "discover" / "packs").resolve()
    try:
        path.resolve().relative_to(packs_dir)
    except ValueError as exc:
        raise ValueError("pack must be under data/discover/packs/") from exc
    summary = pack_summary(path, root=root)
    if summary.get("safety_ok") is False and not accept_unsafe:
        return {
            "ok": False,
            "error": "pack safety_ok=False — tick Accept risk to load it into paper.",
            "pack": summary,
        }
    env_key = to_env_pack_path(path, root=root)
    env_result = apply_paper_env_patch(
        {
            "ENABLE_S11": "true",
            "S11_PACK_PATH": env_key,
            "DRY_RUN": "true",
        },
        env_path=env_path,
    )
    return {
        "ok": bool(env_result.get("ok")),
        "pack_path": env_key,
        "pack": summary,
        "env_applied": env_result,
        "restart_needed": bool(env_result.get("ok")),
        "reminder": (
            "Type RESTART on Engine to load the pack into RAM. Desk restart is not enough."
            if env_result.get("ok")
            else ""
        ),
        "s11": s11_status(root=root, env_path=env_path),
    }
