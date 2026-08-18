"""OHLC / wick / body / volume / prev-bar / higher-TF relations.

Research only. Does not change paper S13 or S16.

Same-bar: open↔high↔low↔close, body, upper/lower wick, close location in range.
Volume: bar volume (session cumulative delta), ticks, vol vs range/body/wicks,
up-bar vs down-bar volume, expansion/contraction vs prev.
Vs previous bar (this TF): close/high/low/open vs prev O/H/L/C, HH/LL/inside,
current high vs prev low, current low vs prev high, gap, wick vs prev wick,
volume vs prev volume (HH/LL with vol up, range-up/vol-down).
Vs last *completed* higher TF (1h vs 1d, 30m vs 1h, …): same geometry plus
this close vs that O/H/L/C and this volume vs that volume.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from backtest_hhhl_candles import Candle
from mtf_bars import floor_bar, parse_ts
from wick_candles import wick_measure


@dataclass
class RelBar(Candle):
    """OHLC plus this-bar volume (session-volume delta) and tick count."""

    volume: float = 0.0
    n_ticks: float = 0.0


def _tick_vol(row: tuple) -> float | None:
    if len(row) < 3 or row[2] is None:
        return None
    try:
        return float(row[2])
    except (TypeError, ValueError):
        return None


def bar_volume_delta(last_vol: float | None, prev_close_vol: float | None) -> float:
    """Bar volume from Angel session-cumulative `volume_trade_for_the_day`.

    First bar of a series is 0 (do not dump the session total). A drop vs the
    previous bar's close volume is a session reset: use last_vol as this bar.
    """
    if last_vol is None:
        return 0.0
    if prev_close_vol is None:
        return 0.0
    if last_vol + 1e-9 < prev_close_vol:
        return max(0.0, last_vol)
    return max(0.0, last_vol - prev_close_vol)


def load_tick_rows(db: Path) -> list[tuple[str, float, float | None]]:
    """`(received_at, ltp, volume)` — volume is session-cumulative, may be None."""
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


def build_rel_bars(rows: list[tuple], minutes: int) -> list[RelBar]:
    """OHLC + bar volume + tick count. 2-tuples `(t, ltp)` get volume=0."""
    bars: list[RelBar] = []
    cur_key = None
    o = h = l = c = None
    n_ticks = 0
    last_vol: float | None = None
    prev_close_vol: float | None = None

    def flush(key) -> None:
        nonlocal o, h, l, c, n_ticks, last_vol, prev_close_vol
        if o is None or h is None or l is None or c is None:
            return
        bars.append(
            RelBar(
                key.strftime("%Y-%m-%d %H:%M:%S"),
                float(o),
                float(h),
                float(l),
                float(c),
                bar_volume_delta(last_vol, prev_close_vol),
                float(n_ticks),
            )
        )
        if last_vol is not None:
            prev_close_vol = last_vol
        o = h = l = c = None
        n_ticks = 0
        last_vol = None

    for row in rows:
        ts = parse_ts(row[0])
        key = floor_bar(ts, minutes)
        px = float(row[1])
        tv = _tick_vol(row)
        if cur_key is None:
            cur_key = key
        if key != cur_key:
            flush(cur_key)
            cur_key = key
        if o is None:
            o = h = l = c = px
            n_ticks = 0
            last_vol = None
        else:
            h = max(h, px)
            l = min(l, px)
            c = px
        n_ticks += 1
        if tv is not None:
            last_vol = tv
    if cur_key is not None:
        flush(cur_key)
    return bars


_EPS = 1e-9


def _rng(pts: float) -> float:
    return max(float(pts), _EPS)


def _parse_bar_time(raw: str) -> datetime | None:
    s = str(raw or "").replace("T", " ")[:19]
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _wick_pack(c: Candle, prefix: str) -> dict[str, float]:
    m = wick_measure(c.open, c.high, c.low, c.close)
    rng = _rng(m.range_pts)
    if c.close > c.open:
        green = 1.0
    elif c.close < c.open:
        green = 0.0
    else:
        green = 0.5
    return {
        f"{prefix}upper": float(m.upper),
        f"{prefix}lower": float(m.lower),
        f"{prefix}body": float(m.body),
        f"{prefix}range": float(m.range_pts),
        f"{prefix}wick_gap": abs(float(m.upper) - float(m.lower)),
        f"{prefix}body_frac": float(m.body) / rng,
        f"{prefix}upper_frac": float(m.upper) / rng,
        f"{prefix}lower_frac": float(m.lower) / rng,
        f"{prefix}close_loc": (float(c.close) - float(c.low)) / rng,
        f"{prefix}green": green,
    }


def _delta(name: str, pts: float, rng: float) -> dict[str, float]:
    return {name: float(pts), f"{name}_r": float(pts) / rng}


def _bar_vol(c: Candle) -> float:
    return max(0.0, float(getattr(c, "volume", 0.0) or 0.0))


def _bar_ticks(c: Candle) -> float:
    return max(0.0, float(getattr(c, "n_ticks", 0.0) or 0.0))


def _vol_features(
    prev: Candle,
    cur: Candle,
    *,
    higher: Candle | None,
    rng: float,
    m: Any,
    pm: Any,
) -> dict[str, float]:
    v = _bar_vol(cur)
    pv = _bar_vol(prev)
    nt = max(_bar_ticks(cur), 1.0)
    if cur.close > cur.open:
        signed = v
    elif cur.close < cur.open:
        signed = -v
    else:
        signed = 0.0
    out = {
        "vol": v,
        "p_vol": pv,
        "n_ticks": _bar_ticks(cur),
        "p_n_ticks": _bar_ticks(prev),
        "vol_vs_p": v - pv,
        "vol_ratio": v / max(pv, 1.0),
        "vol_up": 1.0 if v > pv else 0.0,
        "vol_down": 1.0 if v < pv else 0.0,
        "signed_vol": signed,
        "vol_per_range": v / rng,
        "vol_per_body": v / max(float(m.body), _EPS),
        "vol_per_tick": v / nt,
        "vol_x_upper": v * (float(m.upper) / rng),
        "vol_x_lower": v * (float(m.lower) / rng),
        "vol_x_close_loc": v * ((float(cur.close) - float(cur.low)) / rng),
        "hh_vol_up": 1.0 if cur.high > prev.high and v > pv else 0.0,
        "ll_vol_up": 1.0 if cur.low < prev.low and v > pv else 0.0,
        "green_vol_up": 1.0 if cur.close > cur.open and v > pv else 0.0,
        "red_vol_up": 1.0 if cur.close < cur.open and v > pv else 0.0,
        "range_up_vol_down": 1.0 if m.range_pts > pm.range_pts and v < pv else 0.0,
        "body_up_vol_down": 1.0 if m.body > pm.body and v < pv else 0.0,
        "c_pc_x_vol_ratio": (cur.close - prev.close) * (v / max(pv, 1.0)),
        "h_ph_x_vol_ratio": (cur.high - prev.high) * (v / max(pv, 1.0)),
        "l_pl_x_vol_ratio": (cur.low - prev.low) * (v / max(pv, 1.0)),
        "htf_vol": 0.0,
        "vol_vs_htf": 0.0,
        "vol_htf_ratio": 0.0,
        "htf_signed_vol": 0.0,
    }
    if higher is not None:
        hv = _bar_vol(higher)
        if higher.close > higher.open:
            h_signed = hv
        elif higher.close < higher.open:
            h_signed = -hv
        else:
            h_signed = 0.0
        out["htf_vol"] = hv
        out["vol_vs_htf"] = v - hv
        out["vol_htf_ratio"] = v / max(hv, 1.0)
        out["htf_signed_vol"] = h_signed
    return out


def pair_features(
    prev: Candle,
    cur: Candle,
    *,
    higher: Candle | None = None,
) -> dict[str, float]:
    """Geometry of ``cur`` vs ``prev`` (same TF) and optional completed higher TF."""
    rng = _rng(cur.high - cur.low)
    out: dict[str, float] = {}
    out.update(_wick_pack(cur, ""))
    out.update(_wick_pack(prev, "p_"))

    same = {
        "o_h": cur.high - cur.open,
        "o_l": cur.open - cur.low,
        "o_c": cur.close - cur.open,
        "h_c": cur.high - cur.close,
        "h_l": cur.high - cur.low,
        "l_c": cur.close - cur.low,
    }
    vs_prev = {
        "c_pc": cur.close - prev.close,
        "h_ph": cur.high - prev.high,
        "l_pl": cur.low - prev.low,
        "c_po": cur.close - prev.open,
        "c_ph": cur.close - prev.high,
        "c_pl": cur.close - prev.low,
        "h_pl": cur.high - prev.low,
        "l_ph": cur.low - prev.high,
        "l_pc": cur.low - prev.close,
        "h_pc": cur.high - prev.close,
        "h_po": cur.high - prev.open,
        "l_po": cur.low - prev.open,
        "o_pc": cur.open - prev.close,
        "o_po": cur.open - prev.open,
        "o_ph": cur.open - prev.high,
        "o_pl": cur.open - prev.low,
    }
    for name, pts in {**same, **vs_prev}.items():
        out.update(_delta(name, pts, rng))

    pm = wick_measure(prev.open, prev.high, prev.low, prev.close)
    m = wick_measure(cur.open, cur.high, cur.low, cur.close)
    out["upper_vs_p"] = float(m.upper) - float(pm.upper)
    out["lower_vs_p"] = float(m.lower) - float(pm.lower)
    out["body_vs_p"] = float(m.body) - float(pm.body)
    out["range_vs_p"] = float(m.range_pts) - float(pm.range_pts)

    out["hh"] = 1.0 if cur.high > prev.high else 0.0
    out["ll"] = 1.0 if cur.low < prev.low else 0.0
    out["hl"] = 1.0 if cur.low > prev.low else 0.0
    out["lh"] = 1.0 if cur.high < prev.high else 0.0
    out["inside"] = (
        1.0 if cur.high <= prev.high and cur.low >= prev.low else 0.0
    )
    out["outside"] = (
        1.0 if cur.high > prev.high and cur.low < prev.low else 0.0
    )
    out.update(_vol_features(prev, cur, higher=higher, rng=rng, m=m, pm=pm))

    htf: dict[str, float] = {
        "htf_present": 0.0,
        "htf_c_o": 0.0,
        "htf_c_h": 0.0,
        "htf_c_l": 0.0,
        "htf_c_c": 0.0,
        "htf_h_h": 0.0,
        "htf_l_l": 0.0,
        "htf_h_l": 0.0,
        "htf_l_h": 0.0,
        "htf_o_c": 0.0,
        "htf_upper": 0.0,
        "htf_lower": 0.0,
        "htf_body": 0.0,
        "htf_range": 0.0,
        "htf_green": 0.5,
        "htf_same_green": 0.5,
        "htf_upper_vs": 0.0,
        "htf_lower_vs": 0.0,
    }
    if higher is not None:
        hm = wick_measure(higher.open, higher.high, higher.low, higher.close)
        htf.update(
            {
                "htf_present": 1.0,
                "htf_c_o": cur.close - higher.open,
                "htf_c_h": cur.close - higher.high,
                "htf_c_l": cur.close - higher.low,
                "htf_c_c": cur.close - higher.close,
                "htf_h_h": cur.high - higher.high,
                "htf_l_l": cur.low - higher.low,
                "htf_h_l": cur.high - higher.low,
                "htf_l_h": cur.low - higher.high,
                "htf_o_c": cur.open - higher.close,
                "htf_upper": float(hm.upper),
                "htf_lower": float(hm.lower),
                "htf_body": float(hm.body),
                "htf_range": float(hm.range_pts),
                "htf_green": (
                    1.0
                    if higher.close > higher.open
                    else (0.0 if higher.close < higher.open else 0.5)
                ),
                "htf_same_green": (
                    1.0
                    if (cur.close - cur.open) * (higher.close - higher.open) > 0
                    else 0.0
                ),
                "htf_upper_vs": float(m.upper) - float(hm.upper),
                "htf_lower_vs": float(m.lower) - float(hm.lower),
            }
        )
    out.update(htf)
    return out


FEATURE_COLUMNS: tuple[str, ...] = tuple(
    sorted(
        pair_features(
            RelBar("2026-08-17 10:00:00", 100.0, 105.0, 99.0, 104.0, 1000.0, 10.0),
            RelBar("2026-08-17 11:00:00", 104.0, 110.0, 103.0, 108.0, 1500.0, 12.0),
            higher=RelBar("2026-08-16 00:00:00", 90.0, 120.0, 80.0, 100.0, 8000.0, 80.0),
        ).keys()
    )
)


def feature_vector(feat: dict[str, float]) -> list[float]:
    return [float(feat.get(name) or 0.0) for name in FEATURE_COLUMNS]


def attach_completed_higher(
    bars: list[Candle],
    higher: list[Candle],
    higher_minutes: int,
) -> list[Candle | None]:
    """Last higher-TF bar whose period has already finished at this bar's start."""
    if not higher:
        return [None] * len(bars)
    step = timedelta(minutes=max(1, int(higher_minutes)))
    parsed: list[tuple[Candle, datetime]] = []
    for c in higher:
        dt = _parse_bar_time(c.time)
        if dt is not None:
            parsed.append((c, dt + step))
    out: list[Candle | None] = []
    j = -1
    for b in bars:
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


def labeled_rows(
    candles: list[Candle],
    *,
    higher: list[Candle] | None = None,
    higher_minutes: int = 1440,
    session_ok: Any = None,
) -> list[dict[str, Any]]:
    """One row per finished bar that has a previous bar and a next bar (label)."""
    htf = attach_completed_higher(candles, higher or [], higher_minutes)
    rows: list[dict[str, Any]] = []
    for i in range(1, len(candles) - 1):
        prev, cur, nxt = candles[i - 1], candles[i], candles[i + 1]
        if session_ok is not None and not session_ok(cur):
            continue
        feat = pair_features(prev, cur, higher=htf[i] if i < len(htf) else None)
        y_up = 1 if nxt.close > cur.close else 0
        rows.append(
            {
                "time": cur.time,
                "next_time": nxt.time,
                "close": float(cur.close),
                "next_close": float(nxt.close),
                "y_up": y_up,
                "next_pts": float(nxt.close - cur.close),
                "feat": feat,
            }
        )
    return rows
