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
from strategy_balance import BalanceStrategy
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
DEFAULT_MARKET_OPEN = "09:00"
DEFAULT_MARKET_CLOSE = "23:30"


def _parse_hhmm(value: str) -> tuple[int, int]:
    parts = value.strip().split(":")
    if len(parts) != 2:
        raise RuntimeError(f"Invalid time '{value}', expected HH:MM")
    return int(parts[0]), int(parts[1])


def _market_window() -> tuple[str, str]:
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
    open_s = os.getenv("MARKET_OPEN", DEFAULT_MARKET_OPEN).strip()
    close_s = os.getenv("MARKET_CLOSE", DEFAULT_MARKET_CLOSE).strip()
    return open_s, close_s


def is_market_open(now: datetime | None = None) -> bool:
    """MCX Gold Petal default session: Mon-Fri 09:00-23:30 IST."""
    now = now or datetime.now(IST)
    if now.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    open_s, close_s = _market_window()
    open_h, open_m = _parse_hhmm(open_s)
    close_h, close_m = _parse_hhmm(close_s)
    start = now.replace(hour=open_h, minute=open_m, second=0, microsecond=0)
    end = now.replace(hour=close_h, minute=close_m, second=0, microsecond=0)
    return start <= now <= end


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


def run_once(
    strategy_s1: PressureStrategy,
    strategy_s2: BalanceStrategy,
    stop_flag: dict,
) -> None:
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
    print("S1       : netΔ pressure strategy (30-min bars)", flush=True)
    print("S2       : buy_sum - sell_sum sign flips (every tick)", flush=True)
    print(f"DRY_RUN  : {dry_run} (signals only; no live orders)", flush=True)
    open_s, close_s = _market_window()
    print(f"Hours    : {open_s}-{close_s} IST, Mon-Fri only", flush=True)
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
        if not is_market_open(now):
            msg = (
                f"[{now.isoformat(timespec='seconds')}] "
                "outside market hours — skip bar/signal"
            )
            print(msg, flush=True)
            logger.info(msg)
            next_bar_at = _next_boundary(now, interval)
            return

        if latest["cmp"] is None or latest["bp"] is None or latest["sp"] is None:
            msg = f"[{now.isoformat(timespec='seconds')}] bar skipped — no tick data yet"
            print(msg, flush=True)
            logger.info(msg)
            next_bar_at = _next_boundary(now, interval)
            return

        label_time = now.replace(second=0, microsecond=0)
        minute_block = (label_time.minute // interval) * interval
        label_time = label_time.replace(minute=minute_block)

        bar = BarSnapshot(
            time_label=label_time.isoformat(timespec="seconds"),
            cmp=float(latest["cmp"]),
            bp=float(latest["bp"]),
            sp=float(latest["sp"]),
        )

        # S1 stays on 30-minute bars only.
        result_s1 = strategy_s1.on_bar(bar)
        save_bar(
            time_label=bar.time_label,
            symbol=symbol,
            token=token,
            cmp=bar.cmp,
            bp=bar.bp,
            sp=bar.sp,
            net=result_s1.net,
            price_delta=result_s1.price_delta,
            net_delta=result_s1.net_delta,
        )
        save_signal(
            time_label=bar.time_label,
            symbol=symbol,
            action=result_s1.action,
            position_after=result_s1.position_after,
            reason=result_s1.reason,
            price_delta=result_s1.price_delta,
            net=result_s1.net,
            net_delta=result_s1.net_delta,
            dry_run=dry_run,
            strategy=strategy_s1.name,
        )
        line = (
            f"[{bar.time_label}] {strategy_s1.name} "
            f"CMP={bar.cmp} BP={bar.bp} SP={bar.sp} NET={result_s1.net} "
            f"priceΔ={result_s1.price_delta} netΔ={result_s1.net_delta} "
            f"=> {result_s1.action} (pos={result_s1.position_after}) | {result_s1.reason}"
        )
        print(line, flush=True)
        logger.info(line)

        next_bar_at = _next_boundary(now, interval)

    def emit_s2_if_changed(now: datetime) -> None:
        """S2 reacts instantly when buy-sell sign flips."""
        if latest["cmp"] is None or latest["bp"] is None or latest["sp"] is None:
            return
        tick_bar = BarSnapshot(
            time_label=now.isoformat(timespec="seconds"),
            cmp=float(latest["cmp"]),
            bp=float(latest["bp"]),
            sp=float(latest["sp"]),
        )
        result = strategy_s2.on_bar(tick_bar)
        # Only record transitions (BUY/SHORT/CLOSE), not continuous HOLD.
        if result.action not in {"BUY", "SHORT", "CLOSE"}:
            return
        save_signal(
            time_label=tick_bar.time_label,
            symbol=symbol,
            action=result.action,
            position_after=result.position_after,
            reason=result.reason,
            price_delta=result.price_delta,
            net=result.net,
            net_delta=result.net_delta,
            dry_run=dry_run,
            strategy=strategy_s2.name,
        )
        line = (
            f"[{tick_bar.time_label}] {strategy_s2.name} "
            f"CMP={tick_bar.cmp} BP={tick_bar.bp} SP={tick_bar.sp} NET={result.net} "
            f"=> {result.action} (pos={result.position_after}) | {result.reason}"
        )
        print(line, flush=True)
        logger.info(line)

    def on_data(_wsapp, message):
        nonlocal tick_count, next_bar_at
        if stop_flag["stop"]:
            return
        if not isinstance(message, dict):
            return

        try:
            now = datetime.now(IST)
            # Skip night/weekend noise entirely.
            if not is_market_open(now):
                if now >= next_bar_at:
                    next_bar_at = _next_boundary(now, interval)
                return

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
                    f"next_bar={next_bar_at.strftime('%H:%M:%S')} "
                    f"s2={strategy_s2.position}"
                )
                print(line, flush=True)
                logger.info(line)

            # S2: tick-based sign flips
            emit_s2_if_changed(now)

            # S1: 30-min bars
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
            "WebSocket closed. ticks=%s s1=%s s2=%s args=%s",
            tick_count,
            strategy_s1.position,
            strategy_s2.position,
            args,
        )
        print(
            f"WebSocket closed. ticks={tick_count} "
            f"s1={strategy_s1.position} s2={strategy_s2.position}",
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
    strategy_s1 = PressureStrategy()
    strategy_s2 = BalanceStrategy()
    init_db()
    _seed_strategy_from_db(strategy_s1)

    def handle_signal(_signum, _frame):
        print("\nStopping strategy runner...", flush=True)
        stop_flag["stop"] = True
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    while not stop_flag["stop"]:
        try:
            run_once(strategy_s1, strategy_s2, stop_flag)
        except SystemExit:
            raise
        except Exception:
            logger.exception("Strategy loop crashed; reconnecting")
            print("Strategy loop crashed; reconnecting...", flush=True)

        if stop_flag["stop"]:
            break

        print(
            f"Reconnecting in {RECONNECT_DELAY_SEC}s "
            f"(s1={strategy_s1.position} s2={strategy_s2.position})...",
            flush=True,
        )
        logger.info("Reconnecting in %ss", RECONNECT_DELAY_SEC)
        time.sleep(RECONNECT_DELAY_SEC)


if __name__ == "__main__":
    main()
