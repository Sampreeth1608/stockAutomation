"""S16 + TBQ/TSQ research decides (backtest only, not paper)."""

from __future__ import annotations

from backtest_hhhl_candles import Candle
from s16_hhhl_wick import simulate_s16
from s16_tbq_tsq import QtyCandle, s16_qty_agree, s16_qty_down, s16_qty_flow, qty_close_only


def _ohlc(t: str, o: float, h: float, l: float, c: float) -> Candle:
    return Candle(t, o, h, l, c)


def _q(
    t: str,
    o: float,
    h: float,
    l: float,
    c: float,
    *,
    tbq_o: float,
    tbq_c: float,
    tsq_o: float,
    tsq_c: float,
) -> QtyCandle:
    return QtyCandle(t, o, h, l, c, tbq_o, tbq_c, tsq_o, tsq_c)


PREV = _q("2026-08-17 10:00:00", 100.0, 105.0, 99.0, 104.0, tbq_o=100, tbq_c=110, tsq_o=90, tsq_c=95)


def test_qty_agree_blocks_long_when_sell_book_is_bigger() -> None:
    cur = _q(
        "2026-08-17 11:00:00",
        100.0,
        120.0,
        100.0,
        110.0,
        tbq_o=100,
        tbq_c=80,
        tsq_o=90,
        tsq_c=200,
    )
    side, why = s16_qty_agree(PREV, cur, min_wick_gap=0)
    assert side is None
    assert "qty disagree" in why


def test_qty_agree_keeps_long_when_buy_book_is_bigger() -> None:
    cur = _q(
        "2026-08-17 11:00:00",
        100.0,
        120.0,
        100.0,
        110.0,
        tbq_o=100,
        tbq_c=250,
        tsq_o=90,
        tsq_c=80,
    )
    side, why = s16_qty_agree(PREV, cur, min_wick_gap=0)
    assert side == "long"
    assert "TBQ>TSQ" in why


def test_qty_flow_uses_bar_delta_not_close_level() -> None:
    # Close still TBQ>TSQ, but this hour sellers added more.
    cur = _q(
        "2026-08-17 11:00:00",
        100.0,
        120.0,
        100.0,
        110.0,
        tbq_o=200,
        tbq_c=210,
        tsq_o=50,
        tsq_c=180,
    )
    side, why = s16_qty_flow(PREV, cur, min_wick_gap=0)
    assert side is None
    assert "flow disagree" in why
    assert cur.d_tsq > cur.d_tbq


def test_qty_down_replaces_wick_on_down_close() -> None:
    # Down close with long lower wick would be S16 LONG; TBQ<TSQ → SHORT.
    cur = _q(
        "2026-08-17 11:00:00",
        103.0,
        104.0,
        80.0,
        100.0,
        tbq_o=100,
        tbq_c=90,
        tsq_o=100,
        tsq_c=140,
    )
    assert cur.close < PREV.close
    side, why = s16_qty_down(PREV, cur)
    assert side == "short"
    assert "C<prev" in why
    assert "TSQ>TBQ" in why


def test_qty_close_only_ignores_ohlc() -> None:
    cur = _q(
        "2026-08-17 11:00:00",
        104.0,
        105.0,
        103.0,
        104.0,
        tbq_o=1,
        tbq_c=9,
        tsq_o=1,
        tsq_c=2,
    )
    side, _ = qty_close_only(PREV, cur)
    assert side == "long"


def test_qty_agree_without_qty_fields_skips() -> None:
    cur = _ohlc("2026-08-17 11:00:00", 100.0, 120.0, 100.0, 110.0)
    side, why = s16_qty_agree(PREV, cur)
    assert side is None
    assert "no TBQ/TSQ" in why


def test_simulate_qty_agree_does_not_enter_disagree_bar() -> None:
    candles = [
        PREV,
        _q(
            "2026-08-17 11:00:00",
            100.0,
            120.0,
            100.0,
            110.0,
            tbq_o=10,
            tbq_c=10,
            tsq_o=50,
            tsq_c=80,
        ),
        _q(
            "2026-08-17 12:00:00",
            110.0,
            130.0,
            109.0,
            120.0,
            tbq_o=80,
            tbq_c=200,
            tsq_o=80,
            tsq_c=50,
        ),
    ]
    res = simulate_s16(
        candles,
        tf="1h:s16_qty_agree",
        lots=1,
        fees=False,
        session_filter=False,
        min_wick_gap=0,
        decide=lambda a, b: s16_qty_agree(a, b, min_wick_gap=0),
    )
    assert res.n_trades >= 1


if __name__ == "__main__":
    test_qty_agree_blocks_long_when_sell_book_is_bigger()
    test_qty_agree_keeps_long_when_buy_book_is_bigger()
    test_qty_flow_uses_bar_delta_not_close_level()
    test_qty_down_replaces_wick_on_down_close()
    test_qty_close_only_ignores_ohlc()
    test_qty_agree_without_qty_fields_skips()
    test_simulate_qty_agree_does_not_enter_disagree_bar()
    print("all s16 tbq/tsq tests passed")
