"""Tests for full-day bullish/bearish delivery bias."""

from __future__ import annotations

from day_bias import analyze_day


def _ticks(trend: float, n: int = 80) -> list[dict]:
    rows = []
    px = 14000.0
    for i in range(n):
        px += trend + ((i % 5) - 2) * 0.02
        rows.append(
            {
                "time": f"2026-08-04 10:{i // 60:02d}:{i % 60:02d}",
                "ltp": px,
                "open": 14000,
                "high": max(14010, px + 3),
                "low": min(13990, px - 3),
                "close": 14000,
                "volume": 1000 + i * 10,
                "last_traded_quantity": 1,
                "average_traded_price": (14000 + px) / 2,
                "total_buy_quantity": 6000 if trend > 0 else 3000,
                "total_sell_quantity": 3000 if trend > 0 else 6000,
                "buy1_price": px - 1,
                "buy1_qty": 20 if trend > 0 else 5,
                "buy2_price": px - 2,
                "buy2_qty": 10,
                "buy3_price": px - 3,
                "buy3_qty": 8,
                "buy4_price": px - 4,
                "buy4_qty": 6,
                "buy5_price": px - 5,
                "buy5_qty": 4,
                "sell1_price": px + 1,
                "sell1_qty": 5 if trend > 0 else 20,
                "sell2_price": px + 2,
                "sell2_qty": 8,
                "sell3_price": px + 3,
                "sell3_qty": 6,
                "sell4_price": px + 4,
                "sell4_qty": 4,
                "sell5_price": px + 5,
                "sell5_qty": 2,
                "open_interest": 10000 + i * (2 if trend > 0 else -1),
            }
        )
    return rows


def test_bullish_day() -> None:
    r = analyze_day(_ticks(1.2), bullish_prob=0.55, bearish_prob=0.45)
    assert r is not None
    assert r.bias == "BULLISH"
    assert r.prob_bullish >= 0.55


def test_bearish_day() -> None:
    r = analyze_day(_ticks(-1.2), bullish_prob=0.55, bearish_prob=0.45)
    assert r is not None
    assert r.bias == "BEARISH"
    assert r.prob_bullish <= 0.45


if __name__ == "__main__":
    test_bullish_day()
    test_bearish_day()
    print("ok")
