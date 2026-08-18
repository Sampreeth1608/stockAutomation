"""Self-learning P(win after tax) gate for every strategy book.

Fits a logistic model on closed trades in ticks.db, then refuses new
BUY/SHORT entries unless conservative P(win) is at least
EDGE_TARGET_WINRATE (default 0.70), EV after fees is positive, and Kelly
is positive.

A short per-book warmup (EDGE_WARMUP_MAX, default 8) still allows probes
so a brand-new book can collect labels. After that — including books that
already have a long losing tape — the 70% bar applies immediately.
CLOSE / flatten is never gated.
"""

from __future__ import annotations

import math
import os
import threading
import time
from pathlib import Path
from typing import Any

from control_state import ALL_STRATEGY_NAMES
from quality_filters import (
    expected_value,
    hour_cycle,
    kelly_fraction,
    tick_features,
    wilson_lower,
)

ROOT = Path(__file__).resolve().parent
MODEL_PATH = ROOT / "data" / "models" / "trade_edge.joblib"
_LOCK = threading.Lock()
_LEARNER: "TradeLearner | None" = None

LEARN_STRATEGIES: tuple[str, ...] = ALL_STRATEGY_NAMES
STRAT_INDEX = {name: i for i, name in enumerate(LEARN_STRATEGIES)}


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(float(_env_float(name, float(default))))
    except (TypeError, ValueError):
        return default


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "y"}


def _target_winrate() -> float:
    """70% after-tax win odds, unless EDGE_TARGET_WINRATE is set.

    An old .env with EDGE_MIN_PROBA=0.52 must not undo that bar. Raise the
    alias only when it is already at least 70%. To trade looser, set
    EDGE_TARGET_WINRATE explicitly.
    """
    raw_target = os.getenv("EDGE_TARGET_WINRATE")
    if raw_target is not None and str(raw_target).strip() != "":
        try:
            return max(0.50, min(0.95, float(raw_target)))
        except ValueError:
            return 0.70
    alias = os.getenv("EDGE_MIN_PROBA")
    if alias is not None and str(alias).strip() != "":
        try:
            a = float(alias)
            if a >= 0.70:
                return max(0.50, min(0.95, a))
        except ValueError:
            pass
    return 0.70


def _min_samples() -> int:
    return max(8, _env_int("EDGE_MIN_SAMPLES", 20))


def _warmup_max() -> int:
    return max(0, _env_int("EDGE_WARMUP_MAX", 8))


def _num(row: dict[str, Any], key: str) -> float:
    v = row.get(key, "")
    if v == "" or v is None:
        if key == "pnl_after_tax":
            v = row.get("net_pnl", 0) or 0
        else:
            return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _hour_from_ts(raw: str) -> float:
    s = str(raw or "")
    if "T" in s:
        s = s.split("T", 1)[1]
    parts = s.replace("Z", "").split(":")
    try:
        hh = int(parts[0][:2])
        mm = int(parts[1][:2]) if len(parts) > 1 else 0
        return hh + mm / 60.0
    except (TypeError, ValueError):
        return 12.0


def _weekday_from_ts(raw: str) -> int:
    s = str(raw or "")[:10]
    try:
        from datetime import date

        d = date.fromisoformat(s)
        return int(d.weekday())
    except ValueError:
        return 0


def vectorize(feat: dict[str, Any]) -> list[float]:
    name = str(feat.get("strategy") or "")
    onehot = [0.0] * len(LEARN_STRATEGIES)
    if name in STRAT_INDEX:
        onehot[STRAT_INDEX[name]] = 1.0
    hour = float(feat.get("hour", 12.0) or 12.0)
    if "hour_sin" in feat:
        hs, hc = float(feat["hour_sin"]), float(feat["hour_cos"])
    else:
        hs, hc = hour_cycle(hour)
    return [
        float(feat.get("side", 0.0) or 0.0),
        hs,
        hc,
        float(feat.get("weekday", 0.0) or 0.0),
        math.log1p(abs(float(feat.get("ltp", 0.0) or 0.0))),
        float(feat.get("imb_pct", 0.0) or 0.0) / 100.0,
        *onehot,
    ]


class TradeLearner:
    def __init__(self) -> None:
        self.model: Any = None
        self.n = 0
        self.by_book: dict[str, dict[str, float]] = {}
        self.fitted_at = 0.0
        self.note = "cold"

    def _book_stats(self, trades: list[dict[str, Any]]) -> None:
        self.by_book = {}
        names = list(LEARN_STRATEGIES)
        for t in trades:
            name = str(t.get("strategy") or "")
            if name and name not in names:
                names.append(name)
        for name in names:
            closed = [
                t
                for t in trades
                if t.get("strategy") == name and str(t.get("status", "")).startswith("CLOSED")
            ]
            wins = [t for t in closed if _num(t, "pnl_after_tax") > 0]
            losses = [t for t in closed if _num(t, "pnl_after_tax") < 0]
            n = len(closed)
            n_wins = len(wins)
            p_emp = (n_wins / n) if n else 0.5
            p_laplace = (n_wins + 2.0) / (n + 4.0) if n else 0.5
            p_wilson = wilson_lower(n_wins, n, z=1.0) if n else 0.5
            avg_win = (
                sum(_num(t, "pnl_after_tax") for t in wins) / len(wins) if wins else 0.0
            )
            avg_loss = (
                sum(_num(t, "pnl_after_tax") for t in losses) / len(losses) if losses else 0.0
            )
            ranked = sorted(
                closed,
                key=lambda t: str(t.get("exit_ts") or t.get("entry_ts") or ""),
            )
            streak = 0
            for t in reversed(ranked):
                if _num(t, "pnl_after_tax") < 0:
                    streak += 1
                else:
                    break
            self.by_book[name] = {
                "n": float(n),
                "wins": float(n_wins),
                "p": float(p_emp),
                "p_emp": float(p_emp),
                "laplace": float(p_laplace),
                "wilson": float(p_wilson),
                "avg_win": float(avg_win),
                "avg_loss": float(avg_loss),
                "kelly": float(kelly_fraction(p_emp, avg_win, avg_loss)),
                "loss_streak": float(streak),
            }

    def fit(self, trades: list[dict[str, Any]]) -> None:
        closed = [t for t in trades if str(t.get("status", "")).startswith("CLOSED")]
        self.n = len(closed)
        self._book_stats(trades)
        X: list[list[float]] = []
        y: list[int] = []
        for t in closed:
            feat = tick_features(
                strategy=str(t.get("strategy") or ""),
                side=str(t.get("side") or "BUY"),
                hour_frac=_hour_from_ts(str(t.get("entry_ts") or "")),
                weekday=_weekday_from_ts(str(t.get("entry_ts") or "")),
                ltp=float(t.get("entry_price") or 0) or 0.0,
            )
            X.append(vectorize(feat))
            y.append(1 if _num(t, "pnl_after_tax") > 0 else 0)
        self.model = None
        self.note = f"empirical n={self.n}"
        if len(X) >= _min_samples() and len(set(y)) >= 2:
            try:
                import numpy as np
                from sklearn.linear_model import LogisticRegression
                from sklearn.pipeline import Pipeline
                from sklearn.preprocessing import StandardScaler

                pipe = Pipeline(
                    [
                        ("sc", StandardScaler()),
                        (
                            "lr",
                            # Unweighted so predicted p tracks real win rate,
                            # not a 50/50 rebalance.
                            LogisticRegression(max_iter=400),
                        ),
                    ]
                )
                pipe.fit(np.asarray(X, dtype=float), np.asarray(y, dtype=int))
                self.model = pipe
                self.note = f"logreg n={self.n}"
            except Exception as exc:
                self.note = f"empirical (model skip {type(exc).__name__}) n={self.n}"
        self.fitted_at = time.time()

    def fit_from_db(self, db_path: Path | None = None) -> None:
        from charges import paper_lots
        from storage import DB_PATH, build_trades

        db = db_path or DB_PATH
        rows: list[dict[str, Any]] = []
        try:
            for name in LEARN_STRATEGIES:
                rows.extend(
                    build_trades(
                        strategy=name,
                        db_path=db,
                        signal_limit=1200,
                        lot_size=paper_lots(),
                    )
                )
        except Exception:
            rows = []
        self.fit(rows)

    def maybe_refit(self, *, min_interval_s: float = 120.0) -> None:
        if time.time() - float(self.fitted_at) < min_interval_s:
            return
        self.fit_from_db()

    def predict_p(self, feat: dict[str, Any]) -> float:
        name = str(feat.get("strategy") or "")
        prior = float(self.by_book.get(name, {}).get("p_emp", self.by_book.get(name, {}).get("p", 0.5)))
        if self.model is None:
            return prior
        try:
            import numpy as np

            proba = self.model.predict_proba(np.asarray([vectorize(feat)], dtype=float))[0]
            p_model = float(proba[1]) if len(proba) > 1 else float(proba[-1])
            n = float(self.by_book.get(name, {}).get("n", 0.0))
            w = min(1.0, n / 40.0)
            return w * p_model + (1.0 - w) * prior
        except Exception:
            return prior

    def allow(self, strategy: str, feat: dict[str, Any] | None = None) -> tuple[bool, str]:
        if not _env_flag("EDGE_ML", True):
            return True, "edge_ml_off"
        feat = dict(feat or {})
        feat.setdefault("strategy", strategy)
        stats = self.by_book.get(
            strategy,
            {
                "n": 0.0,
                "wins": 0.0,
                "p": 0.5,
                "p_emp": 0.5,
                "wilson": 0.5,
                "avg_win": 0.0,
                "avg_loss": 0.0,
                "kelly": 0.0,
                "loss_streak": 0.0,
            },
        )
        n = int(stats.get("n") or 0)
        p_emp = float(stats.get("p_emp") or stats.get("p") or 0.5)
        p_blend = self.predict_p(feat)
        target = _target_winrate()
        # Book win rate is the 70% bar. The model can only pull p_gate
        # toward its own estimate once it has enough mixed labels.
        if self.model is None or n < _min_samples():
            p_gate = p_emp if n else 0.50
        else:
            p_gate = 0.5 * p_emp + 0.5 * p_blend
        if n == 0 or n < _warmup_max():
            return True, f"ml_warmup n={n} p={p_gate:.2f}"

        if p_gate < target:
            return False, f"ml_p={p_gate:.2f}<{target:.2f} n={n}"
        avg_win = float(stats.get("avg_win") or 0.0)
        avg_loss = float(stats.get("avg_loss") or 0.0)
        if abs(avg_loss) > 1e-9:
            ev = expected_value(p_gate, avg_win, avg_loss)
            if ev <= 0:
                return False, f"ml_ev={ev:.1f} p={p_gate:.2f} n={n}"
            kel = kelly_fraction(p_gate, avg_win, avg_loss)
            if kel <= 0:
                return False, f"ml_kelly={kel:.2f} p={p_gate:.2f} n={n}"
        else:
            ev = p_gate * avg_win
            kel = 1.0
        streak = int(stats.get("loss_streak") or 0)
        if streak >= 4 and p_gate < max(target, 0.75):
            return False, f"ml_streak={streak} p={p_gate:.2f} n={n}"
        return True, f"ml_p={p_gate:.2f} ev={ev:.1f} k={kel:.2f} n={n}"

    def snapshot(self) -> dict[str, Any]:
        books = {
            k: {ik: round(float(iv), 4) for ik, iv in v.items()}
            for k, v in self.by_book.items()
        }
        target = _target_winrate()
        bits = [self.note, f"target={target:.0%}", f"warmup={_warmup_max()}"]
        for name, st in books.items():
            if float(st.get("n") or 0) <= 0:
                continue
            short = name.split("_")[0]
            bits.append(f"{short} p={st.get('p_emp', st.get('p', 0)):.2f} n={int(st.get('n') or 0)}")
        return {
            "note": self.note,
            "n": self.n,
            "books": books,
            "min_proba": target,
            "target_winrate": target,
            "warmup_max": _warmup_max(),
            "enabled": _env_flag("EDGE_ML", True),
            "line": " · ".join(bits),
        }

    def status_line(self) -> str:
        return (
            f"trade_learner {self.note} books={len(self.by_book)} "
            f"target={_target_winrate():.0%}"
        )


def get_learner() -> TradeLearner:
    global _LEARNER
    with _LOCK:
        if _LEARNER is None:
            _LEARNER = TradeLearner()
        return _LEARNER


def reset_learner() -> TradeLearner:
    global _LEARNER
    with _LOCK:
        _LEARNER = TradeLearner()
        return _LEARNER
