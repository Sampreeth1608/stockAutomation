"""Shared multi-timeframe bar builder from Goldpetal ticks."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from depth import depth_buy_sell_sums

IST = ZoneInfo("Asia/Kolkata")
DB = Path(__file__).resolve().parent / "data" / "ticks.db"

INTERVALS: list[tuple[str, int]] = [
    ("1m", 1),
    ("2m", 2),
    ("3m", 3),
    ("5m", 5),
    ("10m", 10),
    ("15m", 15),
    ("30m", 30),
    ("45m", 45),
    ("1h", 60),
    ("2h", 120),
    ("3h", 180),
    ("4h", 240),
    ("1d", 1440),
]

# Count-based bars (N ticks per bar), for ALIGN hist sweeps.
TICK_INTERVALS: list[tuple[str, int]] = [
    ("5t", 5),
    ("10t", 10),
    ("15t", 15),
    ("20t", 20),
    ("30t", 30),
    ("40t", 40),
    ("50t", 50),
    ("60t", 60),
]


@dataclass
class RichBar:
    tf: str
    time: str
    open: float
    high: float
    low: float
    close: float
    range_pts: float
    n_ticks: int
    tbq_open: float
    tbq_close: float
    tsq_open: float
    tsq_close: float
    net: float
    net_delta: float | None
    imb_pct: float
    price_delta: float | None
    ltq_sum: float
    ltq_avg: float
    buy5_sum: float
    sell5_sum: float
    depth_net: float
    depth_imb_pct: float
    oi_close: float | None
    volume_close: float | None
    bar_volume: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_row(self) -> dict[str, Any]:
        return {
            "tf": self.tf,
            "time": self.time,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "range_pts": self.range_pts,
            "price_delta": self.price_delta,
            "n_ticks": self.n_ticks,
            "tbq_open": self.tbq_open,
            "tbq_close": self.tbq_close,
            "tsq_open": self.tsq_open,
            "tsq_close": self.tsq_close,
            "net": self.net,
            "net_delta": self.net_delta,
            "imb_pct": round(self.imb_pct, 2),
            "ltq_sum": self.ltq_sum,
            "ltq_avg": round(self.ltq_avg, 2),
            "buy5_sum": self.buy5_sum,
            "sell5_sum": self.sell5_sum,
            "depth_net": self.depth_net,
            "depth_imb_pct": round(self.depth_imb_pct, 2),
            "oi_close": self.oi_close,
            "volume_close": self.volume_close,
            "bar_volume": self.bar_volume,
        }


def parse_ts(raw: str) -> datetime:
    try:
        dt = datetime.fromisoformat(str(raw))
    except Exception:
        return datetime.now(IST)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    return dt.astimezone(IST)


def floor_bar(ts: datetime, minutes: int) -> datetime:
    midnight = ts.replace(hour=0, minute=0, second=0, microsecond=0)
    mins = int((ts - midnight).total_seconds() // 60)
    block = (mins // minutes) * minutes
    return midnight + timedelta(minutes=block)


def load_tick_rows(db: Path = DB):
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """
        SELECT id, received_at, exchange_timestamp, ltp, bp, sp, volume, raw_json
        FROM ticks
        WHERE ltp IS NOT NULL
        ORDER BY received_at ASC, id ASC
        """
    ).fetchall()
    con.close()
    return rows


def _msg(row) -> dict[str, Any]:
    if row["raw_json"]:
        try:
            m = json.loads(row["raw_json"])
            if isinstance(m, dict):
                return m
        except (TypeError, json.JSONDecodeError):
            pass
    return {}


def _f(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def tick_metrics(row) -> dict[str, Any]:
    """One tick row with all metrics for sheet export."""
    msg = _msg(row)
    ltp = _f(row["ltp"])
    if ltp is None:
        # raw may be in paise
        raw_ltp = _f(msg.get("last_traded_price"))
        ltp = (raw_ltp / 100.0) if raw_ltp and raw_ltp > 1000 else raw_ltp

    tbq = _f(msg.get("total_buy_quantity"))
    if tbq is None:
        tbq = _f(row["bp"])
    tsq = _f(msg.get("total_sell_quantity"))
    if tsq is None:
        tsq = _f(row["sp"])
    tbq = tbq or 0.0
    tsq = tsq or 0.0
    net = tbq - tsq
    imb = abs(net) / max(tbq, tsq, 1e-9) * 100.0

    buy5, sell5, details = depth_buy_sell_sums(msg)
    depth_net = buy5 - sell5
    depth_imb = abs(depth_net) / max(buy5, sell5, 1e-9) * 100.0
    ltq = _f(msg.get("last_traded_quantity")) or 0.0
    oi = _f(msg.get("open_interest"))
    vol = _f(msg.get("volume_trade_for_the_day") or row["volume"])

    ts = parse_ts(row["received_at"]).strftime("%Y-%m-%d %H:%M:%S")
    out: dict[str, Any] = {
        "time": ts,
        "ltp": ltp,
        "tbq": tbq,
        "tsq": tsq,
        "net": net,
        "imb_pct": round(imb, 2),
        "ltq": ltq,
        "buy5_sum": buy5,
        "sell5_sum": sell5,
        "depth_net": depth_net,
        "depth_imb_pct": round(depth_imb, 2),
        "oi": oi,
        "volume": vol,
    }
    for i in range(1, 6):
        out[f"buy{i}_qty"] = details.get(f"buy{i}_qty", 0.0)
        out[f"sell{i}_qty"] = details.get(f"sell{i}_qty", 0.0)
    return out


def build_rich_bars(rows, tf_name: str, minutes: int) -> list[RichBar]:
    bars: list[RichBar] = []
    cur_key: datetime | None = None
    o = h = l = c = None
    tbq_o = tsq_o = tbq_c = tsq_c = 0.0
    ltq_sum = 0.0
    n = 0
    buy5 = sell5 = 0.0
    oi = vol = None

    def flush(key: datetime) -> None:
        nonlocal o, h, l, c, tbq_o, tsq_o, tbq_c, tsq_c, ltq_sum, n, buy5, sell5, oi, vol
        if o is None or c is None or h is None or l is None:
            return
        net = tbq_c - tsq_c
        imb = abs(net) / max(tbq_c, tsq_c, 1e-9) * 100.0
        dnet = buy5 - sell5
        dimb = abs(dnet) / max(buy5, sell5, 1e-9) * 100.0
        prev_net = bars[-1].net if bars else None
        prev_close = bars[-1].close if bars else None
        prev_vol = bars[-1].volume_close if bars else None
        bar_vol = None
        if vol is not None and prev_vol is not None:
            bar_vol = max(0.0, float(vol) - float(prev_vol))
        bars.append(
            RichBar(
                tf=tf_name,
                time=key.strftime("%Y-%m-%d %H:%M:%S"),
                open=float(o),
                high=float(h),
                low=float(l),
                close=float(c),
                range_pts=float(h - l),
                n_ticks=n,
                tbq_open=float(tbq_o),
                tbq_close=float(tbq_c),
                tsq_open=float(tsq_o),
                tsq_close=float(tsq_c),
                net=float(net),
                net_delta=(float(net - prev_net) if prev_net is not None else None),
                imb_pct=float(imb),
                price_delta=(float(c - prev_close) if prev_close is not None else None),
                ltq_sum=float(ltq_sum),
                ltq_avg=float(ltq_sum / n) if n else 0.0,
                buy5_sum=float(buy5),
                sell5_sum=float(sell5),
                depth_net=float(dnet),
                depth_imb_pct=float(dimb),
                oi_close=oi,
                volume_close=vol,
                bar_volume=bar_vol,
            )
        )
        o = h = l = c = None
        n = 0
        ltq_sum = 0.0

    for row in rows:
        m = tick_metrics(row)
        if m["ltp"] is None:
            continue
        ts = parse_ts(row["received_at"])
        key = floor_bar(ts, minutes)
        ltp = float(m["ltp"])
        if cur_key is None:
            cur_key = key
        if key != cur_key:
            flush(cur_key)
            cur_key = key
        if o is None:
            o = h = l = c = ltp
            tbq_o = float(m["tbq"])
            tsq_o = float(m["tsq"])
            n = 0
            ltq_sum = 0.0
        h = max(h, ltp)
        l = min(l, ltp)
        c = ltp
        tbq_c = float(m["tbq"])
        tsq_c = float(m["tsq"])
        buy5 = float(m["buy5_sum"])
        sell5 = float(m["sell5_sum"])
        oi = m["oi"]
        vol = m["volume"]
        ltq_sum += float(m["ltq"] or 0.0)
        n += 1
    if cur_key is not None:
        flush(cur_key)
    return bars


def build_rich_bars_by_ticks(rows, tf_name: str, n_per_bar: int) -> list[RichBar]:
    """Aggregate every ``n_per_bar`` ticks into one RichBar (count-based TF)."""
    n_per = max(1, int(n_per_bar))
    bars: list[RichBar] = []
    o = h = l = c = None
    tbq_o = tsq_o = tbq_c = tsq_c = 0.0
    ltq_sum = 0.0
    n = 0
    buy5 = sell5 = 0.0
    oi = vol = None
    bar_time: datetime | None = None

    def flush() -> None:
        nonlocal o, h, l, c, tbq_o, tsq_o, tbq_c, tsq_c, ltq_sum, n, buy5, sell5, oi, vol, bar_time
        if o is None or c is None or h is None or l is None or bar_time is None:
            return
        net = tbq_c - tsq_c
        imb = abs(net) / max(tbq_c, tsq_c, 1e-9) * 100.0
        dnet = buy5 - sell5
        dimb = abs(dnet) / max(buy5, sell5, 1e-9) * 100.0
        prev_net = bars[-1].net if bars else None
        prev_close = bars[-1].close if bars else None
        prev_vol = bars[-1].volume_close if bars else None
        bar_vol = None
        if vol is not None and prev_vol is not None:
            bar_vol = max(0.0, float(vol) - float(prev_vol))
        bars.append(
            RichBar(
                tf=tf_name,
                time=bar_time.strftime("%Y-%m-%d %H:%M:%S"),
                open=float(o),
                high=float(h),
                low=float(l),
                close=float(c),
                range_pts=float(h - l),
                n_ticks=n,
                tbq_open=float(tbq_o),
                tbq_close=float(tbq_c),
                tsq_open=float(tsq_o),
                tsq_close=float(tsq_c),
                net=float(net),
                net_delta=(float(net - prev_net) if prev_net is not None else None),
                imb_pct=float(imb),
                price_delta=(float(c - prev_close) if prev_close is not None else None),
                ltq_sum=float(ltq_sum),
                ltq_avg=float(ltq_sum / n) if n else 0.0,
                buy5_sum=float(buy5),
                sell5_sum=float(sell5),
                depth_net=float(dnet),
                depth_imb_pct=float(dimb),
                oi_close=oi,
                volume_close=vol,
                bar_volume=bar_vol,
            )
        )
        o = h = l = c = None
        n = 0
        ltq_sum = 0.0
        bar_time = None

    for row in rows:
        m = tick_metrics(row)
        if m["ltp"] is None:
            continue
        ts = parse_ts(row["received_at"])
        ltp = float(m["ltp"])
        if o is None:
            o = h = l = c = ltp
            tbq_o = float(m["tbq"])
            tsq_o = float(m["tsq"])
            n = 0
            ltq_sum = 0.0
            bar_time = ts
        h = max(h, ltp)
        l = min(l, ltp)
        c = ltp
        tbq_c = float(m["tbq"])
        tsq_c = float(m["tsq"])
        buy5 = float(m["buy5_sum"])
        sell5 = float(m["sell5_sum"])
        oi = m["oi"]
        vol = m["volume"]
        ltq_sum += float(m["ltq"] or 0.0)
        n += 1
        bar_time = ts  # close timestamp
        if n >= n_per:
            flush()
    # drop incomplete trailing bar (partial count)
    return bars
