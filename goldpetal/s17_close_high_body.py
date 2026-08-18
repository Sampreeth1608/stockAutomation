"""S17 research: classify a finished bar by close / high / body.

Not paper. Not live.

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

Leftover under strict > / <: C=pC, H=pH, C=O. Books skip leftover.

Books (FLIP, fill at this bar's close), only on listed+missing:
  hhhl = S12 (HH+green LONG, LL+red SHORT)
  wick = raw wick if |U−L| ≥ min_wick_gap
  and  = both same side
  or   = either side, skip if they fight
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from typing import Any

from backtest_hhhl_candles import Candle, Trade, in_session, make_charge_cfg
from backtest_wick_candles import _tf_result_from_trades
from charges import ChargeConfig, apply_charges_and_tax
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
    "equals are leftover. Record lows + wicks. Books (research): leftover skip; "
    "hhhl / wick / AND / OR-skip-fight. Fill at close. FLIP."
)

BOOK_MODES: tuple[str, ...] = ("hhhl", "wick", "and", "or")


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


def s17_bar_decision(
    prev: Candle,
    cur: Candle,
    *,
    mode: str = "hhhl",
    min_wick_gap: float = 3.0,
) -> tuple[str | None, str]:
    """One finished candle → ('long'|'short'|None, why). Leftover always skip."""
    if mode not in BOOK_MODES:
        raise ValueError(f"unknown book mode {mode!r}. have: {', '.join(BOOK_MODES)}")
    row = classify_bar(prev, cur)
    if row["kind"] == "leftover":
        return None, f"leftover {row['bucket']}"
    hh = None if row["hhhl"] == "none" else str(row["hhhl"])
    wk = None if row["wick"] == "none" else str(row["wick"])
    if float(row["wick_gap"]) < float(min_wick_gap):
        wk = None
    tag = row["bucket"]
    if mode == "hhhl":
        if hh:
            return hh, f"{tag} hhhl {hh}"
        return None, f"{tag} no hhhl"
    if mode == "wick":
        if wk:
            return wk, f"{tag} wick {wk}"
        return None, f"{tag} no wick"
    if mode == "and":
        if hh and wk and hh == wk:
            return hh, f"{tag} and {hh}"
        return None, f"{tag} and skip"
    # or: take the one signal; skip fights
    if hh and wk and hh != wk:
        return None, f"{tag} or fight skip"
    side = hh or wk
    if side:
        return side, f"{tag} or {side}"
    return None, f"{tag} or skip"


def _close_leg(
    *,
    tf: str,
    side: str,
    entry_time: str,
    entry_px: float,
    exit_c: Candle,
    cfg: ChargeConfig,
) -> Trade:
    if side == "LONG":
        pts = exit_c.close - entry_px
        order_side = "BUY"
    else:
        pts = entry_px - exit_c.close
        order_side = "SELL"
    settled = apply_charges_and_tax(
        pts,
        cfg,
        side=order_side,
        entry_price=entry_px,
        exit_price=exit_c.close,
    )
    return Trade(
        tf=tf,
        side=side,
        entry_time=entry_time,
        entry_px=entry_px,
        exit_time=exit_c.time,
        exit_px=exit_c.close,
        gross_pts=float(pts) * float(cfg.lot_size),
        gross_pnl_inr=float(settled["gross_pnl"]),
        after_tax_pnl_inr=float(settled["pnl_after_tax"]),
        fees_inr=float(settled["charges"]),
        lots=float(cfg.lot_size),
    )


def simulate_s17(
    candles: list[Candle],
    *,
    tf: str,
    mode: str = "hhhl",
    lots: float = 100.0,
    fees: bool = True,
    session_filter: bool = True,
    market_open: str = "09:00",
    market_close: str = "23:30",
    charge_cfg: ChargeConfig | None = None,
    min_wick_gap: float = 3.0,
) -> Any:
    """FLIP book on listed+missing buckets. Fill at signal-bar close."""
    cfg = charge_cfg or make_charge_cfg(fees=fees, lots=lots)
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

    for i in range(1, len(candles)):
        prev, cur = candles[i - 1], candles[i]
        sess_ok = (not session_filter) or in_session(
            cur, open_hhmm=market_open, close_hhmm=market_close
        )
        if side is not None and session_filter and not sess_ok:
            close_trade(cur)
            continue
        if session_filter and not sess_ok:
            continue

        want, _why = s17_bar_decision(
            prev, cur, mode=mode, min_wick_gap=min_wick_gap
        )
        want_long = want == "long"
        want_short = want == "short"

        if side == "LONG":
            if want_short:
                close_trade(cur)
                side = "SHORT"
                entry_px = cur.close
                entry_time = cur.time
            continue
        if side == "SHORT":
            if want_long:
                close_trade(cur)
                side = "LONG"
                entry_px = cur.close
                entry_time = cur.time
            continue
        if want_long:
            side = "LONG"
            entry_px = cur.close
            entry_time = cur.time
        elif want_short:
            side = "SHORT"
            entry_px = cur.close
            entry_time = cur.time

    if side is not None and candles:
        close_trade(candles[-1])
    return _tf_result_from_trades(tf, len(candles), trades)
