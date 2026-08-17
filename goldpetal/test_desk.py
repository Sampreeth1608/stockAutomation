"""New 8787 operator desk: one writer, no Streamlit overwrite."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent


def test_desk_html_is_the_operator_page() -> None:
    html = (ROOT / "desk.html").read_text(encoding="utf-8")
    assert "Start bot" in html
    assert "Stop bot" in html
    assert "Start feed only" in html
    assert "Save strategies" in html
    assert "Watch" in html
    assert "Download all (ZIP)" in html
    assert "Download trades CSV" in html
    assert "Copy trades → Sheets" in html
    assert "/api/history" in html or "loadHistory" in html
    assert "Live pick" in html
    assert "Paper only" in html
    assert "Unlock live" in html
    assert "cannot overwrite" in html
    assert "s14-chart" not in html
    assert "btn-s14-pull" not in html


def test_control_panel_serves_desk_file() -> None:
    text = (ROOT / "control_panel.py").read_text(encoding="utf-8")
    assert "load_desk_html" in text
    assert "/api/desk" in text
    assert "/api/desk/books" in text
    assert "/api/bot/start" in text
    assert "/api/bot/stop" in text
    assert "/api/feed/start" in text
    assert "/api/history" in text
    assert "history_payload" in text
    assert "HTML_PAGE = r" not in text


def test_streamlit_still_cannot_write() -> None:
    from operator_desk import streamlit_control_allowed, streamlit_write_blocked

    res = streamlit_write_blocked("Live deploy")
    assert res["ok"] is False
    assert "8787" in res["error"]
    ok, _ = streamlit_control_allowed({"live_unlocked": True})
    assert ok is False


if __name__ == "__main__":
    test_desk_html_is_the_operator_page()
    test_control_panel_serves_desk_file()
    test_streamlit_still_cannot_write()
    print("ALL test_desk OK")
