"""Gold Petal trading station on 8501."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent


def test_station_is_the_operator_page() -> None:
    html = (ROOT / "station.html").read_text(encoding="utf-8")
    assert "Gold Petal Station" in html
    assert "Start bot" in html
    assert "Stop bot" in html
    assert "Start feed only" in html
    assert "Save strategies" in html
    assert "Paper only" in html
    assert "Unlock live" in html
    assert "Positions" in html
    assert "Blotter" in html
    assert "Downloads" in html
    assert 'data-tab="ml"' in html
    assert 'data-tab="lab"' in html
    assert 'data-tab="amise"' in html
    assert 'data-tab="you"' in html
    assert 'id="you-strip"' in html
    assert "You · capture" in html
    assert "Human trade capture" in html
    assert "/api/capture" in html
    assert "/api/mood" in html
    assert 'id="mood-pill"' in html
    assert 'id="fit-strip"' in html
    assert "stand down" in html
    assert "fall starting" in html
    assert "MOOD_GATE" in html
    assert "observe only" in html
    assert "NO TRADE" in html
    assert "Does not place an order" in html
    assert "you_skipped_rule_would_take" in html
    assert "Mon–Fri 09:00–23:30" in html
    assert "Gold Petal running" in html
    assert "Gold Petal stopped" in html
    assert "function renderSessionLine" in html
    assert "function goldPetalSessionNow" in html
    quote = html.split("function renderQuote(")[1].split("function moodKind")[0]
    assert "buy qty" not in quote
    assert "sell qty" not in quote
    assert "ticks" not in quote
    assert "last_tick_at" not in quote
    assert "slice(11, 19)" not in quote
    assert "waiting for quote" in quote
    assert "tape stopped" in quote
    assert "gp-header-v4" in html
    assert " · v4" in html
    assert "tape dead" in html
    assert "function tapeAgeSec" in html
    assert "Approve → paper" in html
    assert "AI Research Lab" in html
    assert "/api/research" in html
    assert "/api/amise" in html
    assert "AMISE" in html
    assert "Profit Guardian" in html
    assert "RESEARCH_FACTORY" not in html.split("const PAPER_BOOKS")[1].split("];")[0]
    assert "S18_OHLC_VOL_HTF" in html
    assert "S19_BODY_CLOSE_1H" in html
    assert "S20_FADE_HL" in html
    assert "S21_AMISE" in html
    assert "Run factory" in html
    assert "After charges ₹" in html
    assert "weekly_s18.sh" in html
    assert 'value="S18_OHLC_VOL_HTF"' in html
    assert "/api/ml" in html
    assert "/api/s11/activate" in html
    assert "/api/proposals/" in html
    assert "S11_PACK_PATH" in html
    assert "Download all (ZIP)" in html
    assert "Copy trades → Sheets" in html
    assert "/api/export/pack.zip" in html
    assert "/api/export/trades.csv" in html
    assert "/api/sheets/pack.zip" in html
    assert 'data-tab="dl"' in html
    assert "/api/tape" in html
    assert "/api/history" in html
    assert "/api/analysis" in html
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
    assert "S21_AMISE" in paper
    assert "S24_AMISE" in paper
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
    assert "Save strategies" in html
    assert "Trading station" in html
    assert "ML / S11 + S18" in html
    assert "Research Lab" in html
    assert "You — capture" in html
    assert "/#you" in html
    assert "/api/mood" in html
    assert "id=\"mood-line\"" in html
    assert "MOOD_GATE" in html
    assert "Mon–Fri 09:00–23:30" in html
    assert "/#lab" in html
    assert "/#amise" in html
    assert "AMISE" in html
    assert "after charges" in html
    assert "weekly_s18.sh" in html
    assert "/api/ml" in html
    assert "Watch" not in html
    assert "Download all (ZIP)" not in html
    assert "/api/history" not in html
    assert "--bg:#ffffff" in html.replace(" ", "")


def test_full_html_keeps_watch_downloads() -> None:
    html = (ROOT / "desk.html").read_text(encoding="utf-8")
    assert "Watch" in html
    assert "Download all (ZIP)" in html
    assert "Copy trades → Sheets" in html
    assert "Save strategies" in html
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


def test_control_panel_serves_station_on_8501() -> None:
    text = (ROOT / "control_panel.py").read_text(encoding="utf-8")
    assert "load_desk_html" in text
    assert "STATION_HTML_PATH" in text
    assert "LITE_HTML_PATH" in text
    assert "/lite" in text
    assert "/api/desk" in text
    assert "/api/ml" in text
    assert "/api/research" in text
    assert "/api/amise" in text
    assert "amise_desk_payload" in text
    assert "/api/capture" in text
    assert "/api/mood" in text
    assert "mood_desk_payload" in text
    assert "record_human" in text
    assert "decide_research" in text
    assert "/api/s11/activate" in text
    assert "decide_proposal_for_desk" in text
    assert "/api/tape" in text
    assert "/api/s14/calc" in text
    assert "/api/analysis" in text
    assert "/api/desk/books" in text
    assert "/api/bot/start" in text
    assert "desk_snapshot" in text
    assert "session_status" in text
    assert "goldpetal_running" in text
    assert "last_tick_snapshot" in text
    assert "default=8501" in text
    sh = (ROOT / "scripts" / "run_desk_vm.sh").read_text(encoding="utf-8")
    assert "control_panel.py" in sh
    assert "8501" in sh
    assert "streamlit run analytics/app.py" not in sh.split("pkill")[0]
    assert "Save strategies" in sh
    assert "gp-header-v4" in sh
    station = (ROOT / "station.html").read_text(encoding="utf-8")
    assert "Save strategies" in station
    mac = (ROOT / "scripts" / "print_open_on_mac.sh").read_text(encoding="utf-8")
    assert "trading station" in mac


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
    assert "goldpetal_running" in sess
    assert isinstance(sess["goldpetal_running"], bool)


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
    test_streamlit_cannot_write()
    print("ALL test_desk OK")
