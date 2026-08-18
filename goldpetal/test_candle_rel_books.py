"""31 CR_* books exist as separate strategies and stay off the paper desk."""

from __future__ import annotations

from pathlib import Path

from backtest_hhhl_candles import Candle
from candle_rel_books import CANDLE_REL_BOOKS, CANDLE_REL_NAMES, book_by_name, book_name
from control_state import ALL_STRATEGY_NAMES, SLIM_PAPER_STRATEGIES
from strategy_candle_rel import (
    CandleRelStrategy,
    all_candle_rel_strategies,
    candle_rel_side,
)


def test_thirty_one_named_books_all_off() -> None:
    assert len(CANDLE_REL_BOOKS) == 31
    assert len(set(CANDLE_REL_NAMES)) == 31
    assert book_name(("ohlc",)) == "CR_OHLC"
    assert book_name(("ohlc", "vol")) == "CR_OHLC_VOL"
    assert book_name(("ohlc", "wick", "prev", "vol", "htf")) == (
        "CR_OHLC_WICK_PREV_VOL_HTF"
    )
    assert book_by_name("CR_VOL") is not None
    for book in CANDLE_REL_BOOKS:
        assert book.paper is False
        assert book.live is False
        assert book.enabled is False
        assert book.name not in ALL_STRATEGY_NAMES
        assert book.name not in SLIM_PAPER_STRATEGIES


def test_not_wired_to_paper_or_station() -> None:
    root = Path(__file__).resolve().parent
    station = (root / "station.html").read_text(encoding="utf-8")
    runner = (root / "run_strategy.py").read_text(encoding="utf-8")
    assert "CR_OHLC" not in station
    assert "strategy_candle_rel" not in runner
    assert "ENABLE_CR" not in runner


def test_strategy_objects_stay_disabled() -> None:
    books = all_candle_rel_strategies()
    assert len(books) == 31
    for s in books:
        assert isinstance(s, CandleRelStrategy)
        assert s.enabled is False
        assert s.paper is False
        assert s.maybe_signal() is None
        assert s.on_bar().action == "HOLD"


def test_side_uses_only_the_fitted_model() -> None:
    prev = Candle("2026-08-17 10:00:00", 100.0, 105.0, 99.0, 104.0)
    cur = Candle("2026-08-17 11:00:00", 104.0, 120.0, 100.0, 110.0)
    assert (
        candle_rel_side(prev, cur, groups=("ohlc",), model={}) == "HOLD"
    )
    model = {
        "columns": ["o_c"],
        "scaler_mean": [0.0],
        "scaler_scale": [1.0],
        "coef": [10.0],
        "intercept": 0.0,
        "long_p": 0.55,
        "short_p": 0.45,
    }
    assert candle_rel_side(prev, cur, groups=("ohlc",), model=model) == "BUY"


if __name__ == "__main__":
    test_thirty_one_named_books_all_off()
    test_not_wired_to_paper_or_station()
    test_strategy_objects_stay_disabled()
    test_side_uses_only_the_fitted_model()
    print("ALL test_candle_rel_books OK")
