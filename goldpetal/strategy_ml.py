"""Strategy 3: ML model BUY/SHORT/CLOSE from full-depth features."""

from __future__ import annotations

import json
import os
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Deque

import pandas as pd

from export_full_ticks import row_from_tick
from ml_features import FEATURE_COLUMNS, build_features
from strategy import Action, Position, SignalResult

DEFAULT_MODEL_DIR = Path(__file__).resolve().parent / "data" / "models"
DEFAULT_BUY_PROB = 0.58
DEFAULT_SHORT_PROB = 0.42
DEFAULT_MIN_HOLD_SEC = 30
DEFAULT_EVERY_N_TICKS = 5
DEFAULT_BUFFER = 80


def resolve_model_path(model_dir: Path | None = None) -> Path | None:
    """Pick best trained model from report.json, else any .joblib."""
    root = model_dir or DEFAULT_MODEL_DIR
    report = root / "report.json"
    if report.exists():
        try:
            meta = json.loads(report.read_text(encoding="utf-8"))
            best = meta.get("best_model")
            if best and best in meta.get("models", {}):
                path = Path(meta["models"][best]["path"])
                if path.exists():
                    return path
                alt = root / f"{best}.joblib"
                if alt.exists():
                    return alt
        except (json.JSONDecodeError, KeyError, TypeError):
            pass
    for name in ("logreg.joblib", "random_forest.joblib", "grad_boost.joblib"):
        path = root / name
        if path.exists():
            return path
    return None


class MLStrategy:
    """Score live ticks with a trained model; emit trade transitions only."""

    name = "S3_ML"

    def __init__(
        self,
        model_path: Path | None = None,
        buy_prob: float = DEFAULT_BUY_PROB,
        short_prob: float = DEFAULT_SHORT_PROB,
        min_hold_sec: float = DEFAULT_MIN_HOLD_SEC,
        every_n_ticks: int = DEFAULT_EVERY_N_TICKS,
        buffer_size: int = DEFAULT_BUFFER,
    ) -> None:
        self.buy_prob = buy_prob
        self.short_prob = short_prob
        self.min_hold_sec = min_hold_sec
        self.every_n_ticks = max(1, every_n_ticks)
        self.buffer: Deque[dict[str, Any]] = deque(maxlen=max(buffer_size, 40))
        self.position: Position = "flat"
        self.enabled = False
        self.model = None
        self.features = list(FEATURE_COLUMNS)
        self.last_prob: float | None = None
        self.last_signal_ts: datetime | None = None
        self._tick_i = 0
        self._load_error: str | None = None

        path = model_path or resolve_model_path()
        if path is None:
            self._load_error = (
                f"No model in {DEFAULT_MODEL_DIR}. "
                "Run: python export_full_ticks.py && python train_models.py"
            )
            return
        try:
            import joblib

            bundle = joblib.load(path)
            self.model = bundle["model"]
            self.features = list(bundle.get("features") or FEATURE_COLUMNS)
            self.enabled = True
            self.model_path = path
        except Exception as exc:  # noqa: BLE001 — surface any load failure
            self._load_error = f"Failed loading {path}: {exc}"
            self.enabled = False

    @property
    def status_line(self) -> str:
        if self.enabled:
            return f"enabled model={getattr(self, 'model_path', '?')} buy>={self.buy_prob} short<={self.short_prob} hold={self.min_hold_sec}s"
        return f"DISABLED ({self._load_error})"

    def push_tick(
        self,
        message: dict[str, Any],
        *,
        received_at: str,
        exchange_timestamp: Any = None,
    ) -> None:
        row = row_from_tick(
            received_at,
            exchange_timestamp,
            json.dumps(message, default=str),
        )
        if row.get("ltp") is None:
            return
        self.buffer.append(row)
        self._tick_i += 1

    def push_row(self, row: dict[str, Any]) -> None:
        self.buffer.append(row)
        self._tick_i += 1

    def _desired_side(self, prob: float) -> Position:
        if prob >= self.buy_prob:
            return "long"
        if prob <= self.short_prob:
            return "short"
        return "flat"

    def _hold_ok(self, now: datetime) -> bool:
        if self.last_signal_ts is None:
            return True
        return (now - self.last_signal_ts).total_seconds() >= self.min_hold_sec

    def maybe_signal(self, now: datetime) -> SignalResult | None:
        """Return BUY/SHORT/CLOSE only on transitions; else None."""
        if not self.enabled or self.model is None:
            return None
        if self._tick_i % self.every_n_ticks != 0:
            return None
        if len(self.buffer) < 25:
            return None

        df = pd.DataFrame(list(self.buffer))
        feat = build_features(df)
        latest = feat.iloc[[-1]]
        if latest[self.features].isna().any(axis=None):
            return None

        X = latest[self.features].astype(float)
        prob = float(self.model.predict_proba(X)[0, 1])
        self.last_prob = prob
        want = self._desired_side(prob)

        if want == self.position:
            return None
        if not self._hold_ok(now):
            return None

        net = float(latest["imb_l5"].iloc[0]) if "imb_l5" in latest else 0.0
        action: Action
        if want == "long":
            action = "BUY"
            self.position = "long"
            reason = f"ml prob_up={prob:.3f} >= {self.buy_prob}; buy"
        elif want == "short":
            action = "SHORT"
            self.position = "short"
            reason = f"ml prob_up={prob:.3f} <= {self.short_prob}; short"
        else:
            action = "CLOSE"
            self.position = "flat"
            reason = (
                f"ml prob_up={prob:.3f} in neutral "
                f"({self.short_prob:.2f},{self.buy_prob:.2f}); close"
            )

        self.last_signal_ts = now
        return SignalResult(
            action=action,
            position_after=self.position,
            price_delta=None,
            net=net,
            net_delta=None,
            prev_net_delta=None,
            reason=reason,
        )


def ml_strategy_from_env() -> MLStrategy:
    """Build S3 from .env / defaults."""
    buy = float(os.getenv("ML_BUY_PROB", str(DEFAULT_BUY_PROB)))
    short = float(os.getenv("ML_SHORT_PROB", str(DEFAULT_SHORT_PROB)))
    hold = float(os.getenv("ML_MIN_HOLD_SEC", str(DEFAULT_MIN_HOLD_SEC)))
    every = int(os.getenv("ML_EVERY_N_TICKS", str(DEFAULT_EVERY_N_TICKS)))
    model_dir = os.getenv("ML_MODEL_DIR", str(DEFAULT_MODEL_DIR)).strip()
    model_file = os.getenv("ML_MODEL_PATH", "").strip()
    path = Path(model_file) if model_file else resolve_model_path(Path(model_dir))
    return MLStrategy(
        model_path=path,
        buy_prob=buy,
        short_prob=short,
        min_hold_sec=hold,
        every_n_ticks=every,
    )
