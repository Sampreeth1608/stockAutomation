"""Resolve current MCX Gold Petal futures contract with monthly rollover."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

SCRIP_MASTER_URL = (
    "https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json"
)
CACHE_PATH = Path(__file__).resolve().parent / "data" / "scrip_master.json"
MCX_EXCHANGE_TYPE = 5
DEFAULT_ROLLOVER_DAYS = 5


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


def _rollover_days() -> int:
    load_dotenv(Path(__file__).resolve().parent / ".env")
    raw = os.getenv("ROLLOVER_DAYS", str(DEFAULT_ROLLOVER_DAYS)).strip()
    try:
        days = int(raw)
    except ValueError as exc:
        raise RuntimeError("ROLLOVER_DAYS must be an integer") from exc
    if days < 0:
        raise RuntimeError("ROLLOVER_DAYS must be >= 0")
    return days


def _list_goldpetal_futures(force_refresh: bool = False) -> list[tuple[datetime, dict[str, Any]]]:
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

    contracts.sort(key=lambda item: item[0])
    return contracts


def _contract_payload(
    chosen_exp: datetime,
    chosen: dict[str, Any],
    *,
    rolled: bool,
    rollover_days: int,
    front_expiry: str,
    next_expiry: str | None,
    days_to_front_expiry: int,
) -> dict[str, Any]:
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
        "rolled": rolled,
        "rollover_days": rollover_days,
        "front_month_expiry": front_expiry,
        "next_month_expiry": next_expiry,
        "days_to_front_expiry": days_to_front_expiry,
    }


def find_goldpetal_futures(force_refresh: bool = False) -> dict[str, Any]:
    """Return the Gold Petal futures contract to trade/collect.

    Rule:
    - Use current (front) month contract
    - Switch to next month when today is within ROLLOVER_DAYS before front expiry
      (default: 5 days before expiry)
    """
    contracts = _list_goldpetal_futures(force_refresh=force_refresh)
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    upcoming = [(exp, row) for exp, row in contracts if exp >= today]
    if not upcoming:
        raise RuntimeError("No upcoming GOLDPETAL futures contracts found")

    front_exp, front = upcoming[0]
    next_exp, nxt = (upcoming[1] if len(upcoming) > 1 else (None, None))
    days_left = (front_exp - today).days
    rollover_days = _rollover_days()

    # From N days before expiry (inclusive), use next month.
    should_roll = days_left <= rollover_days and nxt is not None
    if should_roll:
        chosen_exp, chosen = next_exp, nxt
        rolled = True
    else:
        chosen_exp, chosen = front_exp, front
        rolled = False

    return _contract_payload(
        chosen_exp,
        chosen,
        rolled=rolled,
        rollover_days=rollover_days,
        front_expiry=front.get("expiry", ""),
        next_expiry=(nxt.get("expiry") if nxt else None),
        days_to_front_expiry=days_left,
    )


if __name__ == "__main__":
    contract = find_goldpetal_futures(force_refresh=True)
    print("Active Gold Petal futures (with rollover rule):")
    for key, value in contract.items():
        print(f"  {key}: {value}")

    if contract["rolled"]:
        print(
            f"\nRolled to next month because front expiry "
            f"({contract['front_month_expiry']}) is within "
            f"{contract['rollover_days']} days."
        )
    else:
        print(
            f"\nUsing front month. Auto-roll starts on/after "
            f"{(datetime.strptime(contract['expiry_dt'], '%Y-%m-%d') - timedelta(days=contract['rollover_days'])).date()} "
            f"({contract['rollover_days']} days before expiry)."
        )
