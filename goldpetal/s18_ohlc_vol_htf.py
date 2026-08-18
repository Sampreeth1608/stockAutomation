"""S18 paper: 1h OHLC + prev OHLC + volume + yesterday, pack overlay.

One book. Not live. Leave the 31 CR_* mixes off.

Wait for the 1h to **finish**. Need a previous **same-session** 1h and
yesterday's **completed** day candle. Bar volume is the delta of Angel
session-cumulative volume_trade_for_the_day. Paper starts on the base AND
below. ``learn_s18.py`` may replace that pack from stored ticks (OHLC,
prev OHLC, volume, TBQ/TSQ net, n_ticks, wick). Type RESTART to load.

Base LONG when ALL are true:
    C > O          green
    C > prevC      close up vs previous hour
    H > prevH      higher high
    vol > prev vol this hour traded more than the last hour
    C > dayC       close above yesterday's completed day close

Base SHORT when ALL are true:
    C < O          red
    C < prevC      close down
    L < prevL      lower low
    vol > prev vol
    C < dayC       close below yesterday's completed day close

Anything equal, missing prev/day/volume, or volume not up → skip.
FLIP at this bar's close. Flatten at session end. Fill at close.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from backtest_hhhl_candles import Candle, Trade, in_session, make_charge_cfg
from backtest_wick_candles import _tf_result_from_trades
from charges import ChargeConfig, apply_charges_and_tax
from mtf_bars import floor_bar, parse_ts
from wick_candles import wick_measure

S18_NAME = "S18_OHLC_VOL_HTF"

FORMULA = (
    "Wait for the 1h to finish. Same-session prev 1h + completed yesterday. "
    "Base LONG = green + C>prevC + HH + vol>prevVol + C>dayC. "
    "Base SHORT = red + C<prevC + LL + vol>prevVol + C<dayC. "
    "Learner may drop a core leg or add wick / net / ticks / prev-HL / range / TBQ. "
    "Skip equals / missing prev or day / volume not up. FLIP. Fill at close. Flatten EOD. "
    "Paper only, not live."
)


@dataclass
class VolBar:
    time: str
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    n_ticks: float = 0.0
    tbq: float = 0.0
    tsq: float = 0.0

    @property
    def day(self) -> str:
        return self.time[:10]

    @property
    def net(self) -> float:
        return float(self.tbq) - float(self.tsq)


@dataclass(frozen=True)
class S18Pack:
    """Which legs must fire. Paper starts on ``base``; learner may replace it."""

    name: str = "base"
    require_green_red: bool = True
    require_c_vs_prev: bool = True
    require_hh_ll: bool = True
    require_vol_up: bool = True
    require_vs_day: bool = True
    require_wick_agree: bool = False
    require_net_confirm: bool = False
    require_ticks_up: bool = False
    require_beyond_prev_hl: bool = False
    require_range_up: bool = False
    require_tbq_lead: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "require_green_red": self.require_green_red,
            "require_c_vs_prev": self.require_c_vs_prev,
            "require_hh_ll": self.require_hh_ll,
            "require_vol_up": self.require_vol_up,
            "require_vs_day": self.require_vs_day,
            "require_wick_agree": self.require_wick_agree,
            "require_net_confirm": self.require_net_confirm,
            "require_ticks_up": self.require_ticks_up,
            "require_beyond_prev_hl": self.require_beyond_prev_hl,
            "require_range_up": self.require_range_up,
            "require_tbq_lead": self.require_tbq_lead,
        }


BASE_PACK = S18Pack()
PACK_PATH = Path(__file__).resolve().parent / "data" / "learn" / "s18" / "active.json"


def pack_from_dict(raw: dict[str, Any] | None) -> S18Pack:
    if not raw:
        return BASE_PACK
    src = raw.get("pack") if isinstance(raw.get("pack"), dict) else raw
    return S18Pack(
        name=str(src.get("name") or "pack"),
        require_green_red=bool(src.get("require_green_red", True)),
        require_c_vs_prev=bool(src.get("require_c_vs_prev", True)),
        require_hh_ll=bool(src.get("require_hh_ll", True)),
        require_vol_up=bool(src.get("require_vol_up", True)),
        require_vs_day=bool(src.get("require_vs_day", True)),
        require_wick_agree=bool(src.get("require_wick_agree", False)),
        require_net_confirm=bool(src.get("require_net_confirm", False)),
        require_ticks_up=bool(src.get("require_ticks_up", False)),
        require_beyond_prev_hl=bool(src.get("require_beyond_prev_hl", False)),
        require_range_up=bool(src.get("require_range_up", False)),
        require_tbq_lead=bool(src.get("require_tbq_lead", False)),
    )


def load_active_pack(path: Path | None = None) -> S18Pack:
    p = path or PACK_PATH
    if not p.exists():
        return BASE_PACK
    try:
        return pack_from_dict(json.loads(p.read_text(encoding="utf-8")))
    except Exception:
        return BASE_PACK


def _bar_volume(last_vol: float | None, prev_close_vol: float | None) -> float:
    if last_vol is None or prev_close_vol is None:
        return 0.0
    if last_vol + 1e-9 < prev_close_vol:
        return max(0.0, last_vol)
    return max(0.0, last_vol - prev_close_vol)


def load_vol_rows(db: Path) -> list[tuple[str, float, float | None, float, float]]:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        cols = {row[1] for row in con.execute("PRAGMA table_info(ticks)")}
        has_vol = "volume" in cols
        has_bp = "bp" in cols
        has_sp = "sp" in cols
        sel_vol = "volume" if has_vol else "NULL"
        sel_bp = "bp" if has_bp else "NULL"
        sel_sp = "sp" if has_sp else "NULL"
        sql = (
            f"SELECT received_at, ltp, {sel_vol}, {sel_bp}, {sel_sp} FROM ticks "
            "WHERE ltp IS NOT NULL ORDER BY received_at ASC, id ASC"
        )
        out: list[tuple[str, float, float | None, float, float]] = []
        for r in con.execute(sql):
            vol = None
            if r[2] is not None:
                try:
                    vol = float(r[2])
                except (TypeError, ValueError):
                    vol = None
            try:
                bp = float(r[3] or 0.0)
            except (TypeError, ValueError):
                bp = 0.0
            try:
                sp = float(r[4] or 0.0)
            except (TypeError, ValueError):
                sp = 0.0
            out.append((str(r[0]), float(r[1]), vol, bp, sp))
        return out
    finally:
        con.close()


def build_vol_bars(rows: list[tuple], minutes: int) -> list[VolBar]:
    bars: list[VolBar] = []
    cur_key = None
    o = h = l = c = None
    n_ticks = 0
    last_vol: float | None = None
    last_tbq = 0.0
    last_tsq = 0.0
    prev_close_vol: float | None = None

    def _f(raw: Any) -> float | None:
        if raw is None:
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    def flush(key: datetime) -> None:
        nonlocal o, h, l, c, n_ticks, last_vol, last_tbq, last_tsq, prev_close_vol
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
                float(n_ticks),
                float(last_tbq),
                float(last_tsq),
            )
        )
        if last_vol is not None:
            prev_close_vol = last_vol
        o = h = l = c = None
        n_ticks = 0
        last_vol = None
        last_tbq = 0.0
        last_tsq = 0.0

    for row in rows:
        ts = parse_ts(row[0])
        key = floor_bar(ts, minutes)
        px = float(row[1])
        tv = _f(row[2]) if len(row) >= 3 else None
        tbq = _f(row[3]) if len(row) >= 4 else None
        tsq = _f(row[4]) if len(row) >= 5 else None
        if cur_key is None:
            cur_key = key
        if key != cur_key:
            flush(cur_key)
            cur_key = key
        if o is None:
            o = h = l = c = px
            n_ticks = 0
            last_vol = None
            last_tbq = 0.0
            last_tsq = 0.0
        else:
            h = max(h, px)
            l = min(l, px)
            c = px
        n_ticks += 1
        if tv is not None:
            last_vol = tv
        if tbq is not None:
            last_tbq = tbq
        if tsq is not None:
            last_tsq = tsq
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
    pack: S18Pack | None = None,
) -> tuple[str | None, str]:
    p = pack or BASE_PACK
    if p.require_vs_day and higher is None:
        return None, "need completed day"
    if p.require_vol_up:
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
    above = higher is not None and cur.close > higher.close
    below = higher is not None and cur.close < higher.close
    long_ok = True
    short_ok = True
    if p.require_green_red:
        long_ok = long_ok and green
        short_ok = short_ok and red
    if p.require_c_vs_prev:
        long_ok = long_ok and up_c
        short_ok = short_ok and dn_c
    if p.require_hh_ll:
        long_ok = long_ok and hh
        short_ok = short_ok and ll
    if p.require_vs_day:
        long_ok = long_ok and above
        short_ok = short_ok and below
    if p.require_wick_agree:
        m = wick_measure(cur.open, cur.high, cur.low, cur.close)
        long_ok = long_ok and m.lower > m.upper
        short_ok = short_ok and m.upper > m.lower
    if p.require_net_confirm:
        long_ok = long_ok and cur.net > prev.net
        short_ok = short_ok and cur.net < prev.net
    if p.require_ticks_up:
        long_ok = long_ok and cur.n_ticks > prev.n_ticks
        short_ok = short_ok and cur.n_ticks > prev.n_ticks
    if p.require_beyond_prev_hl:
        long_ok = long_ok and cur.close > prev.high
        short_ok = short_ok and cur.close < prev.low
    if p.require_range_up:
        cur_range = float(cur.high) - float(cur.low)
        prev_range = float(prev.high) - float(prev.low)
        long_ok = long_ok and cur_range > prev_range
        short_ok = short_ok and cur_range > prev_range
    if p.require_tbq_lead:
        long_ok = long_ok and cur.net > 0
        short_ok = short_ok and cur.net < 0
    if long_ok and not short_ok:
        return "long", f"{p.name}:long"
    if short_ok and not long_ok:
        return "short", f"{p.name}:short"
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
    pack: S18Pack | None = None,
) -> Any:
    """FLIP at 1h close when the AND fires. Flatten when the session ends."""
    cfg = charge_cfg or make_charge_cfg(fees=fees, lots=lots)
    use = pack or BASE_PACK
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
        want, _why = s18_bar_decision(
            prev, cur, htf[i] if i < len(htf) else None, use
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

    if side is not None and hours:
        close_trade(hours[-1])
    return _tf_result_from_trades(tf, len(hours), trades)
