#!/usr/bin/env python3
"""Gold Petal trading station — HTML workspace on 8501.

  ./scripts/run_desk_vm.sh --restart
  python3 control_panel.py --host 127.0.0.1 --port 8501
  then http://127.0.0.1:8501/          station
       http://127.0.0.1:8501/lite      compact controls
       http://127.0.0.1:8501/full      archive / downloads
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import traceback
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from capital import (
    capital_snapshot,
    load_capital,
    save_capital,
    update_strategy_budget,
)
from storage import build_trades, count_ticks, latest_ltp, latest_signals, latest_ticks
from charges import paper_lots
from desk_data import (
    history_payload,
    json_safe,
    recent_trades as _recent_trades,
    row_to_dict as _row_to_dict,
    tape_payload,
)
from control_state import (
    paper_strategy_names,
    entries_blocked,
    is_live_mode_allowed,
    load_state,
    save_state,
    set_emergency,
    set_live_approved,
    set_live_unlocked,
    set_trading_enabled,
)
from live_orders import live_lots, recent_orders
from live_readiness import (
    apply_desk_books,
    apply_panel_enables,
    apply_panel_live_env,
    desk_snapshot,
    live_readiness,
    panel_restart_allowed,
    read_live_env,
)
from position_safety import read_bot_health
from paper_report import summarize_trades
from proposals import proposals_snapshot
from s11_desk import (
    activate_s11_pack,
    decide_proposal_for_desk,
    ml_desk_payload,
    pack_summary,
)
from research_desk import decide_research, research_desk_payload
from sheets_pack import sheets_pack_zip_bytes, build_scoreboard_rows, SCORE_FIELDS
from monitor_sheet import (
    STATUS_FIELDS,
    build_status_rows,
    monitor_sheet_zip_bytes,
)
from s14_exchange_sheet import (
    HTML_NAME,
    load_sheet_csv,
    load_sheet_meta,
    missing_sheet_html,
    refresh_status,
    rows_to_tsv as s14_rows_to_tsv,
    sheet_zip_bytes,
    start_angel_refresh,
)
from panel_export import (
    TRADE_CSV_FIELDS,
    TICK_CSV_FIELDS,
    default_date_range,
    export_pack_zip,
    export_summary,
    export_ticks_csv,
    export_trades_csv,
    rows_to_tsv,
    ticks_in_range,
    trades_in_range,
)
from reasoning_cockpit import (
    bars_for_panel,
    load_reasoning,
    panel_timeframes,
    refresh_and_save,
)

ROOT = Path(__file__).resolve().parent
S14_SHEET_DIR = ROOT / "data" / "s14_sheet"


DESK_HTML_PATH = ROOT / "desk.html"
LITE_HTML_PATH = ROOT / "lite.html"
STATION_HTML_PATH = ROOT / "station.html"
MANIFEST_PATH = ROOT / "manifest.webmanifest"


def load_desk_html() -> bytes:
    if STATION_HTML_PATH.is_file():
        return STATION_HTML_PATH.read_bytes()
    path = LITE_HTML_PATH if LITE_HTML_PATH.is_file() else DESK_HTML_PATH
    return path.read_bytes()


def load_lite_html() -> bytes:
    path = LITE_HTML_PATH if LITE_HTML_PATH.is_file() else STATION_HTML_PATH
    return path.read_bytes()


def load_full_desk_html() -> bytes:
    return DESK_HTML_PATH.read_bytes()


def _json_bytes(payload: Any, status: int = 200) -> tuple[int, bytes, str]:
    body = json.dumps(json_safe(payload), default=str, allow_nan=False).encode("utf-8")
    return status, body, "application/json; charset=utf-8"


def dashboard_payload(tick_limit: int = 40, trade_limit: int = 40) -> dict[str, Any]:
    state = load_state()
    ticks = [_row_to_dict(r) for r in latest_ticks(limit=tick_limit)]
    # One DB trade rebuild for the whole dashboard (was 10× before — timed out over tunnel).
    all_trades = build_trades(strategy=None, lot_size=paper_lots())
    closed = [t for t in all_trades if str(t.get("status", "")).startswith("CLOSED")]
    open_t = [t for t in all_trades if t.get("status") == "OPEN"]
    closed_sorted = list(reversed(closed))[:trade_limit]
    trades = closed_sorted + open_t[: max(0, trade_limit - len(closed_sorted))]

    strat_names = paper_strategy_names()
    scoreboard = [summarize_trades(all_trades, s) for s in strat_names]
    scoreboard.append(summarize_trades(all_trades, None))

    live_ok, live_reason = is_live_mode_allowed()
    blocked = entries_blocked()
    # Cached only — do not re-run reasoner on every poll (slow).
    reasoning = load_reasoning()
    live_file = read_live_env()
    return {
        "state": state.to_dict(),
        "entries_blocked": list(blocked),
        "live_allowed": [live_ok, live_reason],
        "live_orders": recent_orders(limit=40),
        "live_env": {
            "dry_run": live_file["dry_run"],
            "lots": live_lots(),
            "max_lots": live_file["live_max_lots"],
            "producttype": (os.getenv("LIVE_PRODUCTTYPE", "CARRYFORWARD") or "CARRYFORWARD"),
        },
        "ltp": latest_ltp(),
        "tick_count": count_ticks(),
        "ticks": ticks,
        "trades": trades,
        "signals": [_row_to_dict(r) for r in latest_signals(limit=25)],
        "capital": capital_snapshot(),
        "proposals": proposals_snapshot(),
        "scoreboard": scoreboard,
        "reasoning": reasoning,
        "timeframes": panel_timeframes(),
        "live_desk": live_readiness(),
        "open_positions": [t for t in all_trades if t.get("status") == "OPEN"][:20],
        "where": {
            "host": "Same trading VM as run_strategy.py / supervise.sh",
            "url": "SSH tunnel → http://127.0.0.1:8788/",
            "ticks_db": "goldpetal/data/ticks.db",
            "bars": "Built on the fly from ticks: 1m,2m,3m,5m,10m,15m,30m,1h,4h,1d",
            "reasoning": "data/control/reasoning_latest.json",
        },
    }


def desk_payload() -> dict[str, Any]:
    """Light snapshot for the HTML desk (no trade rebuild, no tick count)."""
    from analytics.bot_ops import bot_status
    from desk_data import last_tick_snapshot
    from human_capture import session_status

    bot = dict(bot_status(lite=True))
    bot.pop("log_tail", None)
    sess = dict(session_status())
    try:
        tape = last_tick_snapshot()
    except Exception:
        tape = {"tape_live": False, "tape_age_sec": None, "last_tick_at": ""}
    feed = str(bot.get("feed_source") or "off")
    feed_on = bool(bot.get("running")) or feed in {"bot", "collector"}
    sess["tape_live"] = bool(tape.get("tape_live"))
    sess["tape_age_sec"] = tape.get("tape_age_sec")
    sess["last_tick_at"] = tape.get("last_tick_at") or ""
    sess["ltp"] = tape.get("ltp")
    sess["goldpetal_running"] = bool(sess.get("open")) and feed_on and bool(tape.get("tape_live"))
    return {
        "bot": bot,
        "live_desk": desk_snapshot(),
        "state": load_state().to_dict(),
        "capital": capital_snapshot(),
        "session": sess,
    }


class ControlHandler(BaseHTTPRequestHandler):
    server_version = "GoldPetalControl/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        # Keep stdout quiet; runner already logs heavily.
        return

    def _send(self, status: int, body: bytes, content_type: str, extra_headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))

    def do_GET(self) -> None:  # noqa: N802
        try:
            parsed = urlparse(self.path)
            path = parsed.path
            qs = parse_qs(parsed.query)
            if path in {"/", "/index.html", "/station", "/station.html"}:
                if not STATION_HTML_PATH.is_file() and not LITE_HTML_PATH.is_file() and not DESK_HTML_PATH.is_file():
                    self._send(500, b"station.html missing", "text/plain; charset=utf-8")
                    return
                self._send(200, load_desk_html(), "text/html; charset=utf-8")
                return
            if path in {"/lite", "/lite.html", "/controls"}:
                self._send(200, load_lite_html(), "text/html; charset=utf-8")
                return
            if path in {"/full", "/full.html"}:
                self._send(200, load_full_desk_html(), "text/html; charset=utf-8")
                return
            if path in {"/manifest.webmanifest", "/manifest.json"}:
                if not MANIFEST_PATH.is_file():
                    self._send(404, b"manifest missing", "text/plain; charset=utf-8")
                    return
                self._send(
                    200,
                    MANIFEST_PATH.read_bytes(),
                    "application/manifest+json; charset=utf-8",
                )
                return
            if path in {"/s14-sheet", "/s14-sheet.html"}:
                html_path = S14_SHEET_DIR / HTML_NAME
                if html_path.is_file():
                    self._send(200, html_path.read_bytes(), "text/html; charset=utf-8")
                else:
                    self._send(
                        200,
                        missing_sheet_html().encode("utf-8"),
                        "text/html; charset=utf-8",
                    )
                return
            if path == "/api/s14/calc":
                from desk_data import resolve_desk_db
                from s14_calc import cached_calc_payload

                status, body, ctype = _json_bytes(cached_calc_payload(resolve_desk_db()))
                self._send(status, body, ctype)
                return
            if path == "/api/analysis":
                from trade_analysis import analysis_payload

                status, body, ctype = _json_bytes(analysis_payload())
                self._send(status, body, ctype)
                return
            if path == "/api/s14/meta":
                status, body, ctype = _json_bytes(load_sheet_meta(S14_SHEET_DIR))
                self._send(status, body, ctype)
                return
            if path == "/api/s14/refresh":
                status, body, ctype = _json_bytes(refresh_status(S14_SHEET_DIR))
                self._send(status, body, ctype)
                return
            if path == "/api/s14/sheet":
                tf = ((qs.get("tf") or ["1d"])[0] or "1d").strip().lower()
                fields, rows = load_sheet_csv(f"{tf}.csv", S14_SHEET_DIR)
                tsv = s14_rows_to_tsv(rows, fields) if fields else ""
                status, body, ctype = _json_bytes(
                    {
                        "tf": tf,
                        "fields": fields,
                        "rows": rows,
                        "tsv": tsv,
                    }
                )
                self._send(status, body, ctype)
                return
            if path == "/api/s14/sheet.zip":
                if not (S14_SHEET_DIR / HTML_NAME).is_file() and not any(
                    S14_SHEET_DIR.glob("*.csv")
                ):
                    status, body, ctype = _json_bytes(
                        load_sheet_meta(S14_SHEET_DIR), 404
                    )
                    self._send(status, body, ctype)
                    return
                blob = sheet_zip_bytes(S14_SHEET_DIR)
                name = "GoldPetal_S14_sheet.zip"
                self._send(
                    200,
                    blob,
                    "application/zip",
                    {"Content-Disposition": f'attachment; filename="{name}"'},
                )
                return
            if path == "/api/desk":
                status, body, ctype = _json_bytes(desk_payload())
                self._send(status, body, ctype)
                return
            if path == "/api/tape":
                status, body, ctype = _json_bytes(tape_payload())
                self._send(status, body, ctype)
                return
            if path == "/api/dashboard":
                status, body, ctype = _json_bytes(dashboard_payload())
                self._send(status, body, ctype)
                return
            if path == "/api/status":
                status, body, ctype = _json_bytes(
                    {
                        "state": load_state().to_dict(),
                        "entries_blocked": list(entries_blocked()),
                        "live_allowed": list(is_live_mode_allowed()),
                        "live_desk": live_readiness(),
                        "ltp": latest_ltp(),
                        "tick_count": count_ticks(),
                    }
                )
                self._send(status, body, ctype)
                return
            if path == "/api/history":
                limit = int((qs.get("limit") or ["80"])[0])
                strat = ((qs.get("strategy") or [""])[0] or "").strip() or None
                status, body, ctype = _json_bytes(history_payload(limit=limit, strategy=strat))
                self._send(status, body, ctype)
                return
            if path == "/api/ticks":
                limit = int((qs.get("limit") or ["40"])[0])
                rows = [_row_to_dict(r) for r in latest_ticks(limit=limit)]
                status, body, ctype = _json_bytes({"ticks": rows, "count": count_ticks()})
                self._send(status, body, ctype)
                return
            if path == "/api/trades":
                limit = int((qs.get("limit") or ["80"])[0])
                strat = ((qs.get("strategy") or [""])[0] or "").strip() or None
                hist = history_payload(limit=limit, strategy=strat)
                status, body, ctype = _json_bytes(
                    {
                        "trades": hist["trades"],
                        "open": hist["open"],
                        "total_closed": hist["total_closed"],
                        "total_open": hist["total_open"],
                    }
                )
                self._send(status, body, ctype)
                return
            if path == "/api/proposals":
                status, body, ctype = _json_bytes(proposals_snapshot())
                self._send(status, body, ctype)
                return
            if path == "/api/ml":
                status, body, ctype = _json_bytes(ml_desk_payload())
                self._send(status, body, ctype)
                return
            if path == "/api/research":
                status, body, ctype = _json_bytes(research_desk_payload())
                self._send(status, body, ctype)
                return
            if path == "/api/capture":
                from human_capture import capture_desk_payload

                status, body, ctype = _json_bytes(capture_desk_payload())
                self._send(status, body, ctype)
                return
            if path == "/api/mood":
                try:
                    from market_mood import mood_desk_payload

                    payload = mood_desk_payload()
                except Exception as exc:
                    payload = {
                        "ok": False,
                        "mood": "UNKNOWN",
                        "label": "mood unavailable",
                        "reason": str(exc),
                        "gate_on": False,
                        "flatten_on": False,
                        "note": (
                            "Desk still runs. Mood gate defaults on so unfit books do not open. "
                            "Not a paper book. Keep DRY_RUN=true."
                        ),
                    }
                status, body, ctype = _json_bytes(payload)
                self._send(status, body, ctype)
                return
            if path == "/api/amise":
                try:
                    from amise import amise_desk_payload

                    payload = amise_desk_payload()
                except Exception as exc:
                    payload = {
                        "ok": False,
                        "engine": "AMISE",
                        "error": str(exc),
                        "live_blocked": True,
                        "enable_blocked": True,
                        "note": (
                            "Desk still runs. AMISE invents challengers; you Approve. "
                            "Never DRY_RUN=false. Keep DRY_RUN=true."
                        ),
                    }
                status, body, ctype = _json_bytes(payload)
                self._send(status, body, ctype)
                return
            if path == "/api/amise/lab":
                try:
                    from amise import amise_lab_status

                    payload = amise_lab_status()
                except Exception as exc:
                    payload = {"ok": False, "error": str(exc), "running": False}
                status, body, ctype = _json_bytes(payload)
                self._send(status, body, ctype)
                return
                raw = ((qs.get("path") or [""])[0] or "").strip()
                status, body, ctype = _json_bytes(pack_summary(raw))
                self._send(status, body, ctype)
                return
            if path == "/api/capital":
                status, body, ctype = _json_bytes(capital_snapshot())
                self._send(status, body, ctype)
                return
            if path == "/api/bars":
                tf = (qs.get("tf") or ["5m"])[0]
                limit = int((qs.get("limit") or ["60"])[0])
                status, body, ctype = _json_bytes(bars_for_panel(tf, limit=limit))
                self._send(status, body, ctype)
                return
            if path == "/api/reasoning":
                payload = load_reasoning() or {}
                status, body, ctype = _json_bytes(payload)
                self._send(status, body, ctype)
                return
            if path == "/api/timeframes":
                status, body, ctype = _json_bytes({"timeframes": panel_timeframes()})
                self._send(status, body, ctype)
                return
            if path == "/api/export/defaults":
                d0, d1 = default_date_range()
                status, body, ctype = _json_bytes({"date_from": d0, "date_to": d1})
                self._send(status, body, ctype)
                return
            if path == "/api/export/summary":
                d_from = (qs.get("from") or [""])[0]
                d_to = (qs.get("to") or [""])[0]
                status, body, ctype = _json_bytes(export_summary(d_from, d_to))
                self._send(status, body, ctype)
                return
            if path == "/api/export/ticks.csv":
                d_from = (qs.get("from") or [""])[0]
                d_to = (qs.get("to") or [""])[0]
                csv_text = export_ticks_csv(d_from, d_to)
                name = f"goldpetal_ticks_{d_from}_to_{d_to}.csv"
                self._send(
                    200,
                    csv_text.encode("utf-8"),
                    "text/csv; charset=utf-8",
                    {"Content-Disposition": f'attachment; filename="{name}"'},
                )
                return
            if path == "/api/export/trades.csv":
                d_from = (qs.get("from") or [""])[0]
                d_to = (qs.get("to") or [""])[0]
                csv_text = export_trades_csv(d_from, d_to)
                name = f"goldpetal_trades_{d_from}_to_{d_to}.csv"
                self._send(
                    200,
                    csv_text.encode("utf-8"),
                    "text/csv; charset=utf-8",
                    {"Content-Disposition": f'attachment; filename="{name}"'},
                )
                return
            if path == "/api/export/pack.zip":
                d_from = (qs.get("from") or [""])[0]
                d_to = (qs.get("to") or [""])[0]
                blob = export_pack_zip(d_from, d_to)
                name = f"goldpetal_export_{d_from}_to_{d_to}.zip"
                self._send(
                    200,
                    blob,
                    "application/zip",
                    {"Content-Disposition": f'attachment; filename="{name}"'},
                )
                return
            if path == "/api/export/tsv":
                d_from = (qs.get("from") or [""])[0]
                d_to = (qs.get("to") or [""])[0]
                kind = ((qs.get("kind") or ["trades"])[0] or "trades").strip().lower()
                if kind == "ticks":
                    rows = ticks_in_range(d_from, d_to)
                    tsv = rows_to_tsv(rows, TICK_CSV_FIELDS)
                else:
                    rows = trades_in_range(d_from, d_to)
                    tsv = rows_to_tsv(rows, TRADE_CSV_FIELDS)
                status, body, ctype = _json_bytes(
                    {"kind": kind, "rows": len(rows), "tsv": tsv, "from": d_from, "to": d_to}
                )
                self._send(status, body, ctype)
                return
            if path == "/api/sheets/pack.zip":
                blob = sheets_pack_zip_bytes()
                stamp = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y%m%d_%H%M%S")
                name = f"goldpetal_sheets_{stamp}.zip"
                self._send(
                    200,
                    blob,
                    "application/zip",
                    {"Content-Disposition": f'attachment; filename="{name}"'},
                )
                return
            if path == "/api/sheets/monitor.zip":
                blob = monitor_sheet_zip_bytes()
                stamp = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y%m%d_%H%M%S")
                name = f"goldpetal_monitor_{stamp}.zip"
                self._send(
                    200,
                    blob,
                    "application/zip",
                    {"Content-Disposition": f'attachment; filename="{name}"'},
                )
                return
            if path == "/api/sheets/scoreboard.tsv":
                rows = build_scoreboard_rows()
                tsv = rows_to_tsv(rows, SCORE_FIELDS)
                status, body, ctype = _json_bytes(
                    {"kind": "scoreboard", "rows": len(rows), "tsv": tsv}
                )
                self._send(status, body, ctype)
                return
            if path == "/api/sheets/status.tsv":
                rows = build_status_rows()
                tsv = rows_to_tsv(rows, STATUS_FIELDS)
                status, body, ctype = _json_bytes(
                    {"kind": "status", "rows": len(rows), "tsv": tsv}
                )
                self._send(status, body, ctype)
                return
            self._send(*_json_bytes({"error": "not found"}, 404))
        except Exception as exc:
            self._send(*_json_bytes({"error": str(exc), "trace": traceback.format_exc()}, 500))

    def do_POST(self) -> None:  # noqa: N802
        try:
            parsed = urlparse(self.path)
            path = parsed.path
            data = self._read_json()

            if path == "/api/s14/refresh":
                res = start_angel_refresh(
                    root=ROOT,
                    python=sys.executable,
                    sheet_dir=S14_SHEET_DIR,
                )
                self._send(*_json_bytes(res))
                return
            if path == "/api/emergency":
                st = set_emergency(bool(data.get("off")))
                self._send(*_json_bytes({"ok": True, "state": st.to_dict()}))
                return
            if path == "/api/trading":
                st = set_trading_enabled(bool(data.get("enabled")))
                self._send(*_json_bytes({"ok": True, "state": st.to_dict()}))
                return
            if path == "/api/live":
                st = set_live_unlocked(bool(data.get("unlocked")))
                self._send(*_json_bytes({"ok": True, "state": st.to_dict()}))
                return
            if path == "/api/live/approved":
                from live_readiness import LIVE_ELIGIBLE_BOOKS

                names = [
                    str(s).strip()
                    for s in (data.get("strategies") or [])
                    if str(s).strip() in LIVE_ELIGIBLE_BOOKS
                ]
                st = set_live_approved(names, note="control panel live_approved")
                self._send(
                    *_json_bytes(
                        {
                            "ok": True,
                            "live_approved": list(st.live_approved),
                            "state": st.to_dict(),
                            "live_desk": live_readiness(),
                        }
                    )
                )
                return
            if path == "/api/live/enables":
                names = [
                    str(s).strip()
                    for s in (data.get("strategies") or [])
                    if str(s).strip()
                ]
                res = apply_panel_enables(names)
                status = 200 if res.get("ok") else 400
                res = {**res, "live_desk": live_readiness()}
                self._send(*_json_bytes(res, status))
                return
            if path == "/api/live/env":
                dry_raw = data.get("dry_run")
                dry_run = True if dry_raw is None else bool(dry_raw)
                try:
                    lots = int(data.get("live_max_lots") or 1)
                except (TypeError, ValueError):
                    self._send(*_json_bytes({"ok": False, "error": "LIVE_MAX_LOTS must be an integer"}, 400))
                    return
                res = apply_panel_live_env(
                    dry_run=dry_run,
                    live_max_lots=lots,
                    confirm=str(data.get("confirm") or ""),
                )
                status = 200 if res.get("ok") else 400
                res = {**res, "live_desk": live_readiness()}
                self._send(*_json_bytes(res, status))
                return
            if path == "/api/desk/books":
                in_bot = [
                    str(s).strip()
                    for s in (data.get("in_bot") or [])
                    if str(s).strip()
                ]
                live = [
                    str(s).strip()
                    for s in (data.get("live") or [])
                    if str(s).strip()
                ]
                res = apply_desk_books(in_bot, live)
                status = 200 if res.get("ok") else 400
                res = {**res, "live_desk": live_readiness()}
                self._send(*_json_bytes(res, status))
                return
            if path == "/api/amise/lab":
                from amise import start_amise_lab

                res = start_amise_lab(propose=True)
                self._send(*_json_bytes(res, 200 if res.get("ok") else 400))
                return
            if path == "/api/amise/gate":
                from amise import ensure_mood_gate

                res = ensure_mood_gate()
                res["gate_on"] = True
                res["note"] = (
                    "MOOD_GATE=true written. Restart the bot so paper books "
                    "stand down in unfit regimes. Never dumps S13. Keep DRY_RUN=true."
                )
                self._send(*_json_bytes(res, 200 if res.get("ok") else 400))
                return
            if path == "/api/bot/start":
                from analytics.bot_ops import start_bot

                res = start_bot()
                status_info = dict(res.get("status") or {})
                status_info.pop("log_tail", None)
                res["status"] = status_info
                res["live_desk"] = live_readiness()
                self._send(*_json_bytes(res, 200 if res.get("ok") else 400))
                return
            if path == "/api/bot/stop":
                from analytics.bot_ops import stop_bot

                res = stop_bot()
                status_info = dict(res.get("status") or {})
                status_info.pop("log_tail", None)
                res["status"] = status_info
                self._send(*_json_bytes(res))
                return
            if path == "/api/feed/start":
                from analytics.bot_ops import start_feed

                res = start_feed()
                self._send(*_json_bytes(res, 200 if res.get("ok") else 400))
                return
            if path == "/api/feed/stop":
                from analytics.bot_ops import stop_feed

                res = stop_feed()
                self._send(*_json_bytes(res))
                return
            if path == "/api/bot/restart":
                ok, why = panel_restart_allowed(str(data.get("confirm") or ""))
                if not ok:
                    self._send(*_json_bytes({"ok": False, "error": why}, 400))
                    return
                from analytics.bot_ops import restart_bot

                res = restart_bot()
                status_info = dict(res.get("status") or {})
                status_info.pop("log_tail", None)
                res["status"] = status_info
                res["live_desk"] = live_readiness()
                self._send(*_json_bytes(res))
                return
            if path == "/api/reasoning/refresh":
                payload = refresh_and_save(
                    in_position=bool(data.get("in_position", False)),
                    position_side=str(data.get("position_side") or "flat"),
                    open_pnl_pts=float(data.get("open_pnl_pts") or 0),
                )
                self._send(*_json_bytes(payload))
                return
            if path == "/api/capital":
                plan = load_capital()
                if "total_capital_inr" in data:
                    plan.total_capital_inr = float(data["total_capital_inr"])
                if "cash_reserve_pct" in data:
                    plan.cash_reserve_pct = float(data["cash_reserve_pct"])
                if "daily_loss_limit_inr" in data:
                    plan.daily_loss_limit_inr = float(data["daily_loss_limit_inr"])
                if "max_lots_total" in data:
                    plan.max_lots_total = int(data["max_lots_total"])
                save_capital(plan)
                self._send(*_json_bytes({"ok": True, "capital": capital_snapshot()}))
                return
            if path == "/api/capital/strategy":
                strategy = str(data.get("strategy") or "")
                if not strategy:
                    self._send(*_json_bytes({"error": "strategy required"}, 400))
                    return
                update_strategy_budget(
                    strategy,
                    budget_inr=float(data["budget_inr"]) if "budget_inr" in data else None,
                    max_lots=int(data["max_lots"]) if "max_lots" in data else None,
                    max_open_trades=int(data["max_open_trades"])
                    if "max_open_trades" in data
                    else None,
                    enabled=bool(data["enabled"]) if "enabled" in data else None,
                )
                self._send(*_json_bytes({"ok": True, "capital": capital_snapshot()}))
                return
            if path.startswith("/api/proposals/") and path.endswith("/decide"):
                proposal_id = path[len("/api/proposals/") : -len("/decide")]
                decision = str(data.get("decision") or "")
                note = str(data.get("note") or "")
                accept_unsafe = bool(data.get("accept_unsafe"))
                apply_env = bool(data.get("apply_env", True))
                try:
                    res = decide_proposal_for_desk(
                        proposal_id,
                        decision,
                        note=note,
                        apply_env=apply_env,
                        accept_unsafe=accept_unsafe,
                    )
                except KeyError as exc:
                    self._send(*_json_bytes({"error": str(exc)}, 404))
                    return
                except (ValueError, RuntimeError) as exc:
                    self._send(*_json_bytes({"error": str(exc)}, 400))
                    return
                self._send(*_json_bytes(res, 200 if res.get("ok") else 400))
                return
            if path.startswith("/api/research/") and path.endswith("/decide"):
                proposal_id = path[len("/api/research/") : -len("/decide")]
                decision = str(data.get("decision") or "")
                note = str(data.get("note") or "")
                try:
                    res = decide_research(proposal_id, decision, note=note)
                except KeyError as exc:
                    self._send(*_json_bytes({"error": str(exc)}, 404))
                    return
                except (ValueError, RuntimeError) as exc:
                    self._send(*_json_bytes({"error": str(exc)}, 400))
                    return
                self._send(*_json_bytes(res, 200 if res.get("ok") else 400))
                return
            if path == "/api/capture":
                from human_capture import capture_desk_payload, mark_example_order, record_human
                from you_trade import map_capture_action, request_you_order

                action = str(data.get("action") or "")
                place = bool(data.get("place") or data.get("send") or data.get("live"))
                confirm = str(data.get("confirm") or "")
                try:
                    rec = record_human(
                        action,
                        confidence=int(data.get("confidence") or 3),
                        note=str(data.get("note") or ""),
                    )
                except (ValueError, RuntimeError) as exc:
                    self._send(*_json_bytes({"error": str(exc)}, 400))
                    return
                order = None
                mapped = map_capture_action(rec.get("action") or action)
                if place and mapped:
                    order = request_you_order(
                        mapped,
                        confirm=confirm,
                        example_id=str(rec.get("id") or ""),
                        entry_px=rec.get("entry_px"),
                    )
                    rec["places_order"] = bool(order.get("queued"))
                    rec["live"] = bool(order.get("queued"))
                    rec["order"] = order
                    mark_example_order(
                        str(rec.get("id") or ""),
                        queued=bool(order.get("queued")),
                        order=order,
                    )
                payload = capture_desk_payload(settle=False)
                try:
                    from you_learn import after_new_example

                    learn_run = after_new_example()
                    payload["learn_run"] = {
                        "proposed": bool(learn_run.get("proposed")),
                        "deployed": bool(learn_run.get("deployed")),
                        "already": bool(learn_run.get("already")),
                        "slot": learn_run.get("slot"),
                        "note": learn_run.get("note"),
                        "proposal_id": learn_run.get("proposal_id"),
                    }
                    if learn_run.get("learn"):
                        payload["learn"] = learn_run["learn"]
                except Exception as exc:
                    payload["learn_run"] = {"error": str(exc)}
                payload["recorded"] = {
                    "id": rec.get("id"),
                    "action": rec.get("action"),
                    "entry_px": rec.get("entry_px"),
                    "vs_coded": rec.get("vs_coded"),
                    "places_order": bool(rec.get("places_order")),
                    "live": bool(rec.get("live")),
                    "order": order,
                    "you_session_id": rec.get("you_session_id"),
                    "clicked_at_ist": rec.get("clicked_at_ist"),
                    "tape_lag_ms": rec.get("tape_lag_ms"),
                }
                payload["ok"] = True
                self._send(*_json_bytes(payload))
                return
            if path == "/api/capture/learn":
                from you_learn import propose_mimic

                try:
                    res = propose_mimic()
                except (ValueError, RuntimeError) as exc:
                    self._send(*_json_bytes({"error": str(exc)}, 400))
                    return
                self._send(*_json_bytes(res, 200 if res.get("ok") else 400))
                return
            if path == "/api/capture/go":
                from you_learn import start_mimic_paper

                try:
                    res = start_mimic_paper()
                except (ValueError, RuntimeError) as exc:
                    self._send(*_json_bytes({"error": str(exc)}, 400))
                    return
                self._send(*_json_bytes(res, 200 if res.get("ok") else 400))
                return
            if path == "/api/s11/activate":
                pack_path = str(data.get("pack_path") or data.get("path") or "")
                accept_unsafe = bool(data.get("accept_unsafe"))
                try:
                    res = activate_s11_pack(pack_path, accept_unsafe=accept_unsafe)
                except FileNotFoundError as exc:
                    self._send(*_json_bytes({"error": str(exc)}, 404))
                    return
                except ValueError as exc:
                    self._send(*_json_bytes({"error": str(exc)}, 400))
                    return
                self._send(*_json_bytes(res, 200 if res.get("ok") else 400))
                return

            self._send(*_json_bytes({"error": "not found"}, 404))
        except Exception as exc:
            self._send(*_json_bytes({"error": str(exc), "trace": traceback.format_exc()}, 500))


def _amise_auto_loop() -> None:
    import time

    from amise import maybe_start_auto_lab

    while True:
        time.sleep(60)
        try:
            maybe_start_auto_lab()
        except Exception:
            continue


def main() -> None:
    ap = argparse.ArgumentParser(description="Gold Petal control panel")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8501)
    args = ap.parse_args()
    from desk_data import bind_live_ticks_db

    bound = bind_live_ticks_db()
    print(
        f"ticks db {bound.get('live')}  "
        f"bot={bound.get('bot_cwd_db') or 'not running'}  "
        f"quarantined={len(bound.get('quarantined') or [])}",
        flush=True,
    )
    for row in bound.get("quarantined") or []:
        print(f"  stale {row}", flush=True)
    load_state()
    load_capital()
    try:
        from amise import ensure_mood_gate

        ensure_mood_gate()
    except Exception:
        pass
    threading.Thread(target=_amise_auto_loop, name="amise-auto-lab", daemon=True).start()
    httpd = ThreadingHTTPServer((args.host, args.port), ControlHandler)
    print(f"cwd {ROOT}  Gold Petal station → http://{args.host}:{args.port}/", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped", flush=True)


if __name__ == "__main__":
    main()
