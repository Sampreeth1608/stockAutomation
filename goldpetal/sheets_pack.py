"""Rich Google Sheets pack for Gold Petal paper review.

Sheets is better for reading trades / PnL on phone or laptop.
The control panel stays the place for emergency stop, live unlock,
and weekend Approve → paper/live (Sheets cannot replace those safely).

CLI:
  python3 sheets_pack.py
  python3 sheets_pack.py --out-dir data/sheets_pack

Writes a dated folder + ZIP with:
  scoreboard.csv      — per-strategy after-tax summary
  trades_all.csv      — every paper trade (entry/exit/fees/tax)
  trades_<strat>.csv  — one file per strategy
  open_positions.csv  — currently open paper positions
  signals_recent.csv  — latest signals
  control_status.csv  — read-only snapshot (emergency/live/approvals)
  README.txt          — import steps for Google Sheets
"""

from __future__ import annotations

import argparse
import csv
import io
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from capital import capital_snapshot
from control_state import entries_blocked, is_live_mode_allowed, load_state
from paper_report import _summarize
from proposals import proposals_snapshot
from charges import paper_lots
from storage import (
    TRADE_CSV_FIELDS,
    DB_PATH,
    build_trades,
    latest_signals,
)

IST = ZoneInfo("Asia/Kolkata")

STRATEGIES = (
    "S1_NETDELTA",
    "S2_BALANCE",
    "S3_ML",
    "S4_OVERNIGHT",
    "S5_MINEDGE",
    "S6_MIN30",
    "S8_NET_ZIGZAG",
    "S9_STATE30",
    "S10_LEGACY30",
    "S13_HHHL_DAY",
    "S16_HHHL_WICK_1H",
    "S18_OHLC_VOL_HTF",
    "S19_BODY_CLOSE_1H",
    "S20_FADE_HL",
    "S21_AMISE",
    "S22_AMISE",
    "S23_AMISE",
    "S24_AMISE",
)

SCORE_FIELDS = [
    "strategy",
    "trades",
    "closed",
    "open",
    "wins",
    "losses",
    "win_rate",
    "gross_pnl",
    "charges",
    "pnl_after_charges",
    "tax",
    "pnl_after_tax",
    "avg_win",
    "avg_loss",
]

OPEN_FIELDS = [
    "strategy",
    "symbol",
    "side",
    "status",
    "entry_ts",
    "entry_price",
    "entry_reason",
    "entry_net",
]

SIGNAL_FIELDS = [
    "time_label",
    "strategy",
    "action",
    "position_after",
    "cmp",
    "price_delta",
    "net",
    "net_delta",
    "reason",
]

CONTROL_FIELDS = ["key", "value"]


def _now_stamp() -> str:
    return datetime.now(IST).strftime("%Y%m%d_%H%M%S")


def _write_csv(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})
    return len(rows)


def _csv_bytes(fields: list[str], rows: list[dict[str, Any]]) -> bytes:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({k: r.get(k, "") for k in fields})
    return buf.getvalue().encode("utf-8")


def build_scoreboard_rows() -> list[dict[str, Any]]:
    rows = [_summarize(s) for s in STRATEGIES]
    rows.append(_summarize(None))
    return rows


def build_open_rows(*, db_path: Path = DB_PATH) -> list[dict[str, Any]]:
    trades = build_trades(strategy=None, db_path=db_path, lot_size=paper_lots())
    return [t for t in trades if t.get("status") == "OPEN"]


def build_signal_rows(limit: int = 200, *, db_path: Path = DB_PATH) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in latest_signals(limit=limit, db_path=db_path):
        out.append({k: r[k] for k in r.keys()})
    return out


def build_control_rows() -> list[dict[str, str]]:
    st = load_state()
    blocked, why = entries_blocked()
    live_ok, live_why = is_live_mode_allowed()
    cap = capital_snapshot()
    props = proposals_snapshot()
    rows = [
        {"key": "updated_at_ist", "value": datetime.now(IST).isoformat(timespec="seconds")},
        {"key": "emergency_off", "value": str(st.emergency_off)},
        {"key": "trading_enabled", "value": str(st.trading_enabled)},
        {"key": "live_unlocked", "value": str(st.live_unlocked)},
        {"key": "entries_blocked", "value": f"{blocked}:{why}"},
        {"key": "live_allowed", "value": f"{live_ok}:{live_why}"},
        {"key": "paper_approved", "value": ",".join(st.paper_approved) or "-"},
        {"key": "live_approved", "value": ",".join(st.live_approved) or "-"},
        {"key": "force_disabled", "value": ",".join(st.force_disabled) or "-"},
        {"key": "today_pnl_inr", "value": str(cap.get("today_pnl_inr", ""))},
        {"key": "deployable_inr", "value": str(cap.get("deployable_inr", ""))},
        {
            "key": "proposals_pending",
            "value": str((props.get("counts") or {}).get("pending", 0)),
        },
        {
            "key": "note",
            "value": (
                "Sheets = review only. Use control panel for emergency / live unlock / Approve."
            ),
        },
    ]
    return rows


def readme_text(stamp: str, counts: dict[str, int]) -> str:
    return f"""Gold Petal → Google Sheets pack
Generated (IST): {stamp}

Files
-----
scoreboard.csv       Per-strategy paper PnL (fees + tax)
trades_all.csv       All paper trades
trades_<strategy>.csv Split by strategy
open_positions.csv   Open paper positions right now
signals_recent.csv   Latest BUY/SHORT/CLOSE signals
control_status.csv   Read-only bot status snapshot

Import into Google Sheets
-------------------------
1. Open https://sheets.google.com → Blank spreadsheet
2. File → Import → Upload → pick a CSV (start with scoreboard.csv)
3. Import location: "Replace spreadsheet" or "Insert new sheet"
4. Repeat for trades_all.csv (Insert new sheet(s))
5. Optional: Data → Create a filter on trades_all for strategy / date

Safer / richer?
---------------
Richer for reading PnL on phone/laptop: YES
Safer for kill-switch / live unlock: NO — keep control panel for that
Paper vs live: this pack is from signals DB (paper journal). Live Angel
orders only appear after DRY_RUN=false + Unlock live + Approve → live.

Counts: {counts}
"""


def write_sheets_pack(
    out_dir: Path,
    *,
    db_path: Path = DB_PATH,
    signal_limit: int = 200,
) -> dict[str, Any]:
    """Write folder + ZIP. Returns metadata including zip path."""
    stamp = _now_stamp()
    folder = out_dir / f"pack_{stamp}"
    folder.mkdir(parents=True, exist_ok=True)

    score = build_scoreboard_rows()
    all_trades = build_trades(strategy=None, db_path=db_path, lot_size=paper_lots())
    opens = [t for t in all_trades if t.get("status") == "OPEN"]
    signals = build_signal_rows(limit=signal_limit, db_path=db_path)
    control = build_control_rows()

    _write_csv(folder / "scoreboard.csv", SCORE_FIELDS, score)
    _write_csv(folder / "trades_all.csv", TRADE_CSV_FIELDS, all_trades)
    _write_csv(folder / "open_positions.csv", OPEN_FIELDS, opens)
    _write_csv(folder / "signals_recent.csv", SIGNAL_FIELDS, signals)
    _write_csv(folder / "control_status.csv", CONTROL_FIELDS, control)

    by_strat: dict[str, list[dict[str, Any]]] = {}
    for t in all_trades:
        name = str(t.get("strategy") or "UNKNOWN")
        by_strat.setdefault(name, []).append(t)
    for name, rows in sorted(by_strat.items()):
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
        _write_csv(folder / f"trades_{safe}.csv", TRADE_CSV_FIELDS, rows)

    counts = {
        "trades": len(all_trades),
        "open": len(opens),
        "signals": len(signals),
        "strategies": len(by_strat),
    }
    (folder / "README.txt").write_text(readme_text(stamp, counts), encoding="utf-8")

    zip_path = out_dir / f"goldpetal_sheets_{stamp}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(folder.iterdir()):
            if p.is_file():
                zf.write(p, arcname=p.name)

    return {
        "stamp": stamp,
        "folder": str(folder),
        "zip": str(zip_path),
        "counts": counts,
    }


def sheets_pack_zip_bytes(*, db_path: Path = DB_PATH, signal_limit: int = 200) -> bytes:
    """In-memory ZIP for control-panel download."""
    score = build_scoreboard_rows()
    all_trades = build_trades(strategy=None, db_path=db_path, lot_size=paper_lots())
    opens = [t for t in all_trades if t.get("status") == "OPEN"]
    signals = build_signal_rows(limit=signal_limit, db_path=db_path)
    control = build_control_rows()
    stamp = _now_stamp()
    counts = {
        "trades": len(all_trades),
        "open": len(opens),
        "signals": len(signals),
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("scoreboard.csv", _csv_bytes(SCORE_FIELDS, score))
        zf.writestr("trades_all.csv", _csv_bytes(TRADE_CSV_FIELDS, all_trades))
        zf.writestr("open_positions.csv", _csv_bytes(OPEN_FIELDS, opens))
        zf.writestr("signals_recent.csv", _csv_bytes(SIGNAL_FIELDS, signals))
        zf.writestr("control_status.csv", _csv_bytes(CONTROL_FIELDS, control))
        by_strat: dict[str, list[dict[str, Any]]] = {}
        for t in all_trades:
            name = str(t.get("strategy") or "UNKNOWN")
            by_strat.setdefault(name, []).append(t)
        for name, rows in sorted(by_strat.items()):
            safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
            zf.writestr(f"trades_{safe}.csv", _csv_bytes(TRADE_CSV_FIELDS, rows))
        zf.writestr("README.txt", readme_text(stamp, counts))
    return buf.getvalue()


def main() -> None:
    ap = argparse.ArgumentParser(description="Build Google Sheets CSV/ZIP pack")
    ap.add_argument("--out-dir", default="data/sheets_pack")
    ap.add_argument("--db", default=str(DB_PATH))
    ap.add_argument("--signals", type=int, default=200)
    args = ap.parse_args()
    meta = write_sheets_pack(
        Path(args.out_dir),
        db_path=Path(args.db),
        signal_limit=args.signals,
    )
    print("=== Gold Petal Sheets pack ===")
    print(f"folder: {meta['folder']}")
    print(f"zip:    {meta['zip']}")
    print(f"counts: {meta['counts']}")
    print()
    print("Import ZIP CSVs into Google Sheets (File → Import → Upload).")
    print("Keep control panel for emergency / live unlock / approvals.")


if __name__ == "__main__":
    main()
