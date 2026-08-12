"""Reasoning cockpit: entry / hold / exit heads over live market state.

Builds a compact market snapshot from recent ticks + multi-TF bars, then runs
three independent multi-step reasoners so the control panel (and S8 gate) can
show *why* to enter, hold, or exit — with loss-minimization baked into the plan.
"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

from control_state import CONTROL_DIR, ensure_control_dir
from mtf_bars import INTERVALS, build_rich_bars, load_tick_rows, tick_metrics
from s8_reasoner import ReasoningTrace, reason_entry, reason_exit, reason_hold
from storage import DB_PATH, latest_ltp

IST = ZoneInfo("Asia/Kolkata")
REASONING_PATH = CONTROL_DIR / "reasoning_latest.json"
_lock = threading.Lock()


@dataclass
class MarketSnapshot:
    """What the reasoner 'sees' right now."""

    asof_ist: str
    ltp: float | None
    tick_count: int
    net: float
    imb_pct: float
    prev_imb_pct: float
    tbq: float
    tsq: float
    tbq_rising: bool
    tsq_rising: bool
    imb_rising: bool
    regime_guess: str  # TREND_UP | TREND_DOWN | CHOP | QUIET | UNKNOWN
    spread_proxy_pts: float | None
    bar_1m: dict[str, Any] | None = None
    bar_5m: dict[str, Any] | None = None
    bar_30m: dict[str, Any] | None = None
    bar_1h: dict[str, Any] | None = None
    bar_1d: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LifecycleReasoning:
    """Three-head plan for the current market."""

    market: MarketSnapshot
    entry: dict[str, Any]
    hold: dict[str, Any]
    exit: dict[str, Any]
    recommended: Literal["ENTER_LONG", "ENTER_SHORT", "HOLD", "EXIT", "WAIT", "SKIP"]
    loss_guard: str
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "market": self.market.to_dict(),
            "entry": self.entry,
            "hold": self.hold,
            "exit": self.exit,
            "recommended": self.recommended,
            "loss_guard": self.loss_guard,
            "note": self.note,
            "updated_at_ist": datetime.now(IST).isoformat(timespec="seconds"),
        }


def _guess_regime(bars_5m: list[dict[str, Any]]) -> str:
    if len(bars_5m) < 4:
        return "UNKNOWN"
    closes = [float(b["close"]) for b in bars_5m[-8:]]
    nets = [float(b.get("net") or 0) for b in bars_5m[-8:]]
    move = closes[-1] - closes[0]
    avg_range = sum(float(b.get("range_pts") or 0) for b in bars_5m[-8:]) / max(
        1, len(bars_5m[-8:])
    )
    net_sign = 1 if sum(1 for n in nets if n > 0) >= 5 else (-1 if sum(1 for n in nets if n < 0) >= 5 else 0)
    if avg_range < 3.0 and abs(move) < 8:
        return "QUIET"
    if abs(move) >= max(12.0, 1.5 * avg_range) and net_sign != 0:
        return "TREND_UP" if move > 0 and net_sign > 0 else (
            "TREND_DOWN" if move < 0 and net_sign < 0 else "CHOP"
        )
    if abs(move) < avg_range:
        return "CHOP"
    return "TREND_UP" if move > 0 else "TREND_DOWN"


def build_market_snapshot(
    *,
    db: Path = DB_PATH,
    max_ticks: int = 8000,
) -> MarketSnapshot:
    rows = load_tick_rows(Path(db))
    if max_ticks and len(rows) > max_ticks:
        rows = rows[-max_ticks:]
    asof = datetime.now(IST).isoformat(timespec="seconds")
    if not rows:
        return MarketSnapshot(
            asof_ist=asof,
            ltp=latest_ltp(db),
            tick_count=0,
            net=0.0,
            imb_pct=0.0,
            prev_imb_pct=0.0,
            tbq=0.0,
            tsq=0.0,
            tbq_rising=False,
            tsq_rising=False,
            imb_rising=False,
            regime_guess="UNKNOWN",
            spread_proxy_pts=None,
        )

    metrics = []
    for r in rows:
        m = tick_metrics(r)
        if m.get("ltp") is not None:
            metrics.append(m)
    if not metrics:
        return MarketSnapshot(
            asof_ist=asof,
            ltp=None,
            tick_count=len(rows),
            net=0.0,
            imb_pct=0.0,
            prev_imb_pct=0.0,
            tbq=0.0,
            tsq=0.0,
            tbq_rising=False,
            tsq_rising=False,
            imb_rising=False,
            regime_guess="UNKNOWN",
            spread_proxy_pts=None,
        )

    last = metrics[-1]
    prev = metrics[-2] if len(metrics) > 1 else last
    tbq = float(last["tbq"])
    tsq = float(last["tsq"])
    net = float(last["net"])
    imb = float(last["imb_pct"])
    prev_imb = float(prev["imb_pct"])
    tbq_rising = tbq > float(prev["tbq"])
    tsq_rising = tsq > float(prev["tsq"])
    imb_rising = imb > prev_imb

    def _last_bar(minutes: int, name: str) -> dict[str, Any] | None:
        bars = build_rich_bars(rows, name, minutes)
        return bars[-1].to_row() if bars else None

    bars_5 = [b.to_row() for b in build_rich_bars(rows, "5m", 5)]
    regime = _guess_regime(bars_5)

    return MarketSnapshot(
        asof_ist=asof,
        ltp=float(last["ltp"]),
        tick_count=len(rows),
        net=net,
        imb_pct=imb,
        prev_imb_pct=prev_imb,
        tbq=tbq,
        tsq=tsq,
        tbq_rising=tbq_rising,
        tsq_rising=tsq_rising,
        imb_rising=imb_rising,
        regime_guess=regime,
        spread_proxy_pts=None,
        bar_1m=_last_bar(1, "1m"),
        bar_5m=bars_5[-1] if bars_5 else None,
        bar_30m=_last_bar(30, "30m"),
        bar_1h=_last_bar(60, "1h"),
        bar_1d=_last_bar(1440, "1d"),
    )


def _pick_recommended(
    entry: ReasoningTrace,
    hold: ReasoningTrace,
    exit_: ReasoningTrace,
    *,
    in_position: bool,
) -> tuple[str, str]:
    """Loss-minimizing arbitrator across the three heads."""
    if in_position:
        if exit_.action == "EXIT":
            return "EXIT", "exit head wants out (cut loss / take profit / book fail)"
        if hold.action == "HOLD":
            return "HOLD", "hold head OK — ride while support intact"
        return "EXIT", "hold weak and exit soft — prefer flat to minimise loss"
    # flat
    if entry.action in {"ENTER_LONG", "ENTER_SHORT"} and entry.score >= 0.45:
        # Block entries that exit head already flags as toxic
        if exit_.action == "EXIT" and (exit_.score or 0) >= 0.70:
            return "SKIP", "exit head says market toxic — skip new entry"
        return entry.action, entry.summary
    if entry.action == "WAIT":
        return "WAIT", entry.summary
    return "SKIP", entry.summary or "entry head skipped"


def run_lifecycle_reasoning(
    *,
    db: Path = DB_PATH,
    in_position: bool = False,
    position_side: Literal["long", "short", "flat"] = "flat",
    open_pnl_pts: float = 0.0,
    tp: float = 45.0,
    sl: float = 35.0,
    min_imb: float = 10.0,
    lots: float = 100.0,
    entry_proba: float | None = None,
    hold_proba: float | None = None,
    exit_soon_proba: float | None = None,
    require_rising_imb: bool = True,
) -> LifecycleReasoning:
    market = build_market_snapshot(db=db)

    # Regime soft-bias: in QUIET/CHOP raise the bar for entries via min_imb bump.
    regime_min_imb = min_imb
    if market.regime_guess in {"QUIET", "CHOP"}:
        regime_min_imb = max(min_imb, min_imb * 1.25)
    if market.regime_guess == "TREND_UP" and market.net < 0:
        # fighting the tape
        regime_min_imb = max(regime_min_imb, min_imb * 1.5)
    if market.regime_guess == "TREND_DOWN" and market.net > 0:
        regime_min_imb = max(regime_min_imb, min_imb * 1.5)

    entry = reason_entry(
        px=float(market.ltp or 0.0),
        net=float(market.net),
        imb=float(market.imb_pct),
        prev_imb=float(market.prev_imb_pct),
        tp=tp,
        sl=sl,
        min_imb=regime_min_imb,
        imb_rising=market.imb_rising,
        require_rising_imb=require_rising_imb,
        tbq_rising=market.tbq_rising,
        tsq_rising=market.tsq_rising,
        loss_locked=False,
        in_cooldown=False,
        lots=lots,
        entry_proba=entry_proba,
        hold_proba=hold_proba,
        exit_soon_proba=exit_soon_proba,
        min_entry_proba=0.55,
        regime=market.regime_guess,
    )

    side_for_manage: Literal["long", "short"] = (
        position_side if position_side in {"long", "short"} else (
            "long" if market.net >= 0 else "short"
        )
    )
    hold = reason_hold(
        side=side_for_manage,
        move=float(open_pnl_pts),
        tp=tp,
        sl=sl,
        tbq_falling=not market.tbq_rising if side_for_manage == "long" else False,
        tsq_falling=not market.tsq_rising if side_for_manage == "short" else False,
        hold_proba=hold_proba,
        exit_soon_proba=exit_soon_proba,
        regime=market.regime_guess,
        imb=float(market.imb_pct),
        net=float(market.net),
    )
    exit_ = reason_exit(
        side=side_for_manage,
        move=float(open_pnl_pts),
        tp=tp,
        sl=sl,
        tbq_falling=not market.tbq_rising if side_for_manage == "long" else False,
        tsq_falling=not market.tsq_rising if side_for_manage == "short" else False,
        hold_proba=hold_proba,
        exit_soon_proba=exit_soon_proba,
        regime=market.regime_guess,
        imb=float(market.imb_pct),
        net=float(market.net),
        protect_profit_pts=25.0,
    )

    recommended, guard = _pick_recommended(
        entry, hold, exit_, in_position=in_position or position_side in {"long", "short"}
    )
    return LifecycleReasoning(
        market=market,
        entry=entry.to_dict(),
        hold=hold.to_dict(),
        exit=exit_.to_dict(),
        recommended=recommended,  # type: ignore[arg-type]
        loss_guard=guard,
        note=(
            "Three-head reasoner: ENTRY / HOLD / EXIT. "
            "Weekly evolve_s8_ml trains ML heads; this cockpit applies them live."
        ),
    )


def save_reasoning(life: LifecycleReasoning, path: Path = REASONING_PATH) -> Path:
    ensure_control_dir(path)
    with _lock:
        path.write_text(json.dumps(life.to_dict(), indent=2), encoding="utf-8")
    return path


def load_reasoning(path: Path = REASONING_PATH) -> dict[str, Any] | None:
    if not path.exists():
        return None
    with _lock:
        return json.loads(path.read_text(encoding="utf-8"))


def refresh_and_save(**kwargs: Any) -> dict[str, Any]:
    life = run_lifecycle_reasoning(**kwargs)
    save_reasoning(life)
    return life.to_dict()


def panel_timeframes() -> list[dict[str, Any]]:
    """TF list for the control panel (1m → day)."""
    out = [{"tf": name, "minutes": mins} for name, mins in INTERVALS]
    # Ensure day is present even if INTERVALS was extended elsewhere.
    names = {x["tf"] for x in out}
    if "4h" not in names:
        out.append({"tf": "4h", "minutes": 240})
    if "1d" not in names:
        out.append({"tf": "1d", "minutes": 1440})
    return out


def bars_for_panel(
    tf: str,
    *,
    limit: int = 80,
    db: Path = DB_PATH,
    max_ticks: int = 20000,
) -> dict[str, Any]:
    """Aggregate ticks into bars for one TF and return the latest ``limit`` bars."""
    tf = (tf or "5m").strip().lower()
    minutes_map = {name: mins for name, mins in INTERVALS}
    minutes_map.setdefault("4h", 240)
    minutes_map.setdefault("1d", 1440)
    if tf not in minutes_map:
        raise ValueError(f"unknown tf={tf}; choose from {sorted(minutes_map)}")
    rows = load_tick_rows(Path(db))
    if max_ticks and len(rows) > max_ticks:
        rows = rows[-max_ticks:]
    bars = [b.to_row() for b in build_rich_bars(rows, tf, minutes_map[tf])]
    return {
        "tf": tf,
        "minutes": minutes_map[tf],
        "n_bars": len(bars),
        "bars": bars[-limit:],
        "tick_count": len(rows),
    }
