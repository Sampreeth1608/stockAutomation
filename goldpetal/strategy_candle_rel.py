"""Candle-relation books as separate strategies. All disabled.

Pick later which name to paper. Do not add to the desk or run_strategy until then.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from candle_rel_books import CANDLE_REL_BOOKS, CandleRelBook
from candle_relations import pair_features
from strategy import Position, SignalResult


def _sigmoid(z: float) -> float:
    if z >= 30:
        return 1.0
    if z <= -30:
        return 0.0
    return float(1.0 / (1.0 + np.exp(-z)))


def candle_rel_side(
    prev: Any,
    cur: Any,
    *,
    higher: Any = None,
    groups: tuple[str, ...],
    model: dict[str, Any],
) -> str:
    """BUY / SHORT / HOLD from a fitted family mix. HOLD if the model is missing."""
    cols = list(model.get("columns") or [])
    coef = list(model.get("coef") or [])
    mean = list(model.get("scaler_mean") or [])
    scale = list(model.get("scaler_scale") or [])
    if not cols or len(cols) != len(coef) or len(cols) != len(mean):
        return "HOLD"
    feat = pair_features(prev, cur, higher=higher)
    x = np.array([float(feat.get(name) or 0.0) for name in cols], dtype=float)
    mu = np.array(mean, dtype=float)
    sc = np.array(scale, dtype=float)
    sc = np.where(np.abs(sc) < 1e-12, 1.0, sc)
    w = np.array(coef, dtype=float)
    z = float(((x - mu) / sc) @ w + float(model.get("intercept") or 0.0))
    p = _sigmoid(z)
    long_p = float(model.get("long_p") or 0.55)
    short_p = float(model.get("short_p") or 0.45)
    if p >= long_p:
        return "BUY"
    if p <= short_p:
        return "SHORT"
    return "HOLD"


class CandleRelStrategy:
    """One family-mix book. Stays off until you pick it."""

    def __init__(self, book: CandleRelBook) -> None:
        self.book = book
        self.name = book.name
        self.enabled = False
        self.paper = False
        self.position: Position = "flat"
        self.entry_price: float | None = None
        self.last_skip = "off — decide next"
        self.model: dict[str, Any] | None = None

    @property
    def status_line(self) -> str:
        return (
            f"{self.name} RESEARCH_OFF paper=false "
            f"groups={'+'.join(self.book.groups)} skip={self.last_skip}"
        )

    def on_tick(self, *args: Any, **kwargs: Any) -> None:
        return None

    def maybe_signal(self, *args: Any, **kwargs: Any) -> None:
        return None

    def on_bar(self, *args: Any, **kwargs: Any) -> SignalResult:
        return SignalResult(
            action="HOLD",
            position_after="flat",
            price_delta=None,
            net=0.0,
            net_delta=None,
            prev_net_delta=None,
            reason="off — decide next",
        )


def all_candle_rel_strategies() -> list[CandleRelStrategy]:
    return [CandleRelStrategy(book) for book in CANDLE_REL_BOOKS]
