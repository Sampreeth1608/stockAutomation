"""S18 research: 1h green/HH/up-close + volume-up + above yesterday.

Not paper. Not live. Leave the 31 CR_* mixes for later.

Wait for the 1h to **finish**. Need a previous **same-session** 1h and
yesterday's **completed** day candle. Bar volume is the delta of Angel
session-cumulative volume_trade_for_the_day.

LONG when ALL are true:
  C > O          green
  C > prevC      close up vs previous hour
  H > prevH      higher high
  vol > prev vol this hour traded more than the last hour
  C > dayC       close above yesterday's completed day close

SHORT when ALL are true:
  C < O          red
  C < prevC      close down
  L < prevL      lower low
  vol > prev vol
  C < dayC       close below yesterday's completed day close

Anything equal, missing prev/day/volume, or volume not up → skip.
FLIP at this bar's close. Flatten at session end. Fill at close.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from backtest_hhhl_candles import Candle, Trade, in_session, make_charge_cfg
from backtest_wick_candles import _tf_result_from_trades
from charges import ChargeConfig, apply_charges_and_tax
from mtf_bars import floor_bar, parse_ts

S18_NAME = "S18_OHLC_VOL_HTF"

FORMULA = (
    "Wait for the 1h to finish. Same-session prev 1h + completed yesterday. "
    "LONG = green + C>prevC + HH + vol>prevVol + C>dayC. "
    "SHORT = red + C<prevC + LL + vol>prevVol + C<dayC. "
    "Skip equals / missing prev or day / volume not up. FLIP. Fill at close. Flatten EOD."
)


@dataclass
class VolBar:
    time: str
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    @property
    def day(self) -> str:
        return self.time[:10]


def _bar_volume(last_vol: float | None, prev_close_vol: float | None) -> float:
    if last_vol is None or prev_close_vol is None:
        return 0.0
    if last_vol + 1e-9 < prev_close_vol:
        return max(0.0, last_vol)
    return max(0.0, last_vol - prev_close_vol)


def load_vol_rows(db: Path) -> list[tuple[str, float, float | None]]:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        cols = {row[1] for row in con.execute("PRAGMA table_info(ticks)")}
        sql = (
            "SELECT received_at, ltp, volume FROM ticks "
            "WHERE ltp IS NOT NULL ORDER BY received_at ASC, id ASC"
            if "volume" in cols
            else "SELECT received_at, ltp, NULL FROM ticks "
            "WHERE ltp IS NOT NULL ORDER BY received_at ASC, id ASC"
        )
        out: list[tuple[str, float, float | None]] = []
        for r in con.execute(sql):
            vol = None
            if r[2] is not None:
                try:
                    vol = float(r[2])
                except (TypeError, ValueError):
                    vol = None
            out.append((str(r[0]), float(r[1]), vol))
        return out
    finally:
        con.close()


def build_vol_bars(rows: list[tuple], minutes: int) -> list[VolBar]:
    bars: list[VolBar] = []
    cur_key = None
    o = h = l = c = None
    last_vol: float | None = None
    prev_close_vol: float | None = None

    def flush(key: datetime) -> None:
        nonlocal o, h, l, c, last_vol, prev_close_vol
        if o is None or h is None or l is None or c is None:
            return
        bars.append(
            VolBar(
                key.strftime("%Y-%m-%d %H:%M:%S"),
                float(o),
                float(h),
                float(l),
                float(c),
                _bar_volume(last_vol, prev_close_vol),
            )
        )
        if last_vol is not None:
            prev_close_vol = last_vol
        o = h = l = c = None
        last_vol = None

    for row in rows:
        ts = parse_ts(row[0])
        key = floor_bar(ts, minutes)
        px = float(row[1])
        tv = None
        if len(row) >= 3 and row[2] is not None:
            try:
                tv = float(row[2])
            except (TypeError, ValueError):
                tv = None
        if cur_key is None:
            cur_key = key
        if key != cur_key:
            flush(cur_key)
            cur_key = key
        if o is None:
            o = h = l = c = px
            last_vol = None
        else:
            h = max(h, px)
            l = min(l, px)
            c = px
        if tv is not None:
            last_vol = tv
    if cur_key is not None:
        flush(cur_key)
    return bars


def _parse_bar_time(raw: str) -> datetime | None:
    s = str(raw or "").replace("T", " ")[:19]
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def attach_completed_day(hours: list[VolBar], days: list[VolBar]) -> list[VolBar | None]:
    """Yesterday's finished day only — never today's still-open day."""
    if not days:
        return [None] * len(hours)
    step = timedelta(minutes=1440)
    parsed: list[tuple[VolBar, datetime]] = []
    for d in days:
        dt = _parse_bar_time(d.time)
        if dt is not None:
            parsed.append((d, dt + step))
    out: list[VolBar | None] = []
    j = -1
    for b in hours:
        bt = _parse_bar_time(b.time)
        if bt is None or not parsed:
            out.append(None)
            continue
        while j + 1 < len(parsed) and parsed[j + 1][1] <= bt:
            j += 1
        if j >= 0 and parsed[j][1] <= bt:
            out.append(parsed[j][0])
        else:
            out.append(None)
    return out


def s18_bar_decision(
    prev: VolBar,
    cur: VolBar,
    higher: VolBar | None,
) -> tuple[str | None, str]:
    if higher is None:
        return None, "need completed day"
    if cur.volume <= 0 and prev.volume <= 0:
        return None, "need volume"
    if not (cur.volume > prev.volume):
        return None, "vol not up"
    green = cur.close > cur.open
    red = cur.close < cur.open
    hh = cur.high > prev.high
    ll = cur.low < prev.low
    up_c = cur.close > prev.close
    dn_c = cur.close < prev.close
    above = cur.close > higher.close
    below = cur.close < higher.close
    long_ok = green and up_c and hh and above
    short_ok = red and dn_c and ll and below
    if long_ok and not short_ok:
        return "long", "green+C>prev+HH+vol>+C>day"
    if short_ok and not long_ok:
        return "short", "red+C<prev+LL+vol>+C<day"
    if long_ok and short_ok:
        return None, "both sides"
    return None, "no AND"


def _as_candle(bar: VolBar) -> Candle:
    return Candle(bar.time, bar.open, bar.high, bar.low, bar.close)


def _close_leg(
    *,
    tf: str,
    side: str,
    entry_time: str,
    entry_px: float,
    exit_c: VolBar,
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


def simulate_s18(
    hours: list[VolBar],
    days: list[VolBar],
    *,
    tf: str = "1h",
    lots: float = 100.0,
    fees: bool = True,
    session_filter: bool = True,
    market_open: str = "09:00",
    market_close: str = "23:30",
    charge_cfg: ChargeConfig | None = None,
) -> Any:
    """FLIP at 1h close when the AND fires. Flatten when the session ends."""
    cfg = charge_cfg or make_charge_cfg(fees=fees, lots=lots)
    htf = attach_completed_day(hours, days)
    trades: list[Trade] = []
    side: str | None = None
    entry_px = 0.0
    entry_time = ""

    def close_trade(exit_c: VolBar) -> None:
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

    for i in range(1, len(hours)):
        prev, cur = hours[i - 1], hours[i]
        sess_ok = (not session_filter) or in_session(
            _as_candle(cur), open_hhmm=market_open, close_hhmm=market_close
        )
        if side is not None and session_filter and not sess_ok:
            close_trade(cur)
            continue
        if session_filter and not sess_ok:
            continue
        if session_filter:
            prev_sess = in_session(
                _as_candle(prev), open_hhmm=market_open, close_hhmm=market_close
            )
            if (not prev_sess) or prev.time[:10] != cur.time[:10]:
                continue
        want, _why = s18_bar_decision(prev, cur, htf[i] if i < len(htf) else None)
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

    if side is not None and hours:
        close_trade(hours[-1])
    return _tf_result_from_trades(tf, len(hours), trades)
