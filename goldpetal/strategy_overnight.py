"""S4: overnight next-open strategy — enter near close, exit after next open."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import joblib
import pandas as pd

from overnight_features import DAY_FEATURE_COLS, day_bars_from_ticks
from day_bias import DayBiasResult, analyze_day
from strategy import Position, SignalResult

IST = ZoneInfo("Asia/Kolkata")
DEFAULT_MODEL_DIR = Path(__file__).resolve().parent / "data" / "models"
DEFAULT_STATE_PATH = Path(__file__).resolve().parent / "data" / "s4_state.json"


class HeuristicGapModel:
    """Fallback scorer when ML overnight model is not trained yet.

    P(gap_up) ≈ σ( 8*late_ret + 0.8*late_imb + 0.4*clv - 0.1*ret_z )
    using a logistic sigmoid. Not a fitted model — bridge until enough days.
    """

    feature_names_ = [
        "late_ret",
        "late_imb",
        "clv",
        "ret_z",
        "log_ret",
        "parkinson",
    ]

    def predict_proba(self, X):  # noqa: ANN001 — sklearn-like
        import math

        import numpy as np

        arr = np.asarray(X, dtype=float)
        # map known column order from DAY_FEATURE_COLS if wide matrix
        # Prefer named positions when full feature matrix passed
        if arr.ndim == 1:
            arr = arr.reshape(1, -1)
        # DAY_FEATURE_COLS indices
        from overnight_features import DAY_FEATURE_COLS

        idx = {n: i for i, n in enumerate(DAY_FEATURE_COLS)}

        def col(name: str, default: float = 0.0) -> float:
            i = idx.get(name)
            if i is None or i >= arr.shape[1]:
                return default
            v = arr[0, i]
            return float(v) if v == v else default

        z = (
            8.0 * col("late_ret")
            + 0.8 * col("late_imb")
            + 0.4 * col("clv")
            - 0.1 * col("ret_z")
            + 2.0 * col("log_ret")
        )
        # clip for numerical stability
        z = max(-20.0, min(20.0, z))
        p = 1.0 / (1.0 + math.exp(-z))
        return np.array([[1.0 - p, p]])


@dataclass
class OvernightState:
    side: Position  # long/short/flat
    entry_price: float | None = None
    entry_date: str | None = None
    entry_ts: str | None = None
    prob_up: float | None = None


class OvernightStrategy:
    """BUY near close if P(gap up) high; SHORT if P(gap up) low; exit next open."""

    name = "S4_OVERNIGHT"

    def __init__(
        self,
        model_path: Path | None = None,
        buy_prob: float = 0.58,
        short_prob: float = 0.42,
        entry_minutes_before_close: int = 10,
        exit_minutes_after_open: int = 5,
        market_open: str = "09:00",
        market_close: str = "23:30",
        state_path: Path = DEFAULT_STATE_PATH,
    ) -> None:
        self.buy_prob = buy_prob
        self.short_prob = short_prob
        self.entry_minutes_before_close = entry_minutes_before_close
        self.exit_minutes_after_open = exit_minutes_after_open
        self.market_open = self._parse_hhmm(market_open)
        self.market_close = self._parse_hhmm(market_close)
        self.state_path = state_path
        self.enabled = False
        self.model = None
        self.features = list(DAY_FEATURE_COLS)
        self.last_prob: float | None = None
        self._load_error: str | None = None
        self._entered_today = False
        self._exited_today = False
        self.tick_rows: list[dict[str, Any]] = []
        self.state = self._load_state()

        path = model_path or self._resolve_model()
        if path is None:
            # Always-on quant heuristic so S4 can paper-trade while days accumulate
            self.model = HeuristicGapModel()
            self.features = list(DAY_FEATURE_COLS)
            self.enabled = True
            self.model_path = Path("heuristic://gap_sigmoid")
            self._load_error = None
            return
        try:
            bundle = joblib.load(path)
            self.model = bundle["model"]
            self.features = list(bundle.get("features") or DAY_FEATURE_COLS)
            self.enabled = True
            self.model_path = path
        except Exception as exc:  # noqa: BLE001
            self.model = HeuristicGapModel()
            self.features = list(DAY_FEATURE_COLS)
            self.enabled = True
            self.model_path = Path("heuristic://gap_sigmoid")
            self._load_error = f"ML load failed ({exc}); using heuristic"

    @staticmethod
    def _parse_hhmm(value: str) -> time:
        h, m = value.strip().split(":")
        return time(int(h), int(m))

    def _resolve_model(self) -> Path | None:
        root = DEFAULT_MODEL_DIR
        report = root / "overnight_report.json"
        if report.exists():
            try:
                meta = json.loads(report.read_text(encoding="utf-8"))
                best = meta.get("best_model")
                if best and best in meta.get("models", {}):
                    p = Path(meta["models"][best]["path"])
                    if p.exists():
                        return p
                    alt = root / f"{best}.joblib"
                    if alt.exists():
                        return alt
            except (json.JSONDecodeError, KeyError, TypeError):
                pass
        for name in (
            "overnight_heuristic.joblib",
            "overnight_logreg.joblib",
            "overnight_rf.joblib",
            "overnight_gb.joblib",
        ):
            p = root / name
            if p.exists():
                return p
        return None

    def _load_state(self) -> OvernightState:
        if self.state_path.exists():
            try:
                raw = json.loads(self.state_path.read_text(encoding="utf-8"))
                return OvernightState(
                    side=raw.get("side", "flat"),
                    entry_price=raw.get("entry_price"),
                    entry_date=raw.get("entry_date"),
                    entry_ts=raw.get("entry_ts"),
                    prob_up=raw.get("prob_up"),
                )
            except (json.JSONDecodeError, TypeError, ValueError):
                pass
        return OvernightState(side="flat")

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            json.dumps(
                {
                    "side": self.state.side,
                    "entry_price": self.state.entry_price,
                    "entry_date": self.state.entry_date,
                    "entry_ts": self.state.entry_ts,
                    "prob_up": self.state.prob_up,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    @property
    def position(self) -> Position:
        return self.state.side

    @property
    def status_line(self) -> str:
        if self.enabled:
            kind = "heuristic" if "heuristic" in str(self.model_path) else "ml"
            return (
                f"enabled ({kind}+day_bias) delivery "
                f"model={self.model_path} "
                f"buy>={self.buy_prob} short<={self.short_prob} "
                f"entry={self.entry_minutes_before_close}m_before_close "
                f"pos={self.state.side}"
            )
        return f"DISABLED ({self._load_error})"

    def push_tick_row(self, row: dict[str, Any]) -> None:
        self.tick_rows.append(row)
        # keep memory bounded (~1 trading day of dense ticks)
        if len(self.tick_rows) > 50_000:
            self.tick_rows = self.tick_rows[-40_000:]

    def _in_entry_window(self, now: datetime) -> bool:
        close_dt = now.replace(
            hour=self.market_close.hour,
            minute=self.market_close.minute,
            second=0,
            microsecond=0,
        )
        start = close_dt - timedelta(minutes=self.entry_minutes_before_close)
        return start <= now <= close_dt

    def _in_exit_window(self, now: datetime) -> bool:
        open_dt = now.replace(
            hour=self.market_open.hour,
            minute=self.market_open.minute,
            second=0,
            microsecond=0,
        )
        end = open_dt + timedelta(minutes=self.exit_minutes_after_open)
        return open_dt <= now <= end

    def _score_ml(self) -> float | None:
        if not self.tick_rows or self.model is None:
            return None
        df = pd.DataFrame(self.tick_rows)
        days = day_bars_from_ticks(df, late_minutes=30)
        if days.empty:
            return None
        latest = days.iloc[[-1]].copy()
        for c in self.features:
            if c not in latest.columns:
                latest[c] = 0.0
        X = latest[self.features].astype(float).fillna(0.0)
        return float(self.model.predict_proba(X)[0, 1])

    def _decide_delivery(self) -> tuple[float, DayBiasResult] | None:
        """Full-day analysis → blended P(bullish) for delivery position."""
        bias = analyze_day(
            self.tick_rows,
            late_minutes=45,
            bullish_prob=self.buy_prob,
            bearish_prob=self.short_prob,
        )
        if bias is None:
            return None
        ml = self._score_ml()
        # Prefer full-day structure; blend ML gap model when available (not pure heuristic)
        if ml is not None and "heuristic" not in str(self.model_path):
            prob = 0.65 * bias.prob_bullish + 0.35 * ml
        else:
            prob = bias.prob_bullish
        self.last_prob = prob
        return prob, bias

    def maybe_signal(self, now: datetime, cmp: float) -> SignalResult | None:
        if not self.enabled or self.model is None:
            return None
        today = now.astimezone(IST).strftime("%Y-%m-%d")

        # --- EXIT: next session after overnight delivery hold ---
        if self.state.side != "flat" and self.state.entry_date and self.state.entry_date < today:
            if self._in_exit_window(now) and not self._exited_today:
                side = self.state.side
                entry_date = self.state.entry_date
                self.state = OvernightState(side="flat")
                self._save_state()
                self._exited_today = True
                return SignalResult(
                    action="CLOSE",
                    position_after="flat",
                    price_delta=None,
                    net=float(self.last_prob or 0.0),
                    net_delta=None,
                    prev_net_delta=None,
                    reason=(
                        f"delivery exit after open; was_{side} entry_date={entry_date}"
                    ),
                )

        # --- ENTRY: near close after analysing the whole day ---
        if self.state.side != "flat":
            return None
        if self._entered_today:
            return None
        if not self._in_entry_window(now):
            return None

        decided = self._decide_delivery()
        if decided is None:
            return None
        prob, bias = decided

        if bias.bias == "BULLISH" and prob >= self.buy_prob:
            action = "BUY"
            pos: Position = "long"
            reason = (
                f"DELIVERY LONG — full-day BULLISH P={prob:.3f}; {bias.reason}"
            )
        elif bias.bias == "BEARISH" and prob <= self.short_prob:
            action = "SHORT"
            pos = "short"
            reason = (
                f"DELIVERY SHORT — full-day BEARISH P={prob:.3f}; {bias.reason}"
            )
        else:
            return None

        self.state = OvernightState(
            side=pos,
            entry_price=cmp,
            entry_date=today,
            entry_ts=now.isoformat(timespec="seconds"),
            prob_up=prob,
        )
        self._save_state()
        self._entered_today = True
        return SignalResult(
            action=action,
            position_after=pos,
            price_delta=None,
            net=float(prob),
            net_delta=None,
            prev_net_delta=None,
            reason=reason,
        )


def overnight_from_env() -> OvernightStrategy:
    load_open = os.getenv("MARKET_OPEN", "09:00")
    load_close = os.getenv("MARKET_CLOSE", "23:30")
    buy = float(os.getenv("S4_BUY_PROB", "0.58"))
    short = float(os.getenv("S4_SHORT_PROB", "0.42"))
    entry_m = int(os.getenv("S4_ENTRY_MINUTES_BEFORE_CLOSE", "15"))
    exit_m = int(os.getenv("S4_EXIT_MINUTES_AFTER_OPEN", "5"))
    model = os.getenv("S4_MODEL_PATH", "").strip()
    path = Path(model) if model else None
    return OvernightStrategy(
        model_path=path,
        buy_prob=buy,
        short_prob=short,
        entry_minutes_before_close=entry_m,
        exit_minutes_after_open=exit_m,
        market_open=load_open,
        market_close=load_close,
    )
