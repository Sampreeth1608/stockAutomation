"""Angel One live order bridge for Gold Petal (MCX).

Safety stack (all required unless noted):
  1. DRY_RUN=false
  2. control panel live_unlocked=true
  3. trading_enabled and not emergency_off
  4. strategy listed in live_approved (8787 Live money checkboxes)
  5. quantity = strategy capital.max_lots capped by LIVE_MAX_LOTS

Paper remains the default. This module places MARKET DAY CARRYFORWARD
orders on MCX when gates pass.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

from control_state import CONTROL_DIR, ensure_control_dir, is_live_mode_allowed, load_state

IST = ZoneInfo("Asia/Kolkata")
ORDERS_PATH = CONTROL_DIR / "live_orders.jsonl"
_lock = threading.Lock()

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


def live_lots() -> int:
    lots = max(1, _env_int("LIVE_LOTS", 1))
    cap = max(1, _env_int("LIVE_MAX_LOTS", 1))
    return min(lots, cap)


def live_lots_for(strategy: str) -> int:
    """Per-strategy live size from capital.max_lots, hard-capped by LIVE_MAX_LOTS.

    Desk Live Deploy sets max_lots per strategy. LIVE_MAX_LOTS in .env is the
    operator hard ceiling (raise it only when you accept larger real size).
    """
    cap = max(1, _env_int("LIVE_MAX_LOTS", 1))
    default = live_lots()
    try:
        from capital import load_capital

        sb = load_capital().strategies.get(strategy)
        if sb is not None and sb.enabled and int(sb.max_lots) > 0:
            return min(cap, max(1, int(sb.max_lots)))
    except Exception:
        pass
    return min(default, cap)


def strategy_may_trade_live(strategy: str) -> tuple[bool, str]:
    ok, reason = is_live_mode_allowed()
    if not ok:
        return False, reason
    require = _env_bool("LIVE_REQUIRE_APPROVAL", True)
    if not require:
        return True, "ok_no_approval_required"
    st = load_state()
    if strategy not in st.live_approved:
        return False, "strategy_not_live_approved"
    return True, "ok"


def _append_order_log(row: dict[str, Any]) -> None:
    ensure_control_dir(ORDERS_PATH)
    row = dict(row)
    row["ts_ist"] = datetime.now(IST).isoformat(timespec="seconds")
    with _lock:
        with ORDERS_PATH.open("a", encoding="utf-8") as fh:
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
        may, why = strategy_may_trade_live(strategy)
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
        _append_order_log({**res.to_dict(), "params": params})
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
        res = OrderResult(
            ok=True,
            dry_run=True,
            skipped=True,
            reason="dry_run_or_paper",
            strategy=strategy,
            symbol=self.symbol,
            token=self.token,
        )
        self.last_result = res
        self.skip_count += 1
        return res


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
    """
    dry = os.getenv("DRY_RUN", "true").strip().lower() in {"1", "true", "yes", "y"}
    if force_live is False or dry:
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
