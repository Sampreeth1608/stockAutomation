#!/usr/bin/env python3
"""Pull Angel/MCX Gold Petal candles and print S14 formula + decision per bar.

  python3 explain_s14_candles.py --tf 30m,1h,1d
  python3 explain_s14_candles.py --tf 30m --from 2026-08-02
  python3 explain_s14_candles.py --from-ticks --db data/ticks.db --tf 30m

Exchange intervals Angel actually has: 1m 3m 5m 10m 15m 30m 1h 1d.
45m / 2h / 3h are not on the exchange chart (those were tick-built).
"""

from __future__ import annotations

import argparse
import csv
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from strategy_wick import s14_bar_decision
from wick_candles import wick_measure

IST = ZoneInfo("Asia/Kolkata")

ANGEL_INTERVAL: dict[str, str] = {
    "1m": "ONE_MINUTE",
    "3m": "THREE_MINUTE",
    "5m": "FIVE_MINUTE",
    "10m": "TEN_MINUTE",
    "15m": "FIFTEEN_MINUTE",
    "30m": "THIRTY_MINUTE",
    "1h": "ONE_HOUR",
    "1d": "ONE_DAY",
}
TF_MINUTES: dict[str, int] = {
    "1m": 1,
    "3m": 3,
    "5m": 5,
    "10m": 10,
    "15m": 15,
    "30m": 30,
    "1h": 60,
    "1d": 1440,
}

FORMULA = (
    "upper = high − max(open, close)   lower = min(open, close) − low\n"
    "same finished candle: open=high → SHORT | open=low → LONG | "
    "both → wick | else wick (L>U LONG, U>L SHORT, equal skip)"
)


def parse_bar_ts(raw: str) -> datetime:
    s = str(raw).strip().replace("Z", "+00:00")
    if " " in s[:19] and "T" not in s[:19]:
        s = s.replace(" ", "T", 1)
    dt = datetime.fromisoformat(s[:32])
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    return dt.astimezone(IST)


def in_session_dt(
    dt: datetime, *, tf: str, open_hhmm: str = "09:00", close_hhmm: str = "23:30"
) -> bool:
    if dt.weekday() >= 5:
        return False
    if tf == "1d":
        return True
    oh, om = (int(x) for x in open_hhmm.split(":"))
    ch, cm = (int(x) for x in close_hhmm.split(":"))
    start = dt.replace(hour=oh, minute=om, second=0, microsecond=0)
    end = dt.replace(hour=ch, minute=cm, second=0, microsecond=0)
    return start <= dt <= end


def explain_bar(
    *,
    time: str,
    o: float,
    h: float,
    l: float,
    c: float,
    pos: str,
    open_hold: bool = True,
) -> dict[str, Any]:
    m = wick_measure(o, h, l, c)
    oh = float(h) == float(o)
    ol = float(l) == float(o)
    side, why = s14_bar_decision(o, h, l, c, open_hold=open_hold)
    prev = pos
    action = "skip"
    pos_after = pos
    if side is None:
        action = "skip"
    elif side == pos:
        action = "hold"
    else:
        action = "FLIP" if pos != "flat" else "enter"
        pos_after = side
    return {
        "time": time,
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "upper": m.upper,
        "lower": m.lower,
        "open_eq_high": oh,
        "open_eq_low": ol,
        "rule": why,
        "side": side or "skip",
        "action": action,
        "pos_before": prev,
        "pos_after": pos_after,
    }


def format_line(row: dict[str, Any]) -> str:
    oh = "Y" if row["open_eq_high"] else "N"
    ol = "Y" if row["open_eq_low"] else "N"
    side = str(row["side"]).upper()
    return (
        f"{row['time']:<22} "
        f"O={row['open']:<8.1f} H={row['high']:<8.1f} "
        f"L={row['low']:<8.1f} C={row['close']:<8.1f}  "
        f"U={row['upper']:<7.1f} Lwick={row['lower']:<7.1f}  "
        f"O=H {oh}  O=L {ol}  "
        f"{row['rule']:<28} → {side:<5}  {row['action']:<5}  "
        f"{row['pos_before']}→{row['pos_after']}"
    )


def walk_candles(
    candles: list[dict[str, Any]], *, open_hold: bool = True
) -> list[dict[str, Any]]:
    pos = "flat"
    out: list[dict[str, Any]] = []
    for bar in candles:
        row = explain_bar(
            time=str(bar["time"]),
            o=float(bar["open"]),
            h=float(bar["high"]),
            l=float(bar["low"]),
            c=float(bar["close"]),
            pos=pos,
            open_hold=open_hold,
        )
        pos = str(row["pos_after"])
        out.append(row)
    return out


def _parse_angel_row(row: Any) -> dict[str, Any] | None:
    if isinstance(row, dict):
        ts = row.get("timestamp") or row.get("time") or row.get("datetime")
        o, h, l, c = row.get("open"), row.get("high"), row.get("low"), row.get("close")
        if ts is None or o is None:
            return None
        dt = parse_bar_ts(str(ts))
        return {
            "time": dt.strftime("%Y-%m-%d %H:%M:%S"),
            "open": float(o),
            "high": float(h),
            "low": float(l),
            "close": float(c),
        }
    if not isinstance(row, (list, tuple)) or len(row) < 5:
        return None
    dt = parse_bar_ts(str(row[0]))
    return {
        "time": dt.strftime("%Y-%m-%d %H:%M:%S"),
        "open": float(row[1]),
        "high": float(row[2]),
        "low": float(row[3]),
        "close": float(row[4]),
    }


def _chunks(start: datetime, end: datetime, days: int):
    cur = start
    while cur < end:
        nxt = min(cur + timedelta(days=days), end)
        yield cur, nxt
        cur = nxt


def fetch_angel_candles(
    api: Any,
    *,
    token: str,
    interval: str,
    start: datetime,
    end: datetime,
    exchange: str = "MCX",
) -> list[dict[str, Any]]:
    chunk_days = 7 if interval == "ONE_MINUTE" else 28
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for a, b in _chunks(start, end, chunk_days):
        payload = {
            "exchange": exchange,
            "symboltoken": str(token),
            "interval": interval,
            "fromdate": a.strftime("%Y-%m-%d %H:%M"),
            "todate": b.strftime("%Y-%m-%d %H:%M"),
        }
        data = api.getCandleData(payload)
        if not data or data.get("status") is False:
            msg = data.get("message") if isinstance(data, dict) else data
            raise RuntimeError(f"getCandleData failed {interval} {a}→{b}: {msg}")
        rows = data.get("data") or []
        for raw in rows:
            bar = _parse_angel_row(raw)
            if bar is None or bar["time"] in seen:
                continue
            seen.add(bar["time"])
            out.append(bar)
        time.sleep(0.35)
    out.sort(key=lambda r: r["time"])
    return out


def candles_from_ticks(db: Path, minutes: int) -> list[dict[str, Any]]:
    from backtest_hhhl_candles import candles_from_rich
    from mtf_bars import build_rich_bars, load_tick_rows

    bars = build_rich_bars(load_tick_rows(db), f"{minutes}m", minutes)
    return [
        {
            "time": c.time,
            "open": c.open,
            "high": c.high,
            "low": c.low,
            "close": c.close,
        }
        for c in candles_from_rich(bars)
    ]


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "time",
        "open",
        "high",
        "low",
        "close",
        "upper",
        "lower",
        "open_eq_high",
        "open_eq_low",
        "rule",
        "side",
        "action",
        "pos_before",
        "pos_after",
    ]
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def _parse_tfs(raw: str) -> list[str]:
    out: list[str] = []
    for name in (x.strip().lower() for x in raw.split(",") if x.strip()):
        if name == "30":
            name = "30m"
        if name in {"d", "day", "daily"}:
            name = "1d"
        if name not in ANGEL_INTERVAL:
            raise SystemExit(
                f"unknown/unsupported exchange tf {name!r}. "
                f"Angel has: {', '.join(ANGEL_INTERVAL)}"
            )
        out.append(name)
    if not out:
        raise SystemExit("no timeframes")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tf", default="30m,1h,1d", help="comma list of Angel intervals")
    ap.add_argument("--from", dest="date_from", default="2026-08-02")
    ap.add_argument("--to", dest="date_to", default="")
    ap.add_argument("--from-ticks", action="store_true")
    ap.add_argument("--db", type=Path, default=Path("data/ticks.db"))
    ap.add_argument("--session", action="store_true", default=True)
    ap.add_argument("--no-session", action="store_false", dest="session")
    ap.add_argument("--open-hold", type=float, default=2.0, help=">0 same-candle open=high/low")
    ap.add_argument("--out-dir", type=Path, default=Path("data/backtests/s14_candles"))
    ap.add_argument("--token", default="")
    ap.add_argument("--symbol", default="")
    args = ap.parse_args()

    tfs = _parse_tfs(args.tf)
    start = datetime.strptime(args.date_from, "%Y-%m-%d").replace(tzinfo=IST)
    if args.date_to:
        end = datetime.strptime(args.date_to, "%Y-%m-%d").replace(
            hour=23, minute=30, tzinfo=IST
        )
    else:
        end = datetime.now(IST)
    open_hold = float(args.open_hold) > 0

    print(FORMULA, flush=True)
    print(
        f"range {start.strftime('%Y-%m-%d %H:%M')} → {end.strftime('%Y-%m-%d %H:%M')} IST  "
        f"open_hold={open_hold}  source={'ticks' if args.from_ticks else 'Angel MCX'}",
        flush=True,
    )

    contract: dict[str, Any] = {}
    api = None
    if not args.from_ticks:
        from auth import login
        from symbols import find_goldpetal_futures

        contract = find_goldpetal_futures()
        token = str(args.token or contract["token"])
        symbol = str(args.symbol or contract["symbol"])
        print(
            f"contract {symbol} token={token} expiry={contract.get('expiry', '-')}",
            flush=True,
        )
        session = login()
        api = session.api
    else:
        token = args.token or "ticks"
        symbol = args.symbol or "GOLDPETAL_TICKS"
        print(f"ticks db={args.db}", flush=True)

    for tf in tfs:
        if args.from_ticks:
            raw = candles_from_ticks(args.db, TF_MINUTES[tf])
        else:
            assert api is not None
            raw = fetch_angel_candles(
                api,
                token=str(args.token or contract["token"]),
                interval=ANGEL_INTERVAL[tf],
                start=start,
                end=end,
                exchange=str(contract.get("exchange") or "MCX"),
            )
        if args.session:
            bars = [
                b
                for b in raw
                if in_session_dt(parse_bar_ts(b["time"]), tf=tf)
            ]
        else:
            bars = raw
        rows = walk_candles(bars, open_hold=open_hold)
        n_enter = sum(1 for r in rows if r["action"] in {"enter", "FLIP"})
        print(flush=True)
        print(
            f"=== {tf}  bars={len(rows)}  decisions={n_enter}  {symbol} ===",
            flush=True,
        )
        print(
            f"{'time':<22} O H L C   upper  lower   O=H O=L  rule → SIDE  action  pos",
            flush=True,
        )
        for row in rows:
            print(format_line(row), flush=True)
        out = args.out_dir / f"{tf}_{symbol}.csv"
        write_csv(out, rows)
        print(f"wrote {out}", flush=True)


if __name__ == "__main__":
    main()
