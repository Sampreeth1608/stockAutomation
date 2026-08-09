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
from depth import depth_buy_sell_sums
from export_full_ticks import _depth_side
from portfolio import portfolio_from_env
from regime import RegimeDetector
from storage import init_db, latest_bar, save_bar, save_signal, save_tick
from strategy import BarSnapshot, PressureStrategy
from strategy_balance import BalanceStrategy, balance_from_env
from strategy_ml import MLStrategy, ml_strategy_from_env
from strategy_overnight import OvernightStrategy, overnight_from_env
from strategy_minedge import MinEdgeStrategy, min30_from_env, minedge_from_env
from strategy_net_zigzag import NetZigzagStrategy, net_zigzag_from_env
from strategy_state_s9 import StateS9Strategy, state_s9_from_env
from zigzag_recorder import recorder_from_env
from s9_state_journal import s9_journal_from_env
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


def _spread_bps(message: dict, ltp: float | None) -> float | None:
    if ltp is None or not ltp:
        return None
    buy = _depth_side(message, "buy")
    sell = _depth_side(message, "sell")
    b1 = buy[0][0] if buy else None
    s1 = sell[0][0] if sell else None
    if b1 is None or s1 is None:
        return None
    mid = (b1 + s1) / 2.0
    if not mid:
        return None
    return (s1 - b1) / mid * 1e4


def run_once(
    strategy_s1: PressureStrategy,
    strategy_s2: BalanceStrategy,
    strategy_s3: MLStrategy,
    strategy_s4: OvernightStrategy,
    strategy_s5: MinEdgeStrategy,
    strategy_s6: MinEdgeStrategy,
    strategy_s8: NetZigzagStrategy,
    zigzag_rec,
    strategy_s9: StateS9Strategy,
    s9_journal,
    portfolio,
    regime_det: RegimeDetector,
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
    state = {
        "next_bar_at": _next_boundary(datetime.now(IST), interval),
        "tick_count": 0,
    }
    closed = {"done": False}

    print("=== Gold Petal strategy runner ===", flush=True)
    print(f"Symbol   : {symbol}", flush=True)
    print(f"Token    : {token}", flush=True)
    print(f"Expiry   : {contract['expiry']}", flush=True)
    print(f"Interval : {interval} minutes", flush=True)
    print(
        f"Portfolio: enabled={sorted(portfolio.enabled)} "
        f"flatten_on_bad_regime={portfolio.flatten_when_blocked}",
        flush=True,
    )
    print(
        f"S1       : netΔ 30-min [{'ON' if portfolio.is_enabled(strategy_s1.name) else 'OFF'}]",
        flush=True,
    )
    print(
        f"S2       : 1-min depth sum "
        f"[{'ON' if portfolio.is_enabled(strategy_s2.name) else 'OFF'}] "
        f"{strategy_s2.status_line}",
        flush=True,
    )
    print(
        f"S3       : ML [{('ON' if portfolio.is_enabled(strategy_s3.name) else 'OFF')}] "
        f"{strategy_s3.status_line}",
        flush=True,
    )
    print(
        f"S4       : overnight next-open "
        f"[{'ON' if portfolio.is_enabled(strategy_s4.name) else 'OFF'}] "
        f"{strategy_s4.status_line}",
        flush=True,
    )
    print(
        f"S5       : min-edge "
        f"[{'ON' if portfolio.is_enabled(strategy_s5.name) else 'OFF'}] "
        f"{strategy_s5.status_line}",
        flush=True,
    )
    print(
        f"S6       : min-30pts "
        f"[{'ON' if portfolio.is_enabled(strategy_s6.name) else 'OFF'}] "
        f"{strategy_s6.status_line}",
        flush=True,
    )
    print(
        f"S8       : NET zigzag (30m bar always = MTF +₹42k path when configured) "
        f"[{'ON' if portfolio.is_enabled(strategy_s8.name) else 'OFF'}] "
        f"{strategy_s8.status_line}",
        flush=True,
    )
    print(
        f"S8 record: retune db={zigzag_rec.db_path} enabled={zigzag_rec.enabled} "
        f"snap_every={zigzag_rec.snap_every_n}",
        flush=True,
    )
    print(
        f"S9       : state machine "
        f"[{'ON' if portfolio.is_enabled(strategy_s9.name) else 'OFF'}] "
        f"{strategy_s9.status_line}",
        flush=True,
    )
    print(
        f"S9 journal: db={s9_journal.db_path} enabled={s9_journal.enabled}",
        flush=True,
    )
    print(
        f"Mode     : {'PAPER (DRY_RUN)' if dry_run else 'LIVE ORDERS NOT WIRED — staying signals-only'}",
        flush=True,
    )
    open_s, close_s = _market_window()
    print(f"Hours    : {open_s}-{close_s} IST, Mon-Fri only", flush=True)
    print(f"Next bar : {state['next_bar_at'].isoformat(timespec='seconds')}", flush=True)
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
        if not is_market_open(now):
            msg = (
                f"[{now.isoformat(timespec='seconds')}] "
                "outside market hours — skip bar/signal"
            )
            print(msg, flush=True)
            logger.info(msg)
            state["next_bar_at"] = _next_boundary(now, interval)
            return

        if latest["cmp"] is None or latest["bp"] is None or latest["sp"] is None:
            msg = f"[{now.isoformat(timespec='seconds')}] bar skipped — no tick data yet"
            print(msg, flush=True)
            logger.info(msg)
            state["next_bar_at"] = _next_boundary(now, interval)
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
        regime = regime_det.last.regime
        action = result_s1.action
        # Block new entries when regime unfit; optionally flatten.
        if action in {"BUY", "SHORT"} and not portfolio.allows(strategy_s1.name, regime):
            line = (
                f"[{bar.time_label}] {strategy_s1.name} SKIP {action} "
                f"regime={regime} ({regime_det.last.reason})"
            )
            print(line, flush=True)
            logger.info(line)
            # undo internal position open from on_bar
            strategy_s1.position = "flat"
            strategy_s1.entry_cmp = None
            strategy_s1.entry_net_delta = None
            action = "HOLD"
        elif (
            strategy_s1.position != "flat"
            and portfolio.should_flatten(strategy_s1.name, regime)
            and action != "CLOSE"
        ):
            action = "CLOSE"
            strategy_s1.position = "flat"
            strategy_s1.entry_cmp = None
            strategy_s1.entry_net_delta = None
            result_s1 = type(result_s1)(
                action="CLOSE",
                position_after="flat",
                price_delta=result_s1.price_delta,
                net=result_s1.net,
                net_delta=result_s1.net_delta,
                prev_net_delta=result_s1.prev_net_delta,
                reason=f"regime_flatten {regime}: {regime_det.last.reason}",
            )

        if action in {"BUY", "SHORT", "CLOSE"}:
            save_signal(
                time_label=bar.time_label,
                symbol=symbol,
                action=action,
                position_after=result_s1.position_after if action != "CLOSE" else "flat",
                reason=result_s1.reason,
                price_delta=result_s1.price_delta,
                net=result_s1.net,
                net_delta=result_s1.net_delta,
                dry_run=dry_run,
                strategy=strategy_s1.name,
                cmp=bar.cmp,
            )
        line = (
            f"[{bar.time_label}] {strategy_s1.name} regime={regime} "
            f"CMP={bar.cmp} BP={bar.bp} SP={bar.sp} NET={result_s1.net} "
            f"priceΔ={result_s1.price_delta} netΔ={result_s1.net_delta} "
            f"=> {action} (pos={strategy_s1.position}) | {result_s1.reason}"
        )
        print(line, flush=True)
        logger.info(line)

        state["next_bar_at"] = _next_boundary(now, interval)

    def emit_s2_if_changed(now: datetime, message: dict) -> None:
        """S2: accumulate buy1-5/sell1-5 for 1 minute, then trade on net sign."""
        if not portfolio.is_enabled(strategy_s2.name):
            return
        if latest["cmp"] is None:
            return

        result = strategy_s2.on_tick(now, float(latest["cmp"]), message)
        if result is None:
            return

        regime = regime_det.last.regime
        action = result.action
        buy_sum = float((strategy_s2.last_minute or {}).get("buy_sum", 0))
        sell_sum = float((strategy_s2.last_minute or {}).get("sell_sum", 0))

        if action in {"BUY", "SHORT"} and not portfolio.allows(strategy_s2.name, regime):
            strategy_s2.position = "flat"
            return
        if (
            strategy_s2.position != "flat"
            and portfolio.should_flatten(strategy_s2.name, regime)
            and action != "CLOSE"
        ):
            if strategy_s2.position != "flat":
                action = "CLOSE"
                strategy_s2.position = "flat"
                result = type(result)(
                    action="CLOSE",
                    position_after="flat",
                    price_delta=result.price_delta,
                    net=result.net,
                    net_delta=result.net_delta,
                    prev_net_delta=result.prev_net_delta,
                    reason=f"regime_flatten {regime}: {regime_det.last.reason}",
                )
            else:
                return

        if action not in {"BUY", "SHORT", "CLOSE"}:
            return
        save_signal(
            time_label=now.isoformat(timespec="seconds"),
            symbol=symbol,
            action=action,
            position_after=result.position_after if action != "CLOSE" else "flat",
            reason=result.reason,
            price_delta=result.price_delta,
            net=result.net,
            net_delta=result.net_delta,
            dry_run=dry_run,
            strategy=strategy_s2.name,
            cmp=float(latest["cmp"]),
        )
        line = (
            f"[{now.isoformat(timespec='seconds')}] {strategy_s2.name} regime={regime} "
            f"CMP={latest['cmp']} buy_sum={buy_sum:.0f} sell_sum={sell_sum:.0f} "
            f"NET={result.net} "
            f"=> {action} (pos={strategy_s2.position}) | {result.reason}"
        )
        print(line, flush=True)
        logger.info(line)

    def emit_s3_if_changed(now: datetime, message: dict) -> None:
        """S3 ML: score depth/LTP features; emit BUY/SHORT/CLOSE transitions."""
        if not portfolio.is_enabled(strategy_s3.name):
            return
        if not strategy_s3.enabled:
            return
        if latest["cmp"] is None:
            return
        strategy_s3.push_tick(
            message,
            received_at=now.isoformat(timespec="seconds"),
            exchange_timestamp=message.get("exchange_timestamp"),
        )
        pos_before = strategy_s3.position
        result = strategy_s3.maybe_signal(now)
        regime = regime_det.last.regime
        allowed = portfolio.allows(strategy_s3.name, regime)

        if not allowed:
            # Model may have just opened — undo entry; only CLOSE if we were already in.
            if result is not None and result.action in {"BUY", "SHORT"}:
                strategy_s3.position = "flat"
                result = None
            if pos_before == "flat":
                return
            from strategy import SignalResult

            strategy_s3.position = "flat"
            strategy_s3.last_signal_ts = now
            result = SignalResult(
                action="CLOSE",
                position_after="flat",
                price_delta=None,
                net=0.0,
                net_delta=None,
                prev_net_delta=None,
                reason=f"regime_flatten {regime}: {regime_det.last.reason}",
            )

        if result is None or result.action not in {"BUY", "SHORT", "CLOSE"}:
            return

        action = result.action
        save_signal(
            time_label=now.isoformat(timespec="seconds"),
            symbol=symbol,
            action=action,
            position_after="flat" if action == "CLOSE" else result.position_after,
            reason=result.reason,
            price_delta=result.price_delta,
            net=result.net,
            net_delta=result.net_delta,
            dry_run=dry_run,
            strategy=strategy_s3.name,
            cmp=float(latest["cmp"]),
        )
        line = (
            f"[{now.isoformat(timespec='seconds')}] {strategy_s3.name} regime={regime} "
            f"CMP={latest['cmp']} prob_up={strategy_s3.last_prob} "
            f"=> {action} (pos={strategy_s3.position}) | {result.reason}"
        )
        print(line, flush=True)
        logger.info(line)


    def emit_s4_if_changed(now: datetime, message: dict) -> None:
        """S4 overnight: enter near close, exit after next open. Ignores tick regime."""
        if not portfolio.is_enabled(strategy_s4.name):
            return
        if not strategy_s4.enabled:
            return
        if latest["cmp"] is None:
            return
        # Skip new overnight entries if book is abnormally wide at decision time
        if (
            regime_det.last.regime == "WIDE_SPREAD"
            and strategy_s4.position == "flat"
        ):
            return
        # feed tick row for day feature build
        from export_full_ticks import row_from_tick
        import json as _json
        row = row_from_tick(
            now.isoformat(timespec="seconds"),
            message.get("exchange_timestamp"),
            _json.dumps(message, default=str),
        )
        if row.get("ltp") is not None:
            strategy_s4.push_tick_row(row)
        result = strategy_s4.maybe_signal(now, float(latest["cmp"]))
        if result is None or result.action not in {"BUY", "SHORT", "CLOSE"}:
            return
        save_signal(
            time_label=now.isoformat(timespec="seconds"),
            symbol=symbol,
            action=result.action,
            position_after=result.position_after,
            reason=result.reason,
            price_delta=result.price_delta,
            net=result.net,
            net_delta=result.net_delta,
            dry_run=dry_run,
            strategy=strategy_s4.name,
            cmp=float(latest["cmp"]),
        )
        line = (
            f"[{now.isoformat(timespec='seconds')}] {strategy_s4.name} "
            f"CMP={latest['cmp']} prob_gap_up={strategy_s4.last_prob} "
            f"=> {result.action} (pos={strategy_s4.position}) | {result.reason}"
        )
        print(line, flush=True)
        logger.info(line)


    def emit_s5_if_changed(now: datetime, message: dict) -> None:
        """S5: trade only when expected move covers fees / min edge points."""
        if not portfolio.is_enabled(strategy_s5.name):
            return
        if latest["cmp"] is None:
            return
        if not portfolio.allows(strategy_s5.name, regime_det.last.regime):
            # still allow exits
            if strategy_s5.position == "flat":
                return
        result = strategy_s5.on_tick(now, float(latest["cmp"]), message)
        if result is None or result.action not in {"BUY", "SHORT", "CLOSE"}:
            return
        if result.action in {"BUY", "SHORT"} and not portfolio.allows(
            strategy_s5.name, regime_det.last.regime
        ):
            strategy_s5.position = "flat"
            strategy_s5.entry_price = None
            return
        save_signal(
            time_label=now.isoformat(timespec="seconds"),
            symbol=symbol,
            action=result.action,
            position_after=result.position_after,
            reason=result.reason,
            price_delta=result.price_delta,
            net=result.net,
            net_delta=result.net_delta,
            dry_run=dry_run,
            strategy=strategy_s5.name,
            cmp=float(latest["cmp"]),
        )
        line = (
            f"[{now.isoformat(timespec='seconds')}] {strategy_s5.name} "
            f"regime={regime_det.last.regime} CMP={latest['cmp']} "
            f"exp={strategy_s5.last_expected} "
            f"=> {result.action} (pos={strategy_s5.position}) | {result.reason}"
        )
        print(line, flush=True)
        logger.info(line)

    def emit_s6_if_changed(now: datetime, message: dict) -> None:
        """S6: trade when expected move >= 30 points (user floor, no fee gate)."""
        if not portfolio.is_enabled(strategy_s6.name):
            return
        if latest["cmp"] is None:
            return
        if not portfolio.allows(strategy_s6.name, regime_det.last.regime):
            if strategy_s6.position == "flat":
                return
        result = strategy_s6.on_tick(now, float(latest["cmp"]), message)
        if result is None or result.action not in {"BUY", "SHORT", "CLOSE"}:
            return
        if result.action in {"BUY", "SHORT"} and not portfolio.allows(
            strategy_s6.name, regime_det.last.regime
        ):
            strategy_s6.position = "flat"
            strategy_s6.entry_price = None
            return
        save_signal(
            time_label=now.isoformat(timespec="seconds"),
            symbol=symbol,
            action=result.action,
            position_after=result.position_after,
            reason=result.reason,
            price_delta=result.price_delta,
            net=result.net,
            net_delta=result.net_delta,
            dry_run=dry_run,
            strategy=strategy_s6.name,
            cmp=float(latest["cmp"]),
        )
        line = (
            f"[{now.isoformat(timespec='seconds')}] {strategy_s6.name} "
            f"regime={regime_det.last.regime} CMP={latest['cmp']} "
            f"exp={strategy_s6.last_expected} "
            f"=> {result.action} (pos={strategy_s6.position}) | {result.reason}"
        )
        print(line, flush=True)
        logger.info(line)

    def emit_s8_if_changed(now: datetime, message: dict) -> None:
        """S8: fixed NET zigzag (best hist params) + retune recorder."""
        if not portfolio.is_enabled(strategy_s8.name):
            return
        if latest["cmp"] is None:
            return
        ts = now.isoformat(timespec="seconds")
        ltp = float(latest["cmp"])
        tbq = latest.get("bp")
        tsq = latest.get("sp")
        try:
            tbq_f = float(tbq) if tbq is not None else None
            tsq_f = float(tsq) if tsq is not None else None
        except (TypeError, ValueError):
            tbq_f = tsq_f = None
        exch_ts = message.get("exchange_timestamp")
        try:
            exch_ts_i = int(exch_ts) if exch_ts is not None else None
        except (TypeError, ValueError):
            exch_ts_i = None

        if not portfolio.allows(strategy_s8.name, regime_det.last.regime):
            if strategy_s8.position == "flat":
                return
        pos_before = strategy_s8.position
        entry_before = strategy_s8.entry_price
        result = strategy_s8.on_tick(now, ltp, message)

        # Always keep retune path snapshots while in a trade
        zigzag_rec.on_tick_snapshot(
            ts=ts,
            ltp=ltp,
            tbq=tbq_f if tbq_f is not None else strategy_s8.last_tbq,
            tsq=tsq_f if tsq_f is not None else strategy_s8.last_tsq,
            position=strategy_s8.position if strategy_s8.position != "flat" else pos_before,
            entry_ltp=strategy_s8.entry_price or entry_before,
            exchange_timestamp=exch_ts_i,
        )

        if result is None or result.action not in {"BUY", "SHORT", "CLOSE"}:
            return
        if result.action in {"BUY", "SHORT"} and not portfolio.allows(
            strategy_s8.name, regime_det.last.regime
        ):
            strategy_s8.position = "flat"
            strategy_s8.entry_price = None
            return
        if (
            strategy_s8.position != "flat"
            and portfolio.should_flatten(strategy_s8.name, regime_det.last.regime)
            and result.action != "CLOSE"
        ):
            from strategy import SignalResult as _SR

            strategy_s8.position = "flat"
            strategy_s8.entry_price = None
            result = _SR(
                action="CLOSE",
                position_after="flat",
                price_delta=result.price_delta,
                net=result.net,
                net_delta=result.net_delta,
                prev_net_delta=result.prev_net_delta,
                reason=f"regime_flatten {regime_det.last.regime}: {regime_det.last.reason}",
            )

        if result.action == "BUY":
            log_side = "long"
        elif result.action == "SHORT":
            log_side = "short"
        else:
            log_side = pos_before if pos_before != "flat" else None
        zigzag_rec.log(
            ts=ts,
            action=result.action,
            ltp=ltp,
            tbq=strategy_s8.last_tbq,
            tsq=strategy_s8.last_tsq,
            net=strategy_s8.last_net,
            imb_pct=strategy_s8.last_imb,
            side=log_side,
            position=result.position_after,
            entry_ltp=entry_before if result.action == "CLOSE" else strategy_s8.entry_price,
            unrealized_pts=result.price_delta,
            reason=result.reason,
            exchange_timestamp=exch_ts_i,
            extra={"regime": regime_det.last.regime, "bias": strategy_s8.bias},
        )
        save_signal(
            time_label=ts,
            symbol=symbol,
            action=result.action,
            position_after=result.position_after,
            reason=result.reason,
            price_delta=result.price_delta,
            net=result.net,
            net_delta=result.net_delta,
            dry_run=dry_run,
            strategy=strategy_s8.name,
            cmp=ltp,
        )
        line = (
            f"[{ts}] {strategy_s8.name} "
            f"regime={regime_det.last.regime} CMP={ltp} "
            f"bias={strategy_s8.bias} "
            f"net={strategy_s8.last_net:.0f} imb={strategy_s8.last_imb:.1f}% "
            f"tbq={strategy_s8.last_tbq:.0f} tsq={strategy_s8.last_tsq:.0f} "
            f"=> {result.action} (pos={strategy_s8.position}) | {result.reason}"
        )
        print(line, flush=True)
        logger.info(line)

    def emit_s9_if_changed(now: datetime, message: dict) -> None:
        """S9: 27-state TBQ/TSQ/Price machine on N-minute bar closes."""
        if not portfolio.is_enabled(strategy_s9.name):
            return
        if latest["cmp"] is None:
            return
        if not portfolio.allows(strategy_s9.name, regime_det.last.regime):
            if strategy_s9.position == "flat":
                return
        pos_before = strategy_s9.position
        entry_before = strategy_s9.entry_price
        result = strategy_s9.on_tick(now, float(latest["cmp"]), message)

        # Journal every new bar state (even without trade)
        if strategy_s9.last_state not in {"WARMUP"} and strategy_s9.last_state != getattr(
            emit_s9_if_changed, "_last_logged_state", None
        ):
            emit_s9_if_changed._last_logged_state = strategy_s9.last_state  # type: ignore[attr-defined]
            s9_journal.log(
                ts=now.isoformat(timespec="seconds"),
                event="STATE",
                state=strategy_s9.last_state,
                label=strategy_s9.last_label,
                prev_state=strategy_s9.prev_state,
                ltp=float(latest["cmp"]),
                tbq=strategy_s9.last_tbq,
                tsq=strategy_s9.last_tsq,
                net=strategy_s9.last_net,
                imb_pct=strategy_s9.last_imb,
                position=strategy_s9.position,
                entry_ltp=strategy_s9.entry_price,
                reason=strategy_s9.last_skip,
                extra={"regime": regime_det.last.regime},
            )

        if result is None or result.action not in {
            "BUY",
            "SHORT",
            "CLOSE",
            "REVERSE_LONG",
            "REVERSE_SHORT",
        }:
            return
        if result.action in {"BUY", "SHORT", "REVERSE_LONG", "REVERSE_SHORT"} and not portfolio.allows(
            strategy_s9.name, regime_det.last.regime
        ):
            strategy_s9.position = "flat"
            strategy_s9.entry_price = None
            return
        if (
            strategy_s9.position != "flat"
            and portfolio.should_flatten(strategy_s9.name, regime_det.last.regime)
            and result.action not in {"CLOSE", "REVERSE_LONG", "REVERSE_SHORT"}
        ):
            from strategy import SignalResult as _SR

            strategy_s9.position = "flat"
            strategy_s9.entry_price = None
            result = _SR(
                action="CLOSE",
                position_after="flat",
                price_delta=result.price_delta,
                net=result.net,
                net_delta=result.net_delta,
                prev_net_delta=result.prev_net_delta,
                reason=f"regime_flatten {regime_det.last.regime}: {regime_det.last.reason}",
            )

        s9_journal.log(
            ts=now.isoformat(timespec="seconds"),
            event=result.action,
            state=strategy_s9.last_state,
            label=strategy_s9.last_label,
            prev_state=strategy_s9.prev_state,
            ltp=float(latest["cmp"]),
            tbq=strategy_s9.last_tbq,
            tsq=strategy_s9.last_tsq,
            net=strategy_s9.last_net,
            imb_pct=strategy_s9.last_imb,
            position=result.position_after,
            entry_ltp=(
                entry_before
                if result.action in {"CLOSE", "REVERSE_LONG", "REVERSE_SHORT"}
                else strategy_s9.entry_price
            ),
            reason=result.reason,
            extra={"regime": regime_det.last.regime, "pos_before": pos_before},
        )
        save_signal(
            time_label=now.isoformat(timespec="seconds"),
            symbol=symbol,
            action=result.action,
            position_after=result.position_after,
            reason=result.reason,
            price_delta=result.price_delta,
            net=result.net,
            net_delta=result.net_delta,
            dry_run=dry_run,
            strategy=strategy_s9.name,
            cmp=float(latest["cmp"]),
        )
        line = (
            f"[{now.isoformat(timespec='seconds')}] {strategy_s9.name} "
            f"regime={regime_det.last.regime} CMP={latest['cmp']} "
            f"state={strategy_s9.last_label} net={strategy_s9.last_net:.0f} "
            f"=> {result.action} (pos={strategy_s9.position}) | {result.reason}"
        )
        print(line, flush=True)
        logger.info(line)

    def on_data(_wsapp, message):
        if stop_flag["stop"]:
            return
        if not isinstance(message, dict):
            return

        try:
            now = datetime.now(IST)
            # Skip night/weekend noise entirely.
            if not is_market_open(now):
                if now >= state["next_bar_at"]:
                    state["next_bar_at"] = _next_boundary(now, interval)
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

            if latest["cmp"] is not None:
                regime_det.update(
                    float(latest["cmp"]),
                    _spread_bps(message, float(latest["cmp"])),
                )

            state["tick_count"] += 1
            tick_count = state["tick_count"]
            if tick_count == 1 or tick_count % 50 == 0:
                s3_extra = ""
                if strategy_s3.enabled:
                    if strategy_s3.last_prob is not None:
                        s3_extra = f" p={strategy_s3.last_prob:.2f}"
                    else:
                        skip = getattr(strategy_s3, "last_skip", None)
                        if skip:
                            s3_extra = f" ({skip})"
                rs = regime_det.last
                line = (
                    f"[{received_at}] ticks={tick_count} "
                    f"ltp={latest['cmp']} bp={latest['bp']} sp={latest['sp']} "
                    f"regime={rs.regime} "
                    f"next_bar={state['next_bar_at'].strftime('%H:%M:%S')} "
                    f"s2={strategy_s2.position} s3={strategy_s3.position} "
                    f"s4={strategy_s4.position} s5={strategy_s5.position} "
                    f"s6={strategy_s6.position} s8={strategy_s8.position}"
                    f"/{strategy_s8.bias} s9={strategy_s9.position}"
                    f"/{strategy_s9.last_label}{s3_extra}"
                )
                print(line, flush=True)
                logger.info(line)

            # S2: 1-min sum of buy1-5 vs sell1-5
            emit_s2_if_changed(now, message)
            # S3: ML model BUY/SHORT/CLOSE
            emit_s3_if_changed(now, message)
            # S4: overnight next-open
            emit_s4_if_changed(now, message)
            # S5: min-edge (fee-aware)
            emit_s5_if_changed(now, message)
            # S6: min 30 points expected move
            emit_s6_if_changed(now, message)
            # S8: NET zigzag (unchanged)
            emit_s8_if_changed(now, message)
            # S9: 27-state bar machine
            emit_s9_if_changed(now, message)

            # S1: 30-min bars
            if now >= state["next_bar_at"]:
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
            "WebSocket closed. ticks=%s s1=%s s2=%s s3=%s args=%s",
            state["tick_count"],
            strategy_s1.position,
            strategy_s2.position,
            strategy_s3.position,
            args,
        )
        print(
            f"WebSocket closed. ticks={state['tick_count']} "
            f"s1={strategy_s1.position} s2={strategy_s2.position} "
            f"s3={strategy_s3.position}",
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
    strategy_s2 = balance_from_env()
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
    strategy_s3 = ml_strategy_from_env()
    strategy_s4 = overnight_from_env()
    strategy_s5 = minedge_from_env()
    strategy_s6 = min30_from_env()
    strategy_s8 = net_zigzag_from_env()
    zigzag_rec = recorder_from_env()
    zigzag_rec.record_params(strategy_s8.cfg)
    strategy_s9 = state_s9_from_env()
    s9_journal = s9_journal_from_env()
    portfolio = portfolio_from_env()
    regime_det = RegimeDetector(window=60)
    init_db()
    _seed_strategy_from_db(strategy_s1)
    print(f"S3_ML: {strategy_s3.status_line}", flush=True)
    print(f"S4_OVERNIGHT: {strategy_s4.status_line}", flush=True)
    print(f"S5_MINEDGE: {strategy_s5.status_line}", flush=True)
    print(f"S6_MIN30: {strategy_s6.status_line}", flush=True)
    print(f"S8_NET_ZIGZAG: {strategy_s8.status_line}", flush=True)
    print(f"S8 retune recorder: {zigzag_rec.db_path} enabled={zigzag_rec.enabled}", flush=True)
    print(f"S9_STATE30: {strategy_s9.status_line}", flush=True)
    print(f"S9 journal: {s9_journal.db_path} enabled={s9_journal.enabled}", flush=True)
    print(
        f"Portfolio enabled={sorted(portfolio.enabled)} "
        f"(set ENABLE_S1/S2/S3/S4/S5/S6/S8/S9 in .env)",
        flush=True,
    )

    def handle_signal(_signum, _frame):
        print("\nStopping strategy runner...", flush=True)
        stop_flag["stop"] = True
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    while not stop_flag["stop"]:
        try:
            run_once(
                strategy_s1,
                strategy_s2,
                strategy_s3,
                strategy_s4,
                strategy_s5,
                strategy_s6,
                strategy_s8,
                zigzag_rec,
                strategy_s9,
                s9_journal,
                portfolio,
                regime_det,
                stop_flag,
            )
        except SystemExit:
            raise
        except Exception:
            logger.exception("Strategy loop crashed; reconnecting")
            print("Strategy loop crashed; reconnecting...", flush=True)

        if stop_flag["stop"]:
            break

        print(
            f"Reconnecting in {RECONNECT_DELAY_SEC}s "
            f"(s1={strategy_s1.position} s2={strategy_s2.position} "
            f"s3={strategy_s3.position} s4={strategy_s4.position} "
            f"s5={strategy_s5.position} s6={strategy_s6.position} "
            f"s8={strategy_s8.position}/{strategy_s8.bias} "
            f"s9={strategy_s9.position}/{strategy_s9.last_label} "
            f"regime={regime_det.last.regime})...",
            flush=True,
        )
        logger.info("Reconnecting in %ss", RECONNECT_DELAY_SEC)
        time.sleep(RECONNECT_DELAY_SEC)


if __name__ == "__main__":
    main()
