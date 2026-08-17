"""Gold Petal exchange-candle HTML/CSV sheet."""

from __future__ import annotations

from pathlib import Path

from explain_s14_candles import FORMULA, settle_s14_from_walk, walk_candles
from s14_exchange_sheet import (
    HTML_NAME,
    display_bar_row,
    load_sheet_meta,
    write_s14_workbook,
)
from test_explain_s14_candles import EXCHANGE_1D_AUG


def test_display_row_prints_chart_values() -> None:
    rows = walk_candles(EXCHANGE_1D_AUG)
    d = display_bar_row(rows[0])
    assert d["open"] == 14379
    assert d["high"] == 14418
    assert d["low"] == 14300
    assert d["close"] == 14324
    assert d["side"] == "SHORT"
    assert d["O=H"] in {"Y", "N"}
    assert d["O=L"] == "N"


def test_workbook_html_and_csv() -> None:
    import tempfile

    rows = walk_candles(EXCHANGE_1D_AUG)
    settled = settle_s14_from_walk(rows, tf="1d:s14", lots=100, fees=True)
    with tempfile.TemporaryDirectory() as td:
        out = Path(td)
        meta = write_s14_workbook(
            {"1d": rows},
            [settled],
            out_dir=out,
            symbol="GOLDPETAL31AUG26FUT",
            source="test",
            lots=100,
            fees=True,
            formula=FORMULA,
        )
        html = (out / HTML_NAME).read_text(encoding="utf-8")
        assert "14324" in html
        assert "Gold Petal exchange candles" in html
        assert 'canvas class="ohlc"' in html
        assert "drawOhlc" in html
        csv_text = (out / "1d.csv").read_text(encoding="utf-8")
        assert csv_text.splitlines()[0].startswith("time,open,high,low,close")
        assert "14324" in csv_text
        pnl = (out / "pnl.csv").read_text(encoding="utf-8")
        assert "after_tax_inr" in pnl
        loaded = load_sheet_meta(out)
        assert loaded["ok"] is True
        assert loaded["html"] == meta["html"]
        assert "1d" in loaded["tfs"]


def test_format_table_no_column_binary() -> None:
    from s14_exchange_sheet import format_table

    text = format_table(
        [{"time": "2026-08-03", "open": 14379, "close": 14324}],
        ["time", "open", "close"],
    )
    assert "14324" in text
    assert "open" in text.splitlines()[0]


def test_refresh_status_idle() -> None:
    import tempfile

    from s14_exchange_sheet import refresh_status

    with tempfile.TemporaryDirectory() as td:
        st = refresh_status(Path(td))
        assert st["running"] is False
        lock = Path(td) / "refresh.lock"
        lock.write_text('{"pid": 99999999, "started": "x"}', encoding="utf-8")
        st = refresh_status(Path(td))
        assert st["running"] is False
        assert not lock.exists()


def test_panel_mentions_s14_sheet() -> None:
    text = Path(__file__).resolve().parent.joinpath("control_panel.py").read_text(
        encoding="utf-8"
    )
    assert "/s14-sheet" in text
    assert "btn-s14-copy" in text
    assert "Gold Petal exchange candles" in text
    assert "s14-chart" in text
    assert "btn-s14-pull" in text
    assert "127.0.0.1:8787" in text


if __name__ == "__main__":
    test_display_row_prints_chart_values()
    test_workbook_html_and_csv()
    test_format_table_no_column_binary()
    test_refresh_status_idle()
    test_panel_mentions_s14_sheet()
    print("ALL test_s14_exchange_sheet OK")
