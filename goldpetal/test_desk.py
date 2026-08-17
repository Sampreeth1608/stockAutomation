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
    sh = (ROOT / "scripts" / "run_control_panel.sh").read_text(encoding="utf-8")
    assert "desk folder" in sh
    assert "Save strategies" in sh
    assert "HTML_PAGE = r" not in text


def test_streamlit_other_tabs_cannot_write() -> None:
    from operator_desk import streamlit_control_allowed, streamlit_write_blocked

    res = streamlit_write_blocked("Live deploy")
    assert res["ok"] is False
    assert "Desk" in res["error"]
    assert "8501" in res["error"]
    ok, _ = streamlit_control_allowed({"live_unlocked": True})
    assert ok is False


def test_streamlit_desk_tab_is_the_writer() -> None:
    app = (ROOT / "analytics" / "app.py").read_text(encoding="utf-8")
    assert "render_operator_desk" in app
    assert '"Desk"' in app
    assert app.find('"Desk"') < app.find('"S14 chart"')
    tab = (ROOT / "analytics" / "operator_tab.py").read_text(encoding="utf-8")
    assert "Save strategies" in tab
    assert "Start bot" in tab
    assert "Start feed only" in tab
    assert "Download all (ZIP)" in tab
    assert "Copy trades → Sheets" in tab
    sh = (ROOT / "scripts" / "run_desk_vm.sh").read_text(encoding="utf-8")
    assert "first tab Desk" in sh


if __name__ == "__main__":
    test_desk_html_is_the_operator_page()
    test_control_panel_serves_desk_file()
    test_streamlit_other_tabs_cannot_write()
    test_streamlit_desk_tab_is_the_writer()
    print("ALL test_desk OK")
