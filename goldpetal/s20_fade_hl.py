"""S20 paper: fade a finished bar's low/high. Long and short. Not live.

Wait for the candle to **finish**. Need a previous bar on the same TF.

A **low is a buy**. A **high is a sell or short**.

Paper LONG when ALL are true:
    L < prevL      this bar made a lower low
    not H > prevH  not also a higher high (outside bar skips)
    C > O          green — the low bounced (fill is this close)

Paper SHORT when ALL are true:
    H > prevH      this bar made a higher high
    not L < prevL  not also a lower low
    C < O          red — the high rejected (fill is this close)

Inside bars, outside bars, LL that close red (knife), and HH that close
green (chase) are **skipped**. The open side is **held** through a skip.
FLIP only on the opposite fade. Fill at this bar's close.
Intraday TFs flatten at session end. Daily holds overnight until opposite.

We cannot fill at the exact printed low/high without looking into the
future. The close of the bar that made the extreme is the honest fill.

Research rows in the same backtest (not the paper book):
    raw     — LL → long / HH → short, ignore body
    wick    — lower wick → long / upper wick → short (S14-style)
    mtf     — 1h bounce only when 4h and day do not disagree

One paper book (1h). Same formula is scored on 30m / 1h / 2h / 4h / 1d.
Not S13/S16/S17/S18/S19. Not live. 100-lot + fees Gold Petal tape
(2026-05-18→2026-08-17): 1h bounce +₹14k after charges vs S16 +₹41k
and S18 +₹256k. Daily bounce lost to S13. ENABLE_S20 stays false.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Callable

from backtest_hhhl_candles import Candle, in_session
from s16_hhhl_wick import simulate_s16
from wick_candles import wick_measure

S20_NAME = "S20_FADE_HL"

FORMULA = (
    "Wait for the candle to finish. Same-TF prev bar. "
    "LONG = LL (not also HH) and green — buy the low that bounced. "
    "SHORT = HH (not also LL) and red — sell/short the high that rejected. "
    "Skip inside / outside / knife / chase. Hold through skip. "
    "FLIP only on the opposite fade. Fill at close. Intraday flatten EOD; "
    "daily holds overnight. Paper only, not live. "
    "Raw LL/HH and wick-fade are research rows, not this book."
)

RAW_FORMULA = (
    "RESEARCH only — not paper. Finished bar: LONG if LL-only, SHORT if HH-only, "
    "skip inside/outside. Ignores body. Fill at close."
)

WICK_FORMULA = (
    "RESEARCH only — not paper. Finished bar: LONG if lower wick > upper, "
    "SHORT if upper wick > lower, skip equal. Fill at close."
)


def fade_bounce_decision(prev: Candle, cur: Candle) -> tuple[str | None, str]:
    """Paper: buy a bounced low, short a rejected high."""
    hh = cur.high > prev.high
    ll = cur.low < prev.low
    green = cur.close > cur.open
    red = cur.close < cur.open
    if hh and ll:
        return None, "outside HH+LL skip"
    if ll and green:
        return "long", "bounce:LL green buy the low"
    if hh and red:
        return "short", "reject:HH red sell the high"
    if ll and not green:
        return None, "LL but not green (knife)"
    if hh and not red:
        return None, "HH but not red (chase)"
    return None, "inside no new HL"


def fade_raw_decision(prev: Candle, cur: Candle) -> tuple[str | None, str]:
    """Research: every new low is a buy, every new high is a short."""
    hh = cur.high > prev.high
    ll = cur.low < prev.low
    if hh and ll:
        return None, "outside HH+LL skip"
    if ll:
        return "long", "raw:LL buy the low"
    if hh:
        return "short", "raw:HH sell the high"
    return None, "inside no new HL"


def fade_wick_decision(prev: Candle, cur: Candle) -> tuple[str | None, str]:
    """Research: wick rejection of the extreme. Prev unused."""
    del prev
    m = wick_measure(cur.open, cur.high, cur.low, cur.close)
    if m.lower > m.upper:
        return "long", "wick:lower buy the low"
    if m.upper > m.lower:
        return "short", "wick:upper sell the high"
    return None, "equal wick"


def after_charges_inr(result: Any) -> float:
    """Gross minus Angel charges. Tax excluded (same rank as S11/S18/S19)."""
    return float(result.gross_pnl_inr) - float(result.fees_inr)


def candles_from_ohlc(rows: list[dict[str, Any]]) -> list[Candle]:
    out: list[Candle] = []
    for r in rows:
        t = str(r["time"]).replace("T", " ")[:19]
        out.append(
            Candle(
                t,
                float(r["open"]),
                float(r["high"]),
                float(r["low"]),
                float(r["close"]),
            )
        )
    out.sort(key=lambda c: c.time)
    return out


def _parse_bar(ts: str) -> datetime:
    return datetime.strptime(str(ts).replace("T", " ")[:19], "%Y-%m-%d %H:%M:%S")


def aggregate_candles(
    candles: list[Candle],
    minutes: int,
    *,
    origin_hhmm: str = "09:00",
) -> list[Candle]:
    """Merge finished bars into a higher TF, anchored at session open."""
    if minutes <= 1 or not candles:
        return list(candles)
    oh, om = (int(x) for x in origin_hhmm.split(":"))
    groups: dict[datetime, list[Candle]] = {}
    for c in candles:
        dt = _parse_bar(c.time)
        origin = dt.replace(hour=oh, minute=om, second=0, microsecond=0)
        if dt < origin:
            continue
        delta_min = int((dt - origin).total_seconds() // 60)
        block = (delta_min // minutes) * minutes
        key = origin + timedelta(minutes=block)
        groups.setdefault(key, []).append(c)
    out: list[Candle] = []
    for key in sorted(groups):
        bs = groups[key]
        out.append(
            Candle(
                key.strftime("%Y-%m-%d %H:%M:%S"),
                bs[0].open,
                max(b.high for b in bs),
                min(b.low for b in bs),
                bs[-1].close,
            )
        )
    return out


def bar_end(candle: Candle, minutes: int) -> datetime:
    return _parse_bar(candle.time) + timedelta(minutes=int(minutes))


def last_completed_side(
    bars: list[Candle],
    *,
    asof_end: datetime,
    bar_minutes: int,
    decide: Callable[[Candle, Candle], tuple[str | None, str]],
) -> tuple[str | None, str]:
    """Side of the last higher-TF bar that had already finished by asof_end."""
    last_i: int | None = None
    for i, cur in enumerate(bars):
        if bar_end(cur, bar_minutes) <= asof_end:
            last_i = i
        else:
            break
    if last_i is None or last_i < 1:
        return None, "no completed higher bar"
    prev, cur = bars[last_i - 1], bars[last_i]
    return decide(prev, cur)


def simulate_s20(
    candles: list[Candle],
    *,
    tf: str = "1h:S20",
    lots: float = 100.0,
    fees: bool = True,
    session_filter: bool = True,
    decide: Callable[[Candle, Candle], tuple[str | None, str]] | None = None,
    **kwargs: Any,
) -> Any:
    pick = decide or fade_bounce_decision
    return simulate_s16(
        candles,
        tf=tf,
        lots=lots,
        fees=fees,
        session_filter=session_filter,
        decide=pick,
        **kwargs,
    )


def simulate_mtf_s20(
    hours: list[Candle],
    higher: list[tuple[str, list[Candle], int]],
    *,
    tf: str = "1h:S20_mtf",
    lots: float = 100.0,
    fees: bool = True,
    session_filter: bool = True,
    hour_minutes: int = 60,
    decide: Callable[[Candle, Candle], tuple[str | None, str]] | None = None,
    require_all: bool = False,
    **kwargs: Any,
) -> Any:
    """1h fade, blocked when a completed higher TF votes the other side.

    ``require_all``: every higher TF must also fire the same side (rarer).
    Default: higher TF skip (inside) does not block; only a fight blocks.
    """
    pick = decide or fade_bounce_decision

    def mtf_decide(prev: Candle, cur: Candle) -> tuple[str | None, str]:
        side, why = pick(prev, cur)
        if side is None:
            return None, why
        asof = bar_end(cur, hour_minutes)
        votes = [side]
        for name, bars, mins in higher:
            h_side, h_why = last_completed_side(
                bars, asof_end=asof, bar_minutes=mins, decide=pick
            )
            if h_side is None:
                if require_all:
                    return None, f"mtf miss {name}: {h_why}"
                continue
            if h_side != side:
                return None, f"mtf fight 1h={side} {name}={h_side}"
            votes.append(h_side)
        return side, f"mtf agree {why}"

    return simulate_s20(
        hours,
        tf=tf,
        lots=lots,
        fees=fees,
        session_filter=session_filter,
        decide=mtf_decide,
        **kwargs,
    )


def session_hours(
    candles: list[Candle],
    *,
    market_open: str = "09:00",
    market_close: str = "23:30",
) -> list[Candle]:
    return [
        c
        for c in candles
        if in_session(c, open_hhmm=market_open, close_hhmm=market_close)
    ]
