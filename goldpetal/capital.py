"""Capital management: per-strategy budgets, lot caps, daily loss kill-switch.

Used by the control panel and the strategy runner before opening new trades.
"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from control_state import CONTROL_DIR, ensure_control_dir, paper_strategy_names

IST = ZoneInfo("Asia/Kolkata")
CAPITAL_PATH = CONTROL_DIR / "capital.json"
_lock = threading.Lock()

SIZE_LOTS = "lots"
SIZE_CAPITAL = "capital"
# Desk live qty box is 1–1000. Paper max_lots of 100 must not become Angel size
# (legacy live qty used max_lots 1–10 only).
LIVE_LOTS_UI_CAP = 10

# Default Gold Petal paper sizing — 1g lot, quote ₹/1g → 1 pt ≈ ₹1.
DEFAULT_STRATEGIES = (
    "S1_NETDELTA",
    "S2_BALANCE",
    "S3_ML",
    "S4_OVERNIGHT",
    "S5_MINEDGE",
    "S6_MIN30",
    "S8_NET_ZIGZAG",
    "S9_STATE30",
    "S10_LEGACY30",
    "S13_HHHL_DAY",
    "S16_HHHL_WICK_1H",
    "S18_OHLC_VOL_HTF",
    "S19_BODY_CLOSE_1H",
    "S20_FADE_HL",
    "OVERNIGHT_GAP",
    "S21_AMISE",
    "S22_AMISE",
    "S23_AMISE",
    "S24_AMISE",
)

_HUNDRED_LOT = set(paper_strategy_names())


@dataclass
class StrategyBudget:
    strategy: str
    budget_inr: float = 50_000.0
    max_lots: int = 10
    max_open_trades: int = 1
    enabled: bool = True
    # Live tab: "" = not sized for Angel. "lots" or "capital" is how this book goes live.
    live_size_mode: str = ""
    live_lots: int = 0
    # ₹ the operator types on Live. Not the paper even-split of Wallet.
    live_budget_inr: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CapitalPlan:
    total_capital_inr: float = 500_000.0
    cash_reserve_pct: float = 20.0
    daily_loss_limit_inr: float = 10_000.0
    max_lots_total: int = 50
    # Default live size when a book has no per-book tick. Lots = contracts.
    live_size_mode: str = SIZE_LOTS
    strategies: dict[str, StrategyBudget] = field(default_factory=dict)
    # Rolling day book (IST date string → after-tax PnL).
    day_pnl_inr: dict[str, float] = field(default_factory=dict)
    # Open lots currently attributed per strategy (operator / runner updates).
    open_lots: dict[str, int] = field(default_factory=dict)
    updated_at_ist: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_capital_inr": self.total_capital_inr,
            "cash_reserve_pct": self.cash_reserve_pct,
            "daily_loss_limit_inr": self.daily_loss_limit_inr,
            "max_lots_total": self.max_lots_total,
            "live_size_mode": normalize_size_mode(self.live_size_mode) or SIZE_LOTS,
            "strategies": {k: v.to_dict() for k, v in self.strategies.items()},
            "day_pnl_inr": self.day_pnl_inr,
            "open_lots": self.open_lots,
            "updated_at_ist": self.updated_at_ist,
        }


def _now_iso() -> str:
    return datetime.now(IST).isoformat(timespec="seconds")


def _today() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d")


def normalize_size_mode(raw: Any) -> str:
    """lots | capital | '' (not a live size pick)."""
    v = str(raw or "").strip().lower().replace("₹", "").replace("rs", "")
    v = v.strip()
    if v in {"capital", "rupees", "inr", "budget", "money"}:
        return SIZE_CAPITAL
    if v in {"lots", "lot", "qty", "quantity", "contracts"}:
        return SIZE_LOTS
    return ""


def _latest_ltp_safe() -> float | None:
    try:
        from storage import latest_ltp

        val = latest_ltp()
        if val is None:
            return None
        px = float(val)
        return px if px > 0 else None
    except Exception:
        return None


def live_budget_amount(sb: StrategyBudget | None) -> float:
    """₹ used for Angel size. 0 means the operator has not typed live capital yet."""
    if sb is None:
        return 0.0
    try:
        n = float(sb.live_budget_inr or 0)
    except (TypeError, ValueError):
        n = 0.0
    return n if n > 0 else 0.0


def lots_from_budget(budget_inr: float, ltp: float | None, cap: int) -> int:
    """Gold Petal 1g: 1 lot notional ≈ LTP ₹. Cap is LIVE_MAX_LOTS (hard max 1000)."""
    ceiling = max(1, int(cap))
    try:
        budget = float(budget_inr or 0)
    except (TypeError, ValueError):
        budget = 0.0
    if budget <= 0:
        return 1
    if ltp is None or float(ltp) <= 0:
        return 1
    n = int(budget // float(ltp))
    if n < 1:
        return 1
    return min(ceiling, n)


def live_qty_for(
    strategy: str,
    *,
    cap: int,
    default: int = 1,
    ltp: float | None = None,
    plan: CapitalPlan | None = None,
    path: Path | None = None,
) -> int:
    """Angel lots for one book: Lots tick = live_lots; ₹ tick = floor(budget / LTP)."""
    ceiling = max(1, int(cap))
    fallback = max(1, min(ceiling, int(default or 1)))
    try:
        plan = plan if plan is not None else load_capital(path)
    except Exception:
        return fallback
    sb = plan.strategies.get(strategy)
    if sb is None or not sb.enabled:
        return fallback
    book_mode = normalize_size_mode(sb.live_size_mode)
    plan_mode = normalize_size_mode(plan.live_size_mode) or SIZE_LOTS
    mode = book_mode or plan_mode
    if mode == SIZE_CAPITAL:
        px = ltp if ltp is not None else _latest_ltp_safe()
        return lots_from_budget(live_budget_amount(sb), px, ceiling)
    live = int(sb.live_lots or 0)
    if live > 0:
        return max(1, min(ceiling, live))
    paperish = int(sb.max_lots or 0)
    if 1 <= paperish <= LIVE_LOTS_UI_CAP:
        return max(1, min(ceiling, paperish))
    return fallback


def default_plan() -> CapitalPlan:
    # Split remaining risk capital evenly across known strategies.
    total = 500_000.0
    reserve = total * 0.20
    deployable = total - reserve
    each = round(deployable / len(DEFAULT_STRATEGIES), 2)
    strats = {}
    for name in DEFAULT_STRATEGIES:
        # Slim paper books (S5/S8/S11/S13/S16/S18/S19) sized at PAPER_LOTS (100).
        lots = 100 if name in _HUNDRED_LOT else 10
        strats[name] = StrategyBudget(
            strategy=name, budget_inr=each, max_lots=lots
        )
    return CapitalPlan(
        total_capital_inr=total,
        cash_reserve_pct=20.0,
        daily_loss_limit_inr=10_000.0,
        max_lots_total=1000,
        strategies=strats,
        updated_at_ist=_now_iso(),
    )


def load_capital(path: Path | None = None) -> CapitalPlan:
    path = path if path is not None else CAPITAL_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        plan = default_plan()
        save_capital(plan, path=path)
        return plan
    with _lock:
        raw = json.loads(path.read_text(encoding="utf-8"))
    strategies: dict[str, StrategyBudget] = {}
    for name, row in (raw.get("strategies") or {}).items():
        strategies[name] = StrategyBudget(
            strategy=name,
            budget_inr=float(row.get("budget_inr", 0)),
            max_lots=int(row.get("max_lots", 1)),
            max_open_trades=int(row.get("max_open_trades", 1)),
            enabled=bool(row.get("enabled", True)),
            live_size_mode=normalize_size_mode(row.get("live_size_mode")),
            live_lots=int(row.get("live_lots", 0) or 0),
            live_budget_inr=float(row.get("live_budget_inr", 0) or 0),
        )
    # Ensure defaults exist for any new strategy names.
    from charges import paper_lots

    paper_n = int(paper_lots())
    hundred = set(paper_strategy_names())
    for name in list(DEFAULT_STRATEGIES) + list(hundred):
        if name not in strategies:
            lots = paper_n if name in hundred else 10
            strategies[name] = StrategyBudget(strategy=name, max_lots=lots)
    need_total = paper_n * max(len(hundred), 1)
    return CapitalPlan(
        total_capital_inr=float(raw.get("total_capital_inr", 500_000)),
        cash_reserve_pct=float(raw.get("cash_reserve_pct", 20)),
        daily_loss_limit_inr=float(raw.get("daily_loss_limit_inr", 10_000)),
        max_lots_total=max(need_total, int(raw.get("max_lots_total", need_total))),
        live_size_mode=normalize_size_mode(raw.get("live_size_mode")) or SIZE_LOTS,
        strategies=strategies,
        day_pnl_inr={str(k): float(v) for k, v in (raw.get("day_pnl_inr") or {}).items()},
        open_lots={str(k): int(v) for k, v in (raw.get("open_lots") or {}).items()},
        updated_at_ist=str(raw.get("updated_at_ist") or ""),
    )


def save_capital(plan: CapitalPlan, path: Path | None = None) -> CapitalPlan:
    path = path if path is not None else CAPITAL_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    plan.updated_at_ist = _now_iso()
    with _lock:
        path.write_text(json.dumps(plan.to_dict(), indent=2), encoding="utf-8")
    return plan


def deployable_capital(plan: CapitalPlan | None = None) -> float:
    plan = plan or load_capital()
    return plan.total_capital_inr * (1.0 - plan.cash_reserve_pct / 100.0)


def today_pnl(plan: CapitalPlan | None = None) -> float:
    plan = plan or load_capital()
    return float(plan.day_pnl_inr.get(_today(), 0.0))


def record_realized_pnl(amount_inr: float, path: Path | None = None) -> CapitalPlan:
    """Add a closed-trade after-tax PnL to today's book."""
    plan = load_capital(path)
    day = _today()
    plan.day_pnl_inr[day] = float(plan.day_pnl_inr.get(day, 0.0)) + float(amount_inr)
    return save_capital(plan, path=path)


def set_open_lots(strategy: str, lots: int, path: Path | None = None) -> CapitalPlan:
    plan = load_capital(path)
    plan.open_lots[strategy] = max(0, int(lots))
    return save_capital(plan, path=path)


def update_strategy_budget(
    strategy: str,
    *,
    budget_inr: float | None = None,
    max_lots: int | None = None,
    max_open_trades: int | None = None,
    enabled: bool | None = None,
    live_size_mode: str | None = None,
    live_lots: int | None = None,
    live_budget_inr: float | None = None,
    path: Path | None = None,
) -> CapitalPlan:
    plan = load_capital(path)
    sb = plan.strategies.get(strategy) or StrategyBudget(strategy=strategy)
    if budget_inr is not None:
        sb.budget_inr = float(budget_inr)
    if max_lots is not None:
        sb.max_lots = int(max_lots)
    if max_open_trades is not None:
        sb.max_open_trades = int(max_open_trades)
    if enabled is not None:
        sb.enabled = bool(enabled)
    if live_size_mode is not None:
        sb.live_size_mode = normalize_size_mode(live_size_mode)
    if live_lots is not None:
        sb.live_lots = max(0, int(live_lots))
    if live_budget_inr is not None:
        sb.live_budget_inr = max(0.0, float(live_budget_inr))
    plan.strategies[strategy] = sb
    return save_capital(plan, path=path)


def apply_live_capital_allocation(
    allocations: list[dict[str, Any]],
    *,
    total_capital_inr: float | None = None,
    cash_reserve_pct: float | None = None,
    daily_loss_limit_inr: float | None = None,
    max_lots_total: int | None = None,
    live_size_mode: str | None = None,
    disable_others: bool = False,
    known_strategies: tuple[str, ...] | None = None,
    path: Path | None = None,
) -> CapitalPlan:
    """Apply per-strategy live size. Missing keys are left as-is (do not write ₹ 0).

    ``allocations`` rows: strategy, optional budget_inr, live_lots, live_size_mode,
    max_lots (paper cap, legacy), max_open_trades, enabled.
    Tick Lots or tick ₹ on the desk — that is how the book goes live.
    """
    plan = load_capital(path)
    if total_capital_inr is not None:
        plan.total_capital_inr = float(total_capital_inr)
    if cash_reserve_pct is not None:
        plan.cash_reserve_pct = float(cash_reserve_pct)
    if daily_loss_limit_inr is not None:
        plan.daily_loss_limit_inr = float(daily_loss_limit_inr)
    if max_lots_total is not None:
        plan.max_lots_total = int(max_lots_total)
    if live_size_mode is not None and str(live_size_mode).strip():
        plan.live_size_mode = normalize_size_mode(live_size_mode) or SIZE_LOTS
    save_capital(plan, path=path)

    selected: set[str] = set()
    for row in allocations:
        name = str(row.get("strategy") or "").strip()
        if not name:
            continue
        selected.add(name)
        kwargs: dict[str, Any] = {}
        if "enabled" in row:
            kwargs["enabled"] = bool(row.get("enabled"))
        else:
            kwargs["enabled"] = True
        if "budget_inr" in row and row.get("budget_inr") not in (None, ""):
            kwargs["budget_inr"] = float(row["budget_inr"])
        if "max_lots" in row and row.get("max_lots") not in (None, ""):
            kwargs["max_lots"] = int(row["max_lots"])
        if "live_lots" in row and row.get("live_lots") not in (None, ""):
            kwargs["live_lots"] = max(0, int(row["live_lots"]))
        if "live_budget_inr" in row and row.get("live_budget_inr") not in (None, ""):
            kwargs["live_budget_inr"] = float(row["live_budget_inr"])
        if "live_size_mode" in row:
            kwargs["live_size_mode"] = str(row.get("live_size_mode") or "")
        if "max_open_trades" in row and row.get("max_open_trades") not in (None, ""):
            kwargs["max_open_trades"] = int(row["max_open_trades"])
        update_strategy_budget(name, path=path, **kwargs)

    if disable_others:
        names = known_strategies or DEFAULT_STRATEGIES
        for name in names:
            if name not in selected:
                update_strategy_budget(name, enabled=False, path=path)

    return load_capital(path)


def can_open_trade(
    strategy: str,
    lots: int = 1,
    *,
    path: Path | None = None,
) -> tuple[bool, str]:
    """Gate new entries by daily loss, strategy budget, and lot caps."""
    plan = load_capital(path)
    pnl = today_pnl(plan)
    if pnl <= -abs(plan.daily_loss_limit_inr):
        return False, f"daily_loss_limit hit ({pnl:.0f} <= -{plan.daily_loss_limit_inr:.0f})"

    sb = plan.strategies.get(strategy)
    if sb is None:
        return False, f"no_budget_for_{strategy}"
    if not sb.enabled:
        return False, "strategy_budget_disabled"

    open_lots = int(plan.open_lots.get(strategy, 0))
    if open_lots + lots > sb.max_lots:
        return False, f"strategy_max_lots {open_lots}+{lots}>{sb.max_lots}"

    total_open = sum(int(v) for v in plan.open_lots.values())
    if total_open + lots > plan.max_lots_total:
        return False, f"total_max_lots {total_open}+{lots}>{plan.max_lots_total}"

    # ₹ is the live size only when this book (or the plan) is on capital mode.
    book_mode = normalize_size_mode(sb.live_size_mode)
    plan_mode = normalize_size_mode(plan.live_size_mode) or SIZE_LOTS
    mode = book_mode or plan_mode
    if mode == SIZE_CAPITAL and sb.budget_inr <= 0:
        return False, "zero_budget"

    return True, "ok"


def capital_snapshot(path: Path | None = None) -> dict[str, Any]:
    plan = load_capital(path)
    snap = plan.to_dict()
    snap["deployable_inr"] = deployable_capital(plan)
    snap["today"] = _today()
    snap["today_pnl_inr"] = today_pnl(plan)
    snap["daily_loss_breached"] = today_pnl(plan) <= -abs(plan.daily_loss_limit_inr)
    return snap
