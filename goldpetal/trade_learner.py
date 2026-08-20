"""Self-learning blotter scores — AMISE invents, it does not skip entries.

Each strategy still has a base and hour/side scores for the Lab. BUY/SHORT
are never blocked here. CLOSE / flatten is never gated.
"""

from __future__ import annotations

import json
import math
import os
import threading
import time
from pathlib import Path
from typing import Any

from control_state import all_strategy_names
from quality_filters import (
    hour_cycle,
    kelly_fraction,
    tick_features,
    wilson_lower,
)

ROOT = Path(__file__).resolve().parent
MODEL_PATH = ROOT / "data" / "models" / "trade_edge.joblib"
RATCHET_PATH = ROOT / "data" / "models" / "edge_ratchet.json"
_LOCK = threading.Lock()
_LEARNER: "TradeLearner | None" = None

LEARN_STRATEGIES: tuple[str, ...] = ()
STRAT_INDEX: dict[str, int] = {}


def refresh_learn_strategies(
    extra: list[str] | tuple[str, ...] | None = None,
) -> tuple[str, ...]:
    """Every paper book, including S25+ after Lab Approve, joins the hour gate."""
    global LEARN_STRATEGIES
    names: list[str] = []
    for n in list(all_strategy_names()) + list(extra or []):
        s = str(n or "").strip()
        if s and s not in names:
            names.append(s)
    LEARN_STRATEGIES = tuple(names)
    STRAT_INDEX.clear()
    STRAT_INDEX.update({name: i for i, name in enumerate(LEARN_STRATEGIES)})
    return LEARN_STRATEGIES


refresh_learn_strategies()


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
    """Stretch goal (default 70%) — used only when some hour/side hits it."""
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


def _min_floor() -> float:
    """Skip this trade when it looks like a coin flip or worse."""
    return max(0.50, min(0.70, _env_float("EDGE_MIN_FLOOR", 0.52)))


def _above_base() -> float:
    """How much better than the book's usual rate this trade must look."""
    return max(0.0, min(0.20, _env_float("EDGE_ABOVE_BASE", 0.08)))


def _min_samples() -> int:
    return max(8, _env_int("EDGE_MIN_SAMPLES", 20))


def _warmup_max() -> int:
    return max(0, _env_int("EDGE_WARMUP_MAX", 8))


def _recent_window() -> int:
    return max(8, _env_int("EDGE_RECENT_WINDOW", 20))


def _need_cap() -> float:
    return max(0.55, min(0.95, _env_float("EDGE_NEED_CAP", 0.92)))


def _bucket_min() -> int:
    return max(3, _env_int("EDGE_BUCKET_MIN", 5))


def _refit_seconds() -> float:
    return max(1.0, _env_float("EDGE_REFIT_S", 8.0))


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


def _side_token(side_val: float) -> str:
    if side_val > 0:
        return "L"
    if side_val < 0:
        return "S"
    return "F"


def _hour_bin(hour: float) -> int:
    try:
        return int(float(hour)) % 24
    except (TypeError, ValueError):
        return 12


def _bucket_id(strategy: str, side_val: float, hour: float) -> str:
    return f"{strategy}|{_side_token(side_val)}|h{_hour_bin(hour)}"


def _hour_from_feat(feat: dict[str, Any]) -> float:
    if feat.get("hour") not in (None, ""):
        try:
            return float(feat["hour"]) % 24.0
        except (TypeError, ValueError):
            pass
    return 12.0


def _side_from_feat(feat: dict[str, Any]) -> float:
    try:
        return float(feat.get("side") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def vectorize(feat: dict[str, Any]) -> list[float]:
    name = str(feat.get("strategy") or "")
    onehot = [0.0] * len(LEARN_STRATEGIES)
    if name in STRAT_INDEX:
        onehot[STRAT_INDEX[name]] = 1.0
    hour = _hour_from_feat(feat)
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


def _closed_ranked(closed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        closed,
        key=lambda t: str(t.get("exit_ts") or t.get("entry_ts") or ""),
    )


class TradeLearner:
    def __init__(self, *, persist: bool = False) -> None:
        self.model: Any = None
        self.n = 0
        self.by_book: dict[str, dict[str, float]] = {}
        self.buckets: dict[str, dict[str, float]] = {}
        self.ratchet: dict[str, float] = {}
        self.fitted_at = 0.0
        self.note = "cold"
        self.persist = persist
        if persist:
            self._load_ratchet()

    def _load_ratchet(self) -> None:
        try:
            if not RATCHET_PATH.is_file():
                return
            raw = json.loads(RATCHET_PATH.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                out: dict[str, float] = {}
                for k, v in raw.items():
                    try:
                        out[str(k)] = float(v)
                    except (TypeError, ValueError):
                        continue
                self.ratchet = out
        except Exception:
            return

    def _save_ratchet(self) -> None:
        if not self.persist:
            return
        try:
            RATCHET_PATH.parent.mkdir(parents=True, exist_ok=True)
            RATCHET_PATH.write_text(
                json.dumps(self.ratchet, indent=0, sort_keys=True),
                encoding="utf-8",
            )
        except Exception:
            return

    def _book_stats(self, trades: list[dict[str, Any]]) -> None:
        self.by_book = {}
        self.buckets = {}
        names = list(LEARN_STRATEGIES)
        for t in trades:
            name = str(t.get("strategy") or "")
            if name and name not in names:
                names.append(name)
        window = _recent_window()
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
            ranked = _closed_ranked(closed)
            streak = 0
            for t in reversed(ranked):
                if _num(t, "pnl_after_tax") < 0:
                    streak += 1
                else:
                    break
            recent = ranked[-window:] if ranked else []
            r_n = len(recent)
            r_wins = sum(1 for t in recent if _num(t, "pnl_after_tax") > 0)
            recent_p = (r_wins / r_n) if r_n else 0.5
            self.by_book[name] = {
                "n": float(n),
                "wins": float(n_wins),
                "p": float(p_emp),
                "p_emp": float(p_emp),
                "base": float(p_laplace),
                "laplace": float(p_laplace),
                "wilson": float(p_wilson),
                "avg_win": float(avg_win),
                "avg_loss": float(avg_loss),
                "kelly": float(kelly_fraction(p_emp, avg_win, avg_loss)),
                "loss_streak": float(streak),
                "recent_n": float(r_n),
                "recent_p": float(recent_p),
            }
            for t in closed:
                side_u = str(t.get("side") or "BUY").upper()
                side_val = 1.0 if side_u in {"BUY", "LONG"} else -1.0
                bid = _bucket_id(name, side_val, _hour_from_ts(str(t.get("entry_ts") or "")))
                b = self.buckets.setdefault(
                    bid,
                    {"n": 0.0, "wins": 0.0, "avg_win": 0.0, "avg_loss": 0.0, "win_sum": 0.0, "loss_sum": 0.0},
                )
                pnl = _num(t, "pnl_after_tax")
                b["n"] += 1.0
                if pnl > 0:
                    b["wins"] += 1.0
                    b["win_sum"] += pnl
                elif pnl < 0:
                    b["loss_sum"] += pnl
            for b in self.buckets.values():
                bn = float(b.get("n") or 0.0)
                bw = float(b.get("wins") or 0.0)
                b["p"] = (bw / bn) if bn else 0.5
                b["avg_win"] = (float(b["win_sum"]) / bw) if bw else 0.0
                bl = bn - bw
                b["avg_loss"] = (float(b["loss_sum"]) / bl) if bl else 0.0
        for name in self.by_book:
            self.ratchet[name] = self.need_p(name)
        self._save_ratchet()

    def _best_bucket_p(self, strategy: str) -> float:
        prefix = f"{strategy}|"
        need_n = _bucket_min()
        best = 0.0
        found = False
        for bid, b in self.buckets.items():
            if not str(bid).startswith(prefix):
                continue
            if int(b.get("n") or 0) < need_n:
                continue
            found = True
            best = max(best, float(b.get("p") or 0.0))
        return best if found else 0.0

    def _base_p(self, strategy: str) -> float:
        st = self.by_book.get(strategy, {})
        return float(st.get("base") or st.get("laplace") or st.get("p") or 0.5)

    def need_p(self, strategy: str) -> float:
        """Flexible bar: beat the base; demand 70% only when a hour actually hits it."""
        floor = _min_floor()
        stretch = _target_winrate()
        base = self._base_p(strategy)
        best = self._best_bucket_p(strategy)
        need = max(floor, min(stretch, base + _above_base()))
        if best >= stretch:
            need = min(best, max(need, stretch))
        elif best >= floor:
            need = min(need, best)
        return max(floor, min(_need_cap(), need))

    def fit(self, trades: list[dict[str, Any]]) -> None:
        extra = [str(t.get("strategy") or "") for t in trades]
        refresh_learn_strategies(extra)
        closed = [t for t in trades if str(t.get("status", "")).startswith("CLOSED")]
        self.n = len(closed)
        self._book_stats(trades)
        X: list[list[float]] = []
        y: list[int] = []
        for t in _closed_ranked(closed):
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

                # Recent closes weigh more so the model tracks the live tape.
                weights = np.exp(np.linspace(-2.0, 0.0, len(X)))
                pipe = Pipeline(
                    [
                        ("sc", StandardScaler()),
                        (
                            "lr",
                            LogisticRegression(max_iter=400),
                        ),
                    ]
                )
                pipe.fit(
                    np.asarray(X, dtype=float),
                    np.asarray(y, dtype=int),
                    lr__sample_weight=weights,
                )
                self.model = pipe
                self.note = f"logreg n={self.n}"
            except Exception as exc:
                self.note = f"empirical (model skip {type(exc).__name__}) n={self.n}"
        self.fitted_at = time.time()

    def fit_from_db(self, db_path: Path | None = None) -> None:
        from charges import paper_lots
        from storage import DB_PATH, build_trades

        db = db_path or DB_PATH
        refresh_learn_strategies()
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

    def maybe_refit(self, *, min_interval_s: float | None = None) -> None:
        wait = _refit_seconds() if min_interval_s is None else float(min_interval_s)
        if time.time() - float(self.fitted_at) < wait:
            return
        self.fit_from_db()

    def on_close(self, strategy: str = "") -> None:
        """Refit after a CLOSE so the next BUY/SHORT uses the new label."""
        _ = strategy
        if self.fitted_at <= 0:
            return
        self.maybe_refit(min_interval_s=_refit_seconds())

    def predict_p(self, feat: dict[str, Any]) -> float:
        name = str(feat.get("strategy") or "")
        prior = float(self.by_book.get(name, {}).get("recent_p", self.by_book.get(name, {}).get("p", 0.5)))
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

    def _setup_p(self, strategy: str, feat: dict[str, Any]) -> tuple[float, dict[str, float]]:
        bid = _bucket_id(strategy, _side_from_feat(feat), _hour_from_feat(feat))
        bucket = self.buckets.get(bid, {})
        b_n = int(bucket.get("n") or 0)
        p_b = float(bucket["p"]) if b_n else None
        p_m = None
        if self.model is not None:
            try:
                import numpy as np

                proba = self.model.predict_proba(np.asarray([vectorize(feat)], dtype=float))[0]
                p_m = float(proba[1]) if len(proba) > 1 else float(proba[-1])
            except Exception:
                p_m = None
        recent = float(self.by_book.get(strategy, {}).get("recent_p") or 0.5)
        need_n = _bucket_min()
        if p_b is not None and b_n >= need_n:
            p_setup = p_b
        elif p_m is not None:
            p_setup = p_m
        else:
            p_setup = recent
        return p_setup, {
            "bucket_n": float(b_n),
            "bucket_p": float(p_b if p_b is not None else -1.0),
            "model_p": float(p_m if p_m is not None else -1.0),
            "recent_p": recent,
        }

    def allow(self, strategy: str, feat: dict[str, Any] | None = None) -> tuple[bool, str]:
        """Always True. AMISE invents challengers; it does not skip a formula."""
        del feat, strategy
        return True, "amise_invent_only"

    def snapshot(self) -> dict[str, Any]:
        books = {
            k: {ik: round(float(iv), 4) for ik, iv in v.items()}
            for k, v in self.by_book.items()
        }
        floor = _min_floor()
        stretch = _target_winrate()
        bits = [
            self.note,
            f"base-gate floor={floor:.0%} stretch={stretch:.0%}",
            f"warmup={_warmup_max()}",
        ]
        for name, st in books.items():
            if float(st.get("n") or 0) <= 0:
                continue
            short = name.split("_")[0]
            need = self.need_p(name)
            bits.append(
                f"{short} base={st.get('base', st.get('p', 0)):.2f} "
                f"need={need:.2f} n={int(st.get('n') or 0)}"
            )
        return {
            "note": self.note,
            "n": self.n,
            "books": books,
            "ratchet": {k: round(float(v), 4) for k, v in self.ratchet.items()},
            "min_proba": floor,
            "target_winrate": stretch,
            "warmup_max": _warmup_max(),
            "enabled": False,
            "line": " · ".join(bits) + " · amise_invent_only",
        }

    def status_line(self) -> str:
        return (
            f"trade_learner {self.note} books={len(self.by_book)} "
            f"floor={_min_floor():.0%} stretch={_target_winrate():.0%}"
        )


def get_learner() -> TradeLearner:
    global _LEARNER
    with _LOCK:
        if _LEARNER is None:
            _LEARNER = TradeLearner(persist=True)
        return _LEARNER


def reset_learner() -> TradeLearner:
    global _LEARNER
    with _LOCK:
        _LEARNER = TradeLearner(persist=False)
        return _LEARNER
