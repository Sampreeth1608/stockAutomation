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
from desk_http_auth import (
    MAX_BODY_BYTES,
    Access,
    auth_status_payload,
    check_access,
    cookie_header,
    dangerous_requires_totp,
    desk_http_start_error,
    desk_http_start_warning,
    drop_session,
    public_bind_blocked,
    security_headers,
    session_from_headers,
    session_payload,
    try_login,
)
from position_safety import read_bot_health
from paper_report import summarize_trades
from proposals import proposals_snapshot
from sheets_pack import sheets_pack_zip_bytes, build_scoreboard_rows, SCORE_FIELDS
try:
    from monitor_sheet import (
        STATUS_FIELDS,
        build_status_rows,
        monitor_sheet_zip_bytes,
    )
except ImportError:  # partial VM copy — desk must still boot
    STATUS_FIELDS = ["section", "field", "value"]

    def build_status_rows() -> list:
        return []

    def monitor_sheet_zip_bytes() -> bytes:
        raise ModuleNotFoundError(
            "monitor_sheet.py missing on this desk folder — copy it from the branch"
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
    SIGNAL_CSV_FIELDS,
    default_date_range,
    export_pack_zip,
    export_signals_csv,
    export_summary,
    export_ticks_csv,
    export_trades_csv,
    rows_to_tsv,
    signals_in_range,
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
LOGIN_HTML_PATH = ROOT / "login.html"
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


def load_login_html() -> bytes:
    if LOGIN_HTML_PATH.is_file():
        return LOGIN_HTML_PATH.read_bytes()
    return (
        b"<!DOCTYPE html><html><body class='gp-desk-login'>"
        b"<h1>Unlock desk</h1><p>login.html missing</p></body></html>"
    )


def _json_bytes(payload: Any, status: int = 200) -> tuple[int, bytes, str]:
    body = json.dumps(json_safe(payload), default=str, allow_nan=False).encode("utf-8")
    return status, body, "application/json; charset=utf-8"


def _internal_error_bytes(exc: BaseException) -> tuple[int, bytes, str]:
    traceback.print_exc()
    msg = f"internal error: {type(exc).__name__}: {exc}"
    return _json_bytes({"ok": False, "error": msg[:240]}, 500)


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
    try:
        from desk_flatten import flatten_desk_status

        flatten = flatten_desk_status()
    except Exception:
        flatten = {"pending": [], "by_strategy": {}, "recent": [], "bot_running": False}
    try:
        from position_safety import read_bot_health

        health = read_bot_health()
        books_health = dict(health.get("books") or {})
    except Exception:
        books_health = {}
    try:
        from desk_data import live_pnl_payload

        live_pnl = live_pnl_payload(wait=False)
    except Exception:
        live_pnl = {
            "trades": [],
            "open": [],
            "positions": [],
            "closed": [],
            "orders": [],
            "scoreboard": [],
            "placed_count": 0,
            "lots": 1,
            "summary": {"closed": 0, "open": 0, "pnl_after_charges": 0.0},
            "note": "Live P&L unavailable",
        }
    try:
        live_stats = {
            str(r.get("strategy")): r
            for r in (live_pnl.get("scoreboard") or [])
            if isinstance(r, dict) and r.get("strategy")
        }
        live_desk = desk_snapshot(summaries=live_stats)
    except Exception:
        live_desk = {
            "dry_run": True,
            "books": [],
            "would_place_real_orders": False,
            "live_max_lots": 1,
        }
    try:
        state = load_state().to_dict()
    except Exception:
        state = {}
    try:
        capital = capital_snapshot()
    except Exception:
        capital = {}
    return {
        "bot": bot,
        "live_desk": live_desk,
        "state": state,
        "capital": capital,
        "session": sess,
        "flatten": flatten,
        "live_pnl": live_pnl,
        "books_health": books_health,
        "desk_build": "v62",
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
        headers = dict(security_headers())
        if extra_headers:
            headers.update(extra_headers)
        for k, v in headers.items():
            self.send_header(k, v)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _header_map(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for key in self.headers.keys():
            out[str(key)] = str(self.headers.get(key) or "")
        return out

    def _client_ip(self) -> str:
        if not self.client_address:
            return ""
        return str(self.client_address[0] or "")

    def _apply_access(self, access: Access) -> bool:
        """Send a block/login/redirect. True = caller may continue."""
        if access.allow:
            return True
        extra: dict[str, str] = {}
        if access.kind == "redirect" and access.location:
            extra["Location"] = access.location
            self._send(access.status or 302, b"", "text/plain; charset=utf-8", extra)
            return False
        if access.kind == "login_page":
            self._send(200, load_login_html(), "text/html; charset=utf-8")
            return False
        payload = {"ok": False, "error": access.error or "forbidden", "login": "/login"}
        self._send(*_json_bytes(payload, access.status or 401))
        return False

    def _read_json(self) -> dict[str, Any] | None:
        """Parse JSON body. None means a 4xx was already sent."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            length = 0
        if length > MAX_BODY_BYTES:
            self._send(*_json_bytes({"ok": False, "error": "body too large"}, 413))
            return None
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send(*_json_bytes({"ok": False, "error": "invalid json"}, 400))
            return None
        if data is None:
            return {}
        if not isinstance(data, dict):
            self._send(*_json_bytes({"ok": False, "error": "json object required"}, 400))
            return None
        return data

    def do_GET(self) -> None:  # noqa: N802
        try:
            parsed = urlparse(self.path)
            path = parsed.path
            qs = parse_qs(parsed.query)
            access = check_access(method="GET", path=path, headers=self._header_map())
            if not self._apply_access(access):
                return
            if path in {"/login", "/login.html"}:
                self._send(302, b"", "text/plain; charset=utf-8", {"Location": "/"})
                return
            if path == "/api/desk/auth":
                self._send(*_json_bytes(auth_status_payload()))
                return
            if path == "/api/desk/session":
                sess = access.session or session_from_headers(self._header_map())
                self._send(*_json_bytes(session_payload(sess)))
                return
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
                            "Desk still runs. Mood and market regime are observe-only. "
                            "Not a paper book. Keep DRY_RUN=true."
                        ),
                    }
                status, body, ctype = _json_bytes(payload)
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
            if path == "/api/export/signals.csv":
                d_from = (qs.get("from") or [""])[0]
                d_to = (qs.get("to") or [""])[0]
                csv_text = export_signals_csv(d_from, d_to)
                name = f"goldpetal_signals_{d_from}_to_{d_to}.csv"
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
                elif kind == "signals":
                    rows = signals_in_range(d_from, d_to)
                    tsv = rows_to_tsv(rows, SIGNAL_CSV_FIELDS)
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
            self._send(*_internal_error_bytes(exc))

    def do_POST(self) -> None:  # noqa: N802
        try:
            parsed = urlparse(self.path)
            path = parsed.path
            data = self._read_json()
            if data is None:
                return
            if path == "/api/desk/login":
                res = try_login(password=str(data.get("password") or ""), ip=self._client_ip())
                if not res.ok:
                    extra = {"Set-Cookie": cookie_header("", clear=True)}
                    self._send(
                        *_json_bytes(
                            {"ok": False, "error": res.error or "login failed"},
                            res.status or 401,
                        ),
                        extra_headers=extra,
                    )
                    return
                if res.session is None:
                    self._send(*_json_bytes({"ok": True, "auth_required": False, "csrf": ""}))
                    return
                extra = {"Set-Cookie": cookie_header(res.session.sid)}
                self._send(
                    *_json_bytes(
                        {
                            "ok": True,
                            "csrf": res.session.csrf,
                            "totp_required": dangerous_requires_totp(),
                        }
                    ),
                    extra_headers=extra,
                )
                return
            access = check_access(
                method="POST",
                path=path,
                headers=self._header_map(),
                data=data,
            )
            if not self._apply_access(access):
                return
            if path == "/api/desk/logout":
                sess = access.session or session_from_headers(self._header_map())
                if sess is not None:
                    drop_session(sess.sid)
                extra = {"Set-Cookie": cookie_header("", clear=True)}
                self._send(*_json_bytes({"ok": True}), extra_headers=extra)
                return

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
                from live_readiness import book_may_go_live

                names = [
                    str(s).strip()
                    for s in (data.get("strategies") or [])
                    if book_may_go_live(str(s).strip())
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
                    size_confirm=str(data.get("size_confirm") or data.get("size_word") or ""),
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
                intra_raw = data.get("intraday")
                intra = None
                if intra_raw is not None:
                    intra = [
                        str(s).strip()
                        for s in (intra_raw or [])
                        if str(s).strip()
                    ]
                res = apply_desk_books(in_bot, live, intraday=intra)
                status = 200 if res.get("ok") else 400
                res = {**res, "live_desk": live_readiness()}
                self._send(*_json_bytes(res, status))
                return
            if path == "/api/desk/flatten":
                from desk_flatten import flatten_desk_status, request_flatten, request_flatten_all

                if data.get("all"):
                    names = [
                        str(s).strip()
                        for s in (data.get("strategies") or [])
                        if str(s).strip()
                    ]
                    res = request_flatten_all(strategies=names or None)
                else:
                    name = str(data.get("strategy") or "").strip()
                    res = request_flatten(name)
                status = 200 if res.get("ok") else 400
                res = {**res, "flatten": flatten_desk_status()}
                self._send(*_json_bytes(res, status))
                return
            if path == "/api/desk/arm":
                from live_readiness import apply_desk_arm

                allocations = data.get("allocations") or []
                if not isinstance(allocations, list):
                    allocations = []
                try:
                    lots = int(data.get("live_max_lots") or 1)
                except (TypeError, ValueError):
                    lots = 1
                try:
                    total = (
                        float(data["total_capital_inr"])
                        if data.get("total_capital_inr") not in (None, "")
                        else None
                    )
                    day_loss = (
                        float(data["daily_loss_limit_inr"])
                        if data.get("daily_loss_limit_inr") not in (None, "")
                        else None
                    )
                except (TypeError, ValueError):
                    self._send(*_json_bytes({"ok": False, "error": "capital must be numbers"}, 400))
                    return
                res = apply_desk_arm(
                    mode=str(data.get("mode") or "paper"),
                    confirm=str(data.get("confirm") or ""),
                    live_max_lots=lots,
                    in_bot=[
                        str(s).strip()
                        for s in (data.get("in_bot") or [])
                        if str(s).strip()
                    ],
                    live=[
                        str(s).strip()
                        for s in (data.get("live") or [])
                        if str(s).strip()
                    ],
                    intraday=(
                        [
                            str(s).strip()
                            for s in (data.get("intraday") or [])
                            if str(s).strip()
                        ]
                        if data.get("intraday") is not None
                        else None
                    ),
                    total_capital_inr=total,
                    daily_loss_limit_inr=day_loss,
                    allocations=allocations,
                    live_size_mode=str(data.get("live_size_mode") or "") or None,
                    size_confirm=str(data.get("size_confirm") or data.get("size_word") or ""),
                )
                status = 200 if res.get("ok") else 400
                self._send(*_json_bytes(res, status))
                return
            if path == "/api/desk/regime":
                from market_mood import set_regime_gate

                on = bool(data.get("on"))
                res = set_regime_gate(on)
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

            self._send(*_json_bytes({"error": "not found"}, 404))
        except Exception as exc:
            self._send(*_internal_error_bytes(exc))


def main() -> None:
    ap = argparse.ArgumentParser(description="Gold Petal control panel")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8501)
    args = ap.parse_args()
    blocked = public_bind_blocked(args.host)
    if blocked:
        print(blocked, file=sys.stderr, flush=True)
        sys.exit(2)
    start_err = desk_http_start_error()
    if start_err:
        print(start_err, file=sys.stderr, flush=True)
        sys.exit(2)
    warn = desk_http_start_warning()
    if warn:
        print(warn, file=sys.stderr, flush=True)
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
        from live_readiness import ensure_overnight_gap_enable

        ensure_overnight_gap_enable()
    except Exception:
        pass
    httpd = ThreadingHTTPServer((args.host, args.port), ControlHandler)
    print(f"cwd {ROOT}  Gold Petal station → http://{args.host}:{args.port}/", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped", flush=True)


if __name__ == "__main__":
    main()
