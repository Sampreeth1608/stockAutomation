"""Run Gold Petal pressure strategy in DRY_RUN (signals only)."""

from __future__ import annotations

import os
import signal
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from logzero import logger
from SmartApi.smartWebSocketV2 import SmartWebSocketV2

from auth import login
from storage import init_db, latest_bar, save_bar, save_signal, save_tick
from strategy import BarSnapshot, PressureStrategy
from symbols import find_goldpetal_futures

# Make prints show immediately even when piped to tee.
try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass

IST = ZoneInfo("Asia/Kolkata")
SUBSCRIBE_MODE = 3
DEFAULT_INTERVAL_MINUTES = 30
RECONNECT_DELAY_SEC = 5


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
    minute_block = (now.minute // interval_minutes) * interval_minutes
    boundary = now.replace(minute=minute_block, second=0, microsecond=0)
    if now >= boundary:
        boundary += timedelta(minutes=interval_minutes)
    return boundary


def _seed_strategy_from_db(strategy: PressureStrategy) -> None:
    """Continue from last saved bar so restart does not force WAIT."""
    row = latest_bar()
    if not row:
        return
    strategy.prev_cmp = float(row["cmp"])
    strategy.prev_net = float(row["net"])
    strategy.prev_net_delta = (
        float(row["net_delta"]) if row["net_delta"] is not None else None
    )
    logger.info(
        "Seeded strategy from DB bar %s cmp=%s net=%s netΔ=%s",
        row["time_label"],
        row["cmp"],
        row["net"],
        row["net_delta"],
    )
    print(
        f"Seeded from last DB bar @ {row['time_label']} "
        f"CMP={row['cmp']} NET={row['net']} netΔ={row['net_delta']}",
        flush=True,
    )


def run_once(strategy: PressureStrategy, stop_flag: dict) -> None:
    init_db()
    interval = _interval_minutes()
    dry_run = _dry_run()
    contract = find_goldpetal_futures(force_refresh=True)
    session = login()

    symbol = contract["symbol"]
    token = contract["token"]
    exchange_type = contract["exchange_type"]

    latest = {"cmp": None, "bp": None, "sp": None, "message": None}
    next_bar_at = _next_boundary(datetime.now(IST), interval)
    tick_count = 0
    closed = {"done": False}

    print("=== Gold Petal strategy runner ===", flush=True)
    print(f"Symbol   : {symbol}", flush=True)
    print(f"Token    : {token}", flush=True)
    print(f"Expiry   : {contract['expiry']}", flush=True)
    print(f"Interval : {interval} minutes", flush=True)
    print("Rules    : netΔ only (priceΔ ignored for entry)", flush=True)
    print(
        "Exits    : long netΔ↓ / short netΔ↑ / 2%+3x exhaustion / tiny-price+net surge",
        flush=True,
    )
    print(f"DRY_RUN  : {dry_run} (signals only; no live orders)", flush=True)
    print(f"Next bar : {next_bar_at.isoformat(timespec='seconds')}", flush=True)
    print("Press Ctrl+C to stop", flush=True)
    print("=================================", flush=True)

    correlation_id = f"goldpetal_strategy_{int(time.time())}"
    token_list = [{"exchangeType": exchange_type, "tokens": [token]}]

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
            msg = f"[{now.isoformat(timespec='seconds')}] bar skipped — no tick data yet"
            print(msg, flush=True)
            logger.info(msg)
            next_bar_at = _next_boundary(now, interval)
            return

        # Keep bar labels aligned to interval boundaries.
        label_time = now.replace(second=0, microsecond=0)
        minute_block = (label_time.minute // interval) * interval
        label_time = label_time.replace(minute=minute_block)

        bar = BarSnapshot(
            time_label=label_time.isoformat(timespec="seconds"),
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

        line = (
            f"[{bar.time_label}] "
            f"CMP={bar.cmp} BP={bar.bp} SP={bar.sp} NET={result.net} "
            f"priceΔ={result.price_delta} netΔ={result.net_delta} "
            f"prev_netΔ={result.prev_net_delta} => {result.action} "
            f"(pos={result.position_after}) | {result.reason}"
        )
        print(line, flush=True)
        logger.info(line)
        next_bar_at = _next_boundary(now, interval)

    def on_data(_wsapp, message):
        nonlocal tick_count
        if stop_flag["stop"]:
            return
        if not isinstance(message, dict):
            return

        try:
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
                line = (
                    f"[{received_at}] ticks={tick_count} "
                    f"ltp={latest['cmp']} bp={latest['bp']} sp={latest['sp']} "
                    f"next_bar={next_bar_at.strftime('%H:%M:%S')}"
                )
                print(line, flush=True)
                logger.info(line)

            if now >= next_bar_at:
                evaluate_bar(now)
        except Exception:
            logger.exception("Error while handling tick")

    def on_open(_wsapp):
        logger.info("WebSocket open — strategy subscribed to %s", symbol)
        print(f"WebSocket open — subscribed to {symbol}", flush=True)
        sws.subscribe(correlation_id, SUBSCRIBE_MODE, token_list)

    def on_error(_wsapp, error):
        logger.error("WebSocket error: %s", error)
        print(f"WebSocket error: {error}", flush=True)

    def on_close(_wsapp, *args):
        closed["done"] = True
        logger.info(
            "WebSocket closed. ticks=%s position=%s args=%s",
            tick_count,
            strategy.position,
            args,
        )
        print(
            f"WebSocket closed. ticks={tick_count} position={strategy.position}",
            flush=True,
        )

    sws.on_open = on_open
    sws.on_data = on_data
    sws.on_error = on_error
    sws.on_close = on_close

    try:
        sws.connect()
    finally:
        logger.info("connect() returned/finished. closed=%s", closed["done"])
        print(f"connect() finished. closed={closed['done']}", flush=True)


def main() -> None:
    stop_flag = {"stop": False}
    strategy = PressureStrategy()
    init_db()
    _seed_strategy_from_db(strategy)

    def handle_signal(_signum, _frame):
        print("\nStopping strategy runner...", flush=True)
        stop_flag["stop"] = True
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    while not stop_flag["stop"]:
        try:
            run_once(strategy, stop_flag)
        except SystemExit:
            raise
        except Exception:
            logger.exception("Strategy loop crashed; reconnecting")
            print("Strategy loop crashed; reconnecting...", flush=True)

        if stop_flag["stop"]:
            break

        print(
            f"Reconnecting in {RECONNECT_DELAY_SEC}s (position={strategy.position})...",
            flush=True,
        )
        logger.info("Reconnecting in %ss", RECONNECT_DELAY_SEC)
        time.sleep(RECONNECT_DELAY_SEC)


if __name__ == "__main__":
    main()
