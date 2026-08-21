"""Run Gold Petal strategies: idle until Arm live; then Angel orders when gated."""

from __future__ import annotations

import os
import signal
import sys
import threading
import time
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from logzero import logger
from SmartApi.smartWebSocketV2 import SmartWebSocketV2

from auth import login
from depth import depth_buy_sell_sums
from entry_gates import allow_new_entry
from export_full_ticks import _depth_side
from live_orders import broker_from_session, mirror_positions_from_signals
from portfolio import portfolio_from_env
from regime import RegimeDetector
from market_mood import (
    FORMULA_GATE_BOOKS,
    MOOD_EXEMPT_BOOKS,
    MoodDetector,
    mood_blocks_entry,
    mood_wants_flatten,
)
from storage import init_db, latest_bar, latest_signals, save_bar, save_signal as db_save_signal, save_tick
from desk_data import tick_feed_stale
from control_state import entries_blocked, is_live_mode_allowed, load_state
from position_safety import (
    emit_startup_closes,
    in_eod_flatten_window,
    intraday_open_for_flatten,
    startup_reconcile,
    write_bot_health,
)
from strategy import BarSnapshot, PressureStrategy
from strategy_balance import BalanceStrategy, balance_from_env
from strategy_ml import MLStrategy, ml_strategy_from_env
from strategy_minedge import MinEdgeStrategy, min30_from_env, minedge_from_env
from strategy_net_zigzag import (
    NetZigzagStrategy,
    net_zigzag_from_env,
    s10_legacy30_from_env,
)
from strategy_state_s9 import StateS9Strategy, state_s9_from_env
from strategy_hhhl_day import HhhlDayOvernightStrategy, hhhl_day_from_env, s4_swing_from_env
from strategy_s16 import S16HhhlWickStrategy, s16_from_env
from strategy_s18 import S18OhlcVolHtfStrategy, s18_from_env
from strategy_wick import wick_record_actions
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
TICK_WATCHDOG_SEC = float(os.getenv("TICK_WATCHDOG_SEC", "45") or 45)
TICK_FIRST_GRACE_SEC = float(os.getenv("TICK_FIRST_GRACE_SEC", "45") or 45)
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
    strategy_s4: HhhlDayOvernightStrategy,
    strategy_s5: MinEdgeStrategy,
    strategy_s6: MinEdgeStrategy,
    strategy_fb,
    strategy_s8: NetZigzagStrategy,
    zigzag_rec,
    strategy_s9: StateS9Strategy,
    s9_journal,
    strategy_s10: NetZigzagStrategy,
    strategy_s11,
    strategy_s13: HhhlDayOvernightStrategy,
    strategy_s16: S16HhhlWickStrategy,
    strategy_s18: S18OhlcVolHtfStrategy,
    strategy_s19,
    strategy_s20,
    strategy_og,
    portfolio,
    regime_det: RegimeDetector,
    mood_det: MoodDetector,
    amise_slots: list,
    stop_flag: dict,
) -> None:
    init_db()
    try:
        from trade_learner import get_learner

        get_learner().fit_from_db()
        print(f"Learner  : {get_learner().status_line()}", flush=True)
    except Exception as exc:
        print(f"Learner  : skip ({type(exc).__name__}: {exc})", flush=True)
    interval = _interval_minutes()
    dry_run = _dry_run()
    contract = find_goldpetal_futures(force_refresh=True)
    session = login()
    broker = broker_from_session(session, contract)
    # Seed Angel mirror from last live signal. Do not treat paper opens as contracts.
    try:
        seeded = mirror_positions_from_signals(
            latest_signals(limit=400, live_only=True),
            live_only=True,
        )
        if seeded and hasattr(broker, "seed_positions"):
            broker.seed_positions(seeded)
            print(f"Broker positions seeded: {seeded}", flush=True)
        elif not dry_run:
            print("Broker positions seeded: {} (not-armed opens are not live)", flush=True)
    except Exception as exc:
        logger.warning("Could not seed broker positions: %s", exc)

    symbol = contract["symbol"]
    token = contract["token"]
    exchange_type = contract["exchange_type"]
    try:
        from live_orders import refresh_angel_snapshot
        from desk_data import invalidate_live_pnl_cache

        snap = refresh_angel_snapshot(
            getattr(session, "api", None),
            symbol=str(symbol),
            token=str(token),
            min_interval_sec=0,
        )
        invalidate_live_pnl_cache()
        print(
            f"Angel Gold Petal net={snap.get('net')} pnl={snap.get('pnl')} "
            f"cleared={snap.get('cleared') or []}",
            flush=True,
        )
    except Exception as exc:
        logger.warning("Angel snapshot at start failed: %s", exc)

    latest = {"cmp": None, "bp": None, "sp": None, "message": None}
    state = {
        "next_bar_at": _next_boundary(datetime.now(IST), interval),
        "tick_count": 0,
    }
    closed = {"done": False}
    feed_watch = {"t": time.monotonic(), "got": False, "stop": False}

    def _entry_features(strategy_name: str, side: str = "") -> dict:
        from quality_filters import tick_features
        from strategy_net_zigzag import net_imbalance

        now_ist = datetime.now(IST)
        msg = latest.get("message") or {}
        tbq = float(msg.get("total_buy_quantity") or 0)
        tsq = float(msg.get("total_sell_quantity") or 0)
        _net, imb = net_imbalance(tbq, tsq) if (tbq or tsq) else (0.0, 0.0)
        return tick_features(
            strategy=strategy_name,
            side=side,
            hour_frac=now_ist.hour + now_ist.minute / 60.0,
            weekday=now_ist.weekday(),
            ltp=float(latest.get("cmp") or 0) or 0.0,
            imb_pct=imb,
        )

    def _may_enter(
        strategy_name: str, regime: str, *, side: str = ""
    ) -> tuple[bool, str]:
        """Live desk books only, and only after Arm. No paper fills."""
        from live_readiness import LIVE_ELIGIBLE_BOOKS

        if strategy_name not in LIVE_ELIGIBLE_BOOKS:
            return False, "not_live_book"
        if dry_run:
            return False, "not_armed"
        if not portfolio.allows(strategy_name, regime):
            return False, f"regime={regime}"
        blocked, mood_why = mood_blocks_entry(
            mood_det.last, side, strategy=strategy_name
        )
        if blocked:
            return False, mood_why
        return allow_new_entry(
            strategy_name, features=_entry_features(strategy_name, side)
        )

    def _strategy_active(strategy_name: str) -> bool:
        """False when ENABLE_* is off or desk force-disabled."""
        if not portfolio.is_enabled(strategy_name):
            return False
        st = load_state()
        if strategy_name in st.force_disabled:
            return False
        return True

    def _record_signal(
        *,
        time_label: str,
        action: str,
        position_after: str,
        reason: str,
        price_delta,
        net,
        net_delta,
        strategy: str,
        cmp,
    ) -> Any:
        db_save_signal(
            time_label=time_label,
            symbol=symbol,
            action=action,
            position_after=position_after,
            reason=reason,
            price_delta=price_delta,
            net=0.0 if net is None else float(net),
            net_delta=net_delta,
            dry_run=dry_run,
            strategy=strategy,
            cmp=cmp,
        )
        if str(action).upper() in {"CLOSE", "REVERSE_LONG", "REVERSE_SHORT"}:
            try:
                from trade_learner import get_learner

                get_learner().on_close(strategy)
            except Exception:
                pass
        if action in {"BUY", "SHORT", "CLOSE", "REVERSE_LONG", "REVERSE_SHORT"}:
            res = broker.place_signal(
                strategy=strategy,
                action=action,
                price=float(cmp) if cmp is not None else None,
                tag=strategy[:12],
            )
            if not res.dry_run:
                line = (
                    f"[LIVE] {strategy} {action} tx={res.transaction} "
                    f"qty={res.quantity} ok={res.ok} order={res.order_id} "
                    f"| {res.reason}"
                )
                print(line, flush=True)
                logger.info(line)
            return res
        return None

    print("=== Gold Petal strategy runner ===", flush=True)
    print(f"Symbol   : {symbol}", flush=True)
    print(f"Token    : {token}", flush=True)
    print(f"Expiry   : {contract['expiry']}", flush=True)
    print(
        f"Rollover : front={contract.get('front_month_expiry')} "
        f"next={contract.get('next_month_expiry')} "
        f"days_left={contract.get('days_to_front_expiry')} "
        f"rolled={contract.get('rolled')} "
        f"(switch {contract.get('rollover_days')}d before front expiry)",
        flush=True,
    )
    if hasattr(strategy_s13, "set_contract"):
        strategy_s13.set_contract(contract)
    if hasattr(strategy_s4, "set_contract"):
        strategy_s4.set_contract(contract)
    if hasattr(strategy_og, "set_contract"):
        strategy_og.set_contract(contract)
    for slot in amise_slots:
        if hasattr(slot, "set_contract"):
            slot.set_contract(contract)
    print(f"Interval : {interval} minutes", flush=True)
    print(
        f"Portfolio: enabled={sorted(portfolio.enabled)} "
        f"flatten_on_bad_regime={portfolio.flatten_when_blocked} "
        f"mood_gate={mood_det.last.gate_on} mood_flatten={mood_det.last.flatten_on}",
        flush=True,
    )
    try:
        seeded_mood = mood_det.seed_from_db()
        print(
            f"Mood seed n={seeded_mood.n_samples} mood={seeded_mood.mood} "
            f"regime={seeded_mood.regime} {seeded_mood.alignment} "
            f"layers={sum(1 for L in (seeded_mood.layers or []) if L.get('ready'))}/"
            f"{len(seeded_mood.layers or [])} {seeded_mood.label}",
            flush=True,
        )
    except Exception as exc:
        logger.warning("mood seed skipped: %s", exc)
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
        f"S4       : daily HH/LL swing (hold until opposite) "
        f"[{'ON' if portfolio.is_enabled(strategy_s4.name) else 'OFF'}] "
        f"{strategy_s4.status_line}",
        flush=True,
    )
    print(
        f"S5       : min-edge (delivery — not EOD flattened) "
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
        f"FLOW     : ENABLE_FLOW_BRAIN off (not a live book) "
        f"[{'ON' if portfolio.is_enabled(strategy_fb.name) else 'OFF'}] "
        f"{strategy_fb.status_line}",
        flush=True,
    )
    print(
        f"GAP      : ENABLE_OVERNIGHT_GAP close→next-open 09:05 "
        f"[{'ON' if portfolio.is_enabled(strategy_og.name) else 'OFF'}] "
        f"{strategy_og.status_line}",
        flush=True,
    )
    print(
        f"S8       : ALIGN E/H/X models (delivery — not EOD flattened) "
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
        f"S10      : legacy 30m always zigzag (MTF +₹42k) "
        f"[{'ON' if portfolio.is_enabled(strategy_s10.name) else 'OFF'}] "
        f"{strategy_s10.status_line}",
        flush=True,
    )
    print(
        f"S11      : multi-model discover "
        f"[{'ON' if portfolio.is_enabled(strategy_s11.name) else 'OFF'}] "
        f"{strategy_s11.status_line}",
        flush=True,
    )
    print(
        f"S13      : daily S16 swing (hold until opposite / flatten on rollover) "
        f"[{'ON' if portfolio.is_enabled(strategy_s13.name) else 'OFF'}] "
        f"{strategy_s13.status_line}",
        flush=True,
    )
    print(
        f"S16      : 1h HH/LL-or-wick, Live-tab Intraday (default on) — flatten at MARKET_CLOSE "
        f"[{'ON' if portfolio.is_enabled(strategy_s16.name) else 'OFF'}] "
        f"{strategy_s16.status_line}",
        flush=True,
    )
    print(
        f"S18      : ENABLE_S18 1h OHLC+vol+day pack overlay (live-eligible, you Arm) "
        f"[{'ON' if portfolio.is_enabled(strategy_s18.name) else 'OFF'}] "
        f"{strategy_s18.status_line}",
        flush=True,
    )
    print(
        f"S19      : ENABLE_S19 1h aligned body+close (live-eligible, you Arm) "
        f"[{'ON' if portfolio.is_enabled(strategy_s19.name) else 'OFF'}] "
        f"{strategy_s19.status_line}",
        flush=True,
    )
    print(
        f"S20      : ENABLE_S20 off (not a live book) "
        f"[{'ON' if portfolio.is_enabled(strategy_s20.name) else 'OFF'}] "
        f"{strategy_s20.status_line}",
        flush=True,
    )
    for slot in amise_slots:
        print(
            f"{slot.name.split('_')[0]:8} : AMISE slot genome "
            f"[{'ON' if portfolio.is_enabled(slot.name) else 'OFF'}] "
            f"{slot.status_line}",
            flush=True,
        )
    live_ok, live_why = is_live_mode_allowed()
    print(
        f"Mode     : {'NOT ARMED (DRY_RUN)' if dry_run else ('LIVE' if live_ok else f'LIVE-ARMED but blocked ({live_why})')}",
        flush=True,
    )
    print(f"Broker   : {broker.status_line}", flush=True)
    ctrl = load_state()
    print(
        f"Control  : emergency_off={ctrl.emergency_off} "
        f"trading_enabled={ctrl.trading_enabled} live_unlocked={ctrl.live_unlocked} "
        f"live_approved={ctrl.live_approved}",
        flush=True,
    )
    print(
        "Panel    : python3 control_panel.py --host 0.0.0.0 --port 8787",
        flush=True,
    )
    open_s, close_s = _market_window()
    print(f"Hours    : {open_s}-{close_s} IST, Mon-Fri only", flush=True)
    print(f"Next bar : {state['next_bar_at'].isoformat(timespec='seconds')}", flush=True)

    # --- Restart safety: restore RAM from DB or auto-CLOSE orphans ---
    strat_map = {
        strategy_s4.name: strategy_s4,
        strategy_s5.name: strategy_s5,
        strategy_s8.name: strategy_s8,
        strategy_s13.name: strategy_s13,
        strategy_s16.name: strategy_s16,
        strategy_s18.name: strategy_s18,
        strategy_s19.name: strategy_s19,
        strategy_s20.name: strategy_s20,
        strategy_og.name: strategy_og,
        **{s.name: s for s in amise_slots},
        strategy_s2.name: strategy_s2,
        strategy_s3.name: strategy_s3,
        strategy_s6.name: strategy_s6,
        strategy_fb.name: strategy_fb,
        strategy_s9.name: strategy_s9,
        strategy_s10.name: strategy_s10,
        strategy_s11.name: strategy_s11,
    }
    reconcile = startup_reconcile(strat_map)
    for msg in reconcile.get("messages") or []:
        print(f"[SAFETY] {msg}", flush=True)
        logger.info("[SAFETY] %s", msg)
    if reconcile.get("closes"):
        emit_startup_closes(reconcile["closes"], record=_record_signal, symbol=symbol)
        print(
            f"[SAFETY] wrote {len(reconcile['closes'])} startup CLOSE signal(s)",
            flush=True,
        )
    write_bot_health(
        {
            "event": "startup",
            "restart_mode": reconcile.get("mode"),
            "restored": reconcile.get("restored"),
            "orphan_closes": reconcile.get("closes"),
            "positions": {
                n: getattr(o, "position", "flat") for n, o in strat_map.items()
            },
            "runner": "run_strategy",
        }
    )

    print("Press Ctrl+C to stop", flush=True)
    print("=================================", flush=True)

    eod_closed: set[tuple[str, str]] = set()
    mood_flat_done: set[tuple[str, str]] = set()

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

        # S1 stays on 30-minute bars only. Disabled stub used to return None
        # and crash the tick handler on every 30m boundary.
        result_s1 = strategy_s1.on_bar(bar)
        if result_s1 is None:
            save_bar(
                time_label=bar.time_label,
                symbol=symbol,
                token=token,
                cmp=bar.cmp,
                bp=bar.bp,
                sp=bar.sp,
                net=float(bar.bp) - float(bar.sp),
                price_delta=None,
                net_delta=None,
            )
            state["next_bar_at"] = _next_boundary(now, interval)
            return
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
        if not _strategy_active(strategy_s1.name):
            state["next_bar_at"] = _next_boundary(now, interval)
            return
        regime = regime_det.last.regime
        action = result_s1.action
        # Emergency / capital / ENABLE still gate entries. Mood and TREND/CHOP
        # labels do not.
        if action in {"BUY", "SHORT"}:
            ok_enter, enter_why = _may_enter(strategy_s1.name, regime, side=action)
            if not ok_enter:
                line = (
                    f"[{bar.time_label}] {strategy_s1.name} SKIP {action} "
                    f"gate={enter_why} ({regime_det.last.reason})"
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
            _record_signal(
                time_label=bar.time_label,
                action=action,
                position_after=result_s1.position_after if action != "CLOSE" else "flat",
                reason=result_s1.reason,
                price_delta=result_s1.price_delta,
                net=result_s1.net,
                net_delta=result_s1.net_delta,
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
        if not _strategy_active(strategy_s2.name):
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

        if action in {"BUY", "SHORT"}:
            ok_enter, _why = _may_enter(strategy_s2.name, regime, side=action)
            if not ok_enter:
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
        _record_signal(
            time_label=now.isoformat(timespec="seconds"),
            action=action,
            position_after=result.position_after if action != "CLOSE" else "flat",
            reason=result.reason,
            price_delta=result.price_delta,
            net=result.net,
            net_delta=result.net_delta,
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
        if not _strategy_active(strategy_s3.name):
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
        allowed, _gate = _may_enter(strategy_s3.name, regime)

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

        if result.action in {"BUY", "SHORT"}:
            blocked, mood_why = mood_blocks_entry(
                mood_det.last, result.action, strategy=strategy_s3.name
            )
            if blocked:
                strategy_s3.position = pos_before
                line = (
                    f"[{now.isoformat(timespec='seconds')}] {strategy_s3.name} "
                    f"ENTRY BLOCKED ({mood_why})"
                )
                print(line, flush=True)
                logger.info(line)
                return

        action = result.action
        _record_signal(
            time_label=now.isoformat(timespec="seconds"),
            action=action,
            position_after="flat" if action == "CLOSE" else result.position_after,
            reason=result.reason,
            price_delta=result.price_delta,
            net=result.net,
            net_delta=result.net_delta,
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


    def emit_s11_if_changed(now: datetime, message: dict) -> None:
        """S11 discovered pack: multi-model ML entry templates from weekly discover."""
        if not _strategy_active(strategy_s11.name):
            return
        if not strategy_s11.enabled:
            return
        if latest["cmp"] is None:
            return
        strategy_s11.push_tick(
            message,
            received_at=now.isoformat(timespec="seconds"),
            exchange_timestamp=message.get("exchange_timestamp"),
        )
        pos_before = strategy_s11.position
        result = strategy_s11.maybe_signal(now)
        regime = regime_det.last.regime
        allowed, _gate = _may_enter(strategy_s11.name, regime)

        if not allowed:
            if result is not None and result.action in {"BUY", "SHORT"}:
                strategy_s11.position = "flat"
                result = None
            if pos_before == "flat":
                return
            from strategy import SignalResult

            strategy_s11.position = "flat"
            strategy_s11.last_signal_ts = now
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

        if result.action in {"BUY", "SHORT"}:
            blocked, mood_why = mood_blocks_entry(
                mood_det.last, result.action, strategy=strategy_s11.name
            )
            if blocked:
                strategy_s11.position = pos_before
                line = (
                    f"[{now.isoformat(timespec='seconds')}] {strategy_s11.name} "
                    f"ENTRY BLOCKED ({mood_why})"
                )
                print(line, flush=True)
                logger.info(line)
                return

        action = result.action
        _record_signal(
            time_label=now.isoformat(timespec="seconds"),
            action=action,
            position_after="flat" if action == "CLOSE" else result.position_after,
            reason=result.reason,
            price_delta=result.price_delta,
            net=result.net,
            net_delta=result.net_delta,
            strategy=strategy_s11.name,
            cmp=float(latest["cmp"]),
        )
        line = (
            f"[{now.isoformat(timespec='seconds')}] {strategy_s11.name} regime={regime} "
            f"CMP={latest['cmp']} prob_up={strategy_s11.last_prob} "
            f"=> {action} (pos={strategy_s11.position}) | {result.reason}"
        )
        print(line, flush=True)
        logger.info(line)


    def emit_daily_swing_if_changed(strategy, now: datetime, message: dict) -> None:
        """S4/S13 daily swing: last 15m of this day, FLIP, hold until opposite."""
        if not _strategy_active(strategy.name):
            return
        if latest["cmp"] is None:
            return
        if (
            regime_det.last.regime == "WIDE_SPREAD"
            and strategy.position == "flat"
        ):
            return
        prev_pos = strategy.position
        prev_entry = getattr(strategy, "entry_price", None)
        prev_date = getattr(strategy, "entry_date", None)
        result = strategy.on_tick(now, float(latest["cmp"]), message)
        skip = getattr(strategy, "last_skip", None)
        day = getattr(strategy, "_day", None)
        prev = getattr(strategy, "prev_day", None)
        if result is None and state["tick_count"] % 50 == 0:
            prev_s = (
                f"prev={prev.date} prevH={prev.high} prevL={prev.low} prevC={prev.close}"
                if prev is not None
                else "prev=none"
            )
            day_s = (
                f"dayH={day.high} dayL={day.low} dayO={day.open} dayC={day.close}"
                if day is not None
                else "day=none"
            )
            line = (
                f"[{now.isoformat(timespec='seconds')}] {strategy.name} idle "
                f"pos={strategy.position} skip={skip} {prev_s} {day_s}"
            )
            print(line, flush=True)
            logger.info(line)
        planned = wick_record_actions(prev_pos, result)
        if not planned:
            return
        enter_action = planned[-1][0]
        if enter_action in {"BUY", "SHORT"}:
            ok_enter, why = allow_new_entry(
                strategy.name,
                features=_entry_features(strategy.name, enter_action),
            )
            if not ok_enter:
                strategy.position = prev_pos
                strategy.entry_price = prev_entry
                if hasattr(strategy, "entry_date"):
                    strategy.entry_date = prev_date
                if hasattr(strategy, "release_action_lock"):
                    strategy.release_action_lock()
                if hasattr(strategy, "_save_state"):
                    strategy._save_state()
                line = (
                    f"[{now.isoformat(timespec='seconds')}] {strategy.name} "
                    f"ENTRY BLOCKED ({why}) — will retry in confirm window | "
                    f"{result.reason}"
                )
                print(line, flush=True)
                logger.info(line)
                return
        fill_px = (
            float(strategy.entry_price)
            if getattr(strategy, "entry_price", None) is not None
            else float(latest["cmp"])
        )
        for action, pos_after in planned:
            reason = result.reason
            if action == "CLOSE" and len(planned) > 1:
                reason = f"FLIP close {prev_pos} | {result.reason}"
            _record_signal(
                time_label=now.isoformat(timespec="seconds"),
                action=action,
                position_after=pos_after,
                reason=reason,
                price_delta=result.price_delta,
                net=result.net,
                net_delta=result.net_delta,
                strategy=strategy.name,
                cmp=fill_px,
            )
            line = (
                f"[{now.isoformat(timespec='seconds')}] {strategy.name} "
                f"CMP={fill_px} => {action} "
                f"(pos={pos_after}) | {reason}"
            )
            print(line, flush=True)
            logger.info(line)

    def emit_s4_if_changed(now: datetime, message: dict) -> None:
        """S4: old S13 HH/LL daily swing — last 15m, hold until opposite (not next open)."""
        emit_daily_swing_if_changed(strategy_s4, now, message)

    def emit_s13_if_changed(now: datetime, message: dict) -> None:
        """S13: daily S16 close-vs-prev — last 15m, hold until opposite (not next open)."""
        emit_daily_swing_if_changed(strategy_s13, now, message)


    def emit_s5_if_changed(now: datetime, message: dict) -> None:
        """S5: trade only when expected move covers fees / min edge points."""
        if not _strategy_active(strategy_s5.name):
            return
        if latest["cmp"] is None:
            return
        # Always feed ticks into ATR — never skip on_tick when regime blocks.
        # Previously QUIET returned early while flat, so a 200pt smooth rally
        # never updated expected-move and S5 stayed blind until too late.
        result = strategy_s5.on_tick(now, float(latest["cmp"]), message)
        if result is None or result.action not in {"BUY", "SHORT", "CLOSE"}:
            return
        if result.action in {"BUY", "SHORT"}:
            ok_enter, why = _may_enter(
                strategy_s5.name, regime_det.last.regime, side=result.action
            )
            if not ok_enter:
                strategy_s5.position = "flat"
                strategy_s5.entry_price = None
                strategy_s5.last_skip = why
                if state["tick_count"] % 50 == 0:
                    line = (
                        f"[{now.isoformat(timespec='seconds')}] {strategy_s5.name} "
                        f"ENTRY BLOCKED ({why}) exp={strategy_s5.last_expected}"
                    )
                    print(line, flush=True)
                    logger.info(line)
                return
        _record_signal(
            time_label=now.isoformat(timespec="seconds"),
            action=result.action,
            position_after=result.position_after,
            reason=result.reason,
            price_delta=result.price_delta,
            net=result.net,
            net_delta=result.net_delta,
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
        if not _strategy_active(strategy_s6.name):
            return
        if latest["cmp"] is None:
            return
        # Always update ATR/state; gate entries below (same QUIET blind-spot fix as S5).
        result = strategy_s6.on_tick(now, float(latest["cmp"]), message)
        if result is None or result.action not in {"BUY", "SHORT", "CLOSE"}:
            return
        if result.action in {"BUY", "SHORT"}:
            ok_enter, _why = _may_enter(
                strategy_s6.name, regime_det.last.regime, side=result.action
            )
            if not ok_enter:
                strategy_s6.position = "flat"
                strategy_s6.entry_price = None
                return
        _record_signal(
            time_label=now.isoformat(timespec="seconds"),
            action=result.action,
            position_after=result.position_after,
            reason=result.reason,
            price_delta=result.price_delta,
            net=result.net,
            net_delta=result.net_delta,
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
        if not _strategy_active(strategy_s8.name):
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

        # Always process ticks (bars / zigzag state); gate entries below.
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
        if result.action in {"BUY", "SHORT"}:
            ok_enter, why = _may_enter(
                strategy_s8.name, regime_det.last.regime, side=result.action
            )
            if not ok_enter:
                strategy_s8.position = "flat"
                strategy_s8.entry_price = None
                strategy_s8.last_skip = why
                if state["tick_count"] % 50 == 0:
                    line = (
                        f"[{now.isoformat(timespec='seconds')}] {strategy_s8.name} "
                        f"ENTRY BLOCKED ({why}) imb={strategy_s8.last_imb:.1f}%"
                    )
                    print(line, flush=True)
                    logger.info(line)
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
        _record_signal(
            time_label=ts,
            action=result.action,
            position_after=result.position_after,
            reason=result.reason,
            price_delta=result.price_delta,
            net=result.net,
            net_delta=result.net_delta,
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
        if not _strategy_active(strategy_s9.name):
            return
        if latest["cmp"] is None:
            return
        # Always process ticks (30m bar state); gate entries below.
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
        if result.action in {"BUY", "SHORT", "REVERSE_LONG", "REVERSE_SHORT"}:
            ok_enter, _why = _may_enter(
                strategy_s9.name, regime_det.last.regime, side=result.action
            )
            if not ok_enter:
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
        _record_signal(
            time_label=now.isoformat(timespec="seconds"),
            action=result.action,
            position_after=result.position_after,
            reason=result.reason,
            price_delta=result.price_delta,
            net=result.net,
            net_delta=result.net_delta,
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

    def emit_s10_if_changed(now: datetime, message: dict) -> None:
        """S10: legacy 30m always zigzag (MTF +₹42k paper path)."""
        if not _strategy_active(strategy_s10.name):
            return
        if latest["cmp"] is None:
            return
        # Always feed ticks for bar builder; gate entries below.
        result = strategy_s10.on_tick(now, float(latest["cmp"]), message)
        if result is None or result.action not in {"BUY", "SHORT", "CLOSE"}:
            return
        if result.action in {"BUY", "SHORT"}:
            ok_enter, _why = _may_enter(
                strategy_s10.name, regime_det.last.regime, side=result.action
            )
            if not ok_enter:
                strategy_s10.position = "flat"
                strategy_s10.entry_price = None
                return
        if (
            strategy_s10.position != "flat"
            and portfolio.should_flatten(strategy_s10.name, regime_det.last.regime)
            and result.action != "CLOSE"
        ):
            from strategy import SignalResult as _SR

            strategy_s10.position = "flat"
            strategy_s10.entry_price = None
            result = _SR(
                action="CLOSE",
                position_after="flat",
                price_delta=result.price_delta,
                net=result.net,
                net_delta=result.net_delta,
                prev_net_delta=result.prev_net_delta,
                reason=f"regime_flatten {regime_det.last.regime}: {regime_det.last.reason}",
            )
        _record_signal(
            time_label=now.isoformat(timespec="seconds"),
            action=result.action,
            position_after=result.position_after,
            reason=result.reason,
            price_delta=result.price_delta,
            net=result.net,
            net_delta=result.net_delta,
            strategy=strategy_s10.name,
            cmp=float(latest["cmp"]),
        )
        line = (
            f"[{now.isoformat(timespec='seconds')}] {strategy_s10.name} "
            f"regime={regime_det.last.regime} CMP={latest['cmp']} "
            f"bias={strategy_s10.bias} "
            f"net={strategy_s10.last_net:.0f} imb={strategy_s10.last_imb:.1f}% "
            f"=> {result.action} (pos={strategy_s10.position}) | {result.reason}"
        )
        print(line, flush=True)
        logger.info(line)

    def emit_s16_if_changed(now: datetime, message: dict) -> None:
        """S16: 1h close-vs-prev HH/LL or wick — FLIP at the finished hour close."""
        emit_hour_book(strategy_s16, now, message)

    def emit_s18_if_changed(now: datetime, message: dict) -> None:
        """S18: 1h OHLC+vol+yesterday pack — FLIP at the finished hour close. Live-eligible, you Arm."""
        emit_hour_book(strategy_s18, now, message)

    def emit_s19_if_changed(now: datetime, message: dict) -> None:
        """S19: 1h aligned body+close — FLIP at the finished hour close. Live-eligible, you Arm."""
        emit_hour_book(strategy_s19, now, message)

    def emit_s20_if_changed(now: datetime, message: dict) -> None:
        """S20: 1h fade HL — buy bounced low / short rejected high. Paper only."""
        emit_hour_book(strategy_s20, now, message)

    def emit_overnight_gap_if_changed(now: datetime, message: dict) -> None:
        """OVERNIGHT_GAP: close→next-open. Mood/regime exempt. Not S4 swing."""
        if not _strategy_active(strategy_og.name):
            return
        if latest["cmp"] is None:
            return
        prev_pos = strategy_og.position
        prev_entry = getattr(strategy_og, "entry_price", None)
        prev_date = getattr(strategy_og, "entry_date", None)
        result = strategy_og.on_tick(now, float(latest["cmp"]), message)
        skip = getattr(strategy_og, "last_skip", None)
        if result is None and state["tick_count"] % 50 == 0:
            line = (
                f"[{now.isoformat(timespec='seconds')}] {strategy_og.name} idle "
                f"pos={strategy_og.position} skip={skip}"
            )
            print(line, flush=True)
            logger.info(line)
        planned = wick_record_actions(prev_pos, result)
        if not planned:
            return
        enter_action = planned[-1][0]
        if enter_action in {"BUY", "SHORT"}:
            ok_enter, why = allow_new_entry(
                strategy_og.name,
                features=_entry_features(strategy_og.name, enter_action),
            )
            if not ok_enter:
                strategy_og.position = prev_pos
                strategy_og.entry_price = prev_entry
                if hasattr(strategy_og, "entry_date"):
                    strategy_og.entry_date = prev_date
                if hasattr(strategy_og, "release_action_lock"):
                    strategy_og.release_action_lock()
                if hasattr(strategy_og, "_save_state"):
                    strategy_og._save_state()
                line = (
                    f"[{now.isoformat(timespec='seconds')}] {strategy_og.name} "
                    f"ENTRY BLOCKED ({why}) — will retry in close window | "
                    f"{result.reason}"
                )
                print(line, flush=True)
                logger.info(line)
                return
        fill_px = (
            float(strategy_og.entry_price)
            if getattr(strategy_og, "entry_price", None) is not None
            else float(latest["cmp"])
        )
        for action, pos_after in planned:
            _record_signal(
                time_label=now.isoformat(timespec="seconds"),
                action=action,
                position_after=pos_after,
                reason=result.reason,
                price_delta=result.price_delta,
                net=result.net,
                net_delta=result.net_delta,
                strategy=strategy_og.name,
                cmp=fill_px,
            )
            line = (
                f"[{now.isoformat(timespec='seconds')}] {strategy_og.name} "
                f"CMP={fill_px} => {action} "
                f"(pos={pos_after}) | {result.reason}"
            )
            print(line, flush=True)
            logger.info(line)

    def emit_flow_brain_if_changed(now: datetime, message: dict) -> None:
        """FLOW_BRAIN: tick LTP+TBQ+TSQ pressure. Always feed ticks. Paper only. Not S7_HOURLY."""
        if not _strategy_active(strategy_fb.name):
            return
        if latest["cmp"] is None:
            return
        prev = strategy_fb.position
        prev_entry = getattr(strategy_fb, "entry_price", None)
        result = strategy_fb.on_tick(now, float(latest["cmp"]), message)
        planned = wick_record_actions(prev, result)
        if not planned:
            return
        enter_action = planned[-1][0]
        if enter_action in {"BUY", "SHORT"}:
            ok_enter, why = _may_enter(
                strategy_fb.name, regime_det.last.regime, side=enter_action
            )
            if not ok_enter:
                strategy_fb.position = prev
                strategy_fb.entry_price = prev_entry
                line = (
                    f"[{now.isoformat(timespec='seconds')}] {strategy_fb.name} "
                    f"ENTRY BLOCKED ({why}) | {result.reason}"
                )
                print(line, flush=True)
                logger.info(line)
                return
        fill_px = float(latest["cmp"])
        for action, pos_after in planned:
            reason = result.reason
            if action == "CLOSE" and len(planned) > 1:
                reason = f"FLIP close {prev} | {result.reason}"
            _record_signal(
                time_label=now.isoformat(timespec="seconds"),
                action=action,
                position_after=pos_after,
                reason=reason,
                price_delta=result.price_delta,
                net=result.net,
                net_delta=result.net_delta,
                strategy=strategy_fb.name,
                cmp=fill_px,
            )
        line = (
            f"[{now.isoformat(timespec='seconds')}] {strategy_fb.name} "
            f"regime={regime_det.last.regime} CMP={fill_px} "
            f"=> {enter_action} (pos={strategy_fb.position}) | {result.reason}"
        )
        print(line, flush=True)
        logger.info(line)


    def book_health() -> dict[str, dict[str, Any]]:
        """Per-book pos + why-idle so the desk can answer "why is S16 flat?"."""
        out: dict[str, dict[str, Any]] = {}
        for name, obj in strat_map.items():
            row: dict[str, Any] = {
                "position": str(getattr(obj, "position", "flat") or "flat"),
                "enabled": bool(portfolio.is_enabled(name)),
                "skip": str(getattr(obj, "last_skip", "") or ""),
            }
            bar = getattr(obj, "bar_debug", "")
            if bar:
                row["bar"] = str(bar)
            tf = getattr(getattr(obj, "cfg", None), "bar_minutes", None)
            if tf:
                row["bar_minutes"] = int(tf)
                row["next_decision_ist"] = _next_boundary(
                    datetime.now(IST), int(tf)
                ).isoformat(timespec="seconds")
            out[name] = row
        return out

    flatten_lock = threading.Lock()

    def emit_desk_flatten(now: datetime) -> None:
        """Operator Exit: flatten RAM + square leftover Angel even in Paper."""
        from desk_flatten import apply_pending_flattens
        from live_orders import square_fill_leftover

        with flatten_lock:
            cmp = latest.get("cmp")
            if cmp is None:
                try:
                    from storage import latest_ltp as _latest_ltp

                    cmp = _latest_ltp()
                except Exception:
                    cmp = None
            leftover_broker_box: dict[str, Any] = {"b": None}

            try:
                from live_orders import reconcile_fill_leftovers_with_angel
                from desk_data import invalidate_live_pnl_cache

                cleared = reconcile_fill_leftovers_with_angel(
                    getattr(session, "api", None),
                    symbol=str(contract.get("symbol") or ""),
                    token=str(contract.get("token") or ""),
                )
                try:
                    invalidate_live_pnl_cache()
                except Exception:
                    pass
                if cleared:
                    line = (
                        f"[{now.isoformat(timespec='seconds')}] [EXIT] "
                        f"Angel already flat — cleared leftover {', '.join(cleared)}"
                    )
                    print(line, flush=True)
                    logger.info(line)
            except Exception:
                pass

            def _square_leftover(name: str, leftover: dict) -> Any:
                b = leftover_broker_box["b"]
                if b is None:
                    b = broker
                    if not hasattr(b, "place_leftover_square"):
                        b = broker_from_session(session, contract, force_live=True)
                    leftover_broker_box["b"] = b
                return square_fill_leftover(name, leftover=leftover, broker=b)

            rows = apply_pending_flattens(
                strat_map,
                _record_signal,
                now=now,
                cmp=cmp,
                square_leftover=_square_leftover,
            )
        if not rows:
            return
        for row in rows:
            name = str(row.get("strategy") or "")
            res = row.get("result") or {}
            ts = now.isoformat(timespec="seconds")
            if res.get("leftover_squared"):
                broker_res = res.get("broker") or {}
                line = (
                    f"[{ts}] [EXIT] leftover SQUARE {name} "
                    f"tx={broker_res.get('transaction')} qty={res.get('lots')} "
                    f"ok={broker_res.get('ok')} order={broker_res.get('order_id')}"
                )
            elif res.get("already_flat"):
                line = f"[{ts}] [EXIT] {name} already flat"
            elif row.get("status") == "done":
                line = (
                    f"[{ts}] [EXIT] CLOSE {name} was_{res.get('was_side')} "
                    f"queued={row.get('id')}"
                )
            else:
                line = f"[{ts}] [EXIT] {name} failed {row.get('error')}"
            print(line, flush=True)
            logger.info(line)
        write_bot_health(
            {
                "event": "desk_exit",
                "flattened": [
                    {
                        "strategy": r.get("strategy"),
                        "status": r.get("status"),
                        "was_side": (r.get("result") or {}).get("was_side"),
                    }
                    for r in rows
                ],
                "positions": {
                    n: getattr(o, "position", "flat") for n, o in strat_map.items()
                },
                "runner": "run_strategy",
            }
        )

    def emit_hour_book(strategy, now: datetime, message: dict) -> None:
        """1h FLIP books. Mood/regime do not skip a finished-hour close."""
        if not _strategy_active(strategy.name):
            return
        if latest["cmp"] is None:
            return
        prev = strategy.position
        prev_entry = getattr(strategy, "entry_price", None)
        prev_date = getattr(strategy, "entry_date", None)
        result = strategy.on_tick(now, float(latest["cmp"]), message)
        skip = getattr(strategy, "last_skip", None)
        if result is None and state["tick_count"] % 50 == 0:
            line = (
                f"[{now.isoformat(timespec='seconds')}] {strategy.name} idle "
                f"pos={strategy.position} skip={skip} {getattr(strategy, 'bar_debug', '')}"
            )
            print(line, flush=True)
            logger.info(line)
        planned = wick_record_actions(prev, result)
        if not planned:
            return
        enter_action = planned[-1][0]
        if enter_action in {"BUY", "SHORT"}:
            ok_enter, why = _may_enter(
                strategy.name, regime_det.last.regime, side=enter_action
            )
            if not ok_enter:
                strategy.position = prev
                strategy.entry_price = prev_entry
                if hasattr(strategy, "entry_date"):
                    strategy.entry_date = prev_date
                if hasattr(strategy, "release_decision_lock"):
                    strategy.release_decision_lock()
                line = (
                    f"[{now.isoformat(timespec='seconds')}] {strategy.name} "
                    f"ENTRY BLOCKED ({why}) — next 1h close | "
                    f"{result.reason}"
                )
                print(line, flush=True)
                logger.info(line)
                return
        if (
            strategy.position != "flat"
            and portfolio.should_flatten(strategy.name, regime_det.last.regime)
            and enter_action != "CLOSE"
        ):
            from strategy import SignalResult as _SR

            strategy.position = "flat"
            strategy.entry_price = None
            result = _SR(
                action="CLOSE",
                position_after="flat",
                price_delta=result.price_delta,
                net=result.net,
                net_delta=result.net_delta,
                prev_net_delta=result.prev_net_delta,
                reason=f"regime_flatten {regime_det.last.regime}: {result.reason}",
            )
            planned = [("CLOSE", "flat")]
        fill_px = (
            float(strategy.entry_price)
            if strategy.entry_price is not None
            else float(latest["cmp"])
        )
        for action, pos_after in planned:
            reason = result.reason
            if action == "CLOSE" and len(planned) > 1:
                reason = f"FLIP close {prev} | {result.reason}"
            _record_signal(
                time_label=now.isoformat(timespec="seconds"),
                action=action,
                position_after=pos_after,
                reason=reason,
                price_delta=result.price_delta,
                net=result.net,
                net_delta=result.net_delta,
                strategy=strategy.name,
                cmp=fill_px,
            )
            line = (
                f"[{now.isoformat(timespec='seconds')}] {strategy.name} "
                f"regime={regime_det.last.regime} CMP={fill_px} "
                f"=> {action} (pos={pos_after}) | {reason}"
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
            # Skip night/weekend noise entirely. Still honour desk Exit.
            if not is_market_open(now):
                emit_desk_flatten(now)
                if now >= state["next_bar_at"]:
                    state["next_bar_at"] = _next_boundary(now, interval)
                return

            feed_watch["t"] = time.monotonic()
            feed_watch["got"] = True
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
                bp_m = float(latest["bp"] or 0.0) if latest.get("bp") is not None else 0.0
                sp_m = float(latest["sp"] or 0.0) if latest.get("sp") is not None else 0.0
                regime_det.update(
                    float(latest["cmp"]),
                    _spread_bps(message, float(latest["cmp"])),
                )
                mood_det.update(float(latest["cmp"]), bp_m, sp_m)

            state["tick_count"] += 1
            tick_count = state["tick_count"]
            if tick_count == 1 or tick_count % 200 == 0:
                try:
                    from trade_learner import get_learner

                    get_learner().maybe_refit()
                except Exception:
                    pass
            if tick_count == 1 or tick_count % 200 == 0:
                try:
                    mood_det.refresh_layers()
                except Exception:
                    pass
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
                    f"mood={mood_det.last.mood} "
                    f"align={mood_det.last.alignment} "
                    f"regime={mood_det.last.regime} "
                    f"next_bar={state['next_bar_at'].strftime('%H:%M:%S')} "
                    f"s2={strategy_s2.position} s3={strategy_s3.position} "
                    f"s4={strategy_s4.position} "
                    f"s5={strategy_s5.position}/{strategy_s5.last_skip or '-'} "
                    f"s6={strategy_s6.position} fb={strategy_fb.position} "
                    f"s8={strategy_s8.position}/{strategy_s8.bias}/{strategy_s8.last_skip or '-'} "
                    f"s9={strategy_s9.position}/{strategy_s9.last_label} "
                    f"s10={strategy_s10.position}/{strategy_s10.bias} "
                    f"s11={strategy_s11.position} "
                    f"s13={strategy_s13.position} "
                    f"s16={strategy_s16.position} "
                    f"s18={strategy_s18.position} "
                    f"s19={strategy_s19.position} "
                    f"s20={strategy_s20.position} "
                    f"gap={strategy_og.position}{s3_extra}"
                )
                print(line, flush=True)
                logger.info(line)
                write_bot_health(
                    {
                        "event": "heartbeat",
                        "ticks": tick_count,
                        "ltp": latest.get("cmp"),
                        "regime": rs.regime,
                        "positions": {
                            "S4": strategy_s4.position,
                            "S5": strategy_s5.position,
                            "FLOW": strategy_fb.position,
                            "S8": strategy_s8.position,
                            "S13": strategy_s13.position,
                            "S16": strategy_s16.position,
                            "S18": strategy_s18.position,
                            "S19": strategy_s19.position,
                            "S20": strategy_s20.position,
                            "GAP": strategy_og.position,
                        },
                        "skips": {
                            "S5": strategy_s5.last_skip,
                            "S8": strategy_s8.last_skip,
                        },
                        "books": book_health(),
                        "runner": "run_strategy",
                    }
                )

            # S2: 1-min sum of buy1-5 vs sell1-5
            emit_s2_if_changed(now, message)
            # S3: ML model BUY/SHORT/CLOSE
            emit_s3_if_changed(now, message)
            # S11: multi-model discovered pack
            emit_s11_if_changed(now, message)
            # S4: daily HH/LL swing (last 15m, hold until opposite)
            emit_s4_if_changed(now, message)
            # S5: min-edge (fee-aware)
            emit_s5_if_changed(now, message)
            # S6: min 30 points expected move
            emit_s6_if_changed(now, message)
            # FLOW_BRAIN: tick LTP+TBQ+TSQ pressure (paper only, ENABLE default false)
            emit_flow_brain_if_changed(now, message)
            # S8: NET zigzag (unchanged)
            emit_s8_if_changed(now, message)
            # S9: 27-state bar machine
            emit_s9_if_changed(now, message)
            # S10: legacy 30m always zigzag (+₹42k MTF paper path)
            emit_s10_if_changed(now, message)
            # S13: daily S16 close-vs-prev (last 15m of the signal day)
            # Once a day, re-read the rollover rule so S13 flattens the front
            # month before the feed switches to next month.
            day_key = now.astimezone(IST).strftime("%Y-%m-%d")
            if state.get("roll_day") != day_key:
                state["roll_day"] = day_key
                try:
                    fresh = find_goldpetal_futures()
                except Exception as exc:
                    fresh = None
                    print(f"Rollover check skip: {type(exc).__name__}: {exc}", flush=True)
                if fresh is not None:
                    if hasattr(strategy_s13, "set_contract"):
                        strategy_s13.set_contract(fresh)
                    if hasattr(strategy_s4, "set_contract"):
                        strategy_s4.set_contract(fresh)
                    if hasattr(strategy_og, "set_contract"):
                        strategy_og.set_contract(fresh)
                    for slot in amise_slots:
                        if hasattr(slot, "set_contract"):
                            slot.set_contract(fresh)
                    if str(fresh.get("token")) != str(token):
                        state["roll_reconnect"] = True
                        print(
                            f"ROLLOVER: {symbol} → {fresh.get('symbol')} "
                            f"(front {fresh.get('front_month_expiry')} "
                            f"days_left={fresh.get('days_to_front_expiry')}). "
                            "Flatten S13, then reconnect the next-month feed.",
                            flush=True,
                        )
            emit_s13_if_changed(now, message)
            # S16: 1h close-vs-prev HH/LL or wick, FLIP at bar close; flatten at EOD
            emit_s16_if_changed(now, message)
            # S18: 1h OHLC+vol+yesterday pack overlay, FLIP at bar close; you Arm live
            emit_s18_if_changed(now, message)
            # S19: 1h aligned body+close, FLIP at bar close; live-eligible, you Arm
            emit_s19_if_changed(now, message)
            # S20: 1h fade HL, FLIP at bar close; paper only
            emit_s20_if_changed(now, message)
            # OVERNIGHT_GAP: today's tape → next open. Mood-exempt. You Arm live.
            emit_overnight_gap_if_changed(now, message)
            for slot in amise_slots:
                emit_hour_book(slot, now, message)

            # Desk Exit button: flatten that book only (after this tick's entries)
            emit_desk_flatten(now)

            # EOD flatten: Live-tab Intraday ticks (S16 default). Delivery books hold.
            day_key = now.astimezone(IST).strftime("%Y-%m-%d")
            if in_eod_flatten_window(now, market_close=close_s):
                opens = intraday_open_for_flatten(strat_map)
                flattened_now: list[dict] = []
                for row in opens:
                    name = row["strategy"]
                    key = (day_key, name)
                    if key in eod_closed:
                        continue
                    obj = strat_map.get(name)
                    if obj is None:
                        continue
                    side = row["side"]
                    obj.position = "flat"
                    if hasattr(obj, "entry_price"):
                        obj.entry_price = None
                    if hasattr(obj, "entry_date"):
                        obj.entry_date = None
                    ts = now.isoformat(timespec="seconds")
                    _record_signal(
                        time_label=ts,
                        action="CLOSE",
                        position_after="flat",
                        reason=(
                            f"EOD flatten Intraday was_{side} "
                            f"(last {os.getenv('EOD_FLATTEN_MINUTES', '5')}m "
                            f"before {close_s})"
                        ),
                        price_delta=None,
                        net=0.0,
                        net_delta=None,
                        strategy=name,
                        cmp=latest.get("cmp"),
                    )
                    eod_closed.add(key)
                    flattened_now.append(row)
                    line = f"[{ts}] [SAFETY] EOD CLOSE {name} was_{side}"
                    print(line, flush=True)
                    logger.info(line)
                if flattened_now:
                    write_bot_health(
                        {
                            "event": "eod_flatten",
                            "flattened": flattened_now,
                            "positions": {
                                n: getattr(o, "position", "flat")
                                for n, o in strat_map.items()
                            },
                            "runner": "run_strategy",
                        }
                    )

            # Optional mood flatten (MOOD_GATE + MOOD_FLATTEN). Never dumps
            # S13/S4/overnight or S16's 1h formula hold.
            if mood_det.last.gate_on and mood_det.last.flatten_on:
                day_key = now.astimezone(IST).strftime("%Y-%m-%d")
                for name, obj in strat_map.items():
                    if name in MOOD_EXEMPT_BOOKS or name in FORMULA_GATE_BOOKS:
                        continue
                    pos = str(getattr(obj, "position", "flat") or "flat")
                    want, why = mood_wants_flatten(
                        mood_det.last, pos, strategy=name
                    )
                    key = (day_key, name)
                    if not want or key in mood_flat_done:
                        continue
                    obj.position = "flat"
                    if hasattr(obj, "entry_price"):
                        obj.entry_price = None
                    ts = now.isoformat(timespec="seconds")
                    _record_signal(
                        time_label=ts,
                        action="CLOSE",
                        position_after="flat",
                        reason=f"mood_flatten {why}",
                        price_delta=None,
                        net=0.0,
                        net_delta=None,
                        strategy=name,
                        cmp=latest.get("cmp"),
                    )
                    mood_flat_done.add(key)
                    line = f"[{ts}] [MOOD] CLOSE {name} was_{pos} {why}"
                    print(line, flush=True)
                    logger.info(line)

            if state.pop("roll_reconnect", False):
                try:
                    sws.close()
                except Exception as exc:
                    print(f"Rollover reconnect close failed: {exc}", flush=True)

            # S1: 30-min bars
            if now >= state["next_bar_at"]:
                evaluate_bar(now)
        except Exception:
            logger.exception("Error while handling tick")

    def on_open(_wsapp):
        feed_watch["t"] = time.monotonic()
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

    def _tick_watchdog() -> None:
        while (
            not feed_watch["stop"]
            and not closed["done"]
            and not stop_flag.get("stop")
        ):
            time.sleep(5)
            try:
                emit_desk_flatten(datetime.now(IST))
            except Exception as exc:
                logger.warning("desk Exit poll failed: %s", exc)
            idle = time.monotonic() - float(feed_watch["t"])
            if not tick_feed_stale(
                idle_sec=idle,
                market_open=is_market_open(),
                got_tick=bool(feed_watch["got"]),
                watchdog_sec=TICK_WATCHDOG_SEC,
                first_tick_grace_sec=TICK_FIRST_GRACE_SEC,
            ):
                continue
            msg = (
                f"TICK WATCHDOG: no tick for {idle:.0f}s during session — reconnect"
            )
            print(msg, flush=True)
            logger.warning(msg)
            write_bot_health(
                {
                    "event": "tick_watchdog",
                    "idle_sec": round(idle),
                    "runner": "run_strategy",
                }
            )
            try:
                sws.close()
            except Exception as exc:
                print(f"TICK WATCHDOG close failed: {exc}", flush=True)
            print("TICK WATCHDOG: exiting so supervise starts a new socket", flush=True)
            os._exit(1)

    watch_th = threading.Thread(target=_tick_watchdog, name="tick-watchdog", daemon=True)
    watch_th.start()
    try:
        sws.connect()
    finally:
        feed_watch["stop"] = True
        logger.info("connect() returned/finished. closed=%s", closed["done"])
        print(f"connect() finished. closed={closed['done']}", flush=True)


def _optional_book(name: str, import_path: str, attr: str):
    """Load a paper book; missing files must not kill the Gold Petal tape."""
    try:
        import importlib

        mod = importlib.import_module(import_path)
        return getattr(mod, attr)
    except Exception as exc:
        def _missing(*_a, **_k):
            from strategy_disabled import DisabledStrategy

            stub = DisabledStrategy(name)
            stub._load_error = f"{type(exc).__name__}: {exc}"
            print(f"{name}: skip ({stub._load_error})", flush=True)
            return stub

        return _missing


def _load_amise_slots(portfolio):
    try:
        from strategy_amise import load_amise_slot_books

        return load_amise_slot_books(portfolio)
    except Exception as exc:
        print(f"AMISE: skip ({type(exc).__name__}: {exc})", flush=True)
        return []


def main() -> None:
    stop_flag = {"stop": False}
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
    portfolio = portfolio_from_env()
    from strategy_disabled import DisabledStrategy

    def _load(name: str, factory):
        if not portfolio.is_enabled(name):
            return DisabledStrategy(name)
        try:
            return factory()
        except Exception as exc:
            print(f"{name}: skip ({type(exc).__name__}: {exc})", flush=True)
            return DisabledStrategy(name)

    strategy_s1 = _load("S1_NETDELTA", PressureStrategy)
    strategy_s2 = _load("S2_BALANCE", balance_from_env)
    strategy_s3 = _load("S3_ML", ml_strategy_from_env)
    strategy_s4 = _load("S4_OVERNIGHT", s4_swing_from_env)
    strategy_s5 = _load("S5_MINEDGE", minedge_from_env)
    strategy_s6 = _load("S6_MIN30", min30_from_env)
    strategy_fb = _load(
        "FLOW_BRAIN",
        _optional_book("FLOW_BRAIN", "strategy_flow_brain", "flow_brain_from_env"),
    )
    strategy_s8 = _load("S8_NET_ZIGZAG", net_zigzag_from_env)
    zigzag_rec = recorder_from_env()
    if portfolio.is_enabled("S8_NET_ZIGZAG") and hasattr(strategy_s8, "cfg"):
        zigzag_rec.record_params(strategy_s8.cfg)
    strategy_s9 = _load("S9_STATE30", state_s9_from_env)
    s9_journal = s9_journal_from_env()
    strategy_s10 = _load("S10_LEGACY30", s10_legacy30_from_env)
    def _s11_factory():
        from strategy_discovered import discovered_from_env

        return discovered_from_env()

    strategy_s11 = _load("S11_DISCOVERED", _s11_factory)
    strategy_s13 = _load("S13_HHHL_DAY", hhhl_day_from_env)
    strategy_s16 = _load("S16_HHHL_WICK_1H", s16_from_env)
    strategy_s18 = _load("S18_OHLC_VOL_HTF", s18_from_env)
    strategy_s19 = _load(
        "S19_BODY_CLOSE_1H",
        _optional_book("S19_BODY_CLOSE_1H", "strategy_s19", "s19_from_env"),
    )
    strategy_s20 = _load(
        "S20_FADE_HL",
        _optional_book("S20_FADE_HL", "strategy_s20", "s20_from_env"),
    )
    strategy_og = _load(
        "OVERNIGHT_GAP",
        _optional_book("OVERNIGHT_GAP", "strategy_overnight_gap", "overnight_gap_from_env"),
    )
    amise_slots = _load_amise_slots(portfolio)
    regime_det = RegimeDetector(window=60)
    mood_det = MoodDetector(window=80)
    init_db()
    if portfolio.is_enabled("S1_NETDELTA"):
        _seed_strategy_from_db(strategy_s1)
    print(f"S3_ML: {strategy_s3.status_line}", flush=True)
    print(f"S4_OVERNIGHT: {strategy_s4.status_line}", flush=True)
    print(f"S5_MINEDGE: {strategy_s5.status_line}", flush=True)
    print(f"S6_MIN30: {strategy_s6.status_line}", flush=True)
    print(f"FLOW_BRAIN: {strategy_fb.status_line}", flush=True)
    print(f"S8_NET_ZIGZAG: {strategy_s8.status_line}", flush=True)
    print(f"S8 retune recorder: {zigzag_rec.db_path} enabled={zigzag_rec.enabled}", flush=True)
    print(f"S9_STATE30: {strategy_s9.status_line}", flush=True)
    print(f"S9 journal: {s9_journal.db_path} enabled={s9_journal.enabled}", flush=True)
    print(f"S10_LEGACY30: {strategy_s10.status_line}", flush=True)
    print(f"S11_DISCOVERED: {strategy_s11.status_line}", flush=True)
    print(f"S13_HHHL_DAY: {strategy_s13.status_line}", flush=True)
    print(f"S16_HHHL_WICK_1H: {strategy_s16.status_line}", flush=True)
    print(f"S18_OHLC_VOL_HTF: {strategy_s18.status_line}", flush=True)
    print(f"S19_BODY_CLOSE_1H: {strategy_s19.status_line}", flush=True)
    print(f"S20_FADE_HL: {strategy_s20.status_line}", flush=True)
    print(f"OVERNIGHT_GAP: {strategy_og.status_line}", flush=True)
    for slot in amise_slots:
        print(f"{slot.name}: {slot.status_line}", flush=True)
    print(
        f"Portfolio enabled={sorted(portfolio.enabled)} "
        f"(live desk S5/S8/S13/S16/S18/S19/overnight gap — S4 off, S11 off, S20/FLOW off, "
        f"S18/S19 and OVERNIGHT_GAP live-eligible (you Arm), AMISE off. No paper fills.)",
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
                strategy_fb,
                strategy_s8,
                zigzag_rec,
                strategy_s9,
                s9_journal,
                strategy_s10,
                strategy_s11,
                strategy_s13,
                strategy_s16,
                strategy_s18,
                strategy_s19,
                strategy_s20,
                strategy_og,
                portfolio,
                regime_det,
                mood_det,
                amise_slots,
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
            f"fb={strategy_fb.position} "
            f"s8={strategy_s8.position}/{strategy_s8.bias} "
            f"s9={strategy_s9.position}/{strategy_s9.last_label} "
            f"s10={strategy_s10.position}/{strategy_s10.bias} "
            f"s11={strategy_s11.position} "
            f"s13={strategy_s13.position} "
            f"s16={strategy_s16.position} "
            f"s18={strategy_s18.position} "
            f"s19={strategy_s19.position} "
            f"s20={strategy_s20.position} "
            f"gap={strategy_og.position} "
            f"amise={','.join(s.name.split('_')[0] + '=' + str(s.position) for s in amise_slots)} "
            f"regime={regime_det.last.regime})...",
            flush=True,
        )
        logger.info("Reconnecting in %ss", RECONNECT_DELAY_SEC)
        time.sleep(RECONNECT_DELAY_SEC)


if __name__ == "__main__":
    main()
