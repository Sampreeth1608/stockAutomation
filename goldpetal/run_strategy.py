"""Run Gold Petal pressure strategy in DRY_RUN (signals only)."""

from __future__ import annotations

import os
import signal
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from logzero import logger
from SmartApi.smartWebSocketV2 import SmartWebSocketV2

from auth import login
from storage import init_db, save_bar, save_signal, save_tick
from strategy import BarSnapshot, PressureStrategy
from symbols import find_goldpetal_futures

IST = ZoneInfo("Asia/Kolkata")
SUBSCRIBE_MODE = 3
DEFAULT_INTERVAL_MINUTES = 30


def _interval_minutes() -> int:
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
    raw = os.getenv("INTERVAL_MINUTES", str(DEFAULT_INTERVAL_MINUTES)).strip()
    value = int(raw)
    if value <= 0:
        raise RuntimeError("INTERVAL_MINUTES must be > 0")
    return value


def _dry_run() -> bool:
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
    return os.getenv("DRY_RUN", "true").strip().lower() in {"1", "true", "yes", "y"}


def _scale_price(value) -> float | None:
    if value is None:
        return None
    return float(value) / 100.0


def _next_boundary(now: datetime, interval_minutes: int) -> datetime:
    # Align to clock boundaries, e.g. 09:00, 09:30, 10:00 for 30-min.
    minute_block = (now.minute // interval_minutes) * interval_minutes
    boundary = now.replace(minute=minute_block, second=0, microsecond=0)
    if now >= boundary:
        boundary += timedelta(minutes=interval_minutes)
    return boundary


def main() -> None:
    init_db()
    interval = _interval_minutes()
    dry_run = _dry_run()
    contract = find_goldpetal_futures(force_refresh=True)
    session = login()
    strategy = PressureStrategy()

    symbol = contract["symbol"]
    token = contract["token"]
    exchange_type = contract["exchange_type"]

    latest = {
        "cmp": None,
        "bp": None,
        "sp": None,
        "message": None,
    }
    next_bar_at = _next_boundary(datetime.now(IST), interval)

    print("=== Gold Petal strategy runner ===")
    print(f"Symbol   : {symbol}")
    print(f"Token    : {token}")
    print(f"Expiry   : {contract['expiry']}")
    print(f"Interval : {interval} minutes")
    print("Rules    : netΔ only (priceΔ ignored for entry)")
    print("Exits    : long netΔ↓ / short netΔ↑ / 2%+3x exhaustion / tiny-price+net surge")
    print(f"DRY_RUN  : {dry_run} (signals only; no live orders)")
    print(f"Next bar : {next_bar_at.isoformat(timespec='seconds')}")
    print("Press Ctrl+C to stop")
    print("=================================")

    correlation_id = "goldpetal_strategy"
    token_list = [{"exchangeType": exchange_type, "tokens": [token]}]
    tick_count = 0

    sws = SmartWebSocketV2(
        session.auth_token,
        session.api_key,
        session.client_id,
        session.feed_token,
        max_retry_attempt=5,
    )

    def evaluate_bar(now: datetime) -> None:
        nonlocal next_bar_at
        if latest["cmp"] is None or latest["bp"] is None or latest["sp"] is None:
            print(f"[{now.isoformat(timespec='seconds')}] bar skipped — no tick data yet")
            next_bar_at = _next_boundary(now, interval)
            return

        bar = BarSnapshot(
            time_label=now.isoformat(timespec="seconds"),
            cmp=float(latest["cmp"]),
            bp=float(latest["bp"]),
            sp=float(latest["sp"]),
        )
        result = strategy.on_bar(bar)
        save_bar(
            time_label=bar.time_label,
            symbol=symbol,
            token=token,
            cmp=bar.cmp,
            bp=bar.bp,
            sp=bar.sp,
            net=result.net,
            price_delta=result.price_delta,
            net_delta=result.net_delta,
        )
        save_signal(
            time_label=bar.time_label,
            symbol=symbol,
            action=result.action,
            position_after=result.position_after,
            reason=result.reason,
            price_delta=result.price_delta,
            net=result.net,
            net_delta=result.net_delta,
            dry_run=dry_run,
        )

        print(
            f"[{bar.time_label}] "
            f"CMP={bar.cmp} BP={bar.bp} SP={bar.sp} NET={result.net} "
            f"priceΔ={result.price_delta} netΔ={result.net_delta} "
            f"prev_netΔ={result.prev_net_delta} => {result.action} "
            f"(pos={result.position_after}) | {result.reason}"
        )
        if not dry_run:
            print("LIVE ORDER MODE is off-limits in this step; keep DRY_RUN=true")

        next_bar_at = _next_boundary(now, interval)

    def on_data(_wsapp, message):
        nonlocal tick_count, next_bar_at
        if not isinstance(message, dict):
            return

        now = datetime.now(IST)
        received_at = now.isoformat(timespec="seconds")
        save_tick(message, symbol=symbol, token=token, received_at=received_at)

        cmp = _scale_price(message.get("last_traded_price"))
        bp = message.get("total_buy_quantity")
        sp = message.get("total_sell_quantity")
        if cmp is not None:
            latest["cmp"] = cmp
        if bp is not None:
            latest["bp"] = float(bp)
        if sp is not None:
            latest["sp"] = float(sp)
        latest["message"] = message

        tick_count += 1
        if tick_count == 1 or tick_count % 50 == 0:
            print(
                f"[{received_at}] ticks={tick_count} "
                f"ltp={latest['cmp']} bp={latest['bp']} sp={latest['sp']} "
                f"next_bar={next_bar_at.strftime('%H:%M:%S')}"
            )

        if now >= next_bar_at:
            evaluate_bar(now)

    def on_open(_wsapp):
        logger.info("WebSocket open — strategy subscribed to %s", symbol)
        sws.subscribe(correlation_id, SUBSCRIBE_MODE, token_list)

    def on_error(_wsapp, error):
        logger.error("WebSocket error: %s", error)

    def on_close(_wsapp):
        logger.info("WebSocket closed. ticks=%s position=%s", tick_count, strategy.position)

    def handle_signal(_signum, _frame):
        print("\nStopping strategy runner...")
        try:
            sws.close_connection()
        finally:
            sys.exit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    sws.on_open = on_open
    sws.on_data = on_data
    sws.on_error = on_error
    sws.on_close = on_close
    sws.connect()


if __name__ == "__main__":
    main()
