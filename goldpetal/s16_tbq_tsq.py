"""Research: S16 1h formula plus total_buy_quantity / total_sell_quantity.

Paper S16 is unchanged (OHLC only). These decides are for ticks.db backtests.

On each finished bar, ticks already store Angel ``total_buy_quantity`` (TBQ)
and ``total_sell_quantity`` (TSQ) at open and close of the bar.

  qty_close  TBQ>TSQ at bar close → buy-side book; TSQ>TBQ → sell-side
  qty_flow   ΔTBQ vs ΔTSQ during the bar (who added more size this hour)

Variants (FLIP, fill at close, same session flatten as S16):

  s16           current paper: C>prev HH/LL, C<prev wick, C=prev skip
  s16_qty_agree S16 side only if qty_close agrees (long needs TBQ>TSQ)
  s16_qty_flow  S16 side only if qty_flow agrees (long needs ΔTBQ>ΔTSQ)
  s16_qty_down  C>prev still HH/LL; C<prev uses qty_close instead of wick
  qty_close     ignore OHLC S16; TBQ vs TSQ at close only
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from backtest_hhhl_candles import Candle
from mtf_bars import RichBar
from s16_hhhl_wick import s16_bar_decision

DecideFn = Callable[[Candle, Candle], tuple[str | None, str]]


@dataclass
class QtyCandle(Candle):
    tbq_open: float = 0.0
    tbq_close: float = 0.0
    tsq_open: float = 0.0
    tsq_close: float = 0.0

    @property
    def d_tbq(self) -> float:
        return float(self.tbq_close) - float(self.tbq_open)

    @property
    def d_tsq(self) -> float:
        return float(self.tsq_close) - float(self.tsq_open)


def candles_from_rich(bars: list[RichBar]) -> list[QtyCandle]:
    out: list[QtyCandle] = []
    for b in bars:
        out.append(
            QtyCandle(
                time=str(b.time),
                open=float(b.open),
                high=float(b.high),
                low=float(b.low),
                close=float(b.close),
                tbq_open=float(b.tbq_open or 0.0),
                tbq_close=float(b.tbq_close or 0.0),
                tsq_open=float(b.tsq_open or 0.0),
                tsq_close=float(b.tsq_close or 0.0),
            )
        )
    return out


def _qty(cur: Candle) -> QtyCandle | None:
    if isinstance(cur, QtyCandle):
        return cur
    return None


def _close_side(q: QtyCandle) -> tuple[str | None, str]:
    if q.tbq_close > q.tsq_close:
        return "long", f"TBQ>TSQ {q.tbq_close:.0f}>{q.tsq_close:.0f}"
    if q.tsq_close > q.tbq_close:
        return "short", f"TSQ>TBQ {q.tsq_close:.0f}>{q.tbq_close:.0f}"
    return None, f"TBQ=TSQ {q.tbq_close:.0f}"


def _flow_side(q: QtyCandle) -> tuple[str | None, str]:
    if q.d_tbq > q.d_tsq:
        return "long", f"ΔTBQ>ΔTSQ {q.d_tbq:.0f}>{q.d_tsq:.0f}"
    if q.d_tsq > q.d_tbq:
        return "short", f"ΔTSQ>ΔTBQ {q.d_tsq:.0f}>{q.d_tbq:.0f}"
    return None, f"ΔTBQ=ΔTSQ {q.d_tbq:.0f}"


def s16_plain(prev: Candle, cur: Candle, *, min_wick_gap: float = 0.0) -> tuple[str | None, str]:
    return s16_bar_decision(prev, cur, min_wick_gap=min_wick_gap)


def s16_qty_agree(
    prev: Candle, cur: Candle, *, min_wick_gap: float = 0.0
) -> tuple[str | None, str]:
    side, why = s16_bar_decision(prev, cur, min_wick_gap=min_wick_gap)
    q = _qty(cur)
    if side is None:
        return None, why
    if q is None:
        return None, why + " | no TBQ/TSQ"
    close, qwhy = _close_side(q)
    if close == side:
        return side, f"{why} | {qwhy}"
    return None, f"{why} | qty disagree {qwhy}"


def s16_qty_flow(
    prev: Candle, cur: Candle, *, min_wick_gap: float = 0.0
) -> tuple[str | None, str]:
    side, why = s16_bar_decision(prev, cur, min_wick_gap=min_wick_gap)
    q = _qty(cur)
    if side is None:
        return None, why
    if q is None:
        return None, why + " | no TBQ/TSQ"
    flow, qwhy = _flow_side(q)
    if flow == side:
        return side, f"{why} | {qwhy}"
    return None, f"{why} | flow disagree {qwhy}"


def s16_qty_down(
    prev: Candle, cur: Candle, *, min_wick_gap: float = 0.0
) -> tuple[str | None, str]:
    """Up-close still HH/LL. Down-close uses TBQ vs TSQ instead of wick."""
    del min_wick_gap
    if cur.close > prev.close:
        return s16_bar_decision(prev, cur, min_wick_gap=0.0)
    if cur.close < prev.close:
        q = _qty(cur)
        if q is None:
            return None, "C<prev → no TBQ/TSQ"
        side, qwhy = _close_side(q)
        if side is None:
            return None, f"C<prev → {qwhy}"
        return side, f"C<prev → {qwhy}"
    return None, "C=prev skip"


def qty_close_only(prev: Candle, cur: Candle, *, min_wick_gap: float = 0.0) -> tuple[str | None, str]:
    del prev, min_wick_gap
    q = _qty(cur)
    if q is None:
        return None, "no TBQ/TSQ"
    return _close_side(q)


VARIANTS: dict[str, tuple[str, DecideFn]] = {
    "s16": ("paper S16 (OHLC only)", s16_plain),
    "s16_qty_agree": ("S16 + TBQ/TSQ at close must agree", s16_qty_agree),
    "s16_qty_flow": ("S16 + ΔTBQ vs ΔTSQ in the bar must agree", s16_qty_flow),
    "s16_qty_down": ("C>prev HH/LL; C<prev TBQ vs TSQ (no wick)", s16_qty_down),
    "qty_close": ("TBQ vs TSQ at close only (no S16 OHLC)", qty_close_only),
}


def variant_decide(name: str, *, min_wick_gap: float = 0.0) -> DecideFn:
    if name not in VARIANTS:
        raise ValueError(f"unknown variant {name!r}. have: {', '.join(VARIANTS)}")
    _label, fn = VARIANTS[name]

    def _wrapped(prev: Candle, cur: Candle) -> tuple[str | None, str]:
        return fn(prev, cur, min_wick_gap=min_wick_gap)

    _wrapped.__name__ = name
    return _wrapped
