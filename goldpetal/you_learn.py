"""Learn a mimic from You-tab sittings, then paper-trade it on a button.

Records 30m, 1h, 3h, or whole-day sessions. Fits TBQ/TSQ/LTP/candle
relationships to your clicks. Press Let it trade to ENABLE the AMISE
chair in paper, only in the hours you actually trade. Compare You vs
mimic, then keep sitting and press Learn + Let it trade again if needed.
Never sets DRY_RUN=false. Live still needs Unlock + LIVE on Live money.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from amise_slots import (
    allocated_slots,
    assign_slot,
    enable_key,
    load_slot_genome,
    write_slot,
)
from control_state import approve_strategy_paper
from human_capture import EXAMPLES_PATH, _parse_ts, capture_summary, load_examples
from proposals import PaperResult, StrategyProposal, add_proposal
from strategy_genome import (
    RESEARCH_KIND,
    RESEARCH_STRATEGY,
    StrategyGenome,
    env_patch_is_safe,
    research_env_patch,
)

IST = ZoneInfo("Asia/Kolkata")
ROOT = Path(__file__).resolve().parent
MIMIC_PATH = ROOT / "data" / "human_capture" / "mimic.json"
DEPLOY_PATH = ROOT / "data" / "human_capture" / "deploy.json"
MIN_TAKEN = 8
MIN_SKIPS = 4
MIN_TOTAL = 16
AUTO_TAKEN = 16
AUTO_SKIPS = 8
AUTO_TOTAL = 24
AUTO_SESSIONS = 2
AUTO_LONG_SEC = 3 * 3600
AUTO_SETTLED = 8
AUTO_WR = 0.52
SESSION_GAP_MIN = 45
HOUR_PAD_MIN = 10
LIFT_TAKE = 0.55
LIFT_SKIP = 0.45
_lock = threading.Lock()

LONG_FLAGS: tuple[tuple[str, str], ...] = (
    ("bull", "bull"),
    ("hh", "hh"),
    ("hl", "hl"),
    ("hc", "hc"),
    ("vol_up", "vol_up"),
    ("lower_wick", "lower_wick"),
    ("close_high", "close_high"),
    ("px_gt_vwap", "px_gt_vwap"),
    ("imb_buy", "imb_buy"),
    ("depth_buy", "depth_buy"),
    ("mom_up", "mom_up"),
)
SHORT_FLAGS: tuple[tuple[str, str], ...] = (
    ("bear", "bear"),
    ("lh", "lh"),
    ("ll", "ll"),
    ("lc", "lc"),
    ("vol_up", "vol_up"),
    ("upper_wick", "upper_wick"),
    ("close_low", "close_low"),
    ("px_lt_vwap", "px_lt_vwap"),
    ("imb_sell", "imb_sell"),
    ("depth_sell", "depth_sell"),
    ("mom_dn", "mom_dn"),
)
SKIP_FLAGS: tuple[tuple[str, str], ...] = (
    ("spread_wide", "spread_wide"),
    ("rvol_extreme", "rvol_extreme"),
)


def _now_iso() -> str:
    return datetime.now(IST).isoformat(timespec="seconds")


def _ex_dt(ex: dict[str, Any]) -> datetime | None:
    return _parse_ts(str(ex.get("clicked_at_ist") or ex.get("created_at_ist") or ex.get("entry_at") or ""))


def duration_bucket(sec: float) -> str:
    if sec < 45 * 60:
        return "30m"
    if sec < 90 * 60:
        return "1h"
    if sec < 4 * 3600:
        return "3h"
    return "day"


def group_sessions(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Cluster clicks into sittings: 30m, 1h, 3h, or whole day."""
    timed: list[tuple[datetime, dict[str, Any]]] = []
    for ex in items:
        if ex.get("action") == "close":
            continue
        dt = _ex_dt(ex)
        if dt is None:
            continue
        timed.append((dt, ex))
    timed.sort(key=lambda x: x[0])
    groups: list[list[tuple[datetime, dict[str, Any]]]] = []
    cur: list[tuple[datetime, dict[str, Any]]] = []
    for dt, ex in timed:
        if not cur:
            cur = [(dt, ex)]
            continue
        prev_dt, prev_ex = cur[-1]
        same_sid = bool(
            ex.get("you_session_id")
            and prev_ex.get("you_session_id")
            and ex.get("you_session_id") == prev_ex.get("you_session_id")
        )
        same_day = dt.date() == prev_dt.date()
        gap = (dt - prev_dt).total_seconds()
        if same_sid or (same_day and gap <= SESSION_GAP_MIN * 60):
            cur.append((dt, ex))
        else:
            groups.append(cur)
            cur = [(dt, ex)]
    if cur:
        groups.append(cur)
    out: list[dict[str, Any]] = []
    for group in groups:
        start = group[0][0]
        end = group[-1][0]
        sec = max(0.0, (end - start).total_seconds())
        out.append(
            {
                "id": str(group[0][1].get("you_session_id") or group[0][1].get("id") or ""),
                "start": start.isoformat(timespec="seconds"),
                "end": end.isoformat(timespec="seconds"),
                "n": len(group),
                "duration_sec": round(sec, 1),
                "bucket": duration_bucket(sec),
            }
        )
    return out


def _fmt_hhmm(minutes: int) -> str:
    m = max(0, int(minutes))
    return f"{m // 60:02d}:{m % 60:02d}"


def hours_profile(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Hours you actually click. Mimic only trades inside this window."""
    minutes: list[int] = []
    mask = 0
    for ex in items:
        if ex.get("action") == "close":
            continue
        dt = _ex_dt(ex)
        if dt is None:
            continue
        minutes.append(dt.hour * 60 + dt.minute)
        mask |= 1 << dt.hour
    if not minutes:
        return {
            "open_min": None,
            "close_min": None,
            "hours_mask": 0,
            "label": "",
            "n_hours": 0,
        }
    lo = max(9 * 60, min(minutes) - HOUR_PAD_MIN)
    hi = min(23 * 60 + 30, max(minutes) + HOUR_PAD_MIN)
    if hi <= lo:
        hi = min(23 * 60 + 30, lo + 30)
    return {
        "open_min": int(lo),
        "close_min": int(hi),
        "hours_mask": int(mask),
        "label": f"{_fmt_hhmm(lo)}–{_fmt_hhmm(hi)} IST",
        "n_hours": bin(mask).count("1"),
    }


def _bar(ex: dict[str, Any]) -> dict[str, Any]:
    snap = ex.get("snapshot") or {}
    return dict(snap.get("last_1m") or snap.get("last_5m") or snap.get("last_1h") or {})


def example_flags(ex: dict[str, Any]) -> dict[str, bool]:
    """What the tape looked like when you clicked. Not an order."""
    snap = dict(ex.get("snapshot") or {})
    b = _bar(ex)
    imb = float(snap.get("imb") or 0.0)
    dimb = float(snap.get("depth_imb") or 0.0)
    gap = snap.get("vwap_gap")
    spread = snap.get("spread")
    ltp = snap.get("ltp")
    vel = float(snap.get("ltp_velocity_30s") or 0.0)
    body = float(b.get("body_frac") or 0.0)
    upper = float(b.get("upper_wick_frac") or 0.0)
    lower = float(b.get("lower_wick_frac") or 0.0)
    n_ticks = float(b.get("n_ticks") or 0.0)
    return {
        "bull": bool(b.get("bull")),
        "bear": bool(b.get("bear")),
        "hh": bool(b.get("hh")),
        "hl": bool(b.get("hl")),
        "hc": bool(b.get("hc")),
        "lh": bool(b.get("lh")),
        "ll": bool(b.get("ll")),
        "lc": bool(b.get("lc")),
        "vol_up": bool(b.get("vol_up")),
        "oi_up": bool(b.get("oi_up")),
        "lower_wick": lower >= 0.40,
        "upper_wick": upper >= 0.40,
        "close_high": bool(b.get("bull") and body >= 0.45 and upper <= 0.25),
        "close_low": bool(b.get("bear") and body >= 0.45 and lower <= 0.25),
        "px_gt_vwap": gap is not None and float(gap) > 0,
        "px_lt_vwap": gap is not None and float(gap) < 0,
        "imb_buy": imb >= 0.08,
        "imb_sell": imb <= -0.08,
        "depth_buy": dimb >= 0.08,
        "depth_sell": dimb <= -0.08,
        "mom_up": vel > 0,
        "mom_dn": vel < 0,
        "spread_wide": (
            spread is not None
            and ltp is not None
            and float(ltp) > 0
            and (float(spread) / float(ltp)) >= 0.0008
        ),
        "rvol_extreme": n_ticks >= 400,
    }


def _rate(rows: list[dict[str, Any]], flag: str) -> float:
    if not rows:
        return 0.0
    hits = sum(1 for r in rows if example_flags(r).get(flag))
    return hits / float(len(rows))


def pick_atoms(
    taken: list[dict[str, Any]],
    skipped: list[dict[str, Any]],
    pairs: tuple[tuple[str, str], ...],
    *,
    limit: int = 4,
) -> list[str]:
    scored: list[tuple[float, str]] = []
    for flag, atom in pairs:
        t = _rate(taken, flag)
        s = _rate(skipped, flag) if skipped else 0.0
        if t < LIFT_TAKE:
            continue
        if skipped and s > LIFT_SKIP and t - s < 0.12:
            continue
        scored.append((t - s, atom))
    scored.sort(key=lambda x: (-x[0], x[1]))
    out: list[str] = []
    for _, atom in scored:
        if atom not in out:
            out.append(atom)
        if len(out) >= limit:
            break
    return out


def _settled_taken(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in rows:
        if r.get("action") not in {"buy", "short"}:
            continue
        mark = (r.get("outcomes") or {}).get("1m") or {}
        if mark.get("taken_pts") is None:
            continue
        out.append(r)
    return out


def learn_status(items: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = items if items is not None else load_examples()
    buys = [r for r in rows if r.get("action") == "buy"]
    shorts = [r for r in rows if r.get("action") == "short"]
    skips = [r for r in rows if r.get("action") == "no_trade"]
    taken = buys + shorts
    n = len(rows)
    sessions = group_sessions(rows)
    hours = hours_profile(rows)
    buckets: dict[str, int] = {"30m": 0, "1h": 0, "3h": 0, "day": 0}
    max_dur = 0.0
    for s in sessions:
        b = str(s.get("bucket") or "30m")
        buckets[b] = buckets.get(b, 0) + 1
        max_dur = max(max_dur, float(s.get("duration_sec") or 0))
    need_taken = max(0, MIN_TAKEN - len(taken))
    need_skips = max(0, MIN_SKIPS - len(skips))
    need_total = max(0, MIN_TOTAL - n)
    ready = need_taken == 0 and need_skips == 0 and need_total == 0
    summary = capture_summary(rows)
    wr = summary.get("win_rate_1m")
    settled = _settled_taken(rows)
    wr_known = wr is not None and len(settled) >= AUTO_SETTLED
    long_sit = max_dur >= AUTO_LONG_SEC
    sessions_ok = len(sessions) >= AUTO_SESSIONS or long_sit
    knowledge_good = bool(
        len(taken) >= AUTO_TAKEN
        and len(skips) >= AUTO_SKIPS
        and n >= AUTO_TOTAL
        and sessions_ok
        and wr_known
        and float(wr or 0) >= AUTO_WR
    )
    auto_need: list[str] = []
    if len(taken) < AUTO_TAKEN:
        auto_need.append(f"{AUTO_TAKEN - len(taken)} more BUY/SHORT")
    if len(skips) < AUTO_SKIPS:
        auto_need.append(f"{AUTO_SKIPS - len(skips)} more NO TRADE")
    if n < AUTO_TOTAL:
        auto_need.append(f"{AUTO_TOTAL - n} total clicks")
    if not sessions_ok:
        auto_need.append("another sitting (or one 3h+ / whole-day sitting)")
    if not wr_known:
        auto_need.append(f"{max(0, AUTO_SETTLED - len(settled))} settled 1m marks")
    elif float(wr or 0) < AUTO_WR:
        auto_need.append(f"1m win% ≥ {int(AUTO_WR * 100)} (now {int(float(wr) * 100)})")
    if knowledge_good:
        note = (
            "Knowledge is good. Press Let it trade when you want the mimic to "
            f"paper in your hours ({hours.get('label') or 'session'}). "
            "Keep clicking afterwards if you want to teach more. Never DRY_RUN=false."
        )
    elif ready:
        extra = ("; ".join(auto_need) + ". ") if auto_need else ""
        note = (
            "Draft ready. Press Let it trade to paper the mimic in your hours, "
            "then compare You vs mimic. " + extra
            + "Keep sitting to teach more."
        )
    else:
        note = (
            f"Need {need_taken} more BUY/SHORT, {need_skips} more NO TRADE "
            f"(and {need_total} total clicks). Trade now to teach — 30m / 1h / 3h / "
            "whole day all count. Then press Let it trade."
        )
    return {
        "n": n,
        "n_buy": len(buys),
        "n_short": len(shorts),
        "n_no_trade": len(skips),
        "n_taken": len(taken),
        "n_sessions": len(sessions),
        "sessions": sessions[-8:],
        "session_buckets": buckets,
        "max_session_sec": max_dur,
        "hours": hours,
        "min_taken": MIN_TAKEN,
        "min_skips": MIN_SKIPS,
        "min_total": MIN_TOTAL,
        "need_taken": need_taken,
        "need_skips": need_skips,
        "need_total": need_total,
        "ready": ready,
        "can_start": ready,
        "knowledge_good": knowledge_good,
        "auto_need": auto_need,
        "n_settled_1m": len(settled),
        "win_rate_1m": wr,
        "note": note,
    }


def build_mimic_genome(items: list[dict[str, Any]]) -> StrategyGenome:
    buys = [r for r in items if r.get("action") == "buy"]
    shorts = [r for r in items if r.get("action") == "short"]
    skips = [r for r in items if r.get("action") == "no_trade"]
    hours = hours_profile(items)
    long_e = pick_atoms(buys, skips, LONG_FLAGS) if buys else []
    short_e = pick_atoms(shorts, skips, SHORT_FLAGS) if shorts else []
    if not long_e and buys:
        long_e = ["bull", "imb_buy"]
    if not short_e and shorts:
        short_e = ["bear", "imb_sell"]
    no_trade = ["spread_wide", "rvol_extreme"]
    for flag, atom in SKIP_FLAGS:
        if _rate(skips, flag) >= 0.35 and atom not in no_trade:
            no_trade.append(atom)
    if buys and not shorts:
        direction = "long"
        short_e = []
    elif shorts and not buys:
        direction = "short"
        long_e = []
    else:
        direction = "both"
    params: dict[str, float] = {"imb_th": 0.08, "depth_th": 0.08}
    if hours.get("open_min") is not None and hours.get("close_min") is not None:
        params["you_open_min"] = float(hours["open_min"])
        params["you_close_min"] = float(hours["close_min"])
        params["you_hours_mask"] = float(hours.get("hours_mask") or 0)
    return StrategyGenome(
        name="you mimic 1m TBQ/TSQ",
        direction=direction,
        timeframe="1m",
        entry_long=tuple(long_e),
        entry_short=tuple(short_e),
        no_trade=tuple(no_trade),
        exit="flip_eod",
        params=params,
        source="you_capture",
        recipe="",
        notes=(
            "Inferred from You-tab sittings (30m / 1h / 3h / day). "
            f"Hours {hours.get('label') or 'Gold Petal session'}. "
            "Not a rewrite of S13/S16."
        ),
    ).normalized()


def find_you_mimic_slot(*, folder: Path | None = None) -> str | None:
    for name in allocated_slots(folder):
        g = load_slot_genome(name, folder)
        if g is None:
            continue
        if g.source == "you_capture" or "you mimic" in (g.name or "").lower():
            return name
    return None


def _load_deploy(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _save_deploy(row: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(row, indent=2), encoding="utf-8")


def deploy_status(path: Path | None = None) -> dict[str, Any]:
    raw = _load_deploy(path if path is not None else DEPLOY_PATH)
    slot = str(raw.get("slot") or "").strip()
    if not slot:
        return {}
    return {
        "slot": slot,
        "genome_id": raw.get("genome_id"),
        "hours": raw.get("hours") or {},
        "updated_at_ist": raw.get("updated_at_ist"),
        "knowledge_good": bool(raw.get("knowledge_good")),
        "started_at_ist": raw.get("started_at_ist"),
        "armed_by": raw.get("armed_by") or "",
        "armed": True,
    }


def _enable_slot_keep_dry(
    slot: str, *, env_path: Path | None = None, sync_environ: bool = True
) -> dict[str, Any]:
    """Paper ENABLE only. Never writes DRY_RUN (live arm stays yours)."""
    from analytics.env_bridge import write_env_updates

    key = enable_key(slot)
    res = write_env_updates({key: "true"}, path=env_path)
    if res.get("ok") and sync_environ:
        import os

        os.environ[key] = "true"
    return res


def propose_mimic(
    *,
    examples_path: Path = EXAMPLES_PATH,
    proposals_path: Path | None = None,
    mimic_path: Path | None = None,
) -> dict[str, Any]:
    """Write a pending Lab row. Does not arm Angel."""
    items = load_examples(examples_path)
    status = learn_status(items)
    if not status["ready"]:
        return {"ok": False, "error": status["note"], "learn": status, "proposed": False}
    genome = build_mimic_genome(items)
    summary = capture_summary(items)
    wr = float(summary.get("win_rate_1m") or 0.0)
    n_taken = int(summary.get("n_taken") or 0)
    hours = status.get("hours") or {}
    patch = research_env_patch()
    if not env_patch_is_safe(patch):
        raise RuntimeError("you-mimic env_patch must stay DRY_RUN=true")
    extra = {
        "genome": genome.to_dict(),
        "lab": "RESEARCH_FACTORY",
        "paper_next": True,
        "live": False,
        "enable": False,
        "you_mimic": True,
        "n_examples": status["n"],
        "n_buy": status["n_buy"],
        "n_short": status["n_short"],
        "n_no_trade": status["n_no_trade"],
        "n_sessions": status["n_sessions"],
        "hours": hours,
        "session_buckets": status.get("session_buckets") or {},
        "vs_coded": summary.get("vs_coded") or {},
        "knowledge_good": bool(status.get("knowledge_good")),
    }
    prop = StrategyProposal(
        id="",
        week_id="you-mimic",
        kind=RESEARCH_KIND,
        strategy=RESEARCH_STRATEGY,
        title="You mimic (1m TBQ/TSQ/LTP)",
        summary=(
            f"{n_taken} clicks / {status['n_sessions']} sittings → {genome.timeframe} "
            f"{hours.get('label') or ''} "
            f"long={' AND '.join(genome.entry_long) or '—'} "
            f"short={' AND '.join(genome.entry_short) or '—'}."
        ),
        paper=PaperResult(n_trades=n_taken, win_rate=wr, extra=extra),
        model_path="data/human_capture/examples.json",
        safety_ok=bool(status.get("knowledge_good")),
        safety_reasons=[
            "human mimic from You-tab sittings (30m / 1h / 3h / day)",
            "starts only when you press Let it trade; never DRY_RUN=false",
        ],
        status="pending",
        env_patch=patch,
    )
    kwargs: dict[str, Any] = {}
    if proposals_path is not None:
        kwargs["path"] = proposals_path
    saved = add_proposal(prop, **kwargs)
    dest = mimic_path if mimic_path is not None else MIMIC_PATH
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        json.dumps(
            {
                "updated_at_ist": _now_iso(),
                "genome": genome.to_dict(),
                "learn": status,
                "proposal_id": saved.id,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "ok": True,
        "proposed": True,
        "proposal_id": saved.id,
        "genome": genome.to_dict(),
        "learn": status,
        "reminder": (
            "Style updated. Press Let it trade when you want the mimic to paper "
            "in your hours, then compare. Keep DRY_RUN=true until you type LIVE "
            "yourself."
        ),
    }


def maybe_auto_paper(
    *,
    examples_path: Path = EXAMPLES_PATH,
    proposals_path: Path | None = None,
    mimic_path: Path | None = None,
    slots_dir: Path | None = None,
    env_path: Path | None = None,
    state_path: Path | None = None,
    deploy_path: Path | None = None,
    apply_env: bool = True,
    force: bool = False,
) -> dict[str, Any]:
    """ENABLE the you-mimic AMISE chair in paper. Never DRY_RUN=false.

    Does nothing unless force=True (Let it trade). Clicks never start it.
    """
    items = load_examples(examples_path)
    status = learn_status(items)
    dest_deploy = deploy_path if deploy_path is not None else DEPLOY_PATH
    if not force:
        return {
            "ok": True,
            "deployed": False,
            "learn": status,
            "note": "Press Let it trade to start the mimic in paper.",
        }
    if not status["ready"]:
        return {"ok": False, "deployed": False, "learn": status, "error": status["note"]}
    genome = build_mimic_genome(items)
    prev = _load_deploy(dest_deploy)
    slot = find_you_mimic_slot(folder=slots_dir)
    with _lock:
        if slot:
            slot_info = write_slot(
                slot,
                genome,
                proposal_id="you-mimic",
                folder=slots_dir,
                note="you mimic Let it trade",
            )
        else:
            slot_info = assign_slot(
                genome,
                proposal_id="you-mimic",
                folder=slots_dir,
                note="you mimic Let it trade",
            )
        if not slot_info.get("ok"):
            return {
                "ok": False,
                "deployed": False,
                "error": slot_info.get("error") or "slot assign failed",
                "learn": status,
            }
        slot = str(slot_info["slot"])
        env_applied = False
        if apply_env:
            applied = _enable_slot_keep_dry(
                slot, env_path=env_path, sync_environ=env_path is None
            )
            env_applied = bool(applied.get("ok"))
            slot_info["env"] = applied
        approve_strategy_paper(slot, path=state_path)
        started = str(prev.get("started_at_ist") or "") or _now_iso()
        if str(prev.get("genome_id") or "") != genome.genome_id:
            started = _now_iso()
        _save_deploy(
            {
                "updated_at_ist": _now_iso(),
                "started_at_ist": started,
                "armed_by": "button",
                "slot": slot,
                "genome_id": genome.genome_id,
                "hours": status.get("hours") or {},
                "knowledge_good": bool(status.get("knowledge_good")),
            },
            dest_deploy,
        )
    try:
        propose_mimic(
            examples_path=examples_path,
            proposals_path=proposals_path,
            mimic_path=mimic_path,
        )
    except Exception:
        pass
    hours = status.get("hours") or {}
    return {
        "ok": True,
        "deployed": True,
        "slot": slot,
        "genome_id": genome.genome_id,
        "enable_key": enable_key(slot),
        "env_applied": env_applied,
        "restart_needed": True,
        "live_unlocked": False,
        "started_at_ist": started,
        "learn": status,
        "note": (
            f"Named {slot}. Paper ENABLE on. It will trade like you in "
            f"{hours.get('label') or 'your hours'} after Restart. Compare You vs "
            "mimic on this tab. Keep sitting to teach more, then Learn + Let it "
            "trade again. This did not set DRY_RUN=false."
        ),
    }


def _you_marks(items: list[dict[str, Any]]) -> dict[str, Any]:
    taken = [r for r in items if r.get("action") in {"buy", "short"}]
    skips = [r for r in items if r.get("action") == "no_trade"]
    pts: list[float] = []
    for r in taken:
        mark = (r.get("outcomes") or {}).get("1m") or {}
        if mark.get("taken_pts") is not None:
            pts.append(float(mark["taken_pts"]))
    wins = sum(1 for x in pts if x > 0)
    return {
        "n": len(items),
        "n_taken": len(taken),
        "n_no_trade": len(skips),
        "n_settled_1m": len(pts),
        "win_rate_1m": (wins / float(len(pts))) if pts else None,
        "pts_1m": round(sum(pts), 2) if pts else None,
    }


def _ts_ge(raw: str, start: str) -> bool:
    a = _parse_ts(str(raw or ""))
    b = _parse_ts(str(start or ""))
    if a is None or b is None:
        return False
    return a >= b


def _mimic_marks(trades: list[dict[str, Any]], slot: str) -> dict[str, Any]:
    from paper_report import summarize_trades

    s = summarize_trades(trades, strategy=slot)
    closed_rows = [t for t in trades if str(t.get("status") or "").startswith("CLOSED")]

    def _gross(t: dict[str, Any]) -> float:
        v = t.get("gross_pnl")
        if v == "" or v is None:
            v = t.get("net_pnl")
        try:
            return float(v or 0)
        except (TypeError, ValueError):
            return 0.0

    wins = sum(1 for t in closed_rows if _gross(t) > 0)
    closed = len(closed_rows)
    return {
        "n_trades": int(s.get("trades") or 0),
        "n_closed": closed,
        "n_open": int(s.get("open") or 0),
        "n_wins": wins,
        "win_rate": (wins / float(closed)) if closed else None,
        "pnl_after_charges": s.get("pnl_after_charges"),
        "pnl_after_tax": s.get("pnl_after_tax"),
        "gross_pnl": s.get("gross_pnl"),
    }


def compare_you_vs_mimic(
    *,
    examples_path: Path = EXAMPLES_PATH,
    deploy_path: Path | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """You 1m marks vs mimic closed paper trades. Not a live unlock."""
    items = load_examples(examples_path)
    deploy = deploy_status(deploy_path)
    you_all = _you_marks(items)
    started = str(deploy.get("started_at_ist") or "")
    you_since = (
        _you_marks([r for r in items if _ts_ge(str(r.get("created_at_ist") or ""), started)])
        if started
        else you_all
    )
    slot = str(deploy.get("slot") or "")
    mimic_all: dict[str, Any] = {}
    mimic_since: dict[str, Any] = {}
    recent: list[dict[str, Any]] = []
    if slot:
        from desk_data import resolve_desk_db
        from storage import build_trades

        db = db_path or resolve_desk_db()
        trades = build_trades(strategy=slot, db_path=db)
        mimic_all = _mimic_marks(trades, slot)
        since_trades = (
            [t for t in trades if _ts_ge(str(t.get("entry_ts") or ""), started)]
            if started
            else trades
        )
        mimic_since = _mimic_marks(since_trades, slot)
        closed = [t for t in since_trades if str(t.get("status") or "").startswith("CLOSED")]
        for t in closed[-6:]:
            recent.append(
                {
                    "side": t.get("side"),
                    "entry_ts": t.get("entry_ts"),
                    "exit_ts": t.get("exit_ts"),
                    "net_pnl": t.get("net_pnl"),
                    "status": t.get("status"),
                }
            )
    armed = bool(slot)
    ywr = you_since.get("win_rate_1m")
    mwr = mimic_since.get("win_rate")
    if not armed:
        verdict = (
            "Trade here to teach. When the draft is ready, press Let it trade. "
            "Then we compare your 1m marks with the mimic's closed paper trades."
        )
    elif not mimic_since.get("n_closed"):
        verdict = (
            f"Mimic papering {slot}. Type RESTART if you just pressed Let it trade. "
            "Waiting for its first closed paper trade."
        )
    elif ywr is None:
        verdict = (
            f"Mimic has trades on {slot}. Keep clicking so 1m marks fill, then compare win%."
        )
    elif mwr is None:
        verdict = "You have 1m marks. Waiting for the mimic to close a paper trade."
    elif float(mwr) >= float(ywr):
        verdict = (
            "Mimic 1m-vs-paper win% is matching or ahead of you since start. "
            "Keep sitting if you want to teach more, then Learn my style and Let it trade again."
        )
    else:
        verdict = (
            "You are ahead on 1m win% since start. Keep sitting, press Learn my style, "
            "then Let it trade again to reload the mimic."
        )
    return {
        "you": you_all,
        "you_since_start": you_since,
        "mimic": {"slot": slot or None, "armed": armed, **mimic_all},
        "mimic_since_start": mimic_since,
        "started_at_ist": started or None,
        "recent_mimic": recent,
        "verdict": verdict,
        "note": (
            "You column is 1m marks on your clicks (not fills). "
            "Mimic column is closed paper trades on the AMISE chair. "
            "Never DRY_RUN=false from this tab."
        ),
    }


def start_mimic_paper(
    *,
    examples_path: Path = EXAMPLES_PATH,
    **kwargs: Any,
) -> dict[str, Any]:
    """Let it trade button: paper ENABLE the current mimic. Never DRY_RUN=false."""
    dep_keys = (
        "proposals_path",
        "mimic_path",
        "slots_dir",
        "env_path",
        "state_path",
        "deploy_path",
        "apply_env",
    )
    dep_kw = {k: kwargs[k] for k in dep_keys if k in kwargs}
    prop_kw = {k: kwargs[k] for k in ("proposals_path", "mimic_path") if k in kwargs}
    items = load_examples(examples_path)
    status = learn_status(items)
    if not status["ready"]:
        return {
            "ok": False,
            "deployed": False,
            "learn": status,
            "compare": compare_you_vs_mimic(
                examples_path=examples_path,
                deploy_path=kwargs.get("deploy_path"),
            ),
            "error": status["note"],
        }
    try:
        propose_mimic(examples_path=examples_path, **prop_kw)
    except Exception:
        pass
    out = maybe_auto_paper(examples_path=examples_path, force=True, **dep_kw)
    out["compare"] = compare_you_vs_mimic(
        examples_path=examples_path,
        deploy_path=kwargs.get("deploy_path") or dep_kw.get("deploy_path"),
    )
    return out


def after_new_example(
    *,
    examples_path: Path = EXAMPLES_PATH,
    **kwargs: Any,
) -> dict[str, Any]:
    """Refit after a click. Does not start the mimic — that is Let it trade."""
    items = load_examples(examples_path)
    status = learn_status(items)
    out: dict[str, Any] = {"learn": status, "proposed": False, "deployed": False}
    prop_kw = {k: kwargs[k] for k in ("proposals_path", "mimic_path") if k in kwargs}
    if status["ready"]:
        try:
            prop = propose_mimic(examples_path=examples_path, **prop_kw)
            out["proposed"] = bool(prop.get("proposed"))
            out["proposal_id"] = prop.get("proposal_id")
        except Exception as exc:
            out["propose_error"] = str(exc)
    out["note"] = status["note"]
    return out
