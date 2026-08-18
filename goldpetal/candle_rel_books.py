"""31 candle-relation strategies — all off. Decide later which to paper.

Five families (ohlc, wick, prev, vol, htf). Every non-empty mix is its own
named book. Not in ALL_STRATEGY_NAMES. Not on the desk. Not live. Not S13/S16.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from itertools import combinations
from typing import Any

FAMILIES: tuple[str, ...] = ("ohlc", "wick", "prev", "vol", "htf")


@dataclass(frozen=True)
class CandleRelBook:
    name: str
    groups: tuple[str, ...]
    tf: str = "1h"
    higher: str = "1d"
    paper: bool = False
    live: bool = False
    enabled: bool = False

    @property
    def formula(self) -> str:
        bits = "+".join(self.groups)
        htf = f" + completed {self.higher}" if "htf" in self.groups else ""
        return (
            f"{self.tf} close vs prev; families {bits}{htf}; "
            "P(next close up); FLIP at bar close; flatten EOD. Off until picked."
        )

    def as_row(self) -> dict[str, Any]:
        row = asdict(self)
        row["groups"] = list(self.groups)
        row["formula"] = self.formula
        return row


def book_name(groups: tuple[str, ...]) -> str:
    return "CR_" + "_".join(g.upper() for g in groups)


def build_books() -> tuple[CandleRelBook, ...]:
    out: list[CandleRelBook] = []
    for r in range(1, len(FAMILIES) + 1):
        for g in combinations(FAMILIES, r):
            out.append(CandleRelBook(name=book_name(g), groups=g))
    return tuple(out)


CANDLE_REL_BOOKS: tuple[CandleRelBook, ...] = build_books()
CANDLE_REL_NAMES: tuple[str, ...] = tuple(b.name for b in CANDLE_REL_BOOKS)


def book_by_name(name: str) -> CandleRelBook | None:
    want = str(name or "").strip()
    for book in CANDLE_REL_BOOKS:
        if book.name == want:
            return book
    return None
