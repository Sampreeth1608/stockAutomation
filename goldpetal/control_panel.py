#!/usr/bin/env python3
"""Gold Petal operator desk — the only writer for live switches.

Streamlit 8501 is research and cannot overwrite this panel.

  ./scripts/run_control_panel.sh
  python3 control_panel.py --host 0.0.0.0 --port 8787
"""

from __future__ import annotations

import argparse
import json
import os
import sys
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
from control_state import (
    SLIM_PAPER_STRATEGIES,
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
    live_readiness,
    panel_restart_allowed,
    read_live_env,
)
from position_safety import read_bot_health
from paper_report import summarize_trades
from proposals import decide_proposal, proposals_snapshot
from sheets_pack import sheets_pack_zip_bytes, build_scoreboard_rows, SCORE_FIELDS
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


def load_desk_html() -> bytes:
    return DESK_HTML_PATH.read_bytes()


def _json_bytes(payload: Any, status: int = 200) -> tuple[int, bytes, str]:
    body = json.dumps(payload, default=str).encode("utf-8")
    return status, body, "application/json; charset=utf-8"


def _row_to_dict(row: Any) -> dict[str, Any]:
    if hasattr(row, "keys"):
        return {k: row[k] for k in row.keys()}
    return dict(row)


def dashboard_payload(tick_limit: int = 40, trade_limit: int = 40) -> dict[str, Any]:
    state = load_state()
    ticks = [_row_to_dict(r) for r in latest_ticks(limit=tick_limit)]
    # One DB trade rebuild for the whole dashboard (was 10× before — timed out over tunnel).
    all_trades = build_trades(strategy=None)
    closed = [t for t in all_trades if str(t.get("status", "")).startswith("CLOSED")]
    open_t = [t for t in all_trades if t.get("status") == "OPEN"]
    closed_sorted = list(reversed(closed))[:trade_limit]
    trades = closed_sorted + open_t[: max(0, trade_limit - len(closed_sorted))]

    strat_names = (
        "S1_NETDELTA",
        "S2_BALANCE",
        "S3_ML",
        "S4_OVERNIGHT",
        "S5_MINEDGE",
        "S6_MIN30",
        "S8_NET_ZIGZAG",
        "S9_STATE30",
        "S10_LEGACY30",
        "S11_DISCOVERED",
        "S12_HHHL30",
        "S13_HHHL_DAY",
        "S14_WICK30_STRICT",
        "S15_WICK30_NOWICK",
    )
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


_TRADE_CACHE: dict[str, Any] = {"at": 0.0, "rows": []}


def _all_trades_cached() -> list[dict[str, Any]]:
    import time

    now = time.time()
    if now - float(_TRADE_CACHE["at"]) < 12 and _TRADE_CACHE["rows"]:
        return list(_TRADE_CACHE["rows"])
    try:
        rows = build_trades(strategy=None)
    except Exception:
        rows = []
    _TRADE_CACHE["at"] = now
    _TRADE_CACHE["rows"] = rows
    return list(rows)


def _recent_trades(limit: int = 8) -> list[dict[str, Any]]:
    rows = _all_trades_cached()
    open_t = [t for t in rows if t.get("status") == "OPEN"]
    closed = [t for t in rows if str(t.get("status", "")).startswith("CLOSED")]
    return (open_t + list(reversed(closed)))[:limit]


def history_payload(*, limit: int = 80, strategy: str | None = None) -> dict[str, Any]:
    """Trade history, ticks, live orders, scoreboard — same data the old panel exported."""
    rows = _all_trades_cached()
    if strategy:
        rows = [t for t in rows if t.get("strategy") == strategy]
    open_t = [t for t in rows if t.get("status") == "OPEN"]
    closed = [t for t in rows if str(t.get("status", "")).startswith("CLOSED")]
    closed_rev = list(reversed(closed))
    all_rows = _all_trades_cached()
    scoreboard = [summarize_trades(all_rows, s) for s in SLIM_PAPER_STRATEGIES]
    scoreboard.append(summarize_trades(all_rows, None))
    return {
        "strategy": strategy or "",
        "open": open_t,
        "closed": closed_rev[:limit],
        "trades": (open_t + closed_rev)[:limit],
        "total_open": len(open_t),
        "total_closed": len(closed),
        "ticks": [_row_to_dict(r) for r in latest_ticks(limit=40)],
        "signals": [_row_to_dict(r) for r in latest_signals(limit=40)],
        "live_orders": recent_orders(limit=40),
        "scoreboard": scoreboard,
        "tick_count": count_ticks(),
        "ltp": latest_ltp(),
    }


def desk_payload() -> dict[str, Any]:
    """Light snapshot for the operator desk (polls every few seconds)."""
    from analytics.bot_ops import bot_status, feed_status

    bot = dict(bot_status())
    bot.pop("log_tail", None)
    return {
        "writer": "8787",
        "writer_url": "http://127.0.0.1:8787/",
        "research": "http://127.0.0.1:8501/",
        "bot": bot,
        "feed": feed_status(),
        "live_desk": live_readiness(),
        "state": load_state().to_dict(),
        "ltp": latest_ltp(),
        "tick_count": count_ticks(),
        "ticks": [_row_to_dict(r) for r in latest_ticks(limit=8)],
        "trades": _recent_trades(8),
        "live_orders": recent_orders(limit=8),
        "capital": capital_snapshot(),
        "entries_blocked": list(entries_blocked()),
        "live_allowed": list(is_live_mode_allowed()),
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
        self.send_header("Cache-Control", "no-store")
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
            if path in {"/", "/index.html"}:
                if not DESK_HTML_PATH.is_file():
                    self._send(500, b"desk.html missing", "text/plain; charset=utf-8")
                    return
                self._send(200, load_desk_html(), "text/html; charset=utf-8")
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
            if path == "/api/sheets/scoreboard.tsv":
                rows = build_scoreboard_rows()
                tsv = rows_to_tsv(rows, SCORE_FIELDS)
                status, body, ctype = _json_bytes(
                    {"kind": "scoreboard", "rows": len(rows), "tsv": tsv}
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
                names = [
                    str(s).strip()
                    for s in (data.get("strategies") or [])
                    if str(s).strip()
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
                prop = decide_proposal(proposal_id, decision, note=note)
                self._send(*_json_bytes({"ok": True, "proposal": prop.to_dict()}))
                return

            self._send(*_json_bytes({"error": "not found"}, 404))
        except Exception as exc:
            self._send(*_json_bytes({"error": str(exc), "trace": traceback.format_exc()}, 500))


def main() -> None:
    ap = argparse.ArgumentParser(description="Gold Petal control panel")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8787)
    args = ap.parse_args()
    # Ensure default control files exist.
    load_state()
    load_capital()
    httpd = ThreadingHTTPServer((args.host, args.port), ControlHandler)
    print(f"Gold Petal operator desk → http://{args.host}:{args.port}/  (only writer)", flush=True)
    print("Research chart stays on Streamlit 8501. This desk: start/stop, feed, live picks, money.", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped", flush=True)


if __name__ == "__main__":
    main()
