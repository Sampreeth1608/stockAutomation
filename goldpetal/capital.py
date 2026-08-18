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

from control_state import CONTROL_DIR, SLIM_PAPER_STRATEGIES, ensure_control_dir

IST = ZoneInfo("Asia/Kolkata")
CAPITAL_PATH = CONTROL_DIR / "capital.json"
_lock = threading.Lock()

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
    "S11_DISCOVERED",
    "S13_HHHL_DAY",
    "S16_HHHL_WICK_1H",
)

_HUNDRED_LOT = set(SLIM_PAPER_STRATEGIES)


@dataclass
class StrategyBudget:
    strategy: str
    budget_inr: float = 50_000.0
    max_lots: int = 10
    max_open_trades: int = 1
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CapitalPlan:
    total_capital_inr: float = 500_000.0
    cash_reserve_pct: float = 20.0
    daily_loss_limit_inr: float = 10_000.0
    max_lots_total: int = 50
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
            "strategies": {k: v.to_dict() for k, v in self.strategies.items()},
            "day_pnl_inr": self.day_pnl_inr,
            "open_lots": self.open_lots,
            "updated_at_ist": self.updated_at_ist,
        }


def _now_iso() -> str:
    return datetime.now(IST).isoformat(timespec="seconds")


def _today() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d")


def default_plan() -> CapitalPlan:
    # Split remaining risk capital evenly across known strategies.
    total = 500_000.0
    reserve = total * 0.20
    deployable = total - reserve
    each = round(deployable / len(DEFAULT_STRATEGIES), 2)
    strats = {}
    for name in DEFAULT_STRATEGIES:
        # Slim paper books (S5/S8/S11/S13/S16) sized at PAPER_LOTS (100).
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
        )
    # Ensure defaults exist for any new strategy names.
    from charges import paper_lots

    paper_n = int(paper_lots())
    for name in DEFAULT_STRATEGIES:
        if name not in strategies:
            lots = paper_n if name in _HUNDRED_LOT else 10
            strategies[name] = StrategyBudget(strategy=name, max_lots=lots)
    need_total = paper_n * len(_HUNDRED_LOT)
    return CapitalPlan(
        total_capital_inr=float(raw.get("total_capital_inr", 500_000)),
        cash_reserve_pct=float(raw.get("cash_reserve_pct", 20)),
        daily_loss_limit_inr=float(raw.get("daily_loss_limit_inr", 10_000)),
        max_lots_total=max(need_total, int(raw.get("max_lots_total", need_total))),
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
    plan.strategies[strategy] = sb
    return save_capital(plan, path=path)


def apply_live_capital_allocation(
    allocations: list[dict[str, Any]],
    *,
    total_capital_inr: float | None = None,
    cash_reserve_pct: float | None = None,
    daily_loss_limit_inr: float | None = None,
    max_lots_total: int | None = None,
    disable_others: bool = False,
    known_strategies: tuple[str, ...] | None = None,
    path: Path | None = None,
) -> CapitalPlan:
    """Apply per-strategy ₹ budgets + lot caps for live deploy.

    ``allocations`` rows: strategy, budget_inr, max_lots[, max_open_trades, enabled].
    When disable_others=True, strategies not in the allocation list get enabled=False.
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
    save_capital(plan, path=path)

    selected: set[str] = set()
    for row in allocations:
        name = str(row.get("strategy") or "").strip()
        if not name:
            continue
        selected.add(name)
        update_strategy_budget(
            name,
            budget_inr=float(row.get("budget_inr", 0)),
            max_lots=int(row.get("max_lots", 1)),
            max_open_trades=int(row.get("max_open_trades", 1)),
            enabled=bool(row.get("enabled", True)),
            path=path,
        )

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

    # Rough notional check: Gold Petal 1 lot ≈ LTP*1; use budget as soft INR cap
    # without requiring live LTP here (panel shows budgets; runner may refine).
    if sb.budget_inr <= 0:
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
