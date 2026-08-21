"""S19 paper: 1h aligned body + close vs prev. Long and short. Not live.

Wait for the 1h to **finish**. Need a previous **same-session** 1h.

Paper LONG when BOTH are true:
    C > O          green body
    C > prevC      close up vs previous hour

Paper SHORT when BOTH are true:
    C < O          red body
    C < prevC      close down vs previous hour

Mixed hours (green but down-close, red but up-close), dojis, and equal
closes are **skipped**. The open side is **held** through a skip.
FLIP only on the opposite aligned signal. Fill at this bar's close.
Flatten at session end.

This is the coverage vs fee tradeoff: it trades both directions, but it
does **not** reverse every mixed hour (that path is close-follow research
only — round-trip charges eat 100-lot paper).

100-lot + Angel-fees 1h backtest (GOLDPETAL Aug-26 contract, 2026-05-18→2026-08-18
session hours) lost after charges vs S16 and vs S18. Desk lists it live-eligible —
you Arm. Not S16/S17/S18.
"""

from __future__ import annotations

from typing import Any, Callable

from backtest_hhhl_candles import Candle, in_session, make_charge_cfg
from backtest_wick_candles import _tf_result_from_trades
from charges import ChargeConfig
from s18_ohlc_vol_htf import VolBar, _close_leg

S19_NAME = "S19_BODY_CLOSE_1H"

FORMULA = (
    "Wait for the 1h to finish. Same-session prev 1h. "
    "LONG = green (C>O) and C>prevC. SHORT = red (C<O) and C<prevC. "
    "Skip mixed / doji / equal close / missing prev. Hold through skip. "
    "FLIP only on opposite aligned signal. Fill at close. Flatten EOD. "
    "Paper only, not live. Close-follow-every-hour is research, not this book."
)

CLOSE_FOLLOW_FORMULA = (
    "RESEARCH only — not paper. Every finished 1h: LONG if C>prevC, "
    "SHORT if C<prevC, skip equals. FLIP every hour the close changes side. "
    "Covers more bars; 100-lot round-trips usually lose after Angel charges."
)


def s19_bar_decision(prev: VolBar, cur: VolBar) -> tuple[str | None, str]:
    """Aligned body + close. Mixed / doji / equal → skip (hold)."""
    green = cur.close > cur.open
    red = cur.close < cur.open
    up_c = cur.close > prev.close
    dn_c = cur.close < prev.close
    if green and up_c:
        return "long", "aligned:long green C>prevC"
    if red and dn_c:
        return "short", "aligned:short red C<prevC"
    if cur.close == cur.open:
        return None, "doji"
    if cur.close == prev.close:
        return None, "close equals prev"
    if green and dn_c:
        return None, "mixed green down-close"
    if red and up_c:
        return None, "mixed red up-close"
    return None, "no aligned side"


def s19_close_follow_decision(prev: VolBar, cur: VolBar) -> tuple[str | None, str]:
    """Research: follow close vs prev every hour. Not the paper book."""
    if cur.close > prev.close:
        return "long", "follow:long C>prevC"
    if cur.close < prev.close:
        return "short", "follow:short C<prevC"
    return None, "close equals prev"


def _as_candle(bar: VolBar) -> Candle:
    return Candle(bar.time, bar.open, bar.high, bar.low, bar.close)


def simulate_s19(
    hours: list[VolBar],
    *,
    tf: str = "1h",
    lots: float = 100.0,
    fees: bool = True,
    session_filter: bool = True,
    market_open: str = "09:00",
    market_close: str = "23:30",
    charge_cfg: ChargeConfig | None = None,
    decide: Callable[[VolBar, VolBar], tuple[str | None, str]] | None = None,
) -> Any:
    """FLIP at 1h close when decide() fires. Flatten when the session ends."""
    cfg = charge_cfg or make_charge_cfg(fees=fees, lots=lots)
    pick = decide or s19_bar_decision
    trades: list = []
    side: str | None = None
    entry_px = 0.0
    entry_time = ""

    def close_trade(exit_c: VolBar) -> None:
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

    for i in range(1, len(hours)):
        prev, cur = hours[i - 1], hours[i]
        sess_ok = (not session_filter) or in_session(
            _as_candle(cur), open_hhmm=market_open, close_hhmm=market_close
        )
        if side is not None and session_filter and not sess_ok:
            close_trade(cur)
            continue
        if session_filter and not sess_ok:
            continue
        if session_filter:
            prev_sess = in_session(
                _as_candle(prev), open_hhmm=market_open, close_hhmm=market_close
            )
            if (not prev_sess) or prev.time[:10] != cur.time[:10]:
                continue
        want, _why = pick(prev, cur)
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

    if side is not None and hours:
        close_trade(hours[-1])
    return _tf_result_from_trades(tf, len(hours), trades)


def simulate_close_follow(
    hours: list[VolBar],
    **kwargs: Any,
) -> Any:
    kwargs.setdefault("tf", "1h:close_follow")
    return simulate_s19(hours, decide=s19_close_follow_decision, **kwargs)


def after_charges_inr(result: Any) -> float:
    """Gross minus Angel charges. Tax excluded (same rank as S11/S18)."""
    return float(result.gross_pnl_inr) - float(result.fees_inr)


def hours_from_ohlc(rows: list[dict[str, Any]]) -> list[VolBar]:
    """OHLC (+ optional volume) dicts → VolBars. Times as ``YYYY-MM-DD HH:MM:SS``."""
    out: list[VolBar] = []
    for r in rows:
        t = str(r["time"]).replace("T", " ")[:19]
        raw_vol = r.get("volume", r.get("vol", 0.0))
        try:
            volume = float(raw_vol or 0.0)
        except (TypeError, ValueError):
            volume = 0.0
        out.append(
            VolBar(
                t,
                float(r["open"]),
                float(r["high"]),
                float(r["low"]),
                float(r["close"]),
                volume,
            )
        )
    out.sort(key=lambda b: b.time)
    return out
