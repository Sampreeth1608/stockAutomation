#!/usr/bin/env python3
"""AMISE — Adaptive Market Intelligence & Strategy Engine.

One research system. Four brains + your approval. Not a paper book.

  Market state → relationships / factory → strategy manager (fit) →
  profit guardian → risk snapshot → YOU APPROVE → paper (later) → memory

Desk ``GET /api/amise`` is read-only (never runs the factory).
CLI runs the loop:

  python3 amise.py --db data/ticks.db
  python3 amise.py --db data/ticks.db --lab --propose

--lab is slow (walk-forward + robustness). --propose writes Lab pending
rows only. Never ENABLE_*. Never DRY_RUN=false. S13/S16 formulas unchanged.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
ROOT = Path(__file__).resolve().parent
AMISE_DIR = ROOT / "data" / "amise"
MEMORY_PATH = AMISE_DIR / "memory.json"
ENGINE_NAME = "AMISE"


def _now_iso() -> str:
    return datetime.now(IST).isoformat(timespec="seconds")


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def write_memory(payload: dict[str, Any], path: Path | None = None) -> Path:
    dest = path or MEMORY_PATH
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return dest


def load_memory(path: Path | None = None) -> dict[str, Any]:
    return _read_json(path or MEMORY_PATH)


def _parse_ts(raw: str) -> datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def similar_states(
    db: Path,
    current,
    *,
    limit: int = 1600,
    stride: int = 24,
    window: int = 24,
) -> dict[str, Any]:
    """Historical windows with the same regime. Observe only. Not a trade."""
    from market_mood import classify_samples
    from storage import connect, init_db

    init_db(db)
    with connect(db) as conn:
        rows = list(
            conn.execute(
                """
                SELECT received_at, ltp, bp, sp FROM ticks
                WHERE ltp IS NOT NULL
                ORDER BY id DESC
                LIMIT ?
                """,
                (int(limit),),
            )
        )
    rows.reverse()
    if len(rows) < window + 8:
        return {"n": 0, "regime": getattr(current, "regime", "UNKNOWN"), "note": "not enough ticks"}
    want = str(getattr(current, "regime", "") or "")
    up5 = dn5 = flat5 = 0
    up30 = dn30 = flat30 = 0
    n_hit = 0
    i = 0
    while i + window < len(rows):
        chunk = rows[i : i + window]
        samples = [
            (float(r["ltp"]), float(r["bp"] or 0.0), float(r["sp"] or 0.0)) for r in chunk
        ]
        st = classify_samples(samples, gate=False, flatten=False)
        if st.regime == want and want not in {"", "UNKNOWN"}:
            t0 = _parse_ts(str(chunk[-1]["received_at"]))
            px0 = float(chunk[-1]["ltp"])
            n_hit += 1
            if t0 is not None:
                for horizon, bucket in (
                    (timedelta(minutes=5), "5"),
                    (timedelta(minutes=30), "30"),
                ):
                    target = t0 + horizon
                    later = None
                    for r in rows[i + window :]:
                        ts = _parse_ts(str(r["received_at"]))
                        if ts is not None and ts >= target:
                            later = float(r["ltp"])
                            break
                    if later is None:
                        continue
                    delta = later - px0
                    if abs(delta) < 4:
                        key = "flat"
                    elif delta > 0:
                        key = "up"
                    else:
                        key = "down"
                    if bucket == "5":
                        if key == "up":
                            up5 += 1
                        elif key == "down":
                            dn5 += 1
                        else:
                            flat5 += 1
                    else:
                        if key == "up":
                            up30 += 1
                        elif key == "down":
                            dn30 += 1
                        else:
                            flat30 += 1
        i += max(1, int(stride))
    n5 = up5 + dn5 + flat5
    n30 = up30 + dn30 + flat30

    def _pct(x: int, n: int) -> float:
        return round(100.0 * x / n, 1) if n else 0.0

    return {
        "n": n_hit,
        "regime": want,
        "after_5m": {
            "n": n5,
            "up_pct": _pct(up5, n5),
            "flat_pct": _pct(flat5, n5),
            "down_pct": _pct(dn5, n5),
        },
        "after_30m": {
            "n": n30,
            "up_pct": _pct(up30, n30),
            "flat_pct": _pct(flat30, n30),
            "down_pct": _pct(dn30, n30),
        },
        "note": (
            "Similar-state memory on this ticks.db only. Not a forecast you should size on. "
            "Not an order."
        ),
    }


def _safe(fn, fallback: dict[str, Any]) -> dict[str, Any]:
    try:
        out = fn()
        return out if isinstance(out, dict) else fallback
    except Exception as exc:
        err = dict(fallback)
        err["ok"] = False
        err["error"] = f"{type(exc).__name__}: {exc}"
        return err


def amise_desk_payload(*, db: Path | None = None) -> dict[str, Any]:
    """Read-only desk snapshot. Does not run the factory. Does not ENABLE."""
    from capital import capital_snapshot
    from control_state import load_state
    from desk_data import resolve_desk_db
    from profit_guardian import scan_guardian

    path = db or resolve_desk_db()
    mood = _safe(
        lambda: __import__("market_mood", fromlist=["mood_desk_payload"]).mood_desk_payload(path),
        {"ok": False, "mood": "UNKNOWN", "fits": [], "label": "mood unavailable"},
    )
    lab = _safe(
        lambda: __import__("research_desk", fromlist=["research_desk_payload"]).research_desk_payload(),
        {"ok": False, "pending": [], "counts": {}, "challengers": []},
    )
    you = _safe(
        lambda: __import__("human_capture", fromlist=["capture_desk_payload"]).capture_desk_payload(
            db=path, settle=False
        ),
        {"ok": False, "summary": {}},
    )
    guardian = _safe(lambda: scan_guardian(db=path), {"ok": False, "books": [], "portfolio_status": "THIN"})
    risk = _safe(lambda: capital_snapshot(), {"ok": False})
    try:
        st = load_state()
        control = {
            "emergency_off": bool(st.emergency_off),
            "trading_enabled": st.trading_enabled is not False,
            "live_unlocked": bool(st.live_unlocked),
        }
    except Exception:
        control = {"emergency_off": False, "trading_enabled": True, "live_unlocked": False}
    mem = load_memory()
    pending_n = len(lab.get("pending") or [])
    det_n = int(guardian.get("n_deteriorating") or 0)
    return {
        "ok": True,
        "engine": ENGINE_NAME,
        "ts_ist": _now_iso(),
        "live_blocked": True,
        "enable_blocked": True,
        "dry_run_required": True,
        "brains": {
            "market_state": "What is happening now?",
            "relationships": "Which candle / flow atoms have edge on this tape?",
            "factory": "Compose challengers. Validate. Never auto-deploy.",
            "manager": "Which paper book fits this regime?",
            "guardian": "Is the champion's edge intact?",
            "risk": "Size / daily loss / emergency.",
            "you": "Approve / reject / paper. Capture what you see.",
        },
        "market": mood,
        "manager": {
            "fits": mood.get("fits") or [],
            "regime": mood.get("regime") or mood.get("mood") or "UNKNOWN",
            "transition": mood.get("transition") or "",
            "gate_on": bool(mood.get("gate_on")),
        },
        "factory": {
            "last_run_at": lab.get("last_run_at") or "",
            "counts": lab.get("counts") or {},
            "champions": lab.get("champions") or {},
            "pending": lab.get("pending") or [],
            "challengers": (lab.get("challengers") or [])[:8],
            "discovery": (lab.get("discovery") or [])[:12],
        },
        "guardian": guardian,
        "risk": {
            "capital": risk,
            "control": control,
            "daily_loss_breached": bool(risk.get("daily_loss_breached")),
        },
        "human": you.get("summary") or {},
        "memory": {
            "updated_at_ist": mem.get("updated_at_ist") or "",
            "similar": mem.get("similar") or {},
            "note": mem.get("note")
            or "Run python3 amise.py --db data/ticks.db to refresh similar-state memory.",
        },
        "awaiting_you": pending_n,
        "alerts": det_n,
        "pipeline": [
            "TICK DATA",
            "MARKET STATE",
            "RELATIONSHIP / CANDLE ATOMS",
            "STRATEGY FACTORY + VALIDATION",
            "STRATEGY MANAGER (FIT)",
            "PROFIT GUARDIAN",
            "RISK",
            "YOUR APPROVAL",
            "PAPER (later explicit yes)",
            "MEMORY",
        ],
        "note": (
            "AMISE is the research system, not a book. It does not ENABLE, "
            "does not combine S8+S16 into one order, does not rewrite S13/S16, "
            "and cannot guarantee rising profits. Champion stays until a "
            "challenger beats S16 and S18 after charges, walk-forward, 2× costs, "
            "and you click Approve. Keep DRY_RUN=true."
        ),
    }


def run_amise(
    *,
    db: Path,
    lab: bool = False,
    propose: bool = False,
    lots: float = 100.0,
    fees: bool = True,
    minutes: int = 60,
    folds: int = 3,
    memory_path: Path | None = None,
) -> dict[str, Any]:
    """Full research loop. Factory only if lab=True. Never ENABLE."""
    from desk_data import resolve_desk_db
    from market_mood import snapshot_mood
    from profit_guardian import scan_guardian

    path = db or resolve_desk_db()
    mood = snapshot_mood(path)
    guardian = scan_guardian(db=path)
    similar = similar_states(path, mood)
    lab_payload: dict[str, Any] = {}
    if lab:
        from backtest_flow_lab import _load_bars
        from flow_lab import FlowParams, session_bars, tape_flags
        from research_factory import run_research_lab

        ns = argparse.Namespace(
            db=str(path),
            csv=None,
            csv_30m=None,
            ticks=None,
            upstox_json=None,
            upstox_json_30m=None,
            cache_csv=None,
            minutes=int(minutes),
        )
        hours, _m30, source = _load_bars(ns)
        hours = session_bars(hours)
        flags = tape_flags(hours)
        lab_payload = run_research_lab(
            hours,
            lots=float(lots),
            fees=bool(fees),
            n_folds=int(folds),
            params=FlowParams(),
            propose=bool(propose),
        )
        lab_payload = {
            "source": source,
            "tape": flags,
            "counts": lab_payload.get("counts") or {},
            "champions": lab_payload.get("champions") or {},
            "discovery": lab_payload.get("discovery") or [],
            "challengers": lab_payload.get("challengers") or [],
            "proposed_ids": lab_payload.get("proposed_ids") or [],
            "updated_at_ist": lab_payload.get("updated_at_ist") or _now_iso(),
        }
    payload = {
        "engine": ENGINE_NAME,
        "updated_at_ist": _now_iso(),
        "market": {
            "mood": mood.mood,
            "regime": mood.regime,
            "transition": mood.transition,
            "label": mood.label,
            "fits": mood.fits,
            "gate_on": mood.gate_on,
        },
        "guardian": guardian,
        "similar": similar,
        "lab": lab_payload,
        "note": (
            "AMISE run stored. Factory ran." if lab else "AMISE observe run (no factory). "
        )
        + "Not ENABLE. Keep DRY_RUN=true.",
    }
    write_memory(payload, path=memory_path or MEMORY_PATH)
    return payload


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=Path("data/ticks.db"))
    ap.add_argument("--lab", action="store_true", help="run research factory (slow)")
    ap.add_argument("--propose", action="store_true", help="write pending Lab rows (not ENABLE)")
    ap.add_argument("--lots", type=float, default=100.0)
    ap.add_argument("--fees", action="store_true", default=True)
    ap.add_argument("--no-fees", action="store_true")
    ap.add_argument("--minutes", type=int, default=60)
    ap.add_argument("--folds", type=int, default=3)
    args = ap.parse_args()
    fees = bool(args.fees) and not bool(args.no_fees)
    print("AMISE — research system, not a paper book, not live")
    print("Market state → factory (optional) → fit → guardian → memory → you approve")
    print("Does not ENABLE. Keep DRY_RUN=true.", flush=True)
    result = run_amise(
        db=args.db,
        lab=bool(args.lab),
        propose=bool(args.propose),
        lots=float(args.lots),
        fees=fees,
        minutes=int(args.minutes),
        folds=int(args.folds),
    )
    m = result.get("market") or {}
    print(f"regime={m.get('regime')} mood={m.get('mood')} transition={m.get('transition')}")
    g = result.get("guardian") or {}
    print(f"guardian portfolio={g.get('portfolio_status')} deteriorating={g.get('n_deteriorating')}")
    for row in g.get("books") or []:
        print(
            f"  {row['strategy']}: {row['status']} n={row['n']} "
            f"PF={row['profit_factor']} recentPF={row['recent_pf']} "
            f"AC₹={row['pnl_after_charges']}"
        )
    sim = result.get("similar") or {}
    print(f"similar n={sim.get('n')} regime={sim.get('regime')}")
    a5 = sim.get("after_5m") or {}
    if a5.get("n"):
        print(
            f"  after 5m  up={a5.get('up_pct')}% flat={a5.get('flat_pct')}% down={a5.get('down_pct')}%"
        )
    lab = result.get("lab") or {}
    counts = lab.get("counts") or {}
    if counts:
        print(
            f"factory found={counts.get('found', 0)} passed={counts.get('passed_validation', 0)} "
            f"awaiting={counts.get('awaiting_approval', 0)}"
        )
    else:
        print("factory skipped (pass --lab to run discovery + validation)")
    print("You approve on the station AMISE / Lab tabs. Paper ≠ live.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
