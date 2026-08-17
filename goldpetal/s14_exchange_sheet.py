#!/usr/bin/env python3
"""Gold Petal exchange-candle sheet (Angel/MCX OHLC + S14 formula).

Stable files under data/s14_sheet/ so you can reopen them any time:

  GoldPetal_S14.html   ← open in a browser (the sheet)
  30m.csv  1h.csv  1d.csv
  pnl.csv  trades.csv

  ./venv/bin/python s14_exchange_sheet.py --from-csv data/backtests/s14_candles
  ./venv/bin/python explain_s14_candles.py --from-csv data/backtests/s14_candles --tf 30m,1h,1d --no-print-bars

On the control panel: http://127.0.0.1:8787/s14-sheet
"""

from __future__ import annotations

import argparse
import csv
import html
import io
import json
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from backtest_hhhl_candles import TfResult, Trade

IST = ZoneInfo("Asia/Kolkata")

SHEET_DIR = Path("data/s14_sheet")
HTML_NAME = "GoldPetal_S14.html"
BAR_COLUMNS = [
    "time",
    "open",
    "high",
    "low",
    "close",
    "upper",
    "lower",
    "O=H",
    "O=L",
    "rule",
    "side",
    "action",
    "pos_from",
    "pos_to",
]
PNL_COLUMNS = [
    "tf",
    "bars",
    "trades",
    "long",
    "short",
    "win_pct",
    "pts",
    "gross_inr",
    "fees_inr",
    "after_tax_inr",
    "max_dd_inr",
]
TRADE_COLUMNS = [
    "tf",
    "side",
    "entry_time",
    "entry_px",
    "exit_time",
    "exit_px",
    "pts_per_lot",
    "gross_inr",
    "fees_inr",
    "after_tax_inr",
    "lots",
]


def _num(value: Any) -> float | int:
    x = float(value)
    if x == int(x):
        return int(x)
    return round(x, 1)


def display_bar_row(walk: dict[str, Any]) -> dict[str, Any]:
    side = str(walk.get("side") or "skip")
    side_out = "skip" if side.lower() == "skip" else side.upper()
    return {
        "time": str(walk.get("time") or ""),
        "open": _num(walk["open"]),
        "high": _num(walk["high"]),
        "low": _num(walk["low"]),
        "close": _num(walk["close"]),
        "upper": _num(walk.get("upper") or 0),
        "lower": _num(walk.get("lower") or 0),
        "O=H": "Y" if walk.get("open_eq_high") else "N",
        "O=L": "Y" if walk.get("open_eq_low") else "N",
        "rule": str(walk.get("rule") or ""),
        "side": side_out,
        "action": str(walk.get("action") or ""),
        "pos_from": str(walk.get("pos_before") or ""),
        "pos_to": str(walk.get("pos_after") or ""),
    }


def display_pnl_row(r: TfResult) -> dict[str, Any]:
    return {
        "tf": r.tf,
        "bars": r.n_bars,
        "trades": r.n_trades,
        "long": r.n_long,
        "short": r.n_short,
        "win_pct": round(100.0 * r.win_rate, 1),
        "pts": _num(r.gross_pts),
        "gross_inr": round(r.gross_pnl_inr, 1),
        "fees_inr": round(r.fees_inr, 1),
        "after_tax_inr": round(r.after_tax_pnl_inr, 1),
        "max_dd_inr": round(r.max_dd_inr, 1),
    }


def display_trade_row(t: Trade) -> dict[str, Any]:
    lots = float(t.lots) or 1.0
    return {
        "tf": t.tf,
        "side": t.side,
        "entry_time": t.entry_time,
        "entry_px": _num(t.entry_px),
        "exit_time": t.exit_time,
        "exit_px": _num(t.exit_px),
        "pts_per_lot": _num(float(t.gross_pts) / lots),
        "gross_inr": round(t.gross_pnl_inr, 1),
        "fees_inr": round(t.fees_inr, 1),
        "after_tax_inr": round(t.after_tax_pnl_inr, 1),
        "lots": _num(lots),
    }


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def rows_to_tsv(rows: list[dict[str, Any]], fields: list[str]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore", delimiter="\t")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def _td(value: Any, *, cls: str = "") -> str:
    raw = "" if value is None else str(value)
    klass = f' class="{html.escape(cls)}"' if cls else ""
    return f"<td{klass}>{html.escape(raw)}</td>"


def _th(fields: list[str]) -> str:
    return "<tr>" + "".join(f"<th>{html.escape(c)}</th>" for c in fields) + "</tr>"


def _bar_table(rows: list[dict[str, Any]]) -> str:
    body = []
    for r in rows:
        classes = []
        if r["side"] == "LONG":
            classes.append("long")
        elif r["side"] == "SHORT":
            classes.append("short")
        if r["O=H"] == "Y":
            classes.append("oh")
        if r["O=L"] == "Y":
            classes.append("ol")
        tr_cls = " ".join(classes)
        cells = []
        for col in BAR_COLUMNS:
            extra = ""
            if col == "side" and r["side"] in {"LONG", "SHORT"}:
                extra = r["side"].lower()
            if col in {"O=H", "O=L"} and r[col] == "Y":
                extra = "flag"
            cells.append(_td(r[col], cls=extra))
        body.append(f'<tr class="{tr_cls}">{"".join(cells)}</tr>')
    return (
        f'<table><thead>{_th(BAR_COLUMNS)}</thead>'
        f"<tbody>{''.join(body)}</tbody></table>"
    )


def _plain_table(rows: list[dict[str, Any]], fields: list[str]) -> str:
    body = []
    for r in rows:
        body.append("<tr>" + "".join(_td(r.get(c, "")) for c in fields) + "</tr>")
    return (
        f"<table><thead>{_th(fields)}</thead>"
        f"<tbody>{''.join(body)}</tbody></table>"
    )


def render_html(
    *,
    tabs: dict[str, str],
    title: str,
    subtitle: str,
    formula: str,
    generated: str,
    default_tab: str,
) -> str:
    buttons = []
    panes = []
    for i, (name, inner) in enumerate(tabs.items()):
        active = " active" if name == default_tab or (not default_tab and i == 0) else ""
        buttons.append(
            f'<button type="button" class="tab{active}" data-tab="{html.escape(name)}">'
            f"{html.escape(name)}</button>"
        )
        hide = "" if active else " hidden"
        panes.append(
            f'<div class="pane{hide}" id="pane-{html.escape(name)}">{inner}</div>'
        )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{html.escape(title)}</title>
<style>
  :root {{ font-family: "IBM Plex Sans", Arial, sans-serif; }}
  body {{ margin: 0; background: #eef1ea; color: #1a1a1a; }}
  header {{ padding: 1rem 1.2rem .7rem; background: #fff; border-bottom: 1px solid #c9d0c4; }}
  h1 {{ margin: 0 0 .35rem; font-size: 1.35rem; }}
  .sub, .formula {{ color: #444; font-size: .9rem; white-space: pre-wrap; }}
  .tabs {{ display: flex; flex-wrap: wrap; gap: 0; padding: 0 1.2rem;
           background: #e4e9de; border-bottom: 1px solid #c9d0c4; }}
  .tab {{ appearance: none; border: 1px solid transparent; border-bottom: none;
          background: transparent; padding: .55rem .9rem; cursor: pointer;
          font: inherit; }}
  .tab.active {{ background: #fff; border-color: #c9d0c4; font-weight: 600; }}
  .pane {{ padding: .6rem 1rem 2rem; overflow: auto; max-height: calc(100vh - 9rem);
           background: #fff; }}
  .pane.hidden {{ display: none; }}
  table {{ border-collapse: collapse; font-size: 12px; font-family: "IBM Plex Mono", ui-monospace, monospace; }}
  th, td {{ border: 1px solid #d0d7ca; padding: 3px 7px; white-space: nowrap; }}
  th {{ position: sticky; top: 0; background: #1f6b3a; color: #fff; z-index: 1; }}
  tr:nth-child(even) {{ background: #f7faf5; }}
  tr.long {{ background: #eaf7ee; }}
  tr.short {{ background: #fdeeee; }}
  td.long {{ color: #0b7a3b; font-weight: 700; }}
  td.short {{ color: #b42318; font-weight: 700; }}
  td.flag {{ background: #fff3bf; font-weight: 700; }}
  @media print {{
    .tabs {{ display: none; }}
    .pane, .pane.hidden {{ display: block !important; max-height: none; overflow: visible; }}
    .pane::before {{ content: attr(id); display: block; font-weight: 700; margin: 1rem 0 .3rem; }}
  }}
</style>
</head>
<body>
<header>
  <h1>{html.escape(title)}</h1>
  <p class="sub">{html.escape(subtitle)}</p>
  <p class="formula">{html.escape(formula)}</p>
  <p class="sub">{html.escape(generated)}</p>
</header>
<nav class="tabs">{''.join(buttons)}</nav>
{''.join(panes)}
<script>
document.querySelectorAll(".tab").forEach((btn) => {{
  btn.addEventListener("click", () => {{
    document.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".pane").forEach((p) => p.classList.add("hidden"));
    btn.classList.add("active");
    document.getElementById("pane-" + btn.dataset.tab).classList.remove("hidden");
  }});
}});
</script>
</body>
</html>
"""


def write_s14_workbook(
    walks: dict[str, list[dict[str, Any]]],
    results: list[TfResult],
    *,
    out_dir: Path = SHEET_DIR,
    symbol: str = "GOLDPETAL",
    source: str = "",
    lots: float = 100.0,
    fees: bool = True,
    formula: str = "",
) -> dict[str, Any]:
    """Write HTML + CSVs. Returns meta including html path."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    generated = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
    bar_tabs: dict[str, list[dict[str, Any]]] = {}
    for tf, rows in walks.items():
        bar_tabs[tf] = [display_bar_row(r) for r in rows]
        write_csv(out_dir / f"{tf}.csv", bar_tabs[tf], BAR_COLUMNS)
    pnl_rows = [display_pnl_row(r) for r in results]
    trade_rows: list[dict[str, Any]] = []
    for r in results:
        trade_rows.extend(display_trade_row(t) for t in r.trades)
    write_csv(out_dir / "pnl.csv", pnl_rows, PNL_COLUMNS)
    write_csv(out_dir / "trades.csv", trade_rows, TRADE_COLUMNS)

    html_tabs: dict[str, str] = {}
    for tf, rows in bar_tabs.items():
        html_tabs[tf] = _bar_table(rows)
    html_tabs["pnl"] = _plain_table(pnl_rows, PNL_COLUMNS)
    html_tabs["trades"] = _plain_table(trade_rows, TRADE_COLUMNS)
    default_tab = "1d" if "1d" in html_tabs else next(iter(html_tabs), "pnl")
    page = render_html(
        tabs=html_tabs,
        title=f"Gold Petal exchange candles · {symbol}",
        subtitle=(
            f"Angel/MCX chart OHLC (not tick-built). Fill at signal close. "
            f"lots={lots:g} fees={fees}  source={source}"
        ),
        formula=formula,
        generated=f"written {generated}  ·  {out_dir.resolve()}",
        default_tab=default_tab,
    )
    html_path = out_dir / HTML_NAME
    html_path.write_text(page, encoding="utf-8")
    (out_dir / "GOOGLE_SHEETS_IMPORT.txt").write_text(
        "Gold Petal S14 exchange candles → Google Sheets\n\n"
        "1. Open https://sheets.google.com → Blank spreadsheet\n"
        "2. File → Import → Upload 1d.csv (Replace spreadsheet)\n"
        "3. File → Import → Upload 30m.csv, 1h.csv, pnl.csv, trades.csv "
        "(Insert new sheet(s))\n"
        "Or paste: open GoldPetal_S14.html, copy a tab, paste into Sheets.\n"
        "Or on the control panel: Copy tab → Google Sheets.\n",
        encoding="utf-8",
    )
    meta = {
        "ok": True,
        "generated_at": generated,
        "symbol": symbol,
        "source": source,
        "lots": lots,
        "fees": fees,
        "formula": formula,
        "tfs": list(walks),
        "bars": {tf: len(rows) for tf, rows in walks.items()},
        "html": str(html_path),
        "dir": str(out_dir),
    }
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta


def load_sheet_meta(sheet_dir: Path | None = None) -> dict[str, Any]:
    path = Path(sheet_dir or SHEET_DIR) / "meta.json"
    if not path.is_file():
        return {
            "ok": False,
            "hint": (
                "cd ~/goldpetal && ./venv/bin/python explain_s14_candles.py "
                "--from-csv data/backtests/s14_candles --tf 30m,1h,1d --no-print-bars"
            ),
        }
    return json.loads(path.read_text(encoding="utf-8"))


def load_sheet_csv(name: str, sheet_dir: Path | None = None) -> tuple[list[str], list[dict[str, Any]]]:
    path = Path(sheet_dir or SHEET_DIR) / name
    if not path.is_file():
        return [], []
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fields = list(reader.fieldnames or [])
        rows = list(reader)
    return fields, rows


def sheet_zip_bytes(sheet_dir: Path | None = None) -> bytes:
    folder = Path(sheet_dir or SHEET_DIR)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(folder.glob("*")):
            if path.is_file() and path.suffix.lower() in {".csv", ".html", ".txt", ".json"}:
                zf.write(path, arcname=path.name)
    return buf.getvalue()


def missing_sheet_html() -> str:
    hint = load_sheet_meta().get("hint", "")
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'/><title>S14 sheet</title></head>"
        "<body style='font-family:sans-serif;padding:2rem'>"
        "<h1>Gold Petal exchange sheet not built yet</h1>"
        f"<p>Run:</p><pre>{html.escape(str(hint))}</pre>"
        "<p>Then open this page again, or use 8787 → Gold Petal exchange candles.</p>"
        "</body></html>"
    )


def main() -> None:
    from explain_s14_candles import (
        FORMULA,
        bar_is_finished,
        candles_from_walk_csv,
        find_tf_csv,
        in_session_dt,
        parse_bar_ts,
        settle_s14_from_walk,
        walk_candles,
        _parse_tfs,
    )

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--from-csv", type=Path, default=Path("data/backtests/s14_candles"))
    ap.add_argument("--tf", default="30m,1h,1d")
    ap.add_argument("--out-dir", type=Path, default=SHEET_DIR)
    ap.add_argument("--lots", type=float, default=100.0)
    ap.add_argument("--fees", action=argparse.BooleanOptionalAction, default=True)
    ap.add_argument("--open-hold", type=float, default=2.0)
    args = ap.parse_args()
    tfs = _parse_tfs(args.tf)
    open_hold = float(args.open_hold) > 0
    now = datetime.now(IST)
    walks: dict[str, list[dict[str, Any]]] = {}
    results: list[TfResult] = []
    symbol = "GOLDPETAL"
    for tf in tfs:
        path = find_tf_csv(args.from_csv, tf)
        if path is None:
            print(f"skip {tf}: no CSV under {args.from_csv}", flush=True)
            continue
        symbol = path.stem.replace(f"{tf}_", "", 1)
        raw = candles_from_walk_csv(path)
        bars = [
            b
            for b in raw
            if in_session_dt(parse_bar_ts(b["time"]), tf=tf)
            and bar_is_finished(b["time"], tf, now)
        ]
        rows = walk_candles(bars, open_hold=open_hold)
        walks[tf] = rows
        results.append(
            settle_s14_from_walk(rows, tf=f"{tf}:s14", lots=args.lots, fees=args.fees)
        )
        print(f"{tf}: {len(rows)} bars", flush=True)
    meta = write_s14_workbook(
        walks,
        results,
        out_dir=args.out_dir,
        symbol=symbol,
        source=f"csv {args.from_csv}",
        lots=args.lots,
        fees=args.fees,
        formula=FORMULA,
    )
    print(f"wrote {meta['html']}", flush=True)
    print("open: data/s14_sheet/GoldPetal_S14.html  or  http://127.0.0.1:8787/s14-sheet", flush=True)


if __name__ == "__main__":
    main()
