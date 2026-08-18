"""Why the desk win rate looks low — after-tax tape vs what the books actually guess."""

from __future__ import annotations

from typing import Any

from charges import ignore_fees_enabled
from control_state import SLIM_PAPER_STRATEGIES

SHORT = {
    "S4_OVERNIGHT": "S4 overnight",
    "S5_MINEDGE": "S5 minedge",
    "S8_NET_ZIGZAG": "S8 zigzag",
    "S11_DISCOVERED": "S11 pack",
    "S12_HHHL30": "S12 HHHL 30m",
    "S13_HHHL_DAY": "S13 HHHL day",
    "S14_WICK30_STRICT": "S14 wick",
    "S15_WICK30_NOWICK": "S15 no-wick",
}

WHAT_IT_GUESSES = {
    "S4_OVERNIGHT": "Overnight gap (heuristic/ML). One idea near the close — not a 30m coin flip.",
    "S5_MINEDGE": "Depth imbalance vs expected move. With IGNORE_FEES it does not wait to beat Angel costs.",
    "S8_NET_ZIGZAG": "Order-book NET zigzags. Can re-enter often when imbalance flickers.",
    "S11_DISCOVERED": "Auto-discovered rules. They are paper hypotheses, not proven edge.",
    "S12_HHHL30": "30m higher-high / lower-low, last-minute confirm. Noise HH/LL prints many false breaks.",
    "S13_HHHL_DAY": "Same HH/LL rule on the day candle. Fewer trades; still a breakout guess.",
    "S14_WICK30_STRICT": "Always in. Every finished 30m bar must be LONG or SHORT (unless equal wick), then FLIP. That is one guess per bar, not a filter.",
    "S15_WICK30_NOWICK": "Bald 30m body only, HOLD (no reverse). Fewer trades; still no real edge filter.",
}


def _num(row: dict[str, Any], key: str) -> float:
    v = row.get(key, "")
    if v == "" or v is None:
        if key == "pnl_after_tax":
            v = row.get("net_pnl", 0) or 0
        else:
            return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _summarize(trades: list[dict[str, Any]], *, strategy: str | None) -> dict[str, Any]:
    if strategy is not None:
        trades = [t for t in trades if t.get("strategy") == strategy]
    closed = [t for t in trades if str(t.get("status", "")).startswith("CLOSED")]
    open_n = sum(1 for t in trades if t.get("status") == "OPEN")
    gross_wins = [t for t in closed if _num(t, "gross_pnl") > 0]
    gross_losses = [t for t in closed if _num(t, "gross_pnl") < 0]
    tax_wins = [t for t in closed if _num(t, "pnl_after_tax") > 0]
    tax_losses = [t for t in closed if _num(t, "pnl_after_tax") < 0]
    fee_killed = [
        t
        for t in closed
        if _num(t, "gross_pnl") > 0 and _num(t, "pnl_after_tax") <= 0
    ]
    forced = [t for t in closed if str(t.get("status", "")).startswith("CLOSED_FORCED")]
    n = len(closed)
    gross = sum(_num(t, "gross_pnl") for t in closed)
    fees = sum(_num(t, "charges") for t in closed)
    tax = sum(_num(t, "tax") for t in closed)
    after = sum(_num(t, "pnl_after_tax") for t in closed)
    return {
        "strategy": strategy or "ALL",
        "label": SHORT.get(strategy or "", strategy or "All books"),
        "guess": WHAT_IT_GUESSES.get(strategy or "", ""),
        "closed": n,
        "open": open_n,
        "win_rate_gross": round(100.0 * len(gross_wins) / n, 1) if n else 0.0,
        "win_rate_after_tax": round(100.0 * len(tax_wins) / n, 1) if n else 0.0,
        "wins_gross": len(gross_wins),
        "losses_gross": len(gross_losses),
        "wins_after_tax": len(tax_wins),
        "losses_after_tax": len(tax_losses),
        "fee_killed": len(fee_killed),
        "flips": len(forced),
        "gross_pnl": round(gross, 2),
        "fees": round(fees, 2),
        "tax": round(tax, 2),
        "pnl_after_tax": round(after, 2),
        "avg_win": round(sum(_num(t, "pnl_after_tax") for t in tax_wins) / len(tax_wins), 2)
        if tax_wins
        else 0.0,
        "avg_loss": round(sum(_num(t, "pnl_after_tax") for t in tax_losses) / len(tax_losses), 2)
        if tax_losses
        else 0.0,
    }


def _worst(closed: list[dict[str, Any]], limit: int = 8) -> list[dict[str, Any]]:
    ranked = sorted(closed, key=lambda t: _num(t, "pnl_after_tax"))
    out: list[dict[str, Any]] = []
    for t in ranked[:limit]:
        if _num(t, "pnl_after_tax") >= 0:
            break
        out.append(
            {
                "strategy": t.get("strategy") or "",
                "label": SHORT.get(str(t.get("strategy") or ""), str(t.get("strategy") or "")),
                "side": t.get("side") or "",
                "entry_ts": t.get("entry_ts") or "",
                "exit_ts": t.get("exit_ts") or "",
                "entry_price": t.get("entry_price") or "",
                "exit_price": t.get("exit_price") or "",
                "gross_pnl": _num(t, "gross_pnl"),
                "fees": _num(t, "charges"),
                "pnl_after_tax": _num(t, "pnl_after_tax"),
                "why": t.get("exit_reason") or t.get("entry_reason") or "",
                "status": t.get("status") or "",
            }
        )
    return out


def _notes(overall: dict[str, Any], books: list[dict[str, Any]]) -> list[str]:
    notes: list[str] = []
    n = int(overall["closed"])
    if n == 0:
        return [
            "No closed trades in this window yet. Win rate is empty until a book exits or FLIPs.",
        ]
    notes.append(
        f"Scoreboard win rate is after Angel fees and 30% tax on winners only "
        f"({overall['win_rate_after_tax']}% of {n} closes). "
        f"Direction-only (gross points) is {overall['win_rate_gross']}%."
    )
    killed = int(overall["fee_killed"])
    if killed:
        notes.append(
            f"{killed} trades were the right direction (gross > 0) but fees + tax "
            f"turned them into scoreboard losses. Those are not 'wrong guesses' — they are too small."
        )
    flips = int(overall["flips"])
    if flips:
        notes.append(
            f"{flips} closes are FLIPs (CLOSED_FORCED): the book dumped one side to take the other. "
            f"Always-in rules (especially S14 every 30m) do this on noise."
        )
    if ignore_fees_enabled():
        notes.append(
            "IGNORE_FEES=true: strategies enter as if costs are ₹0. The tape still bills "
            "brokerage, MCX, GST and 30% tax. That gap is why many 'wins' print red."
        )
    notes.append(
        "Paper 100 lots is not live size (LIVE_MAX_LOTS cap 10). Fees scale with lots, "
        "so a 100-lot paper tape looks much worse than 1–10 live lots on the same points."
    )
    s14 = next((b for b in books if b["strategy"] == "S14_WICK30_STRICT"), None)
    if s14 and int(s14["closed"]) >= 8:
        notes.append(
            f"S14 wick: {s14['closed']} closes, gross win {s14['win_rate_gross']}%, "
            f"after-tax win {s14['win_rate_after_tax']}%, after-tax ₹ {s14['pnl_after_tax']}. "
            f"It must pick LONG or SHORT on almost every finished 30m candle — that is a coin flip plus fees."
        )
    noisy = [
        b
        for b in books
        if int(b["closed"]) >= 20 and float(b["win_rate_after_tax"]) < 48
    ]
    if noisy:
        names = ", ".join(str(b["label"]) for b in noisy)
        notes.append(
            f"High-frequency books under 48% after-tax: {names}. "
            f"Running several of these at once multiplies guesses on the same Gold Petal ticks."
        )
    if float(overall["pnl_after_tax"]) < 0 and float(overall["gross_pnl"]) > 0:
        notes.append(
            f"Gross was +₹{overall['gross_pnl']} but after-tax is ₹{overall['pnl_after_tax']}. "
            f"Fees ₹{overall['fees']} and tax ₹{overall['tax']} ate the edge — same pattern as S14 Aug 1d "
            f"(+₹12,400 gross → about −₹7,061 after tax at 100 lots)."
        )
    return notes


def analyze_trades(trades: list[dict[str, Any]]) -> dict[str, Any]:
    overall = _summarize(trades, strategy=None)
    overall["label"] = "All books"
    overall["guess"] = "Every enabled book guesses on the same ticks. Their fees add; their edges do not."
    books = [_summarize(trades, strategy=name) for name in SLIM_PAPER_STRATEGIES]
    closed = [t for t in trades if str(t.get("status", "")).startswith("CLOSED")]
    return {
        "overall": overall,
        "books": books,
        "worst": _worst(closed),
        "notes": _notes(overall, books),
        "ignore_fees": ignore_fees_enabled(),
    }


def analysis_payload(*, db_path: Any = None) -> dict[str, Any]:
    from desk_data import all_trades_cached, resolve_desk_db

    db = db_path or resolve_desk_db()
    rows, err = all_trades_cached(db_path=db)
    payload = analyze_trades(rows)
    payload["error"] = err
    payload["db_path"] = str(db)
    payload["window"] = "slim books, last 1500 signals each"
    return payload
