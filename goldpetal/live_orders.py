"""Angel One live order bridge for Gold Petal (MCX).

Safety stack (all required unless noted):
  1. DRY_RUN=false
  2. control panel live_unlocked=true
  3. trading_enabled and not emergency_off
  4. strategy listed in live_approved (8787 Live money checkboxes)
  5. quantity = strategy capital.max_lots capped by LIVE_MAX_LOTS

Exception: desk Exit on a fill-log leftover squares that book's leftover
lots even in Paper (DRY_RUN / live locked). That is CLOSE only — it does
not Arm live and does not open a new book. Emergency off still blocks.
If Angel is already flat (you squared in the app), Exit records the fill
log closed and does not send a new BUY/SELL.

Paper remains the default. This module places MARKET DAY CARRYFORWARD
orders on MCX when gates pass.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

from control_state import CONTROL_DIR, ensure_control_dir, is_live_mode_allowed, load_state

IST = ZoneInfo("Asia/Kolkata")
ORDERS_PATH = CONTROL_DIR / "live_orders.jsonl"
ANGEL_NET_PATH = CONTROL_DIR / "angel_net.json"
_lock = threading.Lock()
_angel_fetch_at = 0.0
HARD_LIVE_MAX_LOTS = 1000

Action = Literal["BUY", "SHORT", "CLOSE", "REVERSE_LONG", "REVERSE_SHORT"]


@dataclass
class OrderResult:
    ok: bool
    dry_run: bool
    skipped: bool
    reason: str
    order_id: str | None = None
    transaction: str | None = None  # BUY / SELL
    quantity: int = 0
    strategy: str = ""
    symbol: str = ""
    token: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "true" if default else "false").strip().lower()
    return raw in {"1", "true", "yes", "y"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)).strip())
    except ValueError:
        return default


def _live_max_cap() -> int:
    return max(1, min(HARD_LIVE_MAX_LOTS, _env_int("LIVE_MAX_LOTS", 1)))


def live_lots() -> int:
    lots = max(1, min(HARD_LIVE_MAX_LOTS, _env_int("LIVE_LOTS", 1)))
    return min(lots, _live_max_cap())


def live_lots_for(strategy: str) -> int:
    """Per-strategy Angel size. Lots tick = live_lots; ₹ tick = floor(budget / LTP).

    Hard-capped by LIVE_MAX_LOTS then 1000. Paper 100 lots is never Angel size.
    """
    cap = _live_max_cap()
    default = live_lots()
    try:
        from capital import live_qty_for

        return live_qty_for(strategy, cap=cap, default=default)
    except Exception:
        return min(default, cap)


FILL_MATCH_SEC = 180.0
FILL_LOOKBACK_SEC = 4 * 3600.0
FILL_EXIT_SLACK_SEC = 120.0


def _qty_int(raw: Any) -> int:
    try:
        n = int(float(raw))
    except (TypeError, ValueError):
        return 0
    return max(0, min(HARD_LIVE_MAX_LOTS, n))


def _when_ist(raw: str) -> datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    return dt.astimezone(IST)


def order_was_placed(row: dict[str, Any]) -> bool:
    if not isinstance(row, dict):
        return False
    if row.get("skipped") or row.get("dry_run"):
        return False
    if row.get("placed") is True:
        return True
    if row.get("ok") and (row.get("order_id") or str(row.get("reason") or "") == "placed"):
        return True
    return False


def iter_placed_orders(*, path: Path | None = None) -> list[dict[str, Any]]:
    """Angel fills from live_orders.jsonl. Quantity is the lots that went out, not today's arm."""
    dest = path or ORDERS_PATH
    if not dest.is_file():
        return []
    try:
        lines = dest.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not order_was_placed(row):
            continue
        qty = _qty_int(row.get("quantity"))
        if qty <= 0:
            continue
        name = str(row.get("strategy") or "").strip()
        if not name:
            continue
        tx = str(row.get("transaction") or row.get("side") or "").strip().upper()
        if tx in {"SHORT", "SELL"}:
            tx = "SELL"
        elif tx == "BUY":
            tx = "BUY"
        else:
            continue
        out.append(
            {
                "strategy": name,
                "transaction": tx,
                "quantity": qty,
                "ts": _when_ist(str(row.get("ts_ist") or row.get("ts") or "")),
            }
        )
    return out


def _qty_near(
    rows: list[tuple[datetime, int]],
    anchor: datetime | None,
    *,
    window_sec: float,
) -> int | None:
    if not rows:
        return None
    if anchor is None:
        return rows[-1][1]
    window = float(window_sec)
    in_win = [
        (ts, qty)
        for ts, qty in rows
        if abs((ts - anchor).total_seconds()) <= window
    ]
    pool = in_win or rows
    _ts, qty = min(pool, key=lambda item: abs((item[0] - anchor).total_seconds()))
    return qty


def lots_on_fill(
    strategy: str,
    entry_ts: str,
    side: str,
    *,
    exit_ts: str = "",
    orders: list[dict[str, Any]] | None = None,
    path: Path | None = None,
    window_sec: float = FILL_MATCH_SEC,
    lookback_sec: float = FILL_LOOKBACK_SEC,
) -> int | None:
    """Lots on the Angel entry/close for this round-trip — not today's Live Lots arm.

    None if that fill is not in the log. When both entry and close fills exist,
    the smaller qty wins so a later 25-lot arm cannot rewrite a 3-lot close.
    """
    name = str(strategy or "").strip()
    want = "BUY" if str(side or "").upper() in {"BUY", "LONG"} else "SELL"
    exit_want = "SELL" if want == "BUY" else "BUY"
    pool = orders if orders is not None else iter_placed_orders(path=path)
    entry = _when_ist(entry_ts)
    leave = _when_ist(exit_ts) if exit_ts else None
    lookback = timedelta(seconds=float(lookback_sec))
    slack = timedelta(seconds=FILL_EXIT_SLACK_SEC)
    lo = (entry - lookback) if entry is not None else None
    hi = (leave + slack) if leave is not None else (
        (entry + lookback) if entry is not None else None
    )

    def _rows(tx: str, *, lo_ts: datetime | None, hi_ts: datetime | None) -> list[tuple[datetime, int]]:
        out: list[tuple[datetime, int]] = []
        untimed: list[int] = []
        for row in pool:
            if str(row.get("strategy") or "") != name:
                continue
            if str(row.get("transaction") or "") != tx:
                continue
            qty = int(row.get("quantity") or 0)
            if qty <= 0:
                continue
            ts = row.get("ts")
            if not isinstance(ts, datetime):
                untimed.append(qty)
                continue
            if lo_ts is not None and ts < lo_ts:
                continue
            if hi_ts is not None and ts > hi_ts:
                continue
            out.append((ts, qty))
        if out:
            return out
        if untimed and len(set(untimed)) == 1:
            stamp = entry or leave or datetime.now(IST)
            return [(stamp, untimed[0])]
        return []

    entry_qty = _qty_near(
        _rows(want, lo_ts=lo, hi_ts=hi),
        entry,
        window_sec=window_sec,
    )
    exit_qty: int | None = None
    if leave is not None or str(exit_ts or "").strip():
        exit_lo = entry if entry is not None else lo
        exit_qty = _qty_near(
            _rows(exit_want, lo_ts=exit_lo, hi_ts=hi),
            leave or entry,
            window_sec=max(float(window_sec), FILL_EXIT_SLACK_SEC),
        )
    if entry_qty and exit_qty:
        return min(entry_qty, exit_qty)
    return entry_qty or exit_qty


def net_open_from_fills(*, path: Path | None = None) -> dict[str, dict[str, Any]]:
    """Per-strategy Angel leftover from placed fills. BUY +qty, SELL -qty.

    Signal tape can look FLAT (paper CLOSE, window cut) while Angel still
    holds the contract. Live tab uses this net so leftover lots show OPEN.
    """
    nets: dict[str, int] = {}
    last_tx: dict[str, str] = {}
    for row in iter_placed_orders(path=path):
        name = str(row.get("strategy") or "").strip()
        qty = int(row.get("quantity") or 0)
        tx = str(row.get("transaction") or "")
        if not name or qty <= 0 or tx not in {"BUY", "SELL"}:
            continue
        signed = qty if tx == "BUY" else -qty
        nets[name] = int(nets.get(name) or 0) + signed
        last_tx[name] = tx
    out: dict[str, dict[str, Any]] = {}
    for name, net in nets.items():
        if net == 0:
            continue
        side = "BUY" if net > 0 else "SHORT"
        out[name] = {
            "strategy": name,
            "side": side,
            "status": "OPEN",
            "lots": abs(int(net)),
            "entry_price": "",
            "exit_price": "",
            "pnl_after_charges": "",
            "entry_reason": "angel fill leftover",
            "exit_reason": "",
            "source": "angel_fill",
            "last_tx": last_tx.get(name) or "",
        }
    return out


def _angel_row_net(row: dict[str, Any]) -> int:
    if not isinstance(row, dict):
        return 0
    for key in ("netqty", "netQty", "net_qty", "cfnetqty"):
        raw = row.get(key)
        if raw in (None, ""):
            continue
        try:
            return int(float(raw))
        except (TypeError, ValueError):
            continue
    buy = 0
    sell = 0
    try:
        buy = int(float(row.get("buyqty") or row.get("buyQty") or 0))
    except (TypeError, ValueError):
        buy = 0
    try:
        sell = int(float(row.get("sellqty") or row.get("sellQty") or 0))
    except (TypeError, ValueError):
        sell = 0
    return buy - sell


def _angel_row_symbol(row: dict[str, Any]) -> str:
    return str(
        row.get("tradingsymbol")
        or row.get("tradingSymbol")
        or row.get("symbol")
        or ""
    ).strip().upper()


def _angel_row_token(row: dict[str, Any]) -> str:
    return str(
        row.get("symboltoken") or row.get("symbolToken") or row.get("token") or ""
    ).strip()


def _pick_angel_rows(
    rows: list[Any], *, symbol: str, token: str
) -> list[dict[str, Any]]:
    want_sym = str(symbol or "").strip().upper()
    want_tok = str(token or "").strip()
    dicts = [r for r in rows if isinstance(r, dict)]
    exact: list[dict[str, Any]] = []
    for row in dicts:
        tok = _angel_row_token(row)
        ts = _angel_row_symbol(row)
        if want_tok and tok and tok == want_tok:
            exact.append(row)
            continue
        if want_sym and ts and ts == want_sym:
            exact.append(row)
    if exact:
        return exact
    fuzzy = [
        r
        for r in dicts
        if "GOLDPETAL" in _angel_row_symbol(r) and "GOLDPETAL" in want_sym
    ]
    if len(fuzzy) == 1:
        return fuzzy
    return []


def angel_net_lots_from_book(
    payload: Any, *, symbol: str, token: str
) -> tuple[str, int | None]:
    """Parse Angel positionBook JSON. ok + signed lots, or error/missing."""
    if not isinstance(payload, dict):
        return "error", None
    if payload.get("status") is False:
        return "error", None
    data = payload.get("data")
    if data in (None, "", [], {}):
        return "ok", 0
    if isinstance(data, str):
        text = data.strip().lower()
        if not text or "no data" in text or text in {"null", "none"}:
            return "ok", 0
        return "error", None
    if isinstance(data, dict):
        rows: list[Any] = [data]
    elif isinstance(data, list):
        rows = list(data)
    else:
        return "error", None
    matched = _pick_angel_rows(rows, symbol=symbol, token=token)
    return "ok", sum(_angel_row_net(r) for r in matched)


def fetch_angel_net_lots(
    api: Any, *, symbol: str, token: str
) -> tuple[str, int | None]:
    """Read Gold Petal net lots from Angel. missing = no position API on this stub."""
    if api is None:
        return "missing", None
    fn = getattr(api, "positionBook", None)
    if not callable(fn):
        fn = getattr(api, "getPosition", None)
    if not callable(fn):
        return "missing", None
    try:
        raw = fn()
    except Exception:
        return "error", None
    return angel_net_lots_from_book(raw, symbol=symbol, token=token)


def load_angel_net_cache(*, path: Path | None = None) -> dict[str, Any]:
    dest = path or ANGEL_NET_PATH
    if not dest.is_file():
        return {}
    try:
        row = json.loads(dest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return row if isinstance(row, dict) else {}


def save_angel_net_cache(
    net: int,
    *,
    symbol: str,
    token: str,
    path: Path | None = None,
) -> None:
    dest = path or ANGEL_NET_PATH
    ensure_control_dir(dest)
    payload = {
        "ok": True,
        "net": int(net),
        "symbol": symbol,
        "token": str(token),
        "at_ist": datetime.now(IST).isoformat(timespec="seconds"),
        "at_unix": time.time(),
    }
    dest.write_text(json.dumps(payload) + "\n", encoding="utf-8")


def angel_net_cache_is_flat(*, path: Path | None = None, max_age_sec: float = 90.0) -> bool:
    row = load_angel_net_cache(path=path)
    if not row.get("ok"):
        return False
    try:
        age = time.time() - float(row.get("at_unix") or 0)
    except (TypeError, ValueError):
        return False
    if age < 0 or age > float(max_age_sec):
        return False
    try:
        return int(row.get("net") or 0) == 0
    except (TypeError, ValueError):
        return False


def leftover_close_tx(leftover: dict[str, Any]) -> tuple[str, int]:
    """Opposite BUY/SELL and lots needed to flatten a fill-log leftover."""
    try:
        lots = abs(int(leftover.get("lots") or 0))
    except (TypeError, ValueError):
        lots = 0
    lots = max(0, min(HARD_LIVE_MAX_LOTS, lots))
    side = str(leftover.get("side") or "").upper()
    if side in {"BUY", "LONG"}:
        return "SELL", lots
    if side in {"SHORT", "SELL"}:
        return "BUY", lots
    return "", lots


def record_leftover_already_flat(
    strategy: str,
    leftover: dict[str, Any],
    *,
    path: Path | None = None,
) -> OrderResult:
    """Zero the fill-log leftover. Angel is already flat — no new order."""
    name = str(strategy or leftover.get("strategy") or "").strip()
    tx, lots = leftover_close_tx(leftover)
    res = OrderResult(
        ok=True,
        dry_run=False,
        skipped=False,
        reason="angel_already_flat",
        order_id="angel-already-flat",
        transaction=tx or None,
        quantity=lots,
        strategy=name,
    )
    logged = {
        **res.to_dict(),
        "placed": True,
        "leftover_square": True,
        "reconciled": True,
    }
    _append_order_log(logged, path=path)
    return res


def reconcile_fill_leftovers_with_angel(
    api: Any,
    *,
    symbol: str,
    token: str,
    path: Path | None = None,
    min_interval_sec: float = 15.0,
) -> list[str]:
    """If Angel Gold Petal is flat, close ghost leftover rows. No new order."""
    global _angel_fetch_at
    leftover = net_open_from_fills(path=path)
    if not leftover:
        return []
    now = time.time()
    if now - float(_angel_fetch_at or 0) < float(min_interval_sec):
        return []
    _angel_fetch_at = now
    kind, net = fetch_angel_net_lots(api, symbol=symbol, token=token)
    if kind != "ok" or net is None:
        return []
    save_angel_net_cache(int(net), symbol=symbol, token=token)
    if int(net) != 0:
        return []
    cleared: list[str] = []
    for name, row in leftover.items():
        record_leftover_already_flat(name, row, path=path)
        cleared.append(name)
    return cleared


def lots_on_fill_for_trade(
    info: dict[str, Any],
    *,
    orders: list[dict[str, Any]] | None = None,
    path: Path | None = None,
) -> float | None:
    got = lots_on_fill(
        str(info.get("strategy") or ""),
        str(info.get("entry_ts") or ""),
        str(info.get("side") or ""),
        exit_ts=str(info.get("exit_ts") or ""),
        orders=orders,
        path=path,
    )
    return float(got) if got else None


def mirror_positions_from_signals(
    rows: list[Any], *, live_only: bool = False
) -> dict[str, str]:
    """Newest row per strategy → long/short/flat. Paper tape is not an Angel position."""
    seeded: dict[str, str] = {}
    for row in rows:
        try:
            raw = row["dry_run"] if not isinstance(row, dict) else row.get("dry_run")
            dry = int(raw or 0)
        except (TypeError, ValueError, KeyError):
            dry = 0
        if live_only and dry != 0:
            continue
        try:
            name = str(
                (row["strategy"] if not isinstance(row, dict) else row.get("strategy")) or ""
            )
            pos = str(
                (
                    row["position_after"]
                    if not isinstance(row, dict)
                    else row.get("position_after")
                )
                or ""
            ).lower()
        except (TypeError, KeyError):
            continue
        if name and name not in seeded and pos in {"long", "short", "flat"}:
            seeded[name] = pos
    return seeded


def strategy_may_trade_live(strategy: str, *, action: str = "") -> tuple[bool, str]:
    ok, reason = is_live_mode_allowed()
    if not ok:
        return False, reason
    from live_readiness import NEVER_LIVE_BOOKS, book_may_go_live

    if str(strategy) == "YOU_MANUAL":
        return True, "ok_you_tab"
    if str(strategy) in NEVER_LIVE_BOOKS:
        return False, "not_live_eligible"
    require = _env_bool("LIVE_REQUIRE_APPROVAL", True)
    st = load_state()
    if require and strategy not in st.live_approved:
        return False, "strategy_not_live_approved"
    act = str(action or "").upper()
    if act == "CLOSE":
        return True, "ok_close"
    if not book_may_go_live(strategy):
        return False, "not_live_eligible"
    return True, "ok"


def _null_skip_reason() -> tuple[str, bool]:
    """When live is armed, paper-broker skips must show on the Live tab.

    NullBroker is chosen at bot start if DRY_RUN was true. Arm live writes
    DRY_RUN=false, but RAM still uses NullBroker until RESTART. Paper tape
    still records; Angel does not. Log that so Angel orders is not empty.
    Pure paper (not armed) stays silent.
    """
    try:
        st = load_state()
    except Exception:
        return "dry_run_or_paper", False
    if not st.live_unlocked:
        return "dry_run_or_paper", False
    env_dry = True
    try:
        from analytics.env_bridge import read_env

        raw = read_env().get("DRY_RUN")
        if raw is None:
            raw = os.getenv("DRY_RUN", "true")
        env_dry = str(raw).strip().lower() in {"1", "true", "yes", "y"}
    except Exception:
        env_dry = os.getenv("DRY_RUN", "true").strip().lower() in {
            "1",
            "true",
            "yes",
            "y",
        }
    if not env_dry:
        return "bot_still_paper_restart_required", True
    return "dry_run_or_paper", True


def _append_order_log(row: dict[str, Any], *, path: Path | None = None) -> None:
    dest = path or ORDERS_PATH
    ensure_control_dir(dest)
    row = dict(row)
    row.setdefault("ts_ist", datetime.now(IST).isoformat(timespec="seconds"))
    with _lock:
        with dest.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str) + "\n")


class LiveBroker:
    """Maps strategy signals to Angel placeOrder calls."""

    def __init__(
        self,
        api: Any,
        *,
        symbol: str,
        token: str,
        exchange: str = "MCX",
        lotsize: int = 1,
        producttype: str | None = None,
    ) -> None:
        self.api = api
        self.symbol = symbol
        self.token = str(token)
        self.exchange = exchange
        self.lotsize = max(1, int(lotsize or 1))
        self.producttype = (
            producttype
            or os.getenv("LIVE_PRODUCTTYPE", "CARRYFORWARD").strip()
            or "CARRYFORWARD"
        )
        self.ordertype = os.getenv("LIVE_ORDERTYPE", "MARKET").strip() or "MARKET"
        self.variety = os.getenv("LIVE_VARIETY", "NORMAL").strip() or "NORMAL"
        # Local mirror of what we believe we hold per strategy (flat/long/short).
        self.positions: dict[str, str] = {}
        self.last_result: OrderResult | None = None
        self.placed_count = 0
        self.skip_count = 0

    @property
    def status_line(self) -> str:
        ok, reason = is_live_mode_allowed()
        return (
            f"live_gates={'OK' if ok else reason} lots={live_lots()} "
            f"product={self.producttype} placed={self.placed_count} skipped={self.skip_count}"
        )

    def _quantity(self, strategy: str = "") -> int:
        # Angel MCX quantity is usually number of lots for these contracts.
        if strategy:
            return live_lots_for(strategy)
        return live_lots()

    def seed_positions(self, positions: dict[str, str]) -> None:
        """Mirror last known strategy positions (e.g. from signals DB) after restart."""
        for name, pos in positions.items():
            p = str(pos or "").lower()
            if p in {"long", "short", "flat"}:
                self.positions[str(name)] = p

    def _tx_for_action(
        self, strategy: str, action: str
    ) -> tuple[str | None, str, int]:
        """Return (BUY/SELL or None, new_position, lot_multiplier).

        Reverse from the opposite side uses 2× lots (close + open opposite)
        in one MARKET order — standard MCX futures square-off+reverse.
        """
        pos = self.positions.get(strategy, "flat")
        act = action.upper()
        if act == "BUY":
            if pos == "long":
                return None, "long", 1
            if pos == "short":
                return "BUY", "long", 2
            return "BUY", "long", 1
        if act == "SHORT":
            if pos == "short":
                return None, "short", 1
            if pos == "long":
                return "SELL", "short", 2
            return "SELL", "short", 1
        if act == "CLOSE":
            if pos == "long":
                return "SELL", "flat", 1
            if pos == "short":
                return "BUY", "flat", 1
            return None, "flat", 1
        if act == "REVERSE_LONG":
            if pos == "long":
                return None, "long", 1
            if pos == "short":
                return "BUY", "long", 2
            return "BUY", "long", 1
        if act == "REVERSE_SHORT":
            if pos == "short":
                return None, "short", 1
            if pos == "long":
                return "SELL", "short", 2
            return "SELL", "short", 1
        return None, pos, 1

    def place_signal(
        self,
        *,
        strategy: str,
        action: str,
        price: float | None = None,
        tag: str = "",
    ) -> OrderResult:
        may, why = strategy_may_trade_live(strategy, action=action)
        if not may:
            self.skip_count += 1
            res = OrderResult(
                ok=False,
                dry_run=True,
                skipped=True,
                reason=why,
                strategy=strategy,
                symbol=self.symbol,
                token=self.token,
            )
            self.last_result = res
            _append_order_log(res.to_dict())
            return res

        tx, new_pos, mult = self._tx_for_action(strategy, action)
        if tx is None:
            self.skip_count += 1
            res = OrderResult(
                ok=False,
                dry_run=False,
                skipped=True,
                reason=f"no_tx_for_action={action} pos={self.positions.get(strategy)}",
                strategy=strategy,
                symbol=self.symbol,
                token=self.token,
            )
            self.last_result = res
            _append_order_log(res.to_dict())
            return res

        qty = self._quantity(strategy) * max(1, int(mult))
        return self._submit(
            strategy=strategy,
            tx=tx,
            qty=qty,
            new_pos=new_pos,
            tag=tag,
            price=price,
        )

    def place_leftover_square(
        self,
        *,
        strategy: str,
        leftover: dict[str, Any],
        tag: str = "leftoverExit",
    ) -> OrderResult:
        """CLOSE leftover Angel lots. Paper/live-lock does not block this."""
        try:
            st = load_state()
        except Exception:
            st = None
        if st is not None and bool(getattr(st, "emergency_off", False)):
            self.skip_count += 1
            res = OrderResult(
                ok=False,
                dry_run=False,
                skipped=True,
                reason="emergency_off",
                strategy=strategy,
                symbol=self.symbol,
                token=self.token,
            )
            self.last_result = res
            _append_order_log({**res.to_dict(), "leftover_square": True})
            return res
        try:
            lots = abs(int(leftover.get("lots") or 0))
        except (TypeError, ValueError):
            lots = 0
        lots = max(0, min(HARD_LIVE_MAX_LOTS, lots))
        side = str(leftover.get("side") or "").upper()
        if side in {"BUY", "LONG"}:
            tx = "SELL"
        elif side in {"SHORT", "SELL"}:
            tx = "BUY"
        else:
            self.skip_count += 1
            res = OrderResult(
                ok=False,
                dry_run=False,
                skipped=True,
                reason=f"bad_leftover_side={side}",
                strategy=strategy,
                symbol=self.symbol,
                token=self.token,
            )
            self.last_result = res
            _append_order_log({**res.to_dict(), "leftover_square": True})
            return res
        if lots <= 0:
            self.skip_count += 1
            res = OrderResult(
                ok=False,
                dry_run=False,
                skipped=True,
                reason="bad_leftover_lots",
                strategy=strategy,
                symbol=self.symbol,
                token=self.token,
            )
            self.last_result = res
            _append_order_log({**res.to_dict(), "leftover_square": True})
            return res
        kind, net = fetch_angel_net_lots(
            self.api, symbol=self.symbol, token=self.token
        )
        if kind == "ok" and net is not None:
            save_angel_net_cache(int(net), symbol=self.symbol, token=self.token)
            if int(net) == 0:
                res = record_leftover_already_flat(strategy, leftover)
                self.positions[strategy] = "flat"
                self.last_result = res
                return res
            same_side = (tx == "SELL" and int(net) > 0) or (
                tx == "BUY" and int(net) < 0
            )
            if not same_side:
                self.skip_count += 1
                res = OrderResult(
                    ok=False,
                    dry_run=False,
                    skipped=True,
                    reason="angel_side_mismatch",
                    strategy=strategy,
                    symbol=self.symbol,
                    token=self.token,
                    transaction=tx,
                    quantity=lots,
                )
                self.last_result = res
                _append_order_log({**res.to_dict(), "leftover_square": True})
                return res
            lots = min(lots, abs(int(net)))
        elif kind == "error":
            self.skip_count += 1
            res = OrderResult(
                ok=False,
                dry_run=False,
                skipped=True,
                reason="angel_position_unknown",
                strategy=strategy,
                symbol=self.symbol,
                token=self.token,
            )
            self.last_result = res
            _append_order_log({**res.to_dict(), "leftover_square": True})
            return res
        return self._submit(
            strategy=strategy,
            tx=tx,
            qty=lots,
            new_pos="flat",
            tag=tag,
            extra_log={"leftover_square": True},
        )

    def _submit(
        self,
        *,
        strategy: str,
        tx: str,
        qty: int,
        new_pos: str,
        tag: str = "",
        price: float | None = None,
        extra_log: dict[str, Any] | None = None,
    ) -> OrderResult:
        params = {
            "variety": self.variety,
            "tradingsymbol": self.symbol,
            "symboltoken": self.token,
            "transactiontype": tx,
            "exchange": self.exchange,
            "ordertype": self.ordertype,
            "producttype": self.producttype,
            "duration": "DAY",
            "price": "0" if self.ordertype == "MARKET" else str(price or 0),
            "squareoff": "0",
            "stoploss": "0",
            "quantity": str(qty),
            "ordertag": (tag or strategy)[:19],
        }

        try:
            if hasattr(self.api, "placeOrderFullResponse"):
                raw = self.api.placeOrderFullResponse(params)
                order_id = None
                if isinstance(raw, dict):
                    data = raw.get("data") or {}
                    order_id = data.get("orderid") or data.get("orderId")
                    ok = bool(raw.get("status")) and bool(order_id)
                    reason = "placed" if ok else f"angel_reject:{raw}"
                else:
                    ok = False
                    reason = f"bad_response:{raw}"
                    raw = {"raw": raw}
            else:
                order_id = self.api.placeOrder(params)
                ok = bool(order_id)
                reason = "placed" if ok else "placeOrder_returned_None"
                raw = {"orderid": order_id}

            if ok:
                self.positions[strategy] = new_pos
                self.placed_count += 1
            else:
                self.skip_count += 1

            res = OrderResult(
                ok=ok,
                dry_run=False,
                skipped=not ok,
                reason=reason,
                order_id=str(order_id) if order_id else None,
                transaction=tx,
                quantity=qty,
                strategy=strategy,
                symbol=self.symbol,
                token=self.token,
                raw=raw if isinstance(raw, dict) else {"raw": raw},
            )
        except Exception as exc:
            self.skip_count += 1
            res = OrderResult(
                ok=False,
                dry_run=False,
                skipped=True,
                reason=f"exception:{exc}",
                transaction=tx,
                quantity=qty,
                strategy=strategy,
                symbol=self.symbol,
                token=self.token,
            )

        self.last_result = res
        logged = {**res.to_dict(), "params": params}
        if extra_log:
            logged.update(extra_log)
        _append_order_log(logged)
        return res


class NullBroker:
    """Paper stub used when live is locked / DRY_RUN."""

    def __init__(self, symbol: str = "", token: str = "") -> None:
        self.symbol = symbol
        self.token = token
        self.positions: dict[str, str] = {}
        self.last_result: OrderResult | None = None
        self.placed_count = 0
        self.skip_count = 0

    def seed_positions(self, positions: dict[str, str]) -> None:
        for name, pos in positions.items():
            p = str(pos or "").lower()
            if p in {"long", "short", "flat"}:
                self.positions[str(name)] = p

    @property
    def status_line(self) -> str:
        return "PAPER/null broker (no live orders)"

    def place_signal(self, *, strategy: str, action: str, price: float | None = None, tag: str = "") -> OrderResult:
        reason, log_it = _null_skip_reason()
        res = OrderResult(
            ok=True,
            dry_run=True,
            skipped=True,
            reason=reason,
            strategy=strategy,
            symbol=self.symbol,
            token=self.token,
            transaction=str(action or "").upper() or None,
        )
        self.last_result = res
        self.skip_count += 1
        if log_it:
            _append_order_log(res.to_dict())
        return res


def square_fill_leftover(
    strategy: str,
    *,
    leftover: dict[str, Any] | None = None,
    broker: Any = None,
    path: Path | None = None,
) -> OrderResult:
    """Square one book's Angel fill leftover. Does not Arm live."""
    name = str(strategy or "").strip()
    row = leftover
    if row is None:
        row = net_open_from_fills(path=path).get(name)
    if not row:
        return OrderResult(
            ok=True,
            dry_run=True,
            skipped=True,
            reason="no_fill_leftover",
            strategy=name,
        )
    fn = getattr(broker, "place_leftover_square", None)
    if not callable(fn):
        return OrderResult(
            ok=False,
            dry_run=False,
            skipped=True,
            reason="no_live_broker_for_leftover",
            strategy=name,
        )
    return fn(strategy=name, leftover=row)


def broker_from_session(
    session: Any,
    contract: dict[str, Any],
    *,
    force_live: bool | None = None,
) -> LiveBroker | NullBroker:
    """Build live broker if gates allow bootstrap; else NullBroker.

    Actual per-order gating still happens inside LiveBroker.place_signal.
    We construct LiveBroker whenever DRY_RUN=false so unlock can take
    effect without restart; place_signal re-checks live_unlocked each time.
    force_live=True builds LiveBroker even in Paper so leftover Exit can square.
    """
    dry = os.getenv("DRY_RUN", "true").strip().lower() in {"1", "true", "yes", "y"}
    if force_live is False or (force_live is not True and dry):
        return NullBroker(symbol=str(contract.get("symbol") or ""), token=str(contract.get("token") or ""))
    lot = contract.get("lotsize") or 1
    try:
        lot_i = int(float(lot))
    except (TypeError, ValueError):
        lot_i = 1
    return LiveBroker(
        session.api,
        symbol=str(contract["symbol"]),
        token=str(contract["token"]),
        exchange=str(contract.get("exchange") or "MCX"),
        lotsize=lot_i,
    )


def recent_orders(limit: int = 30) -> list[dict[str, Any]]:
    if not ORDERS_PATH.exists():
        return []
    lines = ORDERS_PATH.read_text(encoding="utf-8").splitlines()
    out: list[dict[str, Any]] = []
    for line in lines[-limit:]:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return list(reversed(out))
