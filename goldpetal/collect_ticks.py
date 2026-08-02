"""Collect live Gold Petal ticks from Angel One and store them in SQLite."""

from __future__ import annotations

import signal
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from logzero import logger
from SmartApi.smartWebSocketV2 import SmartWebSocketV2

from auth import login
from storage import init_db, save_tick
from symbols import find_goldpetal_futures

IST = ZoneInfo("Asia/Kolkata")
# mode 3 = snap quote (richest tick fields)
SUBSCRIBE_MODE = 3


def main() -> None:
    init_db()
    contract = find_goldpetal_futures(force_refresh=True)
    session = login()

    symbol = contract["symbol"]
    token = contract["token"]
    exchange_type = contract["exchange_type"]

    print("=== Gold Petal tick collector ===")
    print(f"Symbol : {symbol}")
    print(f"Token  : {token}")
    print(f"Expiry : {contract['expiry']}")
    print(f"Front  : {contract['front_month_expiry']} ({contract['days_to_front_expiry']} days left)")
    print(f"Next   : {contract['next_month_expiry']}")
    print(
        f"Roll   : {'YES — using next month' if contract['rolled'] else 'no'} "
        f"(switch {contract['rollover_days']} days before expiry)"
    )
    print("DB     : ~/goldpetal/data/ticks.db")
    print("Press Ctrl+C to stop")
    print("================================")

    correlation_id = "goldpetal_ticks"
    token_list = [{"exchangeType": exchange_type, "tokens": [token]}]
    tick_count = 0

    sws = SmartWebSocketV2(
        session.auth_token,
        session.api_key,
        session.client_id,
        session.feed_token,
        max_retry_attempt=5,
    )

    def on_data(_wsapp, message):
        nonlocal tick_count
        if not isinstance(message, dict):
            return
        received_at = datetime.now(IST).isoformat(timespec="seconds")
        save_tick(message, symbol=symbol, token=token, received_at=received_at)
        tick_count += 1
        ltp = message.get("last_traded_price")
        ltp_display = (float(ltp) / 100.0) if ltp is not None else None
        if tick_count == 1 or tick_count % 25 == 0:
            print(f"[{received_at}] ticks={tick_count} ltp={ltp_display}")

    def on_open(_wsapp):
        logger.info("WebSocket open — subscribing to %s", symbol)
        sws.subscribe(correlation_id, SUBSCRIBE_MODE, token_list)

    def on_error(_wsapp, error):
        logger.error("WebSocket error: %s", error)

    def on_close(_wsapp):
        logger.info("WebSocket closed. Total ticks saved: %s", tick_count)

    def handle_signal(_signum, _frame):
        print("\nStopping collector...")
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
