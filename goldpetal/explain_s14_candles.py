#!/usr/bin/env python3
"""Pull Angel/MCX Gold Petal candles, print S14 per bar, and settle PnL.

  ./venv/bin/python explain_s14_candles.py --tf 30m,1h,1d --from 2026-08-02
  ./venv/bin/python explain_s14_candles.py --from-csv data/backtests/s14_candles --tf 30m,1h,1d
  python3 explain_s14_candles.py --from-ticks --db data/ticks.db --tf 30m

Fill: enter/FLIP at that finished bar's close. Leftover flattened at the last
finished close. Default 100 lots + Angel fees + 30% tax (same tape as the tick
replay). Skips Angel's still-forming last bar.

Exchange intervals Angel actually has: 1m 3m 5m 10m 15m 30m 1h 1d.
45m / 2h / 3h are not on the exchange chart (those were tick-built).
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from backtest_hhhl_candles import (
    TfResult,
    Trade,
    make_charge_cfg,
    print_by_day,
    write_outputs,
)
from charges import ChargeConfig, apply_charges_and_tax
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


def bar_is_finished(time_s: str, tf: str, now: datetime | None = None) -> bool:
    """Angel includes the in-progress bar. S14 only decides on a closed candle."""
    start = parse_bar_ts(time_s)
    now = now or datetime.now(IST)
    if tf == "1d":
        end = start.replace(hour=23, minute=30, second=0, microsecond=0)
        if end <= start:
            end = start + timedelta(days=1)
        return now >= end
    return now >= start + timedelta(minutes=TF_MINUTES[tf])


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


def _book_side(side: str | None) -> str | None:
    if side is None:
        return None
    s = str(side).strip().lower()
    if s in {"", "skip", "flat", "none"}:
        return None
    if s == "long":
        return "LONG"
    if s == "short":
        return "SHORT"
    raise ValueError(f"unknown side {side!r}")


def _close_leg_at(
    *,
    tf: str,
    side: str,
    entry_time: str,
    entry_px: float,
    exit_time: str,
    exit_px: float,
    cfg: ChargeConfig,
) -> Trade:
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
    return Trade(
        tf=tf,
        side=side,
        entry_time=entry_time,
        entry_px=entry_px,
        exit_time=exit_time,
        exit_px=exit_px,
        gross_pts=float(pts) * float(cfg.lot_size),
        gross_pnl_inr=float(settled["gross_pnl"]),
        after_tax_pnl_inr=float(settled["pnl_after_tax"]),
        fees_inr=float(settled["charges"]),
        lots=float(cfg.lot_size),
    )


def settle_s14_from_walk(
    rows: list[dict[str, Any]],
    *,
    tf: str,
    lots: float = 100.0,
    fees: bool = True,
    charge_cfg: ChargeConfig | None = None,
) -> TfResult:
    """Fill enter/FLIP at the signal bar's close. Flatten leftover at last close."""
    from backtest_wick_candles import _tf_result_from_trades

    cfg = charge_cfg or make_charge_cfg(fees=fees, lots=lots)
    trades: list[Trade] = []
    book: str | None = None
    entry_px = 0.0
    entry_time = ""

    def flatten(when: str, px: float) -> None:
        nonlocal book, entry_px, entry_time
        if book is None:
            return
        trades.append(
            _close_leg_at(
                tf=tf,
                side=book,
                entry_time=entry_time,
                entry_px=entry_px,
                exit_time=when,
                exit_px=px,
                cfg=cfg,
            )
        )
        book = None

    for row in rows:
        when = str(row["time"])
        px = float(row["close"])
        if str(row["action"]) not in {"enter", "FLIP"}:
            continue
        want = _book_side(row.get("side"))
        if want is None:
            continue
        if book is not None:
            flatten(when, px)
        book = want
        entry_px = px
        entry_time = when

    if rows:
        last = rows[-1]
        flatten(str(last["time"]), float(last["close"]))
    return _tf_result_from_trades(tf, len(rows), trades)


def print_pnl_row(r: TfResult) -> None:
    print(
        f"{r.tf:>22}  bars={r.n_bars:5d}  trades={r.n_trades:6d}  "
        f"{r.n_long}/{r.n_short}  win={100 * r.win_rate:5.1f}%  "
        f"pts={r.gross_pts:10.1f}  pnl={r.after_tax_pnl_inr:12.1f}  "
        f"fees={r.fees_inr:10.1f}  dd={r.max_dd_inr:12.1f}",
        flush=True,
    )


def format_trade_line(t: Trade) -> str:
    lots = float(t.lots) or 1.0
    pts_per_lot = float(t.gross_pts) / lots
    return (
        f"  {t.side:<5}  {t.entry_time} @{t.entry_px:.1f}  →  "
        f"{t.exit_time} @{t.exit_px:.1f}  "
        f"pts={pts_per_lot:+.1f}/lot  "
        f"gross={t.gross_pnl_inr:+.0f}  fees={t.fees_inr:.0f}  "
        f"after_tax={t.after_tax_pnl_inr:+.0f}"
    )


def candles_from_walk_csv(path: Path) -> list[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8") as f:
        raw = list(csv.DictReader(f))
    out: list[dict[str, Any]] = []
    for row in raw:
        out.append(
            {
                "time": str(row["time"]),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
            }
        )
    return out


def find_tf_csv(folder: Path, tf: str) -> Path | None:
    if folder.is_file():
        return folder
    hits = [
        p
        for p in sorted(folder.glob(f"{tf}_*.csv"))
        if "trades" not in p.name.lower()
    ]
    if hits:
        return hits[0]
    direct = folder / f"{tf}.csv"
    return direct if direct.is_file() else None


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


def _has_angel_login() -> bool:
    try:
        import pyotp  # noqa: F401
        from SmartApi import SmartConnect  # noqa: F401

        return True
    except ImportError:
        return False


def bot_python_candidates() -> list[Path]:
    here = Path(__file__).resolve().parent
    names = [
        here / "venv" / "bin" / "python",
        here / ".venv" / "bin" / "python",
        here.parent / "venv" / "bin" / "python",
        here.parent / ".venv" / "bin" / "python",
        Path.home() / "goldpetal" / "venv" / "bin" / "python",
        Path.home() / "goldpetal" / ".venv" / "bin" / "python",
    ]
    out: list[Path] = []
    seen: set[Path] = set()
    for p in names:
        if not p.is_file():
            continue
        try:
            r = p.resolve()
        except OSError:
            continue
        if r in seen:
            continue
        seen.add(r)
        out.append(p)
    return out


def reexec_with_bot_python() -> None:
    """System python3 on the VM often has no pyotp; the bot venv does."""
    if _has_angel_login():
        return
    me = Path(sys.executable).resolve()
    for cand in bot_python_candidates():
        if cand.resolve() == me:
            continue
        os.execv(str(cand), [str(cand), *sys.argv])
    checked = "\n".join(f"  {p}" for p in bot_python_candidates()) or "  (none found)"
    raise SystemExit(
        "Angel login needs pyotp + SmartApi. This python does not have them.\n"
        "Use the same interpreter as the bot:\n"
        "  ./venv/bin/python explain_s14_candles.py --tf 30m,1h,1d --from 2026-08-02\n"
        "  ./.venv/bin/python explain_s14_candles.py --tf 30m,1h,1d --from 2026-08-02\n"
        f"checked:\n{checked}\n"
        "Or dump tick-built bars (not the exchange chart):\n"
        "  python3 explain_s14_candles.py --from-ticks --db data/ticks.db --tf 30m,1h,1d"
    )


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
    ap.add_argument(
        "--from-csv",
        type=Path,
        default=None,
        help="settle an existing dump dir (or one CSV) without Angel login",
    )
    ap.add_argument("--db", type=Path, default=Path("data/ticks.db"))
    ap.add_argument("--session", action="store_true", default=True)
    ap.add_argument("--no-session", action="store_false", dest="session")
    ap.add_argument("--open-hold", type=float, default=2.0, help=">0 same-candle open=high/low")
    ap.add_argument("--lots", type=float, default=100.0)
    ap.add_argument(
        "--fees",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Angel fees + 30%% tax (default on; --no-fees for gross)",
    )
    ap.add_argument(
        "--pnl",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="print close-fill PnL after the bar dump (default on)",
    )
    ap.add_argument(
        "--print-bars",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    ap.add_argument(
        "--print-trades",
        action="store_true",
        help="print every fill (default: only when trades ≤ 20)",
    )
    ap.add_argument("--out-dir", type=Path, default=Path("data/backtests/s14_candles"))
    ap.add_argument("--token", default="")
    ap.add_argument("--symbol", default="")
    args = ap.parse_args()
    if args.from_csv and args.from_ticks:
        raise SystemExit("use either --from-csv or --from-ticks, not both")
    if not args.from_ticks and args.from_csv is None:
        reexec_with_bot_python()

    tfs = _parse_tfs(args.tf)
    start = datetime.strptime(args.date_from, "%Y-%m-%d").replace(tzinfo=IST)
    if args.date_to:
        end = datetime.strptime(args.date_to, "%Y-%m-%d").replace(
            hour=23, minute=30, tzinfo=IST
        )
    else:
        end = datetime.now(IST)
    open_hold = float(args.open_hold) > 0
    if args.from_csv:
        source = f"csv {args.from_csv}"
    elif args.from_ticks:
        source = "ticks"
    else:
        source = "Angel MCX"

    print(FORMULA, flush=True)
    print(
        "Fill at signal-bar close. Leftover flattened at last finished close. "
        f"lots={args.lots:g}  fees={args.fees}",
        flush=True,
    )
    print(
        f"range {start.strftime('%Y-%m-%d %H:%M')} → {end.strftime('%Y-%m-%d %H:%M')} IST  "
        f"open_hold={open_hold}  source={source}",
        flush=True,
    )

    contract: dict[str, Any] = {}
    api = None
    if args.from_csv is not None:
        token = args.token or "csv"
        symbol = args.symbol or "GOLDPETAL"
        print(f"csv {args.from_csv}", flush=True)
    elif not args.from_ticks:
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

    results: list[TfResult] = []
    now = datetime.now(IST)
    for tf in tfs:
        if args.from_csv is not None:
            path = find_tf_csv(args.from_csv, tf)
            if path is None:
                print(f"=== {tf}  missing CSV under {args.from_csv} ===", flush=True)
                continue
            raw = candles_from_walk_csv(path)
            symbol = args.symbol or path.stem.replace(f"{tf}_", "", 1)
        elif args.from_ticks:
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
        bars = [b for b in bars if bar_is_finished(b["time"], tf, now)]
        rows = walk_candles(bars, open_hold=open_hold)
        n_enter = sum(1 for r in rows if r["action"] in {"enter", "FLIP"})
        print(flush=True)
        print(
            f"=== {tf}  bars={len(rows)}  decisions={n_enter}  {symbol} ===",
            flush=True,
        )
        if args.print_bars:
            print(
                f"{'time':<22} O H L C   upper  lower   O=H O=L  rule → SIDE  action  pos",
                flush=True,
            )
            for row in rows:
                print(format_line(row), flush=True)
        out = args.out_dir / f"{tf}_{symbol}.csv"
        write_csv(out, rows)
        print(f"wrote {out}", flush=True)
        if not args.pnl:
            continue
        settled = settle_s14_from_walk(
            rows,
            tf=f"{tf}:s14",
            lots=args.lots,
            fees=args.fees,
        )
        results.append(settled)
        print_pnl_row(settled)
        show_trades = args.print_trades or settled.n_trades <= 20
        if show_trades and settled.trades:
            print("  fills (signal close → next opposite close / last close):", flush=True)
            for t in settled.trades:
                print(format_trade_line(t), flush=True)

    if results:
        print(flush=True)
        print_by_day(results)
        write_outputs(results, args.out_dir)


if __name__ == "__main__":
    main()
