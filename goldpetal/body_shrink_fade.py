"""3-candle body shrink fade. Research only. Not a paper book. Not live.

Wait for candle 1 and candle 2 to **finish**. Candle 3 is the trade bar.

SHORT (fade two greens):
    1 and 2 are bullish (C>O)
    2's body is smaller than 1's body
    2's body sits lower (body high of 2 < body high of 1)
    On candle 3, sell at the open (honest) or nearer the high (research)

LONG (the reverse):
    1 and 2 are bearish (C<O)
    2's body is smaller than 1's body
    2's body sits higher (body low of 2 > body low of 1)
    On candle 3, buy at the open (honest) or nearer the low (research)

If other books still look bullish / bearish, still take the fade.
That is the point: shrinking same-color body = exhaustion, not chase.

Honest fill is candle-3 **open** (known when candle 2 closes). Near-high /
near-low and the exact high/low use candle 3's printed range — look-ahead
rows, labelled. Exit at candle 3 close. Intraday flatten if the session breaks.

Do not add ENABLE_*. Do not rewrite S16. Overlay on S16/S18/S19/S20 is a
backtest skip of chase entries, default off live. Stay DRY_RUN.
"""

from __future__ import annotations

from typing import Any, Callable, Protocol

from backtest_hhhl_candles import Candle, Trade, in_session, make_charge_cfg
from backtest_wick_candles import _tf_result_from_trades
from charges import ChargeConfig
from s18_ohlc_vol_htf import VolBar, _close_leg

LAB_NAME = "BODY_SHRINK_FADE"

FORMULA = (
    "Two finished candles, same color. Candle 2 body smaller than candle 1 "
    "and sitting lower (greens) / higher (reds). Fade on candle 3: SHORT "
    "near the high after two weakening greens, LONG near the low after two "
    "weakening reds. Honest fill = candle 3 open. Exit candle 3 close. "
    "Other books still bullish/bearish is fine — do not chase the shrink. "
    "Research only. Not S16. Not live. No ENABLE."
)

NEAR_FRAC = 0.85
MIN_BODY = 1.0


class _OHLC(Protocol):
    time: str
    open: float
    high: float
    low: float
    close: float


def body_len(bar: _OHLC) -> float:
    return abs(float(bar.close) - float(bar.open))


def body_high(bar: _OHLC) -> float:
    return max(float(bar.open), float(bar.close))


def body_low(bar: _OHLC) -> float:
    return min(float(bar.open), float(bar.close))


def is_green(bar: _OHLC) -> bool:
    return float(bar.close) > float(bar.open)


def is_red(bar: _OHLC) -> bool:
    return float(bar.close) < float(bar.open)


def setup_side(prev: _OHLC, cur: _OHLC, *, min_body: float = MIN_BODY) -> str | None:
    """None / 'short' / 'long' after two finished candles. Not an order yet."""
    b1 = body_len(prev)
    b2 = body_len(cur)
    need = max(float(min_body), 0.0)
    if b1 < need or b2 < need:
        return None
    if b2 >= b1:
        return None
    if is_green(prev) and is_green(cur):
        if body_high(cur) < body_high(prev):
            return "short"
        return None
    if is_red(prev) and is_red(cur):
        if body_low(cur) > body_low(prev):
            return "long"
        return None
    return None


def skip_chase(prev: _OHLC, cur: _OHLC, want: str | None) -> bool:
    """True = do not chase: skip LONG after shrinking greens, SHORT after shrinking reds."""
    fade = setup_side(prev, cur)
    if fade == "short" and want == "long":
        return True
    if fade == "long" and want == "short":
        return True
    return False


def wrap_skip(
    decide: Callable[..., tuple[str | None, str]],
) -> Callable[..., tuple[str | None, str]]:
    """Backtest overlay: keep the book's formula, skip exhaustion chases."""

    def inner(prev: Any, cur: Any, *args: Any, **kwargs: Any) -> tuple[str | None, str]:
        side, why = decide(prev, cur, *args, **kwargs)
        if skip_chase(prev, cur, side):
            return None, f"body_shrink skip ({why})"
        return side, why

    return inner


def _as_vol(bar: _OHLC) -> VolBar:
    vol = float(getattr(bar, "volume", 0.0) or 0.0)
    return VolBar(bar.time, bar.open, bar.high, bar.low, bar.close, vol)


def third_fill_px(nxt: _OHLC, side: str, how: str, *, frac: float = NEAR_FRAC) -> float | None:
    """Return fill on candle 3, or None if that price never traded from the open."""
    o = float(nxt.open)
    h = float(nxt.high)
    lo = float(nxt.low)
    f = min(0.99, max(0.5, float(frac)))
    if how == "open":
        return o
    if side == "short":
        if how == "extreme":
            return h
        if h <= o:
            return None
        return o + f * (h - o)
    if how == "extreme":
        return lo
    if lo >= o:
        return None
    return o - f * (o - lo)


def _same_session(a: _OHLC, b: _OHLC) -> bool:
    return str(a.time)[:10] == str(b.time)[:10]


def simulate_body_shrink_fade(
    bars: list[Any],
    *,
    tf: str = "1h:body_shrink",
    lots: float = 100.0,
    fees: bool = True,
    session_filter: bool = True,
    market_open: str = "09:00",
    market_close: str = "23:30",
    charge_cfg: ChargeConfig | None = None,
    fill: str = "open",
    min_body: float = MIN_BODY,
    near_frac: float = NEAR_FRAC,
) -> Any:
    """One round-trip per setup: enter on candle 3, exit candle 3 close."""
    cfg = charge_cfg or make_charge_cfg(fees=fees, lots=lots)
    trades: list[Trade] = []
    n_setup = 0
    n_fill = 0
    for i in range(1, len(bars) - 1):
        prev, cur, nxt = bars[i - 1], bars[i], bars[i + 1]
        if session_filter:
            if not in_session(_as_vol(cur), open_hhmm=market_open, close_hhmm=market_close):
                continue
            if not in_session(_as_vol(nxt), open_hhmm=market_open, close_hhmm=market_close):
                continue
            if not _same_session(prev, cur) or not _same_session(cur, nxt):
                continue
        side = setup_side(prev, cur, min_body=min_body)
        if side is None:
            continue
        n_setup += 1
        px = third_fill_px(nxt, side, fill, frac=near_frac)
        if px is None:
            continue
        n_fill += 1
        book_side = "LONG" if side == "long" else "SHORT"
        trades.append(
            _close_leg(
                tf=tf,
                side=book_side,
                entry_time=nxt.time,
                entry_px=float(px),
                exit_c=_as_vol(nxt),
                cfg=cfg,
            )
        )
    result = _tf_result_from_trades(tf, len(bars), trades)
    result.n_setup = n_setup  # type: ignore[attr-defined]
    result.n_fill = n_fill  # type: ignore[attr-defined]
    result.fill = fill  # type: ignore[attr-defined]
    return result


def simulate_close_flip(
    bars: list[Any],
    want_at: Callable[[int, Any, Any], str | None],
    *,
    tf: str,
    lots: float = 100.0,
    fees: bool = True,
    session_filter: bool = True,
    market_open: str = "09:00",
    market_close: str = "23:30",
    charge_cfg: ChargeConfig | None = None,
) -> Any:
    """FLIP at signal-bar close. Overlay helper for S16/S18/S19 skip-chase."""
    cfg = charge_cfg or make_charge_cfg(fees=fees, lots=lots)
    trades: list[Trade] = []
    side: str | None = None
    entry_px = 0.0
    entry_time = ""

    def close_trade(exit_c: Any) -> None:
        nonlocal side, entry_px, entry_time
        assert side is not None
        trades.append(
            _close_leg(
                tf=tf,
                side=side,
                entry_time=entry_time,
                entry_px=entry_px,
                exit_c=_as_vol(exit_c),
                cfg=cfg,
            )
        )
        side = None

    for i in range(1, len(bars)):
        prev, cur = bars[i - 1], bars[i]
        sess_ok = (not session_filter) or in_session(
            _as_vol(cur), open_hhmm=market_open, close_hhmm=market_close
        )
        if side is not None and session_filter and not sess_ok:
            close_trade(cur)
            continue
        if session_filter and not sess_ok:
            continue
        if session_filter:
            prev_ok = in_session(
                _as_vol(prev), open_hhmm=market_open, close_hhmm=market_close
            )
            if (not prev_ok) or not _same_session(prev, cur):
                continue
        want = want_at(i, prev, cur)
        if side == "LONG":
            if want == "short":
                close_trade(cur)
                side = "SHORT"
                entry_px = float(cur.close)
                entry_time = cur.time
            continue
        if side == "SHORT":
            if want == "long":
                close_trade(cur)
                side = "LONG"
                entry_px = float(cur.close)
                entry_time = cur.time
            continue
        if want == "long":
            side = "LONG"
            entry_px = float(cur.close)
            entry_time = cur.time
        elif want == "short":
            side = "SHORT"
            entry_px = float(cur.close)
            entry_time = cur.time
    if side is not None and bars:
        close_trade(bars[-1])
    return _tf_result_from_trades(tf, len(bars), trades)


def after_charges_inr(result: Any) -> float:
    return float(result.gross_pnl_inr) - float(result.fees_inr)


def to_candles(bars: list[Any]) -> list[Candle]:
    return [Candle(b.time, b.open, b.high, b.low, b.close) for b in bars]


def synthetic_bars() -> list[VolBar]:
    """Two setups: shrinking greens then shrinking reds."""
    return [
        VolBar("2026-08-17 10:00:00", 100.0, 112.0, 99.0, 110.0),
        VolBar("2026-08-17 11:00:00", 104.0, 109.0, 103.0, 108.0),
        VolBar("2026-08-17 12:00:00", 108.0, 116.0, 100.0, 102.0),
        VolBar("2026-08-17 13:00:00", 110.0, 111.0, 98.0, 100.0),
        VolBar("2026-08-17 14:00:00", 106.0, 107.0, 101.0, 102.0),
        VolBar("2026-08-17 15:00:00", 102.0, 108.0, 94.0, 107.0),
    ]
