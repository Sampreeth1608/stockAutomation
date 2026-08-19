"""Nested inner-candle net → next-timeframe bias. Research only. Not paper. Not live.

Take a finished parent candle and net the inner candles that tile it.
Example: 15m 10:15–10:30 holds 15×1m, 5×3m, 3×5m. If that net is
positive the *next* 15m is bullish (long); if negative, bearish (short).
Same stack on 30m, 45m, 1h, 1h15, 2h, 3h, and the session day.

Inner net (each inner bar, all inner TFs):
  body = sum(close − open)
  vol  = volume net = sum(volume × sign(close − open))  (up-vol − down-vol)
  tbq  = TBQ net    = sum(TBQ × sign(close − open))     (same stack)
  tsq  = TSQ net    = sum(TSQ × sign(close − open))     (same stack)
  book = sum(TBQ − TSQ) at each inner close
  vote = green bars minus red bars

``sum`` uses body. ``vol`` / ``tbq`` / ``tsq`` use that signed net.
``book`` uses TBQ−TSQ. ``vote`` uses the green/red count.
Tiebreak is body, then book.

Body sums of tiling OHLC inners equal the parent body (counted once
per inner TF). Book snapshots and the green/red vote do not telescope
the same way.

Fill at the finished parent close; exit at the next parent close.
One-bar hold. Intraday parents skip session gaps / overnight. Daily
holds to the next session. Rank after Angel charges, tax excluded.
Stay DRY_RUN. Do not add this name to ALL_STRATEGY_NAMES / ENABLE_*.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable, Mapping

from amise_timeframes import floor_session_bar, parse_tf
from backtest_hhhl_candles import Trade, make_charge_cfg
from backtest_wick_candles import _tf_result_from_trades
from charges import ChargeConfig, apply_charges_and_tax
from flow_lab import FlowBar, flow_bars_from_tick_rows
from mtf_bars import parse_ts
from ohlcv_lab import after_charges_inr

LAB_NAME = "NESTED_TF_NET"

PARENTS: tuple[tuple[str, int], ...] = (
    ("15m", 15),
    ("30m", 30),
    ("45m", 45),
    ("1h", 60),
    ("1h15", 75),
    ("2h", 120),
    ("3h", 180),
    ("1d", 1440),
)

INNER_MINUTES: tuple[int, ...] = (1, 3, 5, 15, 30, 45, 60, 75, 120, 180)
MODES: tuple[str, ...] = ("sum", "vol", "tbq", "tsq", "book", "vote")
SESSION_MINUTES = 14 * 60 + 30  # 09:00–23:30 IST

FORMULA = (
    "Finished parent bar → net inner candles that tile it "
    "(15m: 15×1m + 5×3m + 3×5m; same idea on 30m/45m/1h/1h15/2h/3h/day) "
    "→ body (C−O), volume net (up-vol − down-vol), TBQ net, TSQ net, "
    "TBQ−TSQ book. Positive net = bullish next parent (long); negative = "
    "bearish (short); zero skip. Fill at parent close, exit next parent "
    "close. Intraday no overnight. Research only."
)


def tf_label(minutes: int) -> str:
    return parse_tf(int(minutes)).label


def parent_span_minutes(parent_min: int) -> int:
    return SESSION_MINUTES if int(parent_min) >= 1440 else int(parent_min)


def inner_minutes_for(parent_min: int) -> tuple[int, ...]:
    """Inner rungs that tile the parent. Daily uses every inner rung; partials drop by containment."""
    parent_min = int(parent_min)
    span = parent_span_minutes(parent_min)
    out: list[int] = []
    for m in INNER_MINUTES:
        if parent_min >= 1440:
            if m < 1440:
                out.append(m)
            continue
        if m >= parent_min:
            continue
        if span % m == 0:
            out.append(m)
    return tuple(out)


def expected_inner_count(parent_min: int, inner_min: int) -> int:
    inner_min = int(inner_min)
    if inner_min <= 0:
        return 0
    return parent_span_minutes(parent_min) // inner_min


def parent_end(ts: datetime, parent_min: int) -> datetime:
    if int(parent_min) >= 1440:
        return ts.replace(hour=23, minute=30, second=0, microsecond=0)
    return ts + timedelta(minutes=int(parent_min))


def needed_minutes() -> tuple[int, ...]:
    need: set[int] = set()
    for _label, pmin in PARENTS:
        need.add(int(pmin))
        need.update(inner_minutes_for(pmin))
    return tuple(sorted(need))


def rollup_from_1m(ones: list[FlowBar], minutes: int) -> list[FlowBar]:
    """Fold 1m session bars into a coarser session TF. Last inner keeps TBQ/TSQ."""
    minutes = int(minutes)
    if minutes <= 1:
        return list(ones)
    buckets: dict[datetime, list[FlowBar]] = {}
    order: list[datetime] = []
    for b in ones:
        key = floor_session_bar(parse_ts(b.time), minutes)
        chunk = buckets.get(key)
        if chunk is None:
            buckets[key] = [b]
            order.append(key)
        else:
            chunk.append(b)
    out: list[FlowBar] = []
    for key in order:
        chunk = buckets[key]
        last = chunk[-1]
        out.append(
            FlowBar(
                time=key.strftime("%Y-%m-%d %H:%M:%S"),
                open=float(chunk[0].open),
                high=max(float(x.high) for x in chunk),
                low=min(float(x.low) for x in chunk),
                close=float(last.close),
                volume=sum(float(x.volume) for x in chunk),
                n_ticks=sum(float(x.n_ticks) for x in chunk),
                tbq=float(last.tbq),
                tsq=float(last.tsq),
                oi=float(getattr(last, "oi", 0.0) or 0.0),
                buy5=float(getattr(last, "buy5", 0.0) or 0.0),
                sell5=float(getattr(last, "sell5", 0.0) or 0.0),
                buy_px=getattr(last, "buy_px", ()),
                buy_qty=getattr(last, "buy_qty", ()),
                sell_px=getattr(last, "sell_px", ()),
                sell_qty=getattr(last, "sell_qty", ()),
            )
        )
    return out


def bars_from_ticks(tick_rows: Any, *, progress: bool = False) -> dict[int, list[FlowBar]]:
    """Session-aligned FlowBars: one 1m pass from ticks, then roll up the other rungs."""
    def _p(msg: str) -> None:
        if progress:
            print(msg, flush=True)

    need = needed_minutes()
    _p("building 1m bars from ticks (this is the slow pass)...")
    ones = flow_bars_from_tick_rows(
        tick_rows,
        1,
        session_align=True,
        session_ticks=True,
        split_token=True,
    )
    _p(f"  1m: {len(ones)} bars")
    out: dict[int, list[FlowBar]] = {1: ones}
    for minutes in need:
        if minutes == 1:
            continue
        lab = tf_label(minutes)
        _p(f"rolling {lab} from 1m...")
        out[minutes] = rollup_from_1m(ones, minutes)
        _p(f"  {lab}: {len(out[minutes])} bars")
    return out


def inners_inside(
    bars: Iterable[FlowBar],
    *,
    start: datetime,
    end: datetime,
    inner_min: int,
) -> list[FlowBar]:
    inner_d = timedelta(minutes=int(inner_min))
    out: list[FlowBar] = []
    for b in bars:
        ts = parse_ts(b.time)
        if ts < start:
            continue
        if ts >= end:
            break
        if ts + inner_d <= end:
            out.append(b)
    return out


def collect_inners(
    parent: FlowBar,
    parent_min: int,
    bars_by_min: Mapping[int, list[FlowBar]],
) -> dict[str, list[FlowBar]]:
    start = parse_ts(parent.time)
    end = parent_end(start, parent_min)
    collected: dict[str, list[FlowBar]] = {}
    for minutes in inner_minutes_for(parent_min):
        collected[tf_label(minutes)] = inners_inside(
            bars_by_min.get(minutes, []),
            start=start,
            end=end,
            inner_min=minutes,
        )
    return collected


@dataclass(frozen=True)
class NestedNet:
    parent: str
    parent_time: str
    mode: str
    n_inners: int
    counts: dict[str, int]
    body_net: float
    vol_net: float
    tbq_net: float
    tsq_net: float
    book_net: float
    vote_body: int
    vote_book: int
    bias: int  # +1 bullish next TF, −1 bearish, 0 skip

    @property
    def side(self) -> str | None:
        if self.bias > 0:
            return "LONG"
        if self.bias < 0:
            return "SHORT"
        return None


def _sign(value: float) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


def _first_nonzero(*vals: float) -> float:
    for v in vals:
        if v != 0:
            return float(v)
    return 0.0


def score_nested(
    collected: Mapping[str, list[FlowBar]],
    *,
    parent: str,
    parent_time: str,
    mode: str = "sum",
) -> NestedNet:
    body = 0.0
    vol_net = 0.0
    tbq_net = 0.0
    tsq_net = 0.0
    book = 0.0
    vote_body = 0
    vote_book = 0
    counts: dict[str, int] = {}
    n = 0
    for label, bars in collected.items():
        counts[label] = len(bars)
        n += len(bars)
        for b in bars:
            bd = float(b.close) - float(b.open)
            s = _sign(bd)
            vol = float(b.volume or 0.0)
            tbq = float(b.tbq or 0.0)
            tsq = float(b.tsq or 0.0)
            bk = tbq - tsq
            body += bd
            vol_net += s * vol
            tbq_net += s * tbq
            tsq_net += s * tsq
            book += bk
            vote_body += s
            vote_book += _sign(bk)
    key = str(mode or "sum").strip().lower()
    if key == "vote":
        raw = _first_nonzero(float(vote_body), float(vote_book), body, book)
    elif key == "book":
        raw = _first_nonzero(book, body, vol_net)
    elif key == "vol":
        raw = _first_nonzero(vol_net, body, book)
    elif key == "tbq":
        raw = _first_nonzero(tbq_net, body, book)
    elif key == "tsq":
        raw = _first_nonzero(tsq_net, body, book)
    else:
        raw = _first_nonzero(body, book, vol_net)
    return NestedNet(
        parent=parent,
        parent_time=parent_time,
        mode=key,
        n_inners=n,
        counts=counts,
        body_net=body,
        vol_net=vol_net,
        tbq_net=tbq_net,
        tsq_net=tsq_net,
        book_net=book,
        vote_body=vote_body,
        vote_book=vote_book,
        bias=_sign(raw),
    )


def enough_inners(parent_min: int, collected: Mapping[str, list[FlowBar]]) -> bool:
    specs = inner_minutes_for(parent_min)
    if not specs:
        return False
    for minutes in specs:
        bars = collected.get(tf_label(minutes), [])
        exp = expected_inner_count(parent_min, minutes)
        if not bars:
            return False
        if exp > 0 and len(bars) < max(1, (exp + 1) // 2):
            return False
    return True


def next_is_adjacent(parent: FlowBar, nxt: FlowBar, parent_min: int) -> bool:
    pts = parse_ts(parent.time)
    nts = parse_ts(nxt.time)
    if int(parent_min) >= 1440:
        return nts.date() > pts.date()
    return nts == pts + timedelta(minutes=int(parent_min))


def simulate_nested(
    parents: list[FlowBar],
    bars_by_min: Mapping[int, list[FlowBar]],
    *,
    tf: str,
    parent_min: int,
    mode: str = "sum",
    lots: float = 1.0,
    fees: bool = False,
    charge_cfg: ChargeConfig | None = None,
) -> Any:
    """Long/short the next parent from this parent's inner net. One-bar hold."""
    cfg = charge_cfg or make_charge_cfg(fees=fees, lots=lots)
    trades: list[Trade] = []
    label = tf_label(parent_min) if not tf else tf
    mode_key = str(mode or "sum").strip().lower()
    row_name = f"{label}:{mode_key}"

    for i in range(len(parents) - 1):
        parent = parents[i]
        nxt = parents[i + 1]
        if not next_is_adjacent(parent, nxt, parent_min):
            continue
        collected = collect_inners(parent, parent_min, bars_by_min)
        if not enough_inners(parent_min, collected):
            continue
        net = score_nested(
            collected,
            parent=label,
            parent_time=parent.time,
            mode=mode_key,
        )
        if net.bias == 0 or net.side is None:
            continue
        side = net.side
        entry_px = float(parent.close)
        exit_px = float(nxt.close)
        if side == "LONG":
            pts = exit_px - entry_px
            order_side = "BUY"
        else:
            pts = entry_px - exit_px
            order_side = "SELL"
        settled = apply_charges_and_tax(
            pts,
            cfg,
            side=order_side,
            entry_price=entry_px,
            exit_price=exit_px,
        )
        trades.append(
            Trade(
                tf=row_name,
                side=side,
                entry_time=parent.time,
                entry_px=entry_px,
                exit_time=nxt.time,
                exit_px=exit_px,
                gross_pts=float(pts) * float(cfg.lot_size),
                gross_pnl_inr=float(settled["gross_pnl"]),
                after_tax_pnl_inr=float(settled["pnl_after_tax"]),
                fees_inr=float(settled["charges"]),
                lots=float(cfg.lot_size),
            )
        )
    return _tf_result_from_trades(row_name, len(parents), trades)


def simulate_all(
    bars_by_min: Mapping[int, list[FlowBar]],
    *,
    lots: float = 1.0,
    fees: bool = False,
    modes: Iterable[str] = MODES,
    parents: Iterable[tuple[str, int]] = PARENTS,
    charge_cfg: ChargeConfig | None = None,
) -> list[Any]:
    cfg = charge_cfg or make_charge_cfg(fees=fees, lots=lots)
    results = []
    mode_list = tuple(modes) or MODES
    for label, minutes in parents:
        series = list(bars_by_min.get(minutes, []))
        for mode in mode_list:
            results.append(
                simulate_nested(
                    series,
                    bars_by_min,
                    tf=label,
                    parent_min=minutes,
                    mode=mode,
                    lots=lots,
                    fees=fees,
                    charge_cfg=cfg,
                )
            )
    return results


def after_charges_win_rate(result: Any) -> float:
    trades = list(getattr(result, "trades", []) or [])
    n = len(trades)
    if n == 0:
        return 0.0
    wins = sum(1 for t in trades if (t.gross_pnl_inr - t.fees_inr) > 0)
    return wins / n


def inner_recipe_line(parent_min: int) -> str:
    parts = []
    for minutes in inner_minutes_for(parent_min):
        n = expected_inner_count(parent_min, minutes)
        parts.append(f"{n}×{tf_label(minutes)}")
    return ", ".join(parts)
