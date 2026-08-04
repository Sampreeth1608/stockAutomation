"""Export ticks in full depth sheet format (matching manual Goldpetal sheet)."""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from storage import DB_PATH, connect, init_db

IST = ZoneInfo("Asia/Kolkata")

HEADERS = [
    "time",
    "ltp",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "last_traded_quantity",
    "average_traded_price",
    "total_buy_quantity",
    "total_sell_quantity",
    "buy1_price",
    "buy1_qty",
    "buy2_price",
    "buy2_qty",
    "buy3_price",
    "buy3_qty",
    "buy4_price",
    "buy4_qty",
    "buy5_price",
    "buy5_qty",
    "sell1_price",
    "sell1_qty",
    "sell2_price",
    "sell2_qty",
    "sell3_price",
    "sell3_qty",
    "sell4_price",
    "sell4_qty",
    "sell5_price",
    "sell5_qty",
    "open_interest",
]


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _price(value: Any) -> float | None:
    """Angel websocket prices are usually in paise."""
    number = _as_float(value)
    if number is None:
        return None
    # If already looks like rupees for Gold Petal (~10000+), keep as-is.
    if number > 1000:
        return number
    return number / 100.0


def _qty(value: Any) -> float | None:
    return _as_float(value)


def _depth_side(message: dict[str, Any], side: str) -> list[tuple[float | None, float | None]]:
    """Return 5 (price, qty) levels for buy/sell."""
    key_options = [
        f"best_5_{side}_data",
        f"best5_{side}_data",
        f"{side}_depth",
    ]
    levels: list[Any] = []
    for key in key_options:
        raw = message.get(key)
        if isinstance(raw, list) and raw:
            levels = raw
            break

    out: list[tuple[float | None, float | None]] = []
    for i in range(5):
        if i >= len(levels):
            out.append((None, None))
            continue
        item = levels[i]
        if isinstance(item, dict):
            price = _price(
                item.get("price", item.get("p", item.get("Price")))
            )
            qty = _qty(
                item.get("quantity")
                or item.get("qty")
                or item.get("Quantity")
                or item.get("size")
            )
            out.append((price, qty))
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            # common shapes: [qty, price] or [price, qty]
            a, b = _as_float(item[0]), _as_float(item[1])
            if a is not None and b is not None and a > 1000 and b < 1000:
                out.append((_price(a), _qty(b)))
            elif a is not None and b is not None and b > 1000 and a < 1000:
                out.append((_price(b), _qty(a)))
            else:
                out.append((_price(a), _qty(b)))
        else:
            out.append((None, None))
    return out


def _format_time(received_at: str | None, exchange_ts: Any) -> str:
    if received_at:
        # normalize to "YYYY-MM-DD HH:MM:SS"
        try:
            dt = datetime.fromisoformat(received_at)
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            return received_at.replace("T", " ").split("+")[0]
    ts = _as_float(exchange_ts)
    if ts is None:
        return ""
    # Angel exchange_timestamp is usually ms
    if ts > 1e12:
        ts = ts / 1000.0
    return datetime.fromtimestamp(ts, IST).strftime("%Y-%m-%d %H:%M:%S")


def row_from_tick(received_at: str, exchange_ts: Any, raw_json: str) -> dict[str, Any]:
    try:
        message = json.loads(raw_json) if raw_json else {}
    except json.JSONDecodeError:
        message = {}

    buy = _depth_side(message, "buy")
    sell = _depth_side(message, "sell")

    row = {
        "time": _format_time(received_at, exchange_ts),
        "ltp": _price(message.get("last_traded_price")),
        "open": _price(message.get("open_price_of_the_day") or message.get("open")),
        "high": _price(message.get("high_price_of_the_day") or message.get("high")),
        "low": _price(message.get("low_price_of_the_day") or message.get("low")),
        "close": _price(message.get("closed_price") or message.get("close")),
        "volume": _qty(message.get("volume_trade_for_the_day") or message.get("volume")),
        "last_traded_quantity": _qty(message.get("last_traded_quantity")),
        "average_traded_price": _price(message.get("average_traded_price")),
        "total_buy_quantity": _qty(message.get("total_buy_quantity")),
        "total_sell_quantity": _qty(message.get("total_sell_quantity")),
        "open_interest": _qty(message.get("open_interest")),
    }

    for i in range(5):
        row[f"buy{i+1}_price"] = buy[i][0]
        row[f"buy{i+1}_qty"] = buy[i][1]
        row[f"sell{i+1}_price"] = sell[i][0]
        row[f"sell{i+1}_qty"] = sell[i][1]
    return row


def export_full_ticks(path: Path, limit: int | None = None) -> int:
    init_db()
    query = """
        SELECT received_at, exchange_timestamp, raw_json
        FROM ticks
        ORDER BY id ASC
    """
    params: tuple[Any, ...] = ()
    if limit is not None:
        query = """
            SELECT received_at, exchange_timestamp, raw_json
            FROM (
                SELECT id, received_at, exchange_timestamp, raw_json
                FROM ticks
                ORDER BY id DESC
                LIMIT ?
            ) t
            ORDER BY id ASC
        """
        params = (limit,)

    with connect(DB_PATH) as conn:
        rows = list(conn.execute(query, params))

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=HEADERS)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                row_from_tick(row["received_at"], row["exchange_timestamp"], row["raw_json"])
            )
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Export full-depth tick sheet")
    parser.add_argument(
        "--export",
        type=str,
        default="data/goldpetal_full_ticks.csv",
        help="Output CSV path",
    )
    parser.add_argument("--limit", type=int, default=None, help="Optional latest N rows")
    args = parser.parse_args()

    path = Path(args.export)
    n = export_full_ticks(path, limit=args.limit)
    print(f"Exported {n} full-depth rows to {path}")
    print("Columns:", ", ".join(HEADERS))


if __name__ == "__main__":
    main()
