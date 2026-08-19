"""S16 + hour TBQ/TSQ strength. Research overlay. Not a paper rewrite. Not live.

S16 still picks the side on *strong* hours (C>prev → HH/LL, C<prev → wick).
Same finished hour is graded with TBQ−TSQ:

  price up   (C>O) and net+ (TBQ>TSQ) → strong up   → S16 may LONG
  price up   (C>O) and net- (TBQ<TSQ) → weak up     → SHORT the fake rally
  price down (C<O) and net-          → strong down → S16 may SHORT
  price down (C<O) and net+          → weak down   → BUY the fake dump

``weak=drop``: flatten on weak, do not reverse.
``weak=fade``: weak up shorts, weak down buys, FLIP.

Fill at bar close. Rank after Angel charges, tax excluded. Stay DRY_RUN.
Do not change ENABLE_S16 / strategy_s16.py.
"""

from __future__ import annotations

from typing import Any, Literal

from backtest_hhhl_candles import Candle, Trade, in_session, make_charge_cfg
from backtest_wick_candles import _tf_result_from_trades
from charges import ChargeConfig
from s16_hhhl_wick import _close_leg, s16_bar_decision
from s18_ohlc_vol_htf import VolBar

LAB_NAME = "S16_TBQ_NET"

FORMULA = (
    "S16 1h (C>prev HH/LL, C<prev wick, FLIP) plus same-hour TBQ−TSQ. "
    "C>O and TBQ>TSQ = strong up keep/allow long; C<O and TBQ<TSQ = strong "
    "down keep/allow short. Weak hours: drop = flatten no reverse; fade = "
    "weak up SHORT the fake rally, weak down LONG the fake dump (FLIP). "
    "Research only. Does not rewrite paper S16."
)

STRONG_UP = "strong_up"
WEAK_UP = "weak_up"
STRONG_DOWN = "strong_down"
WEAK_DOWN = "weak_down"
MIXED = "mixed"

WeakMode = Literal["drop", "fade"]


def _bar_net(bar: Any) -> float:
    return float(getattr(bar, "tbq", 0.0) or 0.0) - float(
        getattr(bar, "tsq", 0.0) or 0.0
    )


def flow_kind(bar: Any) -> str:
    """Strong/weak up/down from this hour's body and TBQ−TSQ net."""
    body = float(bar.close) - float(bar.open)
    net = _bar_net(bar)
    if body > 0 and net > 0:
        return STRONG_UP
    if body > 0 and net < 0:
        return WEAK_UP
    if body < 0 and net < 0:
        return STRONG_DOWN
    if body < 0 and net > 0:
        return WEAK_DOWN
    return MIXED


def _as_candle(bar: Any) -> Candle:
    if isinstance(bar, Candle):
        return bar
    return Candle(
        str(bar.time)[:19],
        float(bar.open),
        float(bar.high),
        float(bar.low),
        float(bar.close),
    )


def gate_s16_want(want: str | None, kind: str) -> str | None:
    """S16 side is allowed only on a strong hour of the same colour."""
    if want == "long" and kind == STRONG_UP:
        return "long"
    if want == "short" and kind == STRONG_DOWN:
        return "short"
    return None


def overlay_want(
    s16_want: str | None,
    kind: str,
    *,
    weak: WeakMode = "drop",
) -> str | None:
    """Map S16 + hour kind to a side. Fade uses weak hours as counter-trend."""
    if weak == "fade":
        if kind == WEAK_UP:
            return "short"
        if kind == WEAK_DOWN:
            return "long"
        if kind in (STRONG_UP, STRONG_DOWN):
            return gate_s16_want(s16_want, kind)
        return s16_want
    return gate_s16_want(s16_want, kind)


def simulate_s16_tbq_net(
    bars: list[Any],
    *,
    tf: str = "1h:s16_tbq",
    lots: float = 100.0,
    fees: bool = True,
    session_filter: bool = True,
    market_open: str = "09:00",
    market_close: str = "23:30",
    charge_cfg: ChargeConfig | None = None,
    min_wick_gap: float = 0.0,
    weak: WeakMode = "drop",
) -> Any:
    """S16 decide, then strong/weak hour overlay. Fill at close. Flatten EOD."""
    cfg = charge_cfg or make_charge_cfg(fees=fees, lots=lots)
    candles = [_as_candle(b) for b in bars]
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

    for i in range(1, len(bars)):
        prev_b, cur_b = bars[i - 1], bars[i]
        prev, cur = candles[i - 1], candles[i]
        sess_ok = (not session_filter) or in_session(
            cur, open_hhmm=market_open, close_hhmm=market_close
        )
        if side is not None and session_filter and not sess_ok:
            close_trade(cur)
            continue
        if session_filter and not sess_ok:
            continue
        if session_filter:
            prev_sess = in_session(
                prev, open_hhmm=market_open, close_hhmm=market_close
            )
            if (not prev_sess) or prev.time[:10] != cur.time[:10]:
                continue

        kind = flow_kind(cur_b)
        if weak == "drop":
            if side == "LONG" and kind == WEAK_UP:
                close_trade(cur)
            elif side == "SHORT" and kind == WEAK_DOWN:
                close_trade(cur)

        s16_want, _why = s16_bar_decision(prev, cur, min_wick_gap=min_wick_gap)
        want = overlay_want(s16_want, kind, weak=weak)

        if side == "LONG":
            if want == "short":
                close_trade(cur)
                side = "SHORT"
                entry_px = cur.close
                entry_time = cur.time
            continue
        if side == "SHORT":
            if want == "long":
                close_trade(cur)
                side = "LONG"
                entry_px = cur.close
                entry_time = cur.time
            continue
        if want == "long":
            side = "LONG"
            entry_px = cur.close
            entry_time = cur.time
        elif want == "short":
            side = "SHORT"
            entry_px = cur.close
            entry_time = cur.time

    if side is not None and candles:
        close_trade(candles[-1])
    return _tf_result_from_trades(tf, len(candles), trades)


def volbar(
    t: str,
    o: float,
    h: float,
    l: float,
    c: float,
    *,
    tbq: float = 0.0,
    tsq: float = 0.0,
) -> VolBar:
    return VolBar(t, o, h, l, c, 0.0, 0.0, tbq, tsq)
