"""Why the desk win rate looks low — after-tax tape vs what the books actually guess."""

from __future__ import annotations

from typing import Any

from charges import ignore_fees_enabled
from control_state import paper_strategy_names

SHORT = {
    "S4_OVERNIGHT": "S4 HHHL swing",
    "S5_MINEDGE": "S5 minedge",
    "S8_NET_ZIGZAG": "S8 zigzag",
    "S11_DISCOVERED": "S11 pack",
    "S12_HHHL30": "S12 HHHL 30m",
    "S13_HHHL_DAY": "S13 daily S16",
    "S14_WICK30_STRICT": "S14 wick",
    "S15_WICK30_NOWICK": "S15 no-wick",
    "S16_HHHL_WICK_1H": "S16 HHHL+wick 1h",
    "S18_OHLC_VOL_HTF": "S18 OHLC+vol+day",
    "S19_BODY_CLOSE_1H": "S19 body+close 1h",
    "S20_FADE_HL": "S20 fade low/high",
    "S21_AMISE": "S21 AMISE",
    "S22_AMISE": "S22 AMISE",
    "S23_AMISE": "S23 AMISE",
    "S24_AMISE": "S24 AMISE",
}

WHAT_IT_GUESSES = {
    "S4_OVERNIGHT": "Old S13 HH/LL on the day candle, last 15m. Holds the trend across days/weeks until opposite HH+green / LL+red. FLIP. Never next-open.",
    "S5_MINEDGE": "Depth imbalance vs expected move. Waits until expected points can cover Angel fees (S5_COVER_FEES), even when IGNORE_FEES is on.",
    "S6_MIN30": "Depth imbalance vs a 30-point expected-move floor.",
    "FLOW_BRAIN": "Tick LTP + TBQ + TSQ pressure. LONG when price and buy-flow expand together. SHORT the sell-flow mirror. No trade on absorption. Exit on decay. Paper-wired, ENABLE_FLOW_BRAIN defaults false. Not S7_HOURLY. Not S16. Not live.",
    "S8_NET_ZIGZAG": "Order-book NET zigzags. Needs a strong rising imbalance (default 14%) and a target that can cover fees.",
    "S11_DISCOVERED": "Auto-discovered rules. They are paper hypotheses, not proven edge.",
    "S12_HHHL30": "30m higher-high / lower-low, last-minute confirm. Skips wick-only fakeouts (close must finish beyond the prior high/low).",
    "S13_HHHL_DAY": "S16 close-vs-prev on the day candle, last 15m. Holds the trend across days until the opposite S16 signal or Gold Petal monthly rollover (flatten last front session, then trade next month). FLIP. Never next-open.",
    "S14_WICK30_STRICT": "Same-candle open=high SHORT / open=low LONG, else wick. Weak nearly-equal wicks are skipped (formula order unchanged).",
    "S15_WICK30_NOWICK": "Bald 30m body only, HOLD (no reverse). Fewer trades; still no real edge filter.",
    "S16_HHHL_WICK_1H": "Wait for the 1h candle to finish. Up close: HH+green LONG / LL+red SHORT. Down close: wick (gap 0). FLIP at that close. Intraday only: flatten at MARKET_CLOSE, leftover at next open. Never overnight.",
    "S18_OHLC_VOL_HTF": "Wait for the 1h to finish. Base AND: green/red + close vs prev + HH/LL + volume up + close vs yesterday. Learner may change that pack from ticks (OHLC, volume, TBQ/TSQ, n_ticks). FLIP. Flatten at close. Paper only, not live.",
    "S19_BODY_CLOSE_1H": "Wait for the 1h to finish. LONG = green and C>prevC. SHORT = red and C<prevC. Mixed / doji / equal close: hold. FLIP only on the opposite aligned hour. Flatten at close. Paper-wired but ENABLE_S19 defaults off — 100-lot + fees 1h backtest lost after charges vs S16/S18. Not live.",
    "S20_FADE_HL": "Wait for the 1h to finish. LONG = lower low (not also HH) and green — buy the bounced low. SHORT = higher high (not also LL) and red — sell/short the rejected high. Inside / outside / knife / chase: hold. FLIP at that close. Flatten at close. Same formula scored on 30m/2h/4h/1d in the backtest. Paper-wired but ENABLE_S20 defaults off until a tape beats S16/S18 or S13 after charges. Not live.",
    "S21_AMISE": "AMISE challenger slot. Genome comes from Lab Approve (must beat S16+S18 after charges, walk-forward, 2× costs). 1h FLIP, flatten at close. Paper ENABLE on Approve. Angel still needs Unlock + LIVE. Mood gate picks when it may open.",
    "S22_AMISE": "AMISE challenger slot. Same as S21 — next Lab Approve fills the next free slot.",
    "S23_AMISE": "AMISE challenger slot. Same as S21 — next Lab Approve fills the next free slot.",
    "S24_AMISE": "AMISE challenger slot. Same as S21 — next Lab Approve fills the next free slot.",
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
            f"Always-in rules (S16 on each finished 1h) do this on the opposite signal."
        )
    if ignore_fees_enabled():
        notes.append(
            "IGNORE_FEES=true: most books still skip Angel fees in their own gates, "
            "but S5 now waits for a move that can cover real costs (S5_COVER_FEES). "
            "The tape still bills brokerage, MCX, GST and 30% tax."
        )
    notes.append(
        "Paper 100 lots is not live size (LIVE_MAX_LOTS cap 10). "
        "This desk tape is at PAPER_LOTS (default 100), not 1 lot. "
    )
    notes.append(
        "A desk-wide learner trains on every strategy and refits after each CLOSE. "
        "Each book has a base (usual after-tax win rate). New BUY/SHORT only fire when "
        "this hour/side looks better than that base — a good upcoming trade, not a "
        "coin flip. 70% is a stretch used when some hour actually hits it; if 70% is "
        "never available the book still takes its better-than-base setups. CLOSE is never gated."
    )
    s16 = next((b for b in books if b["strategy"] == "S16_HHHL_WICK_1H"), None)
    if s16 and int(s16["closed"]) >= 8:
        notes.append(
            f"S16 1h: {s16['closed']} closes, gross win {s16['win_rate_gross']}%, "
            f"after-tax win {s16['win_rate_after_tax']}%, after-tax ₹ {s16['pnl_after_tax']}. "
            f"It waits for the hour to finish, then HH/LL on an up close or wick on a down close."
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
    books = [_summarize(trades, strategy=name) for name in paper_strategy_names()]
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
    try:
        from charges import paper_lots

        payload["lots"] = paper_lots()
    except Exception:
        payload["lots"] = 100.0
    try:
        from trade_learner import get_learner

        lr = get_learner()
        if lr.n == 0 and rows:
            lr.fit(rows)
        payload["learner"] = lr.snapshot()
    except Exception as exc:
        payload["learner"] = {"note": f"skip {type(exc).__name__}", "enabled": False}
    return payload
