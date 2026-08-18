"""Lightweight HTML operator desk on 8501."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent


def test_lite_html_is_the_operator_page() -> None:
    html = (ROOT / "lite.html").read_text(encoding="utf-8")
    assert "Start bot" in html
    assert "Stop bot" in html
    assert "Start feed only" in html
    assert "Save strategies" in html
    assert "Paper only" in html
    assert "Unlock live" in html
    assert 'href="/full"' in html
    assert "Trade list" in html
    assert "S14 calculation" in html
    assert "Watch" not in html
    assert "Download all (ZIP)" not in html
    assert "/api/history" not in html
    assert "s14-chart" not in html
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


def test_control_panel_serves_lite_on_8501() -> None:
    text = (ROOT / "control_panel.py").read_text(encoding="utf-8")
    assert "load_desk_html" in text
    assert "LITE_HTML_PATH" in text
    assert "/api/desk" in text
    assert "/api/tape" in text
    assert "/api/s14/calc" in text
    assert "/api/analysis" in text
    assert "/api/desk/books" in text
    assert "/api/bot/start" in text
    assert "desk_snapshot" in text
    assert "default=8501" in text
    sh = (ROOT / "scripts" / "run_desk_vm.sh").read_text(encoding="utf-8")
    assert "control_panel.py" in sh
    assert "8501" in sh
    assert "streamlit run analytics/app.py" not in sh.split("pkill")[0]
    assert "Save strategies" in sh
    lite = (ROOT / "lite.html").read_text(encoding="utf-8")
    assert "Save strategies" in lite


def test_streamlit_cannot_write() -> None:
    from operator_desk import streamlit_control_allowed, streamlit_write_blocked

    res = streamlit_write_blocked("Live deploy")
    assert res["ok"] is False
    assert "8501" in res["error"]
    ok, _ = streamlit_control_allowed({"live_unlocked": True})
    assert ok is False


if __name__ == "__main__":
    test_lite_html_is_the_operator_page()
    test_full_html_keeps_watch_downloads()
    test_control_panel_serves_lite_on_8501()
    test_streamlit_cannot_write()
    print("ALL test_desk OK")
