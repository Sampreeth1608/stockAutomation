"""Profit Guardian — watch paper-book edge. Never auto-ENABLE or auto-kill.

Reads the blotter (after charges). Flags HEALTHY / WATCH / DETERIORATING / THIN.
Does not rewrite S13/S16. Does not flatten. Does not set DRY_RUN=false.
You still approve any change. Learning ≠ deploy.
"""

from __future__ import annotations

from typing import Any

from amise_slots import allocated_slots
from control_state import SLIM_PAPER_STRATEGIES

GUARDIAN_BOOKS: tuple[str, ...] = tuple(
    n
    for n in SLIM_PAPER_STRATEGIES
    if n not in {"S19_BODY_CLOSE_1H", "S20_FADE_HL"} and not n.endswith("_AMISE")
)


def _f(row: dict[str, Any], *keys: str) -> float:
    for key in keys:
        v = row.get(key, "")
        if v == "" or v is None:
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return 0.0


def profit_factor(wins: list[float], losses: list[float]) -> float:
    gp = sum(wins)
    gl = abs(sum(losses))
    if gl < 1e-12:
        return 9.99 if gp > 0 else 0.0
    return round(gp / gl, 3)


def expectancy(pnls: list[float]) -> float:
    if not pnls:
        return 0.0
    return round(sum(pnls) / len(pnls), 2)


def max_drawdown(pnls: list[float]) -> tuple[float, float]:
    """Return (drawdown_inr, peak_inr) on a cumulative curve."""
    peak = 0.0
    eq = 0.0
    dd = 0.0
    for x in pnls:
        eq += x
        if eq > peak:
            peak = eq
        drop = peak - eq
        if drop > dd:
            dd = drop
    return round(dd, 2), round(peak, 2)


def classify_status(
    *,
    n: int,
    pf: float,
    recent_pf: float,
    hist_pf: float,
    dd: float,
    peak: float,
) -> str:
    if n < 8:
        return "THIN"
    dd_frac = (dd / peak) if peak > 50 else 0.0
    if hist_pf >= 1.35 and recent_pf > 0 and recent_pf < 1.08 and n >= 16:
        return "DETERIORATING"
    if pf < 1.05 and n >= 12:
        return "DETERIORATING"
    if dd_frac >= 0.35 or pf < 1.20:
        return "WATCH"
    return "HEALTHY"


def score_closed_trades(closed: list[dict[str, Any]], *, strategy: str) -> dict[str, Any]:
    pnls = [_f(t, "pnl_after_charges", "pnl_after_tax", "gross_pnl") for t in closed]
    wins = [x for x in pnls if x > 0]
    losses = [x for x in pnls if x < 0]
    n = len(pnls)
    recent = pnls[-20:] if n else []
    hist = pnls[:-20] if n > 20 else pnls
    pf = profit_factor(wins, losses)
    r_wins = [x for x in recent if x > 0]
    r_loss = [x for x in recent if x < 0]
    h_wins = [x for x in hist if x > 0]
    h_loss = [x for x in hist if x < 0]
    recent_pf = profit_factor(r_wins, r_loss) if recent else pf
    hist_pf = profit_factor(h_wins, h_loss) if hist else pf
    dd, peak = max_drawdown(pnls)
    status = classify_status(
        n=n, pf=pf, recent_pf=recent_pf, hist_pf=hist_pf, dd=dd, peak=peak
    )
    return {
        "strategy": strategy,
        "n": n,
        "wins": len(wins),
        "losses": len(losses),
        "expectancy": expectancy(pnls),
        "recent_expectancy": expectancy(recent),
        "profit_factor": pf,
        "recent_pf": recent_pf,
        "historical_pf": hist_pf,
        "pnl_after_charges": round(sum(pnls), 2),
        "drawdown": dd,
        "peak": peak,
        "status": status,
        "note": _note(status, strategy),
    }


def _note(status: str, strategy: str) -> str:
    if strategy in {"S13_HHHL_DAY", "S4_OVERNIGHT"}:
        return "Daily swing — guardian watches, never dumps from the tick window."
    if status == "THIN":
        return "Too few closed trades to judge edge."
    if status == "DETERIORATING":
        return (
            "Edge looks weaker recently. Do not retune off the last losses. "
            "Research a challenger; you approve. Not ENABLE."
        )
    if status == "WATCH":
        return "Watch drawdown / PF. Suppress in unfit regimes after you approve."
    return "Edge looks intact on this blotter (after charges)."


def scan_guardian(
    trades: list[dict[str, Any]] | None = None,
    *,
    db=None,
) -> dict[str, Any]:
    """Blotter scan. ``db`` is a Path if trades are not passed in."""
    rows = trades
    if rows is None:
        from storage import build_trades

        kwargs: dict[str, Any] = {"signal_limit": 4000}
        if db is not None:
            kwargs["db_path"] = db
        rows = build_trades(**kwargs)
    books: list[dict[str, Any]] = []
    names = list(GUARDIAN_BOOKS)
    for name in allocated_slots():
        if name not in names:
            names.append(name)
    for name in names:
        closed = [
            t
            for t in rows
            if t.get("strategy") == name
            and str(t.get("status") or "").startswith("CLOSED")
        ]
        books.append(score_closed_trades(closed, strategy=name))
    worst = "HEALTHY"
    order = {"THIN": 0, "HEALTHY": 1, "WATCH": 2, "DETERIORATING": 3}
    for b in books:
        if order.get(b["status"], 0) > order.get(worst, 0):
            worst = b["status"]
    return {
        "ok": True,
        "books": books,
        "portfolio_status": worst,
        "n_deteriorating": sum(1 for b in books if b["status"] == "DETERIORATING"),
        "note": (
            "Guardian measures paper edge. It cannot guarantee rising profits. "
            "It will not auto-replace a champion or ENABLE a book. Keep DRY_RUN=true."
        ),
    }
