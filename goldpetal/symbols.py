"""Resolve current MCX Gold Petal futures contract."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

SCRIP_MASTER_URL = (
    "https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json"
)
CACHE_PATH = Path(__file__).resolve().parent / "data" / "scrip_master.json"
MCX_EXCHANGE_TYPE = 5


def download_scrip_master(force: bool = False) -> list[dict[str, Any]]:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if CACHE_PATH.exists() and not force:
        age_hours = (datetime.now().timestamp() - CACHE_PATH.stat().st_mtime) / 3600
        if age_hours < 12:
            return json.loads(CACHE_PATH.read_text())

    response = requests.get(SCRIP_MASTER_URL, timeout=60)
    response.raise_for_status()
    data = response.json()
    CACHE_PATH.write_text(json.dumps(data))
    return data


def _parse_expiry(value: str) -> datetime | None:
    value = (value or "").strip()
    if not value:
        return None
    for fmt in ("%d%b%Y", "%d%b%y", "%d-%b-%Y"):
        try:
            return datetime.strptime(value.upper(), fmt)
        except ValueError:
            continue
    return None


def find_goldpetal_futures(force_refresh: bool = False) -> dict[str, Any]:
    """Return nearest Gold Petal futures contract on MCX."""
    master = download_scrip_master(force=force_refresh)
    contracts: list[tuple[datetime, dict[str, Any]]] = []

    for row in master:
        name = (row.get("name") or "").upper()
        symbol = (row.get("symbol") or "").upper()
        exch = (row.get("exch_seg") or "").upper()
        itype = (row.get("instrumenttype") or "").upper()

        if exch != "MCX":
            continue
        if name != "GOLDPETAL" and "GOLDPETAL" not in symbol:
            continue
        if itype not in {"FUTCOM", "FUTCUR"} and "FUT" not in symbol:
            continue

        expiry = _parse_expiry(row.get("expiry", ""))
        if expiry is None:
            continue
        contracts.append((expiry, row))

    if not contracts:
        raise RuntimeError("No GOLDPETAL futures found in Angel scrip master")

    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    future = [(exp, row) for exp, row in contracts if exp >= today]
    chosen_exp, chosen = min(future or contracts, key=lambda item: item[0])

    return {
        "symbol": chosen["symbol"],
        "name": chosen.get("name", "GOLDPETAL"),
        "token": str(chosen["token"]),
        "expiry": chosen.get("expiry", ""),
        "expiry_dt": chosen_exp.strftime("%Y-%m-%d"),
        "exchange": "MCX",
        "exchange_type": MCX_EXCHANGE_TYPE,
        "lotsize": chosen.get("lotsize"),
        "tick_size": chosen.get("tick_size"),
    }


if __name__ == "__main__":
    contract = find_goldpetal_futures(force_refresh=True)
    print("Nearest Gold Petal futures:")
    for key, value in contract.items():
        print(f"  {key}: {value}")
