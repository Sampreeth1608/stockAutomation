"""S17 research: classify a finished bar by close / high / body.

Not paper. Not live. No LONG/SHORT yet — counts only, then you add sides later.

c = this finished candle, p = previous finished candle.

The six gates you listed (strict > / <):

  1. C>pC  H>pH  C>O     up_hh_green
  2. C>pC  H>pH  C<O     up_hh_red
  3. C<pC  H<pH  C>O     dn_lh_green
  4. C<pC  H<pH  C<O     dn_lh_red
  5. C<pC  H>pH  C>O     dn_hh_green
  6. C>pC  H<pH  C<O     up_lh_red

Same 2×2×2 grid, two cells not listed (add later if you want them):

  7. C>pC  H<pH  C>O     up_lh_green
  8. C<pC  H>pH  C<O     dn_hh_red

Leftover under strict > / <: C=pC, H=pH, C=O.

Inside every bucket we only *record* (not trade):
  lows vs previous low (HL / LL / equal)
  wick (upper vs lower)
  S12 HH+green / LL+red
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from typing import Any

from backtest_hhhl_candles import Candle, in_session
from wick_candles import wick_measure

LISTED_BUCKETS: tuple[str, ...] = (
    "up_hh_green",
    "up_hh_red",
    "dn_lh_green",
    "dn_lh_red",
    "dn_hh_green",
    "up_lh_red",
)

MISSING_BUCKETS: tuple[str, ...] = (
    "up_lh_green",
    "dn_hh_red",
)

USER_COLUMNS: tuple[tuple[str, str, str, str], ...] = (
    ("1", "C>pC", "H>pH", "green"),
    ("2", "C>pC", "H>pH", "red"),
    ("3", "C<pC", "H<pH", "green"),
    ("4", "C<pC", "H<pH", "red"),
    ("5", "C<pC", "H>pH", "green"),
    ("6", "C>pC", "H<pH", "red"),
)

FORMULA = (
    "Wait for the candle to finish. Classify vs previous close, previous high, "
    "and body (C vs O). Six listed gates; two more cells in the same grid; "
    "equals are leftover. Record lows + wicks. No LONG/SHORT yet."
)


def _cmp_tag(cur: float, prev: float, gt: str, lt: str) -> str:
    if cur > prev:
        return gt
    if cur < prev:
        return lt
    return "eq"


def hhhl_side(prev: Candle, cur: Candle) -> str | None:
    want_long = cur.high > prev.high and cur.close > cur.open
    want_short = cur.low < prev.low and cur.close < cur.open
    if want_long and not want_short:
        return "long"
    if want_short and not want_long:
        return "short"
    return None


def wick_raw_side(cur: Candle) -> str | None:
    return wick_measure(cur.open, cur.high, cur.low, cur.close).dominant


def classify_bar(prev: Candle, cur: Candle) -> dict[str, Any]:
    """One finished candle vs previous → bucket + structure/wick notes."""
    close_vs = _cmp_tag(cur.close, prev.close, "up", "dn")
    high_vs = _cmp_tag(cur.high, prev.high, "hh", "lh")
    low_vs = _cmp_tag(cur.low, prev.low, "hl", "ll")
    if cur.close > cur.open:
        body = "green"
    elif cur.close < cur.open:
        body = "red"
    else:
        body = "doji"

    bucket = f"{close_vs}_{high_vs}_{body}"
    if bucket in LISTED_BUCKETS:
        kind = "listed"
    elif bucket in MISSING_BUCKETS:
        kind = "missing"
    else:
        kind = "leftover"

    m = wick_measure(cur.open, cur.high, cur.low, cur.close)
    hh = hhhl_side(prev, cur)
    wk = wick_raw_side(cur)
    if hh and wk and hh == wk:
        agree = "agree"
    elif hh and wk and hh != wk:
        agree = "fight"
    else:
        agree = "none"

    return {
        "time": cur.time,
        "open": cur.open,
        "high": cur.high,
        "low": cur.low,
        "close": cur.close,
        "prev_close": prev.close,
        "prev_high": prev.high,
        "prev_low": prev.low,
        "close_vs": close_vs,
        "high_vs": high_vs,
        "low_vs": low_vs,
        "body": body,
        "bucket": bucket,
        "kind": kind,
        "upper": round(m.upper, 2),
        "lower": round(m.lower, 2),
        "wick_gap": round(abs(m.upper - m.lower), 2),
        "wick": wk or "none",
        "hhhl": hh or "none",
        "agree": agree,
    }


def walk_candles(
    candles: list[Candle],
    *,
    session_filter: bool = False,
    market_open: str = "09:00",
    market_close: str = "23:30",
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i in range(1, len(candles)):
        prev, cur = candles[i - 1], candles[i]
        if session_filter and not in_session(
            cur, open_hhmm=market_open, close_hhmm=market_close
        ):
            continue
        out.append(classify_bar(prev, cur))
    return out


@dataclass
class BucketCount:
    n: int = 0
    n_hl: int = 0
    n_ll: int = 0
    n_low_eq: int = 0
    n_wick_long: int = 0
    n_wick_short: int = 0
    n_wick_none: int = 0
    n_hhhl_long: int = 0
    n_hhhl_short: int = 0
    n_hhhl_none: int = 0
    n_agree: int = 0
    n_fight: int = 0
    n_or: int = 0
    n_gap_ge3: int = 0
    n_gap_ge5: int = 0
    n_gap_ge10: int = 0
    wick_gap_sum: float = 0.0


def tally_rows(rows: list[dict[str, Any]]) -> dict[str, BucketCount]:
    by: dict[str, BucketCount] = defaultdict(BucketCount)
    for row in rows:
        b = by[str(row["bucket"])]
        b.n += 1
        if row["low_vs"] == "hl":
            b.n_hl += 1
        elif row["low_vs"] == "ll":
            b.n_ll += 1
        else:
            b.n_low_eq += 1
        if row["wick"] == "long":
            b.n_wick_long += 1
        elif row["wick"] == "short":
            b.n_wick_short += 1
        else:
            b.n_wick_none += 1
        if row["hhhl"] == "long":
            b.n_hhhl_long += 1
        elif row["hhhl"] == "short":
            b.n_hhhl_short += 1
        else:
            b.n_hhhl_none += 1
        if row["agree"] == "agree":
            b.n_agree += 1
        elif row["agree"] == "fight":
            b.n_fight += 1
        if row["hhhl"] != "none" or row["wick"] != "none":
            b.n_or += 1
        gap = float(row["wick_gap"])
        b.wick_gap_sum += gap
        if gap >= 3:
            b.n_gap_ge3 += 1
        if gap >= 5:
            b.n_gap_ge5 += 1
        if gap >= 10:
            b.n_gap_ge10 += 1
    return dict(by)


def counts_as_dict(counts: dict[str, BucketCount]) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for name, b in counts.items():
        row = asdict(b)
        row["wick_gap_avg"] = (b.wick_gap_sum / b.n) if b.n else 0.0
        out[name] = row
    return out


def bucket_kind(name: str) -> str:
    if name in LISTED_BUCKETS:
        return "listed"
    if name in MISSING_BUCKETS:
        return "missing"
    return "leftover"


def ordered_bucket_names(counts: dict[str, BucketCount]) -> list[str]:
    names = list(LISTED_BUCKETS) + list(MISSING_BUCKETS)
    extra = sorted(k for k in counts if k not in names)
    return names + extra
