"""Phone Google Sheet: watch Gold Petal. Not the desk.

Sheets is easy for reading on a phone. It cannot start/stop the bot, hit
emergency, Approve, or Unlock live. Rank paper books by after-charges ₹
(tax excluded). Keep DRY_RUN=true.

CLI:
  python3 monitor_sheet.py
  python3 monitor_sheet.py --upload
  python3 monitor_sheet.py --upload --every 300

Writes data/monitor_sheet/pack_* + ZIP. --upload needs a blank spreadsheet
shared with the service-account email, plus:
  GOOGLE_SERVICE_ACCOUNT_JSON
  GOOGLE_MONITOR_SHEET_ID
"""

from __future__ import annotations

import argparse
import csv
import io
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None  # type: ignore[misc, assignment]


from capital import capital_snapshot
from charges import paper_lots
from control_state import entries_blocked, is_live_mode_allowed, load_state, paper_strategy_names
from desk_data import last_tick_snapshot, tape_freshness
from google_sheet_io import (
    resolve_creds_path,
    resolve_sheet_id,
    spreadsheet_url,
    upload_tables,
)
from human_capture import session_status
from live_readiness import PAPER_ONLY_BOOKS, desk_snapshot
from market_mood import snapshot_mood
from paper_report import summarize_trades
from storage import DB_PATH, build_trades, latest_signals

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None  # type: ignore[misc, assignment]

IST = ZoneInfo("Asia/Kolkata")
ROOT = Path(__file__).resolve().parent
WATCH_NOTE = (
    "Sheets = watch only. Start/stop, emergency, Unlock live, and Approve "
    "stay on the desk (SSH tunnel → http://127.0.0.1:8501/). "
    "Rank after_charges₹. Tax excluded from rank. Keep DRY_RUN=true."
)
TAB_ORDER = ["STATUS", "BOOKS", "OPEN", "CLOSED", "MOOD", "SIGNALS", "HOW_TO"]

STATUS_FIELDS = ["key", "value"]
BOOK_FIELDS = [
    "strategy",
    "in_bot",
    "paper_only",
    "live_approved",
    "mood_stance",
    "trades",
    "closed",
    "open",
    "wins_after_charges",
    "losses_after_charges",
    "win_pct_after_charges",
    "gross_₹",
    "charges_₹",
    "after_charges_₹",
    "after_tax_₹",
    "rank_this",
    "note",
]
MOOD_FIELDS = [
    "strategy",
    "stance",
    "weight",
    "preferred_side",
    "why",
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
CLOSED_FIELDS = [
    "strategy",
    "side",
    "status",
    "entry_ts",
    "exit_ts",
    "entry_price",
    "exit_price",
    "gross_pnl",
    "charges",
    "pnl_after_charges",
    "tax",
    "pnl_after_tax",
    "exit_reason",
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
HOW_TO_FIELDS = ["step"]


def _now_stamp() -> str:
    return datetime.now(IST).strftime("%Y%m%d_%H%M%S")


def _yn(v: Any) -> str:
    return "YES" if bool(v) else "NO"


def _num(v: Any) -> float:
    if v == "" or v is None:
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _round(v: Any, n: int = 2) -> str:
    if v is None or v == "":
        return ""
    try:
        return f"{float(v):.{n}f}"
    except (TypeError, ValueError):
        return str(v)


def monitor_book_names() -> list[str]:
    names = list(paper_strategy_names())
    if "FLOW_BRAIN" not in names:
        names.append("FLOW_BRAIN")
    return names


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


def _rows_to_table(fields: list[str], rows: list[dict[str, Any]]) -> list[list[str]]:
    table = [list(fields)]
    for r in rows:
        table.append(["" if r.get(k) is None else str(r.get(k, "")) for k in fields])
    return table


def _after_charges_win_stats(closed: list[dict[str, Any]]) -> tuple[int, int, float]:
    wins = [t for t in closed if _num(t.get("pnl_after_charges")) > 0]
    losses = [t for t in closed if _num(t.get("pnl_after_charges")) < 0]
    pct = (len(wins) / len(closed) * 100.0) if closed else 0.0
    return len(wins), len(losses), pct


def _last_depth(db_path: Path) -> dict[str, Any]:
    empty = {"tbq": None, "tsq": None, "net": None}
    if not Path(db_path).is_file():
        return empty
    import sqlite3

    conn = sqlite3.connect(str(db_path), timeout=2.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout=2000")
        row = conn.execute(
            "SELECT ltp, bp, sp, received_at FROM ticks ORDER BY id DESC LIMIT 1"
        ).fetchone()
    except Exception:
        return empty
    finally:
        conn.close()
    if not row:
        return empty
    tbq = row["bp"]
    tsq = row["sp"]
    net = None
    if tbq is not None and tsq is not None:
        try:
            net = float(tbq) - float(tsq)
        except (TypeError, ValueError):
            net = None
    return {"tbq": tbq, "tsq": tsq, "net": net}


def _bot_running() -> tuple[bool, str]:
    try:
        from analytics.bot_ops import bot_status

        bot = bot_status(lite=True)
    except Exception:
        return False, "off"
    return bool(bot.get("running")), str(bot.get("feed_source") or "off")


def how_to_rows() -> list[dict[str, str]]:
    steps = [
        "Gold Petal phone monitor — watch only. This is not the desk.",
        "Rank books by after_charges₹. Do not rank after_tax₹. Keep DRY_RUN=true.",
        "Cannot start/stop, emergency, Approve, or Unlock live from Sheets.",
        "",
        "Easy today (no Google API):",
        "1. On the VM: cd ~/goldpetal && python3 monitor_sheet.py",
        "2. Open the ZIP (data/monitor_sheet/goldpetal_monitor_*.zip) or use Downloads on the desk.",
        "3. Drive → New → Google Sheets → File → Import → Upload each CSV as a new tab.",
        "4. Start with STATUS.csv, then BOOKS.csv. Phone app → that spreadsheet.",
        "",
        "Auto-refresh on the phone (one-time Google setup):",
        "1. Google Cloud → enable Google Sheets API → service account → JSON key.",
        "2. Copy the JSON to the VM (not git). Example: ~/goldpetal/data/google-sa.json",
        "3. Create a blank Sheet named Gold Petal Monitor.",
        "4. Share it with the service-account email as Editor (the email inside the JSON).",
        "5. Copy the spreadsheet id from the URL (between /d/ and /edit).",
        "6. In ~/goldpetal/.env set GOOGLE_SERVICE_ACCOUNT_JSON and GOOGLE_MONITOR_SHEET_ID.",
        "7. python3 monitor_sheet.py --upload",
        "8. Optional every 5 min in session: python3 monitor_sheet.py --upload --every 300",
        "9. Or cron: ./push_monitor_sheet.sh  (Mon–Fri while the market is open).",
        "10. Open the same Sheet from Google Drive on the phone. Pull to refresh.",
        "",
        "Desk (start/stop) is still SSH tunnel → http://127.0.0.1:8501/",
        "Do not upload .env, the desk password, or ticks.db to Drive.",
    ]
    return [{"step": line} for line in steps]


def build_status_rows(
    *,
    db_path: Path = DB_PATH,
    now: datetime | None = None,
) -> list[dict[str, str]]:
    clock = now or datetime.now(IST)
    tape = last_tick_snapshot(db_path=db_path, now=clock)
    if not tape.get("last_tick_at"):
        tape.update(tape_freshness(last_tick_at="", now=clock))
    depth = _last_depth(db_path)
    sess = session_status(now=clock)
    live = desk_snapshot()
    st = load_state()
    cap = capital_snapshot()
    running, feed = _bot_running()
    mood = snapshot_mood(db_path)
    tape_live = bool(tape.get("tape_live"))
    goldpetal_running = bool(sess.get("open")) and running and tape_live
    blocked, why = entries_blocked()
    live_ok, live_why = is_live_mode_allowed()
    would = bool(live.get("would_place_real_orders"))
    mode = "LIVE-UNSAFE" if would else ("PAPER" if live.get("dry_run") else "CHECK DESK")
    rows = [
        {"key": "updated_at_ist", "value": clock.isoformat(timespec="seconds")},
        {"key": "mode", "value": mode},
        {"key": "goldpetal_running", "value": _yn(goldpetal_running)},
        {"key": "bot_running", "value": _yn(running)},
        {"key": "feed_source", "value": feed},
        {"key": "dry_run", "value": _yn(live.get("dry_run"))},
        {"key": "would_place_real_orders", "value": _yn(would)},
        {"key": "emergency_off", "value": _yn(st.emergency_off)},
        {"key": "trading_enabled", "value": _yn(st.trading_enabled)},
        {"key": "live_unlocked", "value": _yn(st.live_unlocked)},
        {"key": "entries_blocked", "value": f"{blocked}:{why}"},
        {"key": "live_allowed", "value": f"{live_ok}:{live_why}"},
        {"key": "session_open", "value": _yn(sess.get("open"))},
        {"key": "session", "value": str(sess.get("label") or "")},
        {"key": "tape_live", "value": _yn(tape_live)},
        {"key": "tape_age_sec", "value": _round(tape.get("tape_age_sec"), 1)},
        {"key": "last_tick_at", "value": str(tape.get("last_tick_at") or "")},
        {"key": "ltp", "value": _round(tape.get("ltp"), 2)},
        {"key": "tbq", "value": _round(depth.get("tbq"), 0)},
        {"key": "tsq", "value": _round(depth.get("tsq"), 0)},
        {"key": "net_tbq_minus_tsq", "value": _round(depth.get("net"), 0)},
        {"key": "tick_count", "value": str(tape.get("tick_count") or 0)},
        {"key": "mood", "value": str(mood.mood)},
        {"key": "regime", "value": str(mood.regime)},
        {"key": "mood_label", "value": str(mood.label)},
        {"key": "mood_gate", "value": _yn(mood.gate_on)},
        {"key": "paper_lots", "value": str(paper_lots())},
        {"key": "today_pnl_inr", "value": _round(cap.get("today_pnl_inr"), 2)},
        {"key": "deployable_inr", "value": _round(cap.get("deployable_inr"), 2)},
        {"key": "open_note", "value": "OPEN tab = paper positions right now"},
        {"key": "rank", "value": "BOOKS after_charges₹ — tax excluded"},
        {"key": "note", "value": WATCH_NOTE},
    ]
    return rows


def build_mood_rows(*, db_path: Path = DB_PATH) -> list[dict[str, Any]]:
    mood = snapshot_mood(db_path)
    rows: list[dict[str, Any]] = []
    for fit in mood.fits:
        rows.append(
            {
                "strategy": fit.get("strategy", ""),
                "stance": fit.get("stance", ""),
                "weight": _round(fit.get("weight"), 2),
                "preferred_side": fit.get("preferred_side", ""),
                "why": fit.get("why", ""),
            }
        )
    if not rows:
        rows.append(
            {
                "strategy": "",
                "stance": "",
                "weight": "",
                "preferred_side": "",
                "why": mood.reason or "warming up",
            }
        )
    return rows


def build_book_rows(*, db_path: Path = DB_PATH) -> list[dict[str, Any]]:
    live = desk_snapshot()
    enables = dict(live.get("enables") or {})
    approved = {
        str(b.get("strategy"))
        for b in (live.get("books") or [])
        if b.get("live_approved")
    }
    mood = snapshot_mood(db_path)
    names = monitor_book_names()
    all_trades: list[dict[str, Any]] = []
    for name in names:
        all_trades.extend(
            build_trades(strategy=name, db_path=db_path, lot_size=paper_lots())
        )
    rows: list[dict[str, Any]] = []
    for name in names:
        trades = [t for t in all_trades if t.get("strategy") == name]
        summary = summarize_trades(trades, strategy=name)
        closed = [t for t in trades if str(t.get("status", "")).startswith("CLOSED")]
        wins, losses, pct = _after_charges_win_stats(closed)
        fit = mood.fit_for(name) or {}
        paper_only = name in PAPER_ONLY_BOOKS
        in_bot = bool(enables.get(name))
        note = ""
        if name == "FLOW_BRAIN" and not in_bot:
            note = "research — ENABLE_FLOW_BRAIN stays false until a sized after-charges tape"
        elif name == "S20_FADE_HL" and not in_bot:
            note = "off until ENABLE — not a go from a thin tape"
        elif paper_only:
            note = "paper only — not live"
        rows.append(
            {
                "strategy": name,
                "in_bot": _yn(in_bot),
                "paper_only": _yn(paper_only),
                "live_approved": _yn(name in approved),
                "mood_stance": fit.get("stance") or "",
                "trades": summary.get("trades") or 0,
                "closed": summary.get("closed") or 0,
                "open": summary.get("open") or 0,
                "wins_after_charges": wins,
                "losses_after_charges": losses,
                "win_pct_after_charges": f"{pct:.1f}",
                "gross_₹": _round(summary.get("gross_pnl"), 1),
                "charges_₹": _round(summary.get("charges"), 1),
                "after_charges_₹": _round(summary.get("pnl_after_charges"), 1),
                "after_tax_₹": _round(summary.get("pnl_after_tax"), 1),
                "rank_this": "after_charges_₹",
                "note": note,
            }
        )
    rows.sort(key=lambda r: _num(r.get("after_charges_₹")), reverse=True)
    all_closed = [t for t in all_trades if str(t.get("status", "")).startswith("CLOSED")]
    all_sum = summarize_trades(all_trades, strategy=None)
    wins, losses, pct = _after_charges_win_stats(all_closed)
    rows.append(
        {
            "strategy": "ALL",
            "in_bot": "",
            "paper_only": "",
            "live_approved": "",
            "mood_stance": "",
            "trades": all_sum.get("trades") or 0,
            "closed": all_sum.get("closed") or 0,
            "open": all_sum.get("open") or 0,
            "wins_after_charges": wins,
            "losses_after_charges": losses,
            "win_pct_after_charges": f"{pct:.1f}",
            "gross_₹": _round(all_sum.get("gross_pnl"), 1),
            "charges_₹": _round(all_sum.get("charges"), 1),
            "after_charges_₹": _round(all_sum.get("pnl_after_charges"), 1),
            "after_tax_₹": _round(all_sum.get("pnl_after_tax"), 1),
            "rank_this": "after_charges_₹",
            "note": "do not rank after_tax₹",
        }
    )
    return rows


def build_open_rows(*, db_path: Path = DB_PATH) -> list[dict[str, Any]]:
    trades = build_trades(strategy=None, db_path=db_path, lot_size=paper_lots())
    return [t for t in trades if t.get("status") == "OPEN"]


def build_closed_rows(*, db_path: Path = DB_PATH, limit: int = 80) -> list[dict[str, Any]]:
    trades = build_trades(strategy=None, db_path=db_path, lot_size=paper_lots())
    closed = [t for t in trades if str(t.get("status", "")).startswith("CLOSED")]
    return list(reversed(closed))[:limit]


def build_signal_rows(*, db_path: Path = DB_PATH, limit: int = 80) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in latest_signals(limit=limit, db_path=db_path):
        out.append({k: r[k] for k in r.keys()})
    return out


def build_monitor_tables(
    *,
    db_path: Path = DB_PATH,
    closed_limit: int = 80,
    signal_limit: int = 80,
) -> dict[str, list[list[str]]]:
    status = build_status_rows(db_path=db_path)
    books = build_book_rows(db_path=db_path)
    opens = build_open_rows(db_path=db_path)
    closed = build_closed_rows(db_path=db_path, limit=closed_limit)
    mood = build_mood_rows(db_path=db_path)
    signals = build_signal_rows(db_path=db_path, limit=signal_limit)
    how = how_to_rows()
    return {
        "STATUS": _rows_to_table(STATUS_FIELDS, status),
        "BOOKS": _rows_to_table(BOOK_FIELDS, books),
        "OPEN": _rows_to_table(OPEN_FIELDS, opens),
        "CLOSED": _rows_to_table(CLOSED_FIELDS, closed),
        "MOOD": _rows_to_table(MOOD_FIELDS, mood),
        "SIGNALS": _rows_to_table(SIGNAL_FIELDS, signals),
        "HOW_TO": _rows_to_table(HOW_TO_FIELDS, how),
    }


def write_monitor_pack(
    out_dir: Path,
    *,
    db_path: Path = DB_PATH,
    closed_limit: int = 80,
    signal_limit: int = 80,
) -> dict[str, Any]:
    stamp = _now_stamp()
    folder = out_dir / f"pack_{stamp}"
    folder.mkdir(parents=True, exist_ok=True)
    status = build_status_rows(db_path=db_path)
    books = build_book_rows(db_path=db_path)
    opens = build_open_rows(db_path=db_path)
    closed = build_closed_rows(db_path=db_path, limit=closed_limit)
    mood = build_mood_rows(db_path=db_path)
    signals = build_signal_rows(db_path=db_path, limit=signal_limit)
    how = how_to_rows()
    tables = {
        "STATUS": _rows_to_table(STATUS_FIELDS, status),
        "BOOKS": _rows_to_table(BOOK_FIELDS, books),
        "OPEN": _rows_to_table(OPEN_FIELDS, opens),
        "CLOSED": _rows_to_table(CLOSED_FIELDS, closed),
        "MOOD": _rows_to_table(MOOD_FIELDS, mood),
        "SIGNALS": _rows_to_table(SIGNAL_FIELDS, signals),
        "HOW_TO": _rows_to_table(HOW_TO_FIELDS, how),
    }
    _write_csv(folder / "STATUS.csv", STATUS_FIELDS, status)
    _write_csv(folder / "BOOKS.csv", BOOK_FIELDS, books)
    _write_csv(folder / "OPEN.csv", OPEN_FIELDS, opens)
    _write_csv(folder / "CLOSED.csv", CLOSED_FIELDS, closed)
    _write_csv(folder / "MOOD.csv", MOOD_FIELDS, mood)
    _write_csv(folder / "SIGNALS.csv", SIGNAL_FIELDS, signals)
    _write_csv(folder / "HOW_TO.csv", HOW_TO_FIELDS, how)
    (folder / "README.txt").write_text(
        "\n".join(r["step"] for r in how) + "\n", encoding="utf-8"
    )
    zip_path = out_dir / f"goldpetal_monitor_{stamp}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(folder.iterdir()):
            if p.is_file():
                zf.write(p, arcname=p.name)
    return {
        "stamp": stamp,
        "folder": str(folder),
        "zip": str(zip_path),
        "tables": tables,
        "counts": {
            "books": max(0, len(books) - 1),
            "open": len(opens),
            "closed": len(closed),
            "signals": len(signals),
        },
    }


def monitor_sheet_zip_bytes(
    *,
    db_path: Path = DB_PATH,
    closed_limit: int = 80,
    signal_limit: int = 80,
) -> bytes:
    status = build_status_rows(db_path=db_path)
    books = build_book_rows(db_path=db_path)
    opens = build_open_rows(db_path=db_path)
    closed = build_closed_rows(db_path=db_path, limit=closed_limit)
    mood = build_mood_rows(db_path=db_path)
    signals = build_signal_rows(db_path=db_path, limit=signal_limit)
    how = how_to_rows()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("STATUS.csv", _csv_bytes(STATUS_FIELDS, status))
        zf.writestr("BOOKS.csv", _csv_bytes(BOOK_FIELDS, books))
        zf.writestr("OPEN.csv", _csv_bytes(OPEN_FIELDS, opens))
        zf.writestr("CLOSED.csv", _csv_bytes(CLOSED_FIELDS, closed))
        zf.writestr("MOOD.csv", _csv_bytes(MOOD_FIELDS, mood))
        zf.writestr("SIGNALS.csv", _csv_bytes(SIGNAL_FIELDS, signals))
        zf.writestr("HOW_TO.csv", _csv_bytes(HOW_TO_FIELDS, how))
        zf.writestr("README.txt", "\n".join(r["step"] for r in how) + "\n")
    return buf.getvalue()


def upload_monitor(
    *,
    db_path: Path = DB_PATH,
    sheet_id: str = "",
    creds_path: str = "",
) -> str:
    sid = sheet_id or resolve_sheet_id()
    creds = creds_path or resolve_creds_path()
    tables = build_monitor_tables(db_path=db_path)
    return upload_tables(tables, sheet_id=sid, creds_path=creds, tab_order=TAB_ORDER)


def _print_status(rows: list[dict[str, str]]) -> None:
    print("=== Gold Petal monitor (watch only) ===")
    for r in rows:
        print(f"{r['key']}: {r['value']}")


def main() -> None:
    if load_dotenv is not None:
        load_dotenv(ROOT / ".env")
    ap = argparse.ArgumentParser(description="Gold Petal phone Google Sheet (read-only)")
    ap.add_argument("--out-dir", default="data/monitor_sheet")
    ap.add_argument("--db", default=str(DB_PATH))
    ap.add_argument("--upload", action="store_true", help="Push tabs to GOOGLE_MONITOR_SHEET_ID")
    ap.add_argument(
        "--every",
        type=int,
        default=0,
        help="Repeat every N seconds (use with --upload). 0 = once.",
    )
    ap.add_argument("--print-status", action="store_true")
    args = ap.parse_args()
    db = Path(args.db)

    def once() -> dict[str, Any]:
        meta = write_monitor_pack(Path(args.out_dir), db_path=db)
        if args.print_status or not args.upload:
            _print_status(build_status_rows(db_path=db))
        print(f"folder: {meta['folder']}")
        print(f"zip:    {meta['zip']}")
        print(f"counts: {meta['counts']}")
        if args.upload:
            url = upload_monitor(db_path=db)
            meta["url"] = url
            print(f"sheet:  {url}")
        else:
            sid = resolve_sheet_id()
            if sid:
                print(f"sheet:  {spreadsheet_url(sid)}  (add --upload to push)")
            print("Import STATUS.csv then BOOKS.csv into Google Sheets, or pass --upload.")
        print(WATCH_NOTE)
        return meta

    if args.every > 0:
        while True:
            try:
                once()
            except Exception as exc:
                print(f"ERROR: {exc}")
            time.sleep(max(30, int(args.every)))
    else:
        once()


if __name__ == "__main__":
    main()
