"""S16 research formula: close vs previous close picks HH/LL or wick.

Not paper. Not live. Backtest first.

Wait for the candle to **finish**. Same closed bar vs previous closed bar:

  If close > previous close → HH/LL only (S12):
    LONG  = higher high AND green  (H > prevH and C > O)
    SHORT = lower low  AND red    (L < prevL and C < O)
    else skip (wicks are ignored on an up close)

  If close < previous close → wick only (S14 raw):
    upper = high − max(open, close)
    lower = min(open, close) − low
    require |upper − lower| ≥ min_wick_gap (default 3; experiment 0/3/5/10)
    LONG  = lower > upper
    SHORT = upper > lower
    else skip (HH/LL is ignored on a down close)

  If close = previous close → skip

FLIP if already the other side. Fill at this bar's close.
Stay in until the opposite signal (or session flatten / last bar).
No range skip. No bald-body. No open=high/low (that is S14).
"""

from __future__ import annotations

from typing import Any

from backtest_hhhl_candles import Candle, Trade, in_session, make_charge_cfg
from backtest_wick_candles import _tf_result_from_trades
from charges import ChargeConfig, apply_charges_and_tax
from wick_candles import wick_measure

FORMULA = (
    "Wait for the candle to finish. Same closed bar vs previous close: "
    "C>prevC → HH/LL (HH+green LONG, LL+red SHORT) | "
    "C<prevC → wick if |U−L| ≥ gap (lower>upper LONG, upper>lower SHORT) | "
    "C=prevC skip. FLIP if already the other side. Fill at this bar's close."
)


def hhhl_side(prev: Candle, cur: Candle) -> str | None:
    want_long = cur.high > prev.high and cur.close > cur.open
    want_short = cur.low < prev.low and cur.close < cur.open
    if want_long and not want_short:
        return "long"
    if want_short and not want_long:
        return "short"
    return None


def wick_raw_side(cur: Candle) -> str | None:
    return wick_measure(cur.open, cur.high, cur.low, cur.close).dominant


def s16_bar_decision(
    prev: Candle,
    cur: Candle,
    *,
    min_wick_gap: float = 3.0,
) -> tuple[str | None, str]:
    """One finished candle → ('long'|'short'|None, why)."""
    if cur.close > prev.close:
        hh = hhhl_side(prev, cur)
        if hh == "long":
            return "long", "C>prev → HH+green"
        if hh == "short":
            return "short", "C>prev → LL+red"
        return None, "C>prev → no HH/LL"
    if cur.close < prev.close:
        m = wick_measure(cur.open, cur.high, cur.low, cur.close)
        diff = abs(m.upper - m.lower)
        if diff < float(min_wick_gap):
            return None, f"C<prev → wick gap {diff:.1f}<{float(min_wick_gap):g}"
        if m.lower > m.upper:
            return "long", "C<prev → lower wick"
        if m.upper > m.lower:
            return "short", "C<prev → upper wick"
        return None, "C<prev → equal wick"
    return None, "C=prev skip"


def explain_bar(
    prev: Candle,
    cur: Candle,
    pos: str,
    *,
    min_wick_gap: float = 3.0,
) -> dict[str, Any]:
    m = wick_measure(cur.open, cur.high, cur.low, cur.close)
    hh = hhhl_side(prev, cur)
    wk = wick_raw_side(cur)
    side, why = s16_bar_decision(prev, cur, min_wick_gap=min_wick_gap)
    if cur.close > prev.close:
        gate = "hhhl"
    elif cur.close < prev.close:
        gate = "wick"
    else:
        gate = "equal"
    prev_pos = pos
    action = "skip"
    pos_after = pos
    if side is None:
        action = "skip"
    elif side == pos:
        action = "hold"
    else:
        action = "FLIP" if pos != "flat" else "enter"
        pos_after = side
    return {
        "time": cur.time,
        "open": cur.open,
        "high": cur.high,
        "low": cur.low,
        "close": cur.close,
        "prev_close": prev.close,
        "prev_high": prev.high,
        "prev_low": prev.low,
        "upper": round(m.upper, 2),
        "lower": round(m.lower, 2),
        "wick_gap": round(abs(m.upper - m.lower), 2),
        "min_wick_gap": float(min_wick_gap),
        "gate": gate,
        "hhhl": hh or "none",
        "wick": wk or "none",
        "rule": why,
        "side": side or "skip",
        "action": action,
        "pos_before": prev_pos,
        "pos_after": pos_after,
    }


def walk_candles(
    candles: list[Candle],
    *,
    min_wick_gap: float = 3.0,
) -> list[dict[str, Any]]:
    pos = "flat"
    out: list[dict[str, Any]] = []
    for i in range(1, len(candles)):
        row = explain_bar(candles[i - 1], candles[i], pos, min_wick_gap=min_wick_gap)
        pos = str(row["pos_after"])
        out.append(row)
    return out


def _close_leg(
    *,
    tf: str,
    side: str,
    entry_time: str,
    entry_px: float,
    exit_c: Candle,
    cfg: ChargeConfig,
) -> Trade:
    if side == "LONG":
        pts = exit_c.close - entry_px
        order_side = "BUY"
    else:
        pts = entry_px - exit_c.close
        order_side = "SELL"
    settled = apply_charges_and_tax(
        pts,
        cfg,
        side=order_side,
        entry_price=entry_px,
        exit_price=exit_c.close,
    )
    return Trade(
        tf=tf,
        side=side,
        entry_time=entry_time,
        entry_px=entry_px,
        exit_time=exit_c.time,
        exit_px=exit_c.close,
        gross_pts=float(pts) * float(cfg.lot_size),
        gross_pnl_inr=float(settled["gross_pnl"]),
        after_tax_pnl_inr=float(settled["pnl_after_tax"]),
        fees_inr=float(settled["charges"]),
        lots=float(cfg.lot_size),
    )


def simulate_s16(
    candles: list[Candle],
    *,
    tf: str,
    lots: float = 100.0,
    fees: bool = True,
    session_filter: bool = True,
    market_open: str = "09:00",
    market_close: str = "23:30",
    charge_cfg: ChargeConfig | None = None,
    min_wick_gap: float = 3.0,
) -> Any:
    """Close-vs-prev gate with same-candle FLIP. Fill at signal-bar close."""
    cfg = charge_cfg or make_charge_cfg(fees=fees, lots=lots)
    trades: list[Trade] = []
    side: str | None = None
    entry_px = 0.0
    entry_time = ""

    def close_trade(exit_c: Candle) -> None:
        nonlocal side, entry_px, entry_time
        assert side is not None
        trades.append(
            _close_leg(
                tf=tf,
                side=side,
                entry_time=entry_time,
                entry_px=entry_px,
                exit_c=exit_c,
                cfg=cfg,
            )
        )
        side = None

    for i in range(1, len(candles)):
        prev, cur = candles[i - 1], candles[i]
        sess_ok = (not session_filter) or in_session(
            cur, open_hhmm=market_open, close_hhmm=market_close
        )
        if side is not None and session_filter and not sess_ok:
            close_trade(cur)
            continue
        if session_filter and not sess_ok:
            continue

        want, _why = s16_bar_decision(prev, cur, min_wick_gap=min_wick_gap)
        want_long = want == "long"
        want_short = want == "short"

        if side == "LONG":
            if want_short:
                close_trade(cur)
                side = "SHORT"
                entry_px = cur.close
                entry_time = cur.time
            continue
        if side == "SHORT":
            if want_long:
                close_trade(cur)
                side = "LONG"
                entry_px = cur.close
                entry_time = cur.time
            continue
        if want_long:
            side = "LONG"
            entry_px = cur.close
            entry_time = cur.time
        elif want_short:
            side = "SHORT"
            entry_px = cur.close
            entry_time = cur.time

    if side is not None and candles:
        close_trade(candles[-1])
    return _tf_result_from_trades(tf, len(candles), trades)
