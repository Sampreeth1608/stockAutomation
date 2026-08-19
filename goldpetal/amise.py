#!/usr/bin/env python3
"""AMISE — Adaptive Market Intelligence & Strategy Engine.

One research system. Four brains + your approval.

  Market state → relationships / factory → strategy manager (fit) →
  profit guardian → risk snapshot → YOU APPROVE → S21, S22, … paper → memory

Desk ``GET /api/amise`` is read-only (never runs the factory).
``POST /api/amise/lab`` starts the factory in the background (cheap-screen,
then the strong lab) and writes Lab pending rows. Approve on Lab names the
next slot (S21, then S22, then S25 after S24) and turns paper ENABLE on.
This module never sets DRY_RUN=false.

CLI:

  python3 amise.py --db data/ticks.db
  python3 amise.py --db data/ticks.db --lab --propose --fast
  python3 amise.py --db data/ticks.db --lab --propose --full
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from amise_slots import desk_slots_payload

IST = ZoneInfo("Asia/Kolkata")
ROOT = Path(__file__).resolve().parent
AMISE_DIR = ROOT / "data" / "amise"
MEMORY_PATH = AMISE_DIR / "memory.json"
LAB_LOCK = AMISE_DIR / "lab.lock"
LAB_LOG = AMISE_DIR / "lab.log"
ENGINE_NAME = "AMISE"


def auto_lab_on() -> bool:
    return (os.getenv("AMISE_AUTO_LAB") or "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "y",
    }


def auto_lab_hours() -> float:
    raw = (os.getenv("AMISE_AUTO_LAB_HOURS") or "12").strip()
    try:
        return min(72.0, max(1.0, float(raw)))
    except ValueError:
        return 12.0


def lab_fast_on() -> bool:
    """Desk / auto-lab default: light factory. Set AMISE_FAST_LAB=false for weekly depth."""
    return (os.getenv("AMISE_FAST_LAB") or "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "y",
    }


def ensure_mood_gate(*, path: Path | None = None) -> dict[str, Any]:
    """Turn MOOD_GATE on so fit actually picks who may open. Never DRY_RUN=false."""
    from analytics.env_bridge import write_env_updates

    res = write_env_updates({"MOOD_GATE": "true"}, path=path)
    if res.get("ok"):
        os.environ["MOOD_GATE"] = "true"
    return res


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


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError, PermissionError):
        return False


def amise_lab_status() -> dict[str, Any]:
    AMISE_DIR.mkdir(parents=True, exist_ok=True)
    lock = _read_json(LAB_LOCK)
    pid = int(lock.get("pid") or 0)
    running = _pid_alive(pid)
    if lock and not running:
        try:
            LAB_LOCK.unlink()
        except OSError:
            pass
        lock = {}
    last = ""
    mem = load_memory()
    last = str((mem.get("lab") or {}).get("updated_at_ist") or mem.get("updated_at_ist") or "")
    return {
        "ok": True,
        "running": running,
        "pid": pid if running else None,
        "started_at_ist": lock.get("started_at_ist") or "",
        "last_run_at": last,
        "log": str(LAB_LOG) if LAB_LOG.is_file() else "",
        "auto": auto_lab_on(),
        "auto_hours": auto_lab_hours(),
        "fast": bool(lock.get("fast")) if lock else lab_fast_on(),
    }


def auto_lab_due(*, now: datetime | None = None) -> bool:
    if not auto_lab_on():
        return False
    st = amise_lab_status()
    if st.get("running"):
        return False
    last = _parse_ts(str(st.get("last_run_at") or ""))
    now = now or datetime.now(IST)
    if last is None:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=IST)
    return (now - last.astimezone(IST)) >= timedelta(hours=auto_lab_hours())


def start_amise_lab(
    *,
    db: Path | None = None,
    propose: bool = True,
    python: str | None = None,
    fast: bool | None = None,
) -> dict[str, Any]:
    """Background factory. Writes Lab pending only. Never ENABLE. Never DRY_RUN=false."""
    from desk_data import resolve_desk_db

    st = amise_lab_status()
    if st.get("running"):
        return {**st, "started_new": False, "note": "factory already running"}
    AMISE_DIR.mkdir(parents=True, exist_ok=True)
    path = db or resolve_desk_db()
    py = python or os.getenv("PYTHON") or "python3"
    venv = ROOT / "venv" / "bin" / "python"
    if venv.is_file() and python is None:
        py = str(venv)
    use_fast = lab_fast_on() if fast is None else bool(fast)
    cmd = [
        str(py),
        str(ROOT / "amise.py"),
        "--db",
        str(path),
        "--lab",
    ]
    if propose:
        cmd.append("--propose")
    cmd.append("--fast" if use_fast else "--full")
    logf = LAB_LOG.open("a", encoding="utf-8")
    logf.write(f"\n--- {datetime.now(IST).isoformat(timespec='seconds')} fast={use_fast} ---\n")
    logf.flush()
    proc = subprocess.Popen(
        cmd,
        cwd=str(ROOT),
        stdout=logf,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    LAB_LOCK.write_text(
        json.dumps(
            {
                "pid": proc.pid,
                "started_at_ist": _now_iso(),
                "cmd": cmd,
                "propose": bool(propose),
                "fast": bool(use_fast),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "ok": True,
        "started_new": True,
        "running": True,
        "pid": proc.pid,
        "propose": bool(propose),
        "fast": bool(use_fast),
        "note": (
            "Factory started: cheap-screen, then 3-fold + recipes + sklearn ranks "
            "+ 2×/3× costs + holdout. Skipping those does not make stronger books. "
            "Challengers that pass go to Lab. Filled chairs also get a same-slot "
            "improve pass from closed trades / tape. You Approve. Does not ENABLE until Approve. "
            "Keep DRY_RUN=true."
        ),
    }


def maybe_start_auto_lab() -> dict[str, Any]:
    if not auto_lab_due():
        return {"ok": True, "started_new": False, "due": False, **amise_lab_status()}
    return start_amise_lab(propose=True)


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
            "job": amise_lab_status(),
        },
        "slots": desk_slots_payload(),
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
            "S21+ PAPER (new name or same-slot improve after Approve)",
            "ANGEL (after Unlock + LIVE)",
            "MEMORY",
        ],
        "note": (
            "AMISE reads the regime and lets fitting paper books trade (mood gate on). "
            "It invents challengers on Run factory / auto lab (screen, then strong gates). "
            "You Approve on Lab — that names the next slot (S21, S22, … S25 after S24) "
            "or overwrites a filled chair with a stronger genome. "
            "Existing books keep learning from closed trades. Restart the bot. "
            "This tab never sets DRY_RUN=false. Angel still needs Unlock + LIVE. "
            "Does not rewrite S13/S16. Keep DRY_RUN=true until you type LIVE."
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
    fast: bool = False,
    improve: bool = True,
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
    improve_payload: dict[str, Any] = {}
    hours: list[Any] = []
    if lab:
        from backtest_flow_lab import _load_bars
        from flow_lab import FlowParams, session_bars, tape_flags
        from improve_books import run_improve
        from research_factory import FAST_LAB_KWARGS, run_research_lab

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
        lab_kw: dict[str, Any] = dict(FAST_LAB_KWARGS)
        if not fast:
            lab_kw["n_folds"] = int(folds)
        lab_payload = run_research_lab(
            hours,
            lots=float(lots),
            fees=bool(fees),
            params=FlowParams(),
            propose=bool(propose),
            **lab_kw,
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
            "fast": bool(fast),
            "strong": True,
            "gates": lab_payload.get("gates") or [],
            "holdout_bars": lab_payload.get("holdout_bars") or 0,
        }
        if improve:
            improve_payload = run_improve(
                hours,
                db=path,
                lots=float(lots),
                fees=bool(fees),
                n_folds=int(lab_kw.get("n_folds") or 3),
                strong=True,
                propose=bool(propose),
                full=not bool(fast),
            )
            lab_payload["improve"] = {
                "proposed_ids": improve_payload.get("proposed_ids") or [],
                "amise": [
                    {
                        "slot": r.get("slot"),
                        "status": r.get("status"),
                        "proposed": r.get("proposed"),
                    }
                    for r in (improve_payload.get("amise") or [])
                ],
                "s18_proposed": bool((improve_payload.get("s18") or {}).get("proposed")),
                "hour_gate_n": (improve_payload.get("hour_gate") or {}).get("n"),
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
        "improve": improve_payload,
        "note": (
            "AMISE run stored. Factory ran." if lab else "AMISE observe run (no factory). "
        )
        + "Existing books also get an improve pass when the factory runs. "
        + "Not ENABLE. Keep DRY_RUN=true.",
    }
    write_memory(payload, path=memory_path or MEMORY_PATH)
    return payload


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=Path("data/ticks.db"))
    ap.add_argument("--lab", action="store_true", help="run research factory")
    ap.add_argument("--propose", action="store_true", help="write pending Lab rows (not ENABLE)")
    ap.add_argument("--lots", type=float, default=100.0)
    ap.add_argument("--fees", action="store_true", default=True)
    ap.add_argument("--no-fees", action="store_true")
    ap.add_argument("--minutes", type=int, default=60)
    ap.add_argument("--folds", type=int, default=3)
    ap.add_argument("--fast", action="store_true", help="screen losers first, then the same strong lab")
    ap.add_argument("--full", action="store_true", help="full weekly lab (overrides --fast)")
    ap.add_argument(
        "--no-improve",
        action="store_true",
        help="skip the closed-trade / same-slot improve pass after the factory",
    )
    args = ap.parse_args()
    fees = bool(args.fees) and not bool(args.no_fees)
    use_fast = bool(args.fast) and not bool(args.full)
    print("AMISE — invents challengers, you approve S21, S22, …. Keep DRY_RUN=true.")
    print("Market state → factory (optional) → improve existing books → you approve")
    print("Approve on Lab names the next slot (or overwrites that chair). Never DRY_RUN=false.", flush=True)
    result = run_amise(
        db=args.db,
        lab=bool(args.lab),
        propose=bool(args.propose),
        lots=float(args.lots),
        fees=fees,
        minutes=int(args.minutes),
        folds=int(args.folds),
        fast=use_fast,
        improve=not bool(args.no_improve),
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
    imp = result.get("improve") or {}
    if imp:
        print(
            f"improve proposed={len(imp.get('proposed_ids') or [])} "
            f"s18={((imp.get('s18') or {}).get('proposed'))} "
            f"hour_n={((imp.get('hour_gate') or {}).get('n'))}"
        )
    print("You approve on the station AMISE / Lab tabs. New slot or same-chair improve. Keep DRY_RUN=true.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
