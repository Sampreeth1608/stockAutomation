#!/usr/bin/env python3
"""Export tick + MTF bars + trade reports for manual Google Sheets testing.

Writes CSVs under data/manual_pack/ (one file per tab). Optionally uploads
to a Google Spreadsheet if credentials are configured.

Usage:
  python export_mtf_manual_pack.py
  python export_mtf_manual_pack.py --lots 100 --tick-limit 50000
  python export_mtf_manual_pack.py --upload   # needs GOOGLE_* env

Google Sheets (manual import — always works):
  1) Open https://sheets.google.com → Blank spreadsheet
  2) File → Import → Upload each CSV → "Replace current sheet" / "Insert new sheet"
  Suggested tabs: ticks, bars_1m … bars_1h, trades_summary, trades_30m_always, …
"""

from __future__ import annotations

import argparse
import csv
import os
from collections import Counter
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from mtf_bars import INTERVALS, DB, build_rich_bars, load_tick_rows, tick_metrics
from strategy_net_zigzag import NetZigzagConfig, NetZigzagStrategy

OUT_DIR = Path("data/manual_pack")


def fee_rt(ltp: float, lots: float) -> float:
    brokerage = 40.0
    buy = sell = ltp * lots
    txn = 0.000021 * (buy + sell)
    sebi = 0.000001 * (buy + sell)
    stamp = 0.000020 * buy
    ctt = 0.00010 * sell
    gst = 0.18 * (brokerage + txn + sebi)
    return brokerage + txn + sebi + stamp + ctt + gst


def write_csv(path: Path, rows: list[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("")
        return 0
    # stable column order: union in first-seen order
    fields: list[str] = []
    seen = set()
    for r in rows:
        for k in r.keys():
            if k not in seen:
                seen.add(k)
                fields.append(k)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return len(rows)


def sim_trades(
    bar_rows: list[dict[str, Any]],
    cfg: NetZigzagConfig,
    lots: float,
    tf: str,
    mode: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    s = NetZigzagStrategy(cfg)
    trades: list[dict[str, Any]] = []
    side = None
    entry = None
    entry_t = None
    entry_tbq = entry_tsq = None
    reasons: Counter = Counter()

    for br in bar_rows:
        msg = {
            "total_buy_quantity": br["tbq_close"],
            "total_sell_quantity": br["tsq_close"],
        }
        from datetime import datetime as _dt
        from zoneinfo import ZoneInfo

        IST = ZoneInfo("Asia/Kolkata")
        try:
            now = _dt.strptime(br["time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
        except Exception:
            now = _dt.now(IST)
        close = float(br["close"])
        sig = s.on_tick(now, close, msg)
        if not sig:
            continue
        if sig.action in {"BUY", "SHORT"}:
            side = "long" if sig.action == "BUY" else "short"
            entry = close
            entry_t = br["time"]
            entry_tbq = br["tbq_close"]
            entry_tsq = br["tsq_close"]
        elif sig.action == "CLOSE" and side and entry is not None:
            gross = (close - entry) if side == "long" else (entry - close)
            fees = fee_rt(close, lots)
            pnl = gross * lots - fees
            r = sig.reason or ""
            tag = "other"
            if r.startswith("tp"):
                tag = "tp"
            elif r.startswith("sl"):
                tag = "sl"
            elif "weaken" in r:
                tag = "weaken"
            elif "flip" in r:
                tag = "flip"
            reasons[tag] += 1
            trades.append(
                {
                    "tf": tf,
                    "mode": mode,
                    "side": side,
                    "entry_time": entry_t,
                    "exit_time": br["time"],
                    "entry": entry,
                    "exit": close,
                    "gross_pts": round(gross, 2),
                    "fees_₹": round(fees, 2),
                    "pnl_₹": round(pnl, 2),
                    "entry_tbq": entry_tbq,
                    "entry_tsq": entry_tsq,
                    "exit_tbq": br["tbq_close"],
                    "exit_tsq": br["tsq_close"],
                    "exit_net": br["net"],
                    "exit_imb_pct": br["imb_pct"],
                    "reason": r,
                    "exit_tag": tag,
                    "lots": lots,
                }
            )
            side = None
            entry = None

    n = len(trades)
    summary = {
        "tf": tf,
        "mode": mode,
        "n": n,
        "dir_win%": round(sum(1 for t in trades if t["gross_pts"] > 0) / n * 100, 1)
        if n
        else 0.0,
        "net_win%": round(sum(1 for t in trades if t["pnl_₹"] > 0) / n * 100, 1)
        if n
        else 0.0,
        "avg_gross_pts": round(sum(t["gross_pts"] for t in trades) / n, 2) if n else 0.0,
        "sum_pnl_₹": round(sum(t["pnl_₹"] for t in trades), 1) if n else 0.0,
        "tp": reasons.get("tp", 0),
        "sl": reasons.get("sl", 0),
        "weaken": reasons.get("weaken", 0),
        "flip": reasons.get("flip", 0),
        "lots": lots,
        "tp_points": cfg.tp_points,
        "sl_points": cfg.sl_points,
    }
    return trades, summary


def upload_to_google(folder: Path, sheet_id: str, creds_path: str) -> None:
    """Upload each CSV as a worksheet tab. Requires: pip install gspread google-auth"""
    import gspread
    from google.oauth2.service_account import Credentials

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_file(creds_path, scopes=scopes)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(sheet_id)

    csvs = sorted(folder.glob("*.csv"))
    print(f"Uploading {len(csvs)} CSVs → spreadsheet {sheet_id}")
    for path in csvs:
        title = path.stem[:100]
        with path.open(newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f))
        if not rows:
            continue
        try:
            ws = sh.worksheet(title)
            ws.clear()
        except gspread.WorksheetNotFound:
            # Sheets soft limit ~100 tabs / cell caps — create
            rows_n = max(len(rows), 1000)
            cols_n = max(len(rows[0]), 10)
            ws = sh.add_worksheet(title=title, rows=rows_n, cols=cols_n)
        # chunk updates
        ws.update("A1", rows, value_input_option="USER_ENTERED")
        print(f"  tab {title}: {len(rows)-1} data rows")
    print(f"Done. Open: https://docs.google.com/spreadsheets/d/{sheet_id}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Export MTF manual pack for Google Sheets")
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--out", default=str(OUT_DIR))
    ap.add_argument("--lots", type=float, default=100.0)
    ap.add_argument("--tp", type=float, default=25.0)
    ap.add_argument("--sl", type=float, default=20.0)
    ap.add_argument(
        "--tick-limit",
        type=int,
        default=None,
        help="Limit tick rows exported (default: all). Bars always use full history.",
    )
    ap.add_argument(
        "--upload",
        action="store_true",
        help="Upload CSVs to Google Sheet (needs GOOGLE_SERVICE_ACCOUNT_JSON + GOOGLE_SHEET_ID)",
    )
    args = ap.parse_args()

    db = Path(args.db)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    print("Loading ticks...")
    rows = load_tick_rows(db)
    print(f"ticks={len(rows)}")

    # --- ticks sheet ---
    tick_rows = []
    src = rows[-args.tick_limit :] if args.tick_limit else rows
    prev_net = None
    prev_ltp = None
    for row in src:
        m = tick_metrics(row)
        if m["ltp"] is None:
            continue
        net = m["net"]
        m["net_delta"] = (net - prev_net) if prev_net is not None else None
        m["price_delta"] = (m["ltp"] - prev_ltp) if prev_ltp is not None else None
        prev_net = net
        prev_ltp = m["ltp"]
        tick_rows.append(m)
    n = write_csv(out / "ticks.csv", tick_rows)
    print(f"wrote ticks.csv ({n})")

    # --- bars per TF ---
    all_bar_rows: dict[str, list[dict[str, Any]]] = {}
    for tf, mins in INTERVALS:
        bars = build_rich_bars(rows, tf, mins)
        brows = [b.to_row() for b in bars]
        all_bar_rows[tf] = brows
        n = write_csv(out / f"bars_{tf}.csv", brows)
        print(f"wrote bars_{tf}.csv ({n})")

    # --- trade reports ---
    summaries: list[dict[str, Any]] = []
    modes = [
        (
            "gated_both_cd2",
            NetZigzagConfig(
                tp_points=args.tp,
                sl_points=args.sl,
                entry_mode="both",
                cooldown_ticks=2,
                use_fee_gate=False,
            ),
        ),
        (
            "always",
            NetZigzagConfig(
                tp_points=args.tp,
                sl_points=args.sl,
                entry_mode="always",
                cooldown_ticks=0,
                use_fee_gate=False,
            ),
        ),
    ]
    for tf, _ in INTERVALS:
        for mode_name, cfg in modes:
            trades, summary = sim_trades(
                all_bar_rows[tf], cfg, args.lots, tf, mode_name
            )
            summaries.append(summary)
            n = write_csv(out / f"trades_{tf}_{mode_name}.csv", trades)
            print(f"wrote trades_{tf}_{mode_name}.csv ({n})")

    write_csv(out / "trades_summary.csv", summaries)
    print(f"wrote trades_summary.csv ({len(summaries)})")

    # README for Google Sheets import
    readme = out / "GOOGLE_SHEETS_IMPORT.txt"
    readme.write_text(
        f"""Goldpetal manual pack — import into Google Sheets
Generated: {datetime.now().isoformat(timespec='seconds')}
Lots={args.lots} TP={args.tp} SL={args.sl}

Files:
  ticks.csv              — tick LTP, TBQ, TSQ, NET, IMB%, LTQ, buy1-5/sell1-5
  bars_1m.csv … bars_1h.csv — OHLCV + TBQ/TSQ open/close + NET/IMB + depth + LTQ
  trades_summary.csv     — PnL summary per TF × mode
  trades_<tf>_<mode>.csv — individual trade blotter for manual review

Manual import (no API keys):
  1. Go to https://sheets.google.com and create a blank spreadsheet
  2. File → Import → Upload → select a CSV
  3. Import location: "Insert new sheet(s)"
  4. Repeat for each CSV (or zip-upload if using Drive then Open with Sheets)

Optional API upload from VM:
  pip install gspread google-auth
  export GOOGLE_SERVICE_ACCOUNT_JSON=/path/to/sa.json
  export GOOGLE_SHEET_ID=your_spreadsheet_id
  Share the sheet with the service-account email (Editor)
  python export_mtf_manual_pack.py --upload
""",
        encoding="utf-8",
    )
    print(f"wrote {readme}")

    # print summary table
    print()
    print(f"{'tf':>4} {'mode':18} {'n':>4} {'dir%':>6} {'avgG':>7} {'sum₹':>10}")
    print("-" * 60)
    for s in summaries:
        print(
            f"{s['tf']:>4} {s['mode']:18} {s['n']:4} {s['dir_win%']:6.1f} "
            f"{s['avg_gross_pts']:7.2f} {s['sum_pnl_₹']:10.1f}"
        )

    print()
    print(f"Pack ready: {out.resolve()}")
    print("Open GOOGLE_SHEETS_IMPORT.txt for import steps.")

    if args.upload:
        creds = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
        sheet_id = os.getenv("GOOGLE_SHEET_ID", "").strip()
        if not creds or not sheet_id:
            print(
                "ERROR: set GOOGLE_SERVICE_ACCOUNT_JSON and GOOGLE_SHEET_ID for --upload"
            )
            return
        upload_to_google(out, sheet_id, creds)


if __name__ == "__main__":
    main()
