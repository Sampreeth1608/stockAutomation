"""Google Sheets trading dashboard tabs — view only, real Gold Petal numbers.

Python owns ticks, mood, books, and orders. Sheets shows a 1–60s snapshot.
There is no single "AI DECISION LONG 73%" brain. Books decide separately.
Rank after-charges ₹ (tax excluded). Keep DRY_RUN=true.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from capital import capital_snapshot, load_capital
from charges import paper_lots
from control_state import load_state, paper_strategy_names
from desk_data import last_tick_snapshot, tape_freshness
from human_capture import session_status
from live_readiness import PAPER_ONLY_BOOKS, desk_snapshot
from market_mood import snapshot_mood
from paper_report import summarize_trades
from profit_guardian import score_closed_trades
from storage import DB_PATH

IST = ZoneInfo("Asia/Kolkata")

LIVE_FIELDS = ["section", "field", "value", "bar"]
MARKET_FIELDS = ["field", "value"]
STRATEGY_FIELDS = [
    "strategy",
    "state",
    "mood_fit_pct",
    "mood_stance",
    "pf_after_charges",
    "after_charges_₹",
    "win_pct_after_charges",
    "closed",
    "open",
    "guardian",
    "status",
    "in_bot",
    "note",
]
LAB_FIELDS = [
    "id",
    "strategy",
    "title",
    "status",
    "n_trades",
    "after_charges_₹",
    "profit_factor",
    "win_rate",
    "safety_ok",
    "approve",
]
RISK_FIELDS = ["key", "value", "enforced_in"]
COMMAND_FIELDS = ["command", "request", "allowed", "last_result", "note"]

DASHBOARD_TABS = (
    "LIVE",
    "MARKET",
    "STRATEGIES",
    "SIGNALS",
    "TRADES",
    "LAB",
    "RISK",
    "COMMANDS",
)


def _yn(v: Any) -> str:
    return "YES" if bool(v) else "NO"


def _num(v: Any) -> float:
    if v == "" or v is None:
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _round(v: Any, n: int = 2) -> str:
    if v is None or v == "":
        return ""
    try:
        return f"{float(v):.{n}f}"
    except (TypeError, ValueError):
        return str(v)


def _bar(pct: float, width: int = 14) -> str:
    try:
        p = float(pct)
    except (TypeError, ValueError):
        p = 0.0
    p = max(0.0, min(100.0, p))
    filled = int(round(p / 100.0 * width))
    return ("█" * filled) + ("░" * (width - filled))


def _pct(part: float, whole: float) -> float:
    if whole <= 1e-12:
        return 0.0
    return 100.0 * part / whole


def monitor_book_names() -> list[str]:
    names = list(paper_strategy_names())
    if "FLOW_BRAIN" not in names:
        names.append("FLOW_BRAIN")
    return names


def _oi_from_raw(raw_json: str) -> float | None:
    try:
        msg = json.loads(raw_json or "{}")
    except json.JSONDecodeError:
        return None
    if not isinstance(msg, dict):
        return None
    for key in ("open_interest", "opnInterest", "oi"):
        if msg.get(key) in (None, ""):
            continue
        try:
            return float(msg[key])
        except (TypeError, ValueError):
            continue
    return None


def last_quote(db_path: Path) -> dict[str, Any]:
    """Latest tick snapshot for the LIVE tab. Not a tick stream into Sheets."""
    empty: dict[str, Any] = {
        "ltp": None,
        "prev_ltp": None,
        "volume": None,
        "oi": None,
        "tbq": None,
        "tsq": None,
        "received_at": "",
    }
    if not Path(db_path).is_file():
        return empty
    conn = sqlite3.connect(str(db_path), timeout=2.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout=2000")
        rows = conn.execute(
            "SELECT ltp, volume, bp, sp, raw_json, received_at "
            "FROM ticks ORDER BY id DESC LIMIT 2"
        ).fetchall()
    except Exception:
        return empty
    finally:
        conn.close()
    if not rows:
        return empty
    cur = rows[0]
    prev = rows[1] if len(rows) > 1 else None
    return {
        "ltp": cur["ltp"],
        "prev_ltp": prev["ltp"] if prev is not None else None,
        "volume": cur["volume"],
        "oi": _oi_from_raw(str(cur["raw_json"] or "")),
        "tbq": cur["bp"],
        "tsq": cur["sp"],
        "received_at": cur["received_at"] or "",
    }


def _kv(section: str, field: str, value: Any, bar: str = "") -> dict[str, str]:
    return {
        "section": section,
        "field": field,
        "value": "" if value is None else str(value),
        "bar": bar,
    }


def build_live_rows(
    *,
    db_path: Path = DB_PATH,
    now: datetime | None = None,
) -> list[dict[str, str]]:
    clock = now or datetime.now(IST)
    quote = last_quote(db_path)
    tape = last_tick_snapshot(db_path=db_path, now=clock)
    if not tape.get("last_tick_at"):
        tape.update(tape_freshness(last_tick_at="", now=clock))
    sess = session_status(now=clock)
    live = desk_snapshot()
    mood = snapshot_mood(db_path)
    running = False
    try:
        from analytics.bot_ops import bot_status

        running = bool(bot_status(lite=True).get("running"))
    except Exception:
        running = False
    ltp = _num(quote.get("ltp"))
    prev = quote.get("prev_ltp")
    chg = (ltp - _num(prev)) if prev not in (None, "") else None
    chg_pct = (100.0 * chg / ltp) if chg is not None and ltp else None
    tbq = _num(quote.get("tbq"))
    tsq = _num(quote.get("tsq"))
    tot = tbq + tsq
    buy_pct = _pct(tbq, tot)
    sell_pct = _pct(tsq, tot)
    imb_pct = buy_pct - sell_pct
    would = bool(live.get("would_place_real_orders"))
    mode = "LIVE-UNSAFE" if would else ("PAPER" if live.get("dry_run") else "CHECK DESK")
    enables = dict(live.get("enables") or {})
    in_bot = [n for n in monitor_book_names() if enables.get(n)]
    fits_trade = []
    for name in in_bot:
        fit = mood.fit_for(name) or {}
        if str(fit.get("stance") or "") == "trade":
            side = str(fit.get("preferred_side") or "none")
            fits_trade.append(f"{name}:{side}")
    goldpetal_running = bool(sess.get("open")) and running and bool(tape.get("tape_live"))
    return [
        _kv("QUOTE", "title", "GOLDPETAL PAPER DASHBOARD"),
        _kv("QUOTE", "updated_at_ist", clock.isoformat(timespec="seconds")),
        _kv("QUOTE", "goldpetal_running", _yn(goldpetal_running)),
        _kv("QUOTE", "mode", mode),
        _kv("QUOTE", "ltp", _round(quote.get("ltp"), 2)),
        _kv("QUOTE", "change_₹", _round(chg, 2) if chg is not None else ""),
        _kv("QUOTE", "change_pct", _round(chg_pct, 3) if chg_pct is not None else ""),
        _kv("QUOTE", "volume", _round(quote.get("volume"), 0)),
        _kv("QUOTE", "oi", _round(quote.get("oi"), 0)),
        _kv("QUOTE", "tape_age_sec", _round(tape.get("tape_age_sec"), 1)),
        _kv("PRESSURE", "buy_pct", _round(buy_pct, 1), _bar(buy_pct)),
        _kv("PRESSURE", "sell_pct", _round(sell_pct, 1), _bar(sell_pct)),
        _kv("PRESSURE", "imbalance_pct", _round(imb_pct, 1)),
        _kv("PRESSURE", "tbq", _round(quote.get("tbq"), 0)),
        _kv("PRESSURE", "tsq", _round(quote.get("tsq"), 0)),
        _kv("MOOD", "market_mood", f"{mood.mood} · {mood.direction}"),
        _kv("MOOD", "label", mood.label),
        _kv("MOOD", "momentum", _round(mood.momentum, 3)),
        _kv("MOOD", "volatility", _round(mood.volatility, 3)),
        _kv("MOOD", "regime", mood.regime),
        _kv("MOOD", "transition", mood.transition),
        _kv(
            "RESEARCH",
            "small_medium_large_move",
            "not live — FLOW_BRAIN next-lab is research; ENABLE_FLOW_BRAIN=false",
        ),
        _kv(
            "DECISION",
            "ai_decision",
            "none — each book decides; there is no one LONG 73% brain",
        ),
        _kv("DECISION", "mood_confidence_pct", _round(100.0 * _num(mood.confidence), 1)),
        _kv("DECISION", "books_in_bot", ",".join(in_bot) or "-"),
        _kv("DECISION", "fits_now_trade", ",".join(fits_trade) or "-"),
        _kv("ENGINE", "dry_run", _yn(live.get("dry_run"))),
        _kv("ENGINE", "would_place_real_orders", _yn(would)),
        _kv("ENGINE", "session", str(sess.get("label") or "")),
        _kv(
            "NOTE",
            "architecture",
            "Angel WS → Python ticks.db → mood/books → this Sheet. Not tick-into-Sheets.",
        ),
    ]


def build_market_rows(*, db_path: Path = DB_PATH) -> list[dict[str, str]]:
    mood = snapshot_mood(db_path)
    quote = last_quote(db_path)
    tbq = _num(quote.get("tbq"))
    tsq = _num(quote.get("tsq"))
    tot = tbq + tsq
    return [
        {"field": "mood", "value": str(mood.mood)},
        {"field": "direction", "value": str(mood.direction)},
        {"field": "label", "value": str(mood.label)},
        {"field": "reason", "value": str(mood.reason)},
        {"field": "regime", "value": str(mood.regime)},
        {"field": "transition", "value": str(mood.transition)},
        {"field": "trend_strength", "value": _round(mood.trend_strength, 3)},
        {"field": "direction_score", "value": _round(mood.direction_score, 3)},
        {"field": "momentum", "value": _round(mood.momentum, 3)},
        {"field": "volatility", "value": _round(mood.volatility, 3)},
        {"field": "heat", "value": _round(mood.heat, 3)},
        {"field": "buy_pressure", "value": _round(mood.buy_pressure, 3)},
        {"field": "imbalance", "value": _round(mood.imb, 3)},
        {"field": "imbalance_delta", "value": _round(mood.imb_delta, 3)},
        {"field": "tbq", "value": _round(quote.get("tbq"), 0)},
        {"field": "tsq", "value": _round(quote.get("tsq"), 0)},
        {"field": "buy_pct", "value": _round(_pct(tbq, tot), 1)},
        {"field": "oi", "value": _round(quote.get("oi"), 0)},
        {"field": "volume", "value": _round(quote.get("volume"), 0)},
        {"field": "compression", "value": _round(mood.compression, 3)},
        {"field": "breakout_probability", "value": _round(mood.breakout_probability, 3)},
        {"field": "reversal_probability", "value": _round(mood.reversal_probability, 3)},
        {"field": "confidence", "value": _round(mood.confidence, 3)},
        {"field": "allow_long", "value": _yn(mood.allow_long)},
        {"field": "allow_short", "value": _yn(mood.allow_short)},
        {"field": "gate_on", "value": _yn(mood.gate_on)},
        {"field": "flatten_on", "value": _yn(mood.flatten_on)},
        {"field": "n_samples", "value": str(mood.n_samples)},
    ]


def _after_charges_win_stats(closed: list[dict[str, Any]]) -> tuple[int, int, float]:
    wins = [t for t in closed if _num(t.get("pnl_after_charges")) > 0]
    losses = [t for t in closed if _num(t.get("pnl_after_charges")) < 0]
    pct = (len(wins) / len(closed) * 100.0) if closed else 0.0
    return len(wins), len(losses), pct


def build_strategy_rows(
    trades: list[dict[str, Any]],
    *,
    db_path: Path = DB_PATH,
) -> list[dict[str, Any]]:
    live = desk_snapshot()
    st = load_state()
    enables = dict(live.get("enables") or {})
    mood = snapshot_mood(db_path)
    rows: list[dict[str, Any]] = []
    for name in monitor_book_names():
        book_trades = [t for t in trades if t.get("strategy") == name]
        closed = [t for t in book_trades if str(t.get("status", "")).startswith("CLOSED")]
        summary = summarize_trades(book_trades, strategy=name)
        _w, _l, win_pct = _after_charges_win_stats(closed)
        scored = score_closed_trades(closed, strategy=name)
        fit = mood.fit_for(name) or {}
        in_bot = bool(enables.get(name))
        paper_only = name in PAPER_ONLY_BOOKS
        side = str(fit.get("preferred_side") or "none")
        stance = str(fit.get("stance") or "")
        if not in_bot:
            state = "OFF"
            status = "OFF"
        elif not st.trading_enabled or st.emergency_off:
            state = "HOLD"
            status = "PAUSED"
        elif stance == "stand_down":
            state = "HOLD"
            status = "STAND_DOWN"
        elif paper_only:
            state = side.upper() if side in {"long", "short"} else "HOLD"
            status = "PAPER"
        elif live.get("dry_run"):
            state = side.upper() if side in {"long", "short"} else "HOLD"
            status = "ACTIVE"
        else:
            state = side.upper() if side in {"long", "short"} else "HOLD"
            status = "CHECK DESK"
        note = ""
        if name == "FLOW_BRAIN" and not in_bot:
            note = "research — ENABLE stays false until a sized after-charges tape"
        elif name == "S20_FADE_HL" and not in_bot:
            note = "off until ENABLE"
        elif paper_only:
            note = "paper only — not live"
        rows.append(
            {
                "strategy": name,
                "state": state,
                "mood_fit_pct": _round(100.0 * _num(fit.get("weight")), 1),
                "mood_stance": stance,
                "pf_after_charges": _round(scored.get("profit_factor"), 3),
                "after_charges_₹": _round(summary.get("pnl_after_charges"), 1),
                "win_pct_after_charges": f"{win_pct:.1f}",
                "closed": summary.get("closed") or 0,
                "open": summary.get("open") or 0,
                "guardian": scored.get("status") or "",
                "status": status,
                "in_bot": _yn(in_bot),
                "note": note,
            }
        )
    rows.sort(key=lambda r: _num(r.get("after_charges_₹")), reverse=True)
    return rows


def build_lab_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    try:
        from research_desk import research_desk_payload

        lab = research_desk_payload()
        pending.extend(list(lab.get("pending") or []))
    except Exception:
        lab = {}
    try:
        from proposals import proposals_snapshot

        snap = proposals_snapshot()
        seen = {str(p.get("id")) for p in pending}
        for p in snap.get("pending") or []:
            if str(p.get("id")) not in seen:
                pending.append(p)
    except Exception:
        pass
    if not pending:
        rows.append(
            {
                "id": "",
                "strategy": "",
                "title": "No pending lab/ML proposals",
                "status": "",
                "n_trades": "",
                "after_charges_₹": "",
                "profit_factor": "",
                "win_rate": "",
                "safety_ok": "",
                "approve": "Approve / Reject / Paper test stay on desk Lab + ML tabs",
            }
        )
        return rows
    for p in pending:
        paper = p.get("paper") or {}
        if not isinstance(paper, dict):
            paper = {}
        extra = paper.get("extra") if isinstance(paper.get("extra"), dict) else {}
        metrics = extra.get("metrics") if isinstance(extra.get("metrics"), dict) else extra
        if not isinstance(metrics, dict):
            metrics = {}
        ac = (
            metrics.get("after_charges")
            if isinstance(metrics, dict)
            else None
        )
        pf = metrics.get("profit_factor") if isinstance(metrics, dict) else None
        rows.append(
            {
                "id": p.get("id") or "",
                "strategy": p.get("strategy") or "",
                "title": p.get("title") or p.get("summary") or "",
                "status": p.get("status") or "pending",
                "n_trades": paper.get("n_trades") or metrics.get("n_trades") or "",
                "after_charges_₹": _round(ac if ac is not None else paper.get("gross_pnl_inr"), 1),
                "profit_factor": _round(pf, 3) if pf is not None else "",
                "win_rate": _round(paper.get("win_rate"), 1),
                "safety_ok": _yn(p.get("safety_ok")),
                "approve": "desk Lab/ML — Sheets cannot Approve / micro-live / Unlock",
            }
        )
    return rows


def build_risk_rows() -> list[dict[str, str]]:
    st = load_state()
    cap = capital_snapshot()
    plan = load_capital()
    live = desk_snapshot()
    env = live
    return [
        {"key": "paper_lots", "value": str(paper_lots()), "enforced_in": "Python runner"},
        {
            "key": "live_max_lots",
            "value": str(env.get("live_max_lots") or ""),
            "enforced_in": "Python live_orders — desk only",
        },
        {
            "key": "max_lots_total",
            "value": str(plan.max_lots_total),
            "enforced_in": "Python capital.can_open_trade",
        },
        {
            "key": "daily_loss_limit_inr",
            "value": _round(plan.daily_loss_limit_inr, 0),
            "enforced_in": "Python capital.can_open_trade",
        },
        {
            "key": "today_pnl_inr",
            "value": _round(cap.get("today_pnl_inr"), 2),
            "enforced_in": "Python capital snapshot",
        },
        {
            "key": "daily_loss_breached",
            "value": _yn(cap.get("daily_loss_breached")),
            "enforced_in": "Python",
        },
        {
            "key": "deployable_inr",
            "value": _round(cap.get("deployable_inr"), 2),
            "enforced_in": "Python",
        },
        {
            "key": "emergency_off",
            "value": _yn(st.emergency_off),
            "enforced_in": "Python control_state",
        },
        {
            "key": "trading_enabled",
            "value": _yn(st.trading_enabled),
            "enforced_in": "Python control_state",
        },
        {"key": "dry_run", "value": _yn(live.get("dry_run")), "enforced_in": "Python .env"},
        {
            "key": "live_unlocked",
            "value": _yn(st.live_unlocked),
            "enforced_in": "Python — Unlock stays on desk",
        },
        {
            "key": "would_place_real_orders",
            "value": _yn(live.get("would_place_real_orders")),
            "enforced_in": "Python",
        },
        {
            "key": "micro_live",
            "value": "refused from Sheets — type LIVE on desk 8501",
            "enforced_in": "desk",
        },
        {
            "key": "note",
            "value": "Sheet cells do not cap lots. Python does. COMMANDS may pause/emergency only.",
            "enforced_in": "Python",
        },
    ]


def command_template_rows(*, results: dict[str, str] | None = None) -> list[dict[str, str]]:
    from sheet_commands import COMMAND_SPECS

    got = results or {}
    rows: list[dict[str, str]] = []
    for spec in COMMAND_SPECS:
        rows.append(
            {
                "command": spec["command"],
                "request": "",
                "allowed": spec["allowed"],
                "last_result": got.get(spec["command"], ""),
                "note": spec["note"],
            }
        )
    return rows
