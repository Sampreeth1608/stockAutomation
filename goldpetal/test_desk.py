"""Gold Petal trading station on 8501."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent


def test_station_is_the_operator_page() -> None:
    html = (ROOT / "station.html").read_text(encoding="utf-8")
    assert "Gold Petal Desk" in html
    assert "Gold Petal Station" not in html
    assert 'rel="manifest"' in html
    assert "/manifest.webmanifest" in html
    assert "Start bot" in html
    assert "Stop bot" in html
    assert "Start feed only" in html
    assert "Save strategies" in html
    assert "Paper is already running" in html
    assert "Stop live" in html
    assert ">Paper only<" not in html
    assert "Wallet ₹" in html
    assert "Arm live" in html
    assert "Unlock live" not in html
    assert 'data-tab="live"' in html
    assert "Exit all" in html
    assert "/api/desk/arm" in html
    assert 'data-tab="open">Paper Positions</button>' in html
    assert "No paper positions" in html
    assert "Blotter" in html
    assert 'data-tab="score">Paper P&amp;L</button>' in html
    assert "paper AC ₹" in html
    assert "No paper P&L yet" in html
    assert "Downloads" in html
    assert 'data-tab="you"' in html
    assert 'id="you-strip"' not in html
    assert 'id="fit-strip"' not in html
    assert "You · trade" in html
    tabs = html.split('id="tabs"')[1].split("</div>")[0]
    assert "Enable regime" not in tabs
    assert 'id="btn-regime"' not in tabs
    assert 'id="mood-box"' not in tabs
    header = html.split('<header class="top">')[1].split("</header>")[0]
    assert 'id="mood-box"' not in header
    assert 'id="mood-pill"' not in header
    assert 'id="btn-regime"' not in html
    assert "Enable regime" not in html
    assert "function renderMood" not in html
    assert "loadMood.busy" not in html
    assert 'id="mood-pill"' not in html
    assert "/api/desk/regime" not in html
    assert ".mood-box" not in html
    assert "You — trade and teach" in html
    assert "/api/capture" in html
    assert "/api/capture/learn" in html
    assert "Send to Angel" in html
    assert "Learn my style" in html
    assert "type YOU" in html
    assert "NO TRADE" in html
    assert "Send to Angel" in html
    assert "you_skipped_rule_would_take" in html
    assert "Mon–Fri 09:00–23:30" in html
    assert "Gold Petal running" in html
    assert "Gold Petal stopped" in html
    assert "function goldPetalSessionNow" in html
    run_fn = html.split("function goldPetalIsRunning")[1].split("function renderSessionLine")[0]
    assert "goldpetal_running" in run_fn
    sess_fn = html.split("function renderSessionLine")[1].split("function thead")[0]
    assert "waiting for desk" in sess_fn
    quote = html.split("function renderQuote(")[1].split("function renderKpis")[0]
    assert "buy qty" not in quote
    assert "sell qty" not in quote
    assert "ticks" not in quote
    assert "last_tick_at" not in quote
    assert "slice(11, 19)" not in quote
    assert "waiting for quote" in quote
    assert "tape stopped" not in quote
    assert "quote stale" in html
    assert "LIVE PATH" not in html
    desk_pills = html.split("function renderDesk")[1].split("function renderAll")[0]
    assert "live_unlocked && live.dry_run" not in desk_pills
    assert "gp-header-v54" in html
    assert " · v54" in html
    assert "books_health" in html
    assert "function renderBooksWhy" in html
    assert "waiting_1h_close" in html
    assert "need_prev_1h" in html
    assert "squares leftover Angel even in Paper" in html
    assert "Exit flattens that book only after you Arm live" not in html
    assert "Angel CLOSE only if this book is live-armed" not in html
    assert "not today's Live Lots" in html
    assert "Raising Live Lots does not rewrite" in html
    assert "data-intraday" in html
    assert "Intraday" in html
    assert "function collectIntraday" in html
    assert "intra !== null" in html
    assert "lastDesk" in html.split("function collectIntraday")[1].split("function collectAllocations")[0]
    assert "waiting for desk" in html
    assert "Gold Petal hours" in html
    assert "waiting for desk…" in html
    assert "lastMood" not in html
    assert "loadAmise" not in html
    assert "loadMl" not in html
    assert "loadLab" not in html
    assert 'data-tab="ml"' not in html
    assert 'data-tab="lab"' not in html
    assert 'data-tab="amise"' not in html
    assert "AMISE" not in html
    assert "collectArmBody(\"keep\")" in html
    assert "PAPER · Angel open" in html
    assert "Save does not switch you to Paper" in html
    assert "Enable regime is off — observe only" not in html
    assert "withDeskAuth" in html
    assert "X-GP-CSRF" in html
    assert "bootAuth" in html
    assert 'id="desk-totp"' in html
    assert 'id="btn-lock-desk"' in html
    assert "data-exit=" in html
    assert "/api/desk/flatten" in html
    assert "Exit all" in html
    assert 'id="live-panel"' in html
    assert "Tick <b>Lots</b>" in html
    assert 'data-size="lots"' in html
    assert 'data-size="capital"' in html
    assert "liveMoneyDirty" in html
    assert "live_budget_inr" in html
    assert "liveBudgetDisplay" in html
    assert 'id="live-max"' in html
    assert 'max="1000"' in html
    assert "SIZE if ceiling" in html
    assert "size_confirm" in html
    assert "WR% AC" in html
    assert "Paper AC" in html
    assert "live-kpi-pnl" in html
    assert 'id="live-pnl-open"' in html
    assert 'id="live-pnl-books"' in html
    assert "Live P&amp;L by book" in html
    assert "function livePosRows" in html
    assert "function liveBookScoreRows" in html
    assert "Live positions" in html
    assert "Live round-trips" not in html
    assert 'id="blotter-live-closed"' in html
    assert 'id="dl-orders-body"' in html
    assert 'id="btn-regime"' not in html
    assert "/api/desk/regime" not in html
    assert "function fillLiveClosed" in html
    assert "function fillAngelOrders" in html
    assert "<th>Pos</th>" in html
    assert "p.positions" in html
    assert "Real Angel P&amp;L" in html
    assert "function renderLivePnl" in html
    assert "Overnight gap papers now" in html
    assert "S18 / S19 / S20 / overnight gap stay paper" in html
    assert "live_eligible" in html
    assert "exact IST click time" in html
    assert "tape_lag_ms" in html
    assert "Let it trade" in html
    assert "You vs mimic" in html
    assert "/api/capture/go" in html
    assert "whole day" in html
    assert "30m" in html
    assert "lastLtpChangeAt" in html
    assert "sess.ltp" in html
    hist = html.split("async function loadHistory()")[1].split("async function act")[0]
    assert "tape_live" not in hist
    desk_fn = html.split("function renderDesk")[1].split("function renderAll")[0]
    assert "tape_live" not in desk_fn
    assert "const money " not in desk_fn
    assert "moneyEl" in desk_fn
    assert "tape dead" not in html
    assert "function tapeAgeSec" in html
    assert 'data-tab="why"' not in html
    assert 'data-tab="ticks"' not in html
    assert 'data-tab="signals"' not in html
    assert 'id="books"' not in html
    assert "Approve already papers" in html
    assert "only Angel" in html
    assert "WR% AC of 40" in html
    assert "no book at 40% WR% AC yet" in html
    assert 'id="paper-books"' not in html
    assert "data-in=" not in html
    assert "function currentInBot" in html
    assert "Save strategies + live size" in html
    assert "Download signals CSV" in html
    assert "/api/export/signals.csv" in html
    assert "Copy signals → Sheets" in html
    assert "function renderDlPreview" in html
    assert "S18_OHLC_VOL_HTF" in html
    assert "S19_BODY_CLOSE_1H" in html
    assert "S20_FADE_HL" in html
    assert "OVERNIGHT_GAP" in html
    assert "overnight gap" in html
    assert "overnight gap stay paper until they hit 40%" in html
    assert "Download all (ZIP)" in html
    assert "Copy trades → Sheets" in html
    assert "/api/export/pack.zip" in html
    assert "/api/export/trades.csv" in html
    assert "/api/sheets/pack.zip" in html
    assert "/api/sheets/monitor.zip" in html
    assert "Download phone monitor" in html
    assert "Copy status → Sheets" in html
    assert 'data-tab="dl"' in html
    assert "/api/tape" in html
    assert "/api/history" in html
    assert "/api/s14/calc" not in html
    assert 'data-tab="s14"' not in html
    assert "S14 calc" not in html
    assert 'href="/full"' in html
    assert 'href="/lite"' in html
    assert "minimumFractionDigits: 2" in html
    assert "PAPER_BOOKS" in html
    assert "S16_HHHL_WICK_1H" in html
    paper = html.split("const PAPER_BOOKS")[1].split("];")[0]
    assert "S16_HHHL_WICK_1H" in paper
    assert "S18_OHLC_VOL_HTF" in paper
    assert "S19_BODY_CLOSE_1H" in paper
    assert "S20_FADE_HL" in paper
    assert "OVERNIGHT_GAP" in paper
    assert "S13_HHHL_DAY" in paper
    assert "S11_DISCOVERED" not in paper
    assert "S4_OVERNIGHT" not in paper
    assert "S12_HHHL30" not in paper
    assert "S14_WICK30_STRICT" not in paper
    assert "S15_WICK30_NOWICK" not in paper
    assert "s14-chart" not in html
    assert "fonts.googleapis.com" in html
    assert "Source Sans 3" in html
    assert "Fraunces" in html
    assert "IBM Plex Mono" in html
    assert "font: 14px/1.45" in html


def test_lite_html_is_compact_controls() -> None:
    html = (ROOT / "lite.html").read_text(encoding="utf-8")
    assert "Start bot" in html
    assert 'rel="manifest"' in html
    assert "Save strategies" in html
    assert "Gold Petal Desk" in html
    assert "href=\"/\"" in html
    assert "You — trade" in html
    assert "whole day" in html
    assert "30m" in html
    assert "Let it trade" in html
    assert "/api/capture/go" in html
    assert "/#you" in html
    assert "/api/mood" not in html
    assert "id=\"mood-line\"" not in html
    assert "press Enable regime" not in html
    assert "Enable regime" not in html
    assert 'id="btn-regime"' not in html
    assert "/api/desk/regime" not in html
    assert "PAPER · Angel open" in html
    assert "collectArmBody(\"keep\")" in html
    assert "live path" not in html
    assert "live_unlocked && live.dry_run" not in html
    assert "squares leftover Angel even in Paper" in html
    assert "Angel CLOSE only if live-armed" not in html
    assert "AMISE" not in html
    assert "loadMl" not in html
    assert "loadLab" not in html
    assert "After charges" in html
    assert "Approve already papers" in html
    assert "only Angel" in html
    assert "40% WR% AC" in html
    assert "Overnight gap papers now" in html
    assert "overnight gap stay paper until they hit 40%" in html
    assert "no book at 40% WR% AC yet" in html
    assert 'id="paper-books"' not in html
    assert "data-in=" not in html
    assert "function currentInBot" in html
    assert "Save strategies + live size" in html
    assert 'data-size="lots"' in html
    assert 'data-size="capital"' in html
    assert "data-intraday" in html
    assert "Intraday" in html
    assert "function collectIntraday" in html
    assert "intra !== null" in html
    assert "lastLite" in html.split("function collectIntraday")[1].split("function collectAllocations")[0]
    assert "Tick Lots" in html
    assert "liveMoneyDirty" in html
    assert "liveBudgetDisplay" in html
    assert "SIZE if ceiling" in html
    assert "size_confirm" in html
    assert 'max="1000"' in html
    assert "live_eligible" in html
    assert "data-exit=" in html
    assert "/api/desk/flatten" in html
    assert "Exit all" in html
    assert "Arm live" in html
    assert "/api/desk/arm" in html
    assert "Unlock live" not in html
    assert "withDeskAuth" in html
    assert "X-GP-CSRF" in html
    assert "bootAuth" in html
    assert 'id="desk-totp"' in html
    assert "Live books + money" in html
    assert "live-pnl-line" in html
    assert 'id="live-pnl-open"' in html
    assert 'id="live-pnl-books"' in html
    assert "Live P&amp;L by book" in html
    assert "function livePosRows" in html
    assert "Live positions" in html
    assert "Real Angel P&amp;L" in html
    assert "OVERNIGHT_GAP" in html
    assert "overnight gap" in html
    assert "/api/ml" not in html
    assert "Watch" not in html
    assert "Download all (ZIP)" not in html
    assert "/api/history" not in html
    assert "--bg:#ffffff" in html.replace(" ", "")


def test_full_html_keeps_watch_downloads() -> None:
    html = (ROOT / "desk.html").read_text(encoding="utf-8")
    assert "Watch" in html
    assert "Download all (ZIP)" in html
    assert "Copy trades → Sheets" in html
    assert "Download phone monitor" in html
    assert "/api/sheets/monitor.zip" in html
    assert "Save strategies" in html
    assert "data-exit=" in html
    assert "/api/desk/flatten" in html
    assert "--bg:#ffffff" in html.replace(" ", "")
    assert "/api/tape" in html
    assert "loadTape" in html
    assert "/api/history" in html
    assert "S14 calculation" in html
    assert "/api/s14/calc" in html
    assert "Why win rate is low" in html
    assert "/api/analysis" in html
    assert "why-ml" in html
    assert "h.lots" in html
    assert "trading station" in html
    assert "withDeskAuth" in html
    assert "X-GP-CSRF" in html
    assert "bootAuth" in html
    assert "OVERNIGHT_GAP" in html
    assert "overnight gap" in html


def test_control_panel_serves_station_on_8501() -> None:
    text = (ROOT / "control_panel.py").read_text(encoding="utf-8")
    assert "load_desk_html" in text
    assert "STATION_HTML_PATH" in text
    assert "LITE_HTML_PATH" in text
    assert "/lite" in text
    assert "/api/desk" in text
    assert "/api/ml" not in text
    assert "/api/research" not in text
    assert "/api/amise" not in text
    assert "/api/s11" not in text
    assert "s11_desk" not in text
    assert "research_desk" not in text
    assert "/api/capture" in text
    assert "/api/capture/learn" in text
    assert "/api/capture/go" in text
    assert "after_new_example" in text
    assert "start_mimic_paper" in text
    assert "request_you_order" in text
    assert "/api/mood" in text
    assert "mood_desk_payload" in text
    assert "record_human" in text
    assert "/api/tape" in text
    assert "/api/s14/calc" in text
    assert "/api/analysis" in text
    assert "/api/desk/books" in text
    assert "/api/desk/flatten" in text
    assert "request_flatten" in text
    assert "request_flatten_all" in text
    assert "/api/desk/arm" in text
    assert "apply_desk_arm" in text
    assert "desk_http_auth" in text
    assert "/api/desk/login" in text
    assert "/api/desk/logout" in text
    assert "/api/desk/session" in text
    assert "public_bind_blocked" in text
    assert "load_login_html" in text
    assert '"trace": traceback' not in text
    assert "internal error" in text
    assert "_internal_error_bytes" in text
    assert "_amise_auto_loop" not in text
    runner = (ROOT / "run_strategy.py").read_text(encoding="utf-8")
    assert "emit_desk_flatten" in runner
    assert "apply_pending_flattens" in runner
    assert "square_fill_leftover" in runner
    assert "force_live=True" in runner
    assert "def book_health" in runner
    assert '"books": book_health()' in runner
    assert "mirror_positions_from_signals" in runner
    assert "live_only=not dry_run" in runner
    assert "FORMULA_GATE_BOOKS" in runner
    assert "MOOD_EXEMPT_BOOKS" in runner
    assert "/api/bot/start" in text
    assert "desk_snapshot" in text
    assert "session_status" in text
    assert "goldpetal_running" in text
    assert "bind_live_ticks_db" in text
    assert "last_tick_snapshot" in text
    assert "default=8501" in text
    assert "/api/sheets/pack.zip" in text
    assert "/api/sheets/monitor.zip" in text
    assert "monitor_sheet_zip_bytes" in text
    assert "except ImportError" in text
    assert "/manifest.webmanifest" in text
    assert "MANIFEST_PATH" in text
    sh = (ROOT / "scripts" / "run_desk_vm.sh").read_text(encoding="utf-8")
    assert "control_panel.py" in sh
    assert "8501" in sh
    assert "streamlit run analytics/app.py" not in sh.split("pkill")[0]
    assert "Save strategies" in sh
    assert "Unlock desk" in sh
    assert "gp-desk-login" in sh
    assert "0.0.0.0" in sh
    assert "gp-header-v" in sh
    assert "preflight_desk" in sh
    assert "ensure_desk_imports" in sh
    assert "sync_desk_runtime.sh" in sh
    assert "import control_panel" in sh
    assert "Leaving the running station alone" in sh
    assert "old station.html on disk" not in sh
    station = (ROOT / "station.html").read_text(encoding="utf-8")
    assert "Save strategies" in station
    mac = (ROOT / "scripts" / "print_open_on_mac.sh").read_text(encoding="utf-8")
    assert "trading station" in mac
    assert "GoldPetal.command" in mac
    assert "git show origin" in mac
    assert "live_orders.py" in mac or "sync_desk_runtime.sh" in mac
    assert "HARD_LIVE_MAX_LOTS" in mac
    sync = (ROOT / "scripts" / "sync_desk_runtime.sh").read_text(encoding="utf-8")
    assert "goldpetal/live_orders.py" in sync
    assert "goldpetal/live_readiness.py" in sync
    assert "goldpetal/control_state.py" in sync
    assert "goldpetal/market_mood.py" in sync
    assert "goldpetal/run_strategy.py" in sync
    assert "goldpetal/position_safety.py" in sync
    assert "goldpetal/strategy_s16.py" in sync
    assert "goldpetal/strategy_s18.py" in sync
    assert "goldpetal/trade_learner.py" in sync
    assert "goldpetal/entry_gates.py" in sync
    assert "goldpetal/.env.example" in sync
    assert "goldpetal/portfolio.py" in sync
    assert "goldpetal/storage.py" in sync
    assert "goldpetal/analytics/env_bridge.py" in sync
    assert "goldpetal/overnight_gap.py" in sync
    assert "goldpetal/strategy_overnight_gap.py" in sync
    assert "chmod +x" in mac
    assert "--tunnel-through-iap" in mac
    cmd = (ROOT / "scripts" / "GoldPetal.command").read_text(encoding="utf-8")
    assert "Darwin" in cmd
    assert "gcloud compute ssh" in cmd
    assert "127.0.0.1:8501" in cmd
    assert "seq 1 40" in cmd
    assert "curl -sL" in cmd
    assert "Google Chrome" in cmd
    assert "Gold Petal v54" in cmd
    assert "Starting the station on the VM" in cmd
    assert "Connection refused" in cmd
    assert "GP_QUIET_OPEN=1" in cmd
    assert "--tunnel-through-iap" in cmd
    assert "8.231.125.120" in cmd
    assert 'cd "$(dirname "$0")/.."' not in cmd
    from live_readiness import LIVE_ELIGIBLE_BOOKS, NEVER_LIVE_BOOKS, PAPER_ONLY_BOOKS

    assert "S13_HHHL_DAY" in LIVE_ELIGIBLE_BOOKS
    assert "S16_HHHL_WICK_1H" in LIVE_ELIGIBLE_BOOKS
    assert "S18_OHLC_VOL_HTF" in PAPER_ONLY_BOOKS
    assert "S18_OHLC_VOL_HTF" not in LIVE_ELIGIBLE_BOOKS
    assert "S21_AMISE" not in LIVE_ELIGIBLE_BOOKS
    assert "OVERNIGHT_GAP" in PAPER_ONLY_BOOKS
    assert "OVERNIGHT_GAP" not in NEVER_LIVE_BOOKS
    assert "OVERNIGHT_GAP" not in LIVE_ELIGIBLE_BOOKS
    assert "FLOW_BRAIN" in NEVER_LIVE_BOOKS
    from live_readiness import LIVE_WR_MIN_PCT, summary_qualifies_live

    assert LIVE_WR_MIN_PCT == 40.0
    assert summary_qualifies_live({"closed": 5, "win_rate_after_charges": 40.0}) is True
    assert summary_qualifies_live({"closed": 5, "win_rate_after_charges": 39.9}) is False
    tunnel = (ROOT / "panel_tunnel.sh").read_text(encoding="utf-8")
    assert "--tunnel-through-iap" in tunnel
    assert "exec ssh -N -L" not in tunnel
    launcher = (ROOT / "scripts" / "open_desk_mac.sh").read_text(encoding="utf-8")
    assert "GoldPetal.command" in launcher
    man = (ROOT / "manifest.webmanifest").read_text(encoding="utf-8")
    assert '"display": "standalone"' in man
    assert '"name": "Gold Petal Desk"' in man or '"name":"Gold Petal Desk"' in man.replace(" ", "")


def test_desk_payload_includes_session() -> None:
    from control_panel import desk_payload

    payload = desk_payload()
    sess = payload["session"]
    assert "open" in sess
    assert sess["open_hhmm"]
    assert sess["close_hhmm"]
    assert "now_ist" in sess
    assert "label" in sess
    assert "tape_live" in sess
    assert "ltp" in sess
    assert "goldpetal_running" in sess
    assert isinstance(sess["goldpetal_running"], bool)
    assert "flatten" in payload
    assert "by_strategy" in payload["flatten"]
    assert isinstance(payload["books_health"], dict)


def test_login_html_is_the_gate() -> None:
    html = (ROOT / "login.html").read_text(encoding="utf-8")
    assert "gp-desk-login" in html
    assert "Unlock desk" in html
    assert "/api/desk/login" in html
    assert "/api/desk/auth" in html
    auth = (ROOT / "desk_http_auth.py").read_text(encoding="utf-8")
    assert "COOKIE_NAME" in auth
    assert "CSRF_HEADER" in auth
    assert "PUBLIC_BIND_HOSTS" in auth
    assert "path_requires_totp" in auth


def test_streamlit_cannot_write() -> None:
    from operator_desk import streamlit_control_allowed, streamlit_write_blocked

    res = streamlit_write_blocked("Live deploy")
    assert res["ok"] is False
    assert "8501" in res["error"]
    ok, _ = streamlit_control_allowed({"live_unlocked": True})
    assert ok is False


if __name__ == "__main__":
    test_station_is_the_operator_page()
    test_lite_html_is_compact_controls()
    test_full_html_keeps_watch_downloads()
    test_control_panel_serves_station_on_8501()
    test_desk_payload_includes_session()
    test_login_html_is_the_gate()
    test_streamlit_cannot_write()
    print("ALL test_desk OK")
