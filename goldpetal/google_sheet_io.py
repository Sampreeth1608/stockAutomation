"""Push CSV tables to an existing Google Spreadsheet.

Read-only monitor path: the VM writes numbers; Sheets never starts, stops,
or live-unlocks the bot. You create a blank spreadsheet, share it with the
service-account email as Editor, then we replace named tabs.

Optional extras (commented in requirements.txt):
  pip install gspread google-auth
"""

from __future__ import annotations

import csv
import os
from pathlib import Path
from typing import Any


SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"


def resolve_creds_path(*, env: dict[str, str] | None = None) -> str:
    src = env if env is not None else os.environ
    return str(src.get("GOOGLE_SERVICE_ACCOUNT_JSON") or "").strip()


def resolve_sheet_id(*, env: dict[str, str] | None = None) -> str:
    """Prefer the phone-monitor workbook; fall back to the generic sheet id."""
    src = env if env is not None else os.environ
    return str(
        src.get("GOOGLE_MONITOR_SHEET_ID")
        or src.get("GOOGLE_SHEET_ID")
        or ""
    ).strip()


def spreadsheet_url(sheet_id: str) -> str:
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}"


def tables_from_csv_dir(folder: Path) -> dict[str, list[list[str]]]:
    out: dict[str, list[list[str]]] = {}
    for path in sorted(Path(folder).glob("*.csv")):
        with path.open(newline="", encoding="utf-8") as fh:
            rows = [list(r) for r in csv.reader(fh)]
        if rows:
            out[path.stem[:100]] = rows
    return out


def upload_tables(
    tables: dict[str, list[list[Any]]],
    *,
    sheet_id: str,
    creds_path: str,
    tab_order: list[str] | None = None,
) -> str:
    """Replace named tabs. Returns the spreadsheet URL."""
    if not sheet_id:
        raise ValueError("GOOGLE_MONITOR_SHEET_ID (or GOOGLE_SHEET_ID) is empty")
    if not creds_path:
        raise ValueError("GOOGLE_SERVICE_ACCOUNT_JSON is empty")
    creds_file = Path(creds_path)
    if not creds_file.is_file():
        raise FileNotFoundError(f"service account JSON not found: {creds_path}")

    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError as exc:
        raise ImportError(
            "Google upload needs: pip install gspread google-auth"
        ) from exc

    creds = Credentials.from_service_account_file(
        str(creds_file), scopes=[SHEETS_SCOPE]
    )
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(sheet_id)

    names = list(tab_order or [])
    for title in tables:
        if title not in names:
            names.append(title)

    for title in names:
        rows = tables.get(title)
        if not rows:
            continue
        clean = [[("" if c is None else str(c)) for c in row] for row in rows]
        try:
            ws = sh.worksheet(title)
            ws.clear()
        except gspread.WorksheetNotFound:
            rows_n = max(len(clean) + 20, 80)
            cols_n = max(len(clean[0]), 8)
            ws = sh.add_worksheet(title=title, rows=rows_n, cols=cols_n)
        ws.update("A1", clean, value_input_option="USER_ENTERED")
    return spreadsheet_url(sheet_id)


def open_spreadsheet(sheet_id: str, creds_path: str):
    """Authorized gspread Spreadsheet. Raises ImportError if gspread is missing."""
    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError as exc:
        raise ImportError(
            "Google upload needs: pip install gspread google-auth"
        ) from exc
    creds_file = Path(creds_path)
    if not creds_file.is_file():
        raise FileNotFoundError(f"service account JSON not found: {creds_path}")
    creds = Credentials.from_service_account_file(
        str(creds_file), scopes=[SHEETS_SCOPE]
    )
    gc = gspread.authorize(creds)
    return gc.open_by_key(sheet_id)


def read_table(sh: Any, title: str) -> list[list[str]]:
    """All values from a tab, or [] if the tab does not exist."""
    import gspread

    try:
        ws = sh.worksheet(title)
    except gspread.WorksheetNotFound:
        return []
    return [list(r) for r in ws.get_all_values()]


def upload_csv_dir(
    folder: Path,
    *,
    sheet_id: str,
    creds_path: str,
    tab_order: list[str] | None = None,
) -> str:
    return upload_tables(
        tables_from_csv_dir(folder),
        sheet_id=sheet_id,
        creds_path=creds_path,
        tab_order=tab_order,
    )
