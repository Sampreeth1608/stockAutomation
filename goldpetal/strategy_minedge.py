"""S5: only trade when market can move enough points to beat costs.

Uses rolling expected-move (ATR + session range) + strong depth imbalance.
Enters BUY/SHORT only if expected_points >= required_points
(required = max(MIN_EDGE_POINTS, fee_break_even * safety) when COVER_FEES=true).

Why COVER_FEES matters:
  Angel Gold Petal round-trip fees ≈ ₹50 → break-even ≈ 50 points
  (₹1/point with TURNOVER_MULT=1.0; quote is ₹ per 1 gram).
  MIN_EDGE_POINTS is the *floor*; fee cover raises the bar when enabled.

Exits when:
  - move from entry reaches +expected (target), or
  - adverse move hits stop (~0.45 * expected), or
  - regime / portfolio flattens (handled outside)
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from depth import depth_buy_sell_sums
from edge import PointATR, edge_thresholds_from_env
from strategy import Action, Position, SignalResult


class MinEdgeStrategy:
    name = "S5_MINEDGE"

    def __init__(
        self,
        min_edge_points: float | None = None,
        every_n_ticks: int = 5,
        atr_window: int = 100,
        imbalance_ratio: float = 1.35,
        *,
        name: str | None = None,
        cover_fees: bool | None = None,
        require_reasoning: bool = False,
        reasoning_min_score: float = 0.45,
        reasoning_lots: float = 1.0,
        ml_model_path: str | None = None,
        require_ml: bool = False,
        min_ml_proba: float = 0.55,
    ) -> None:
        thr = edge_thresholds_from_env()
        if name:
            self.name = name
        self.min_edge_points = (
            float(min_edge_points)
            if min_edge_points is not None
            else thr.min_edge_points
        )
        self.cover_fees = thr.cover_fees if cover_fees is None else bool(cover_fees)
        self.safety_mult = thr.safety_mult
        self.fee_break_even = thr.fee_break_even_points
        self.every_n_ticks = max(1, every_n_ticks)
        self.imbalance_ratio = max(1.05, imbalance_ratio)
        self.atr = PointATR(window=atr_window)
        self.position: Position = "flat"
        self.entry_price: float | None = None
        self.target_points: float | None = None
        self.stop_points: float | None = None
        self.last_expected: float | None = None
        self._tick_i = 0
        self.last_skip: str | None = None
        self.require_reasoning = bool(require_reasoning)
        self.reasoning_min_score = float(reasoning_min_score)
        self.reasoning_lots = float(reasoning_lots)
        self.require_ml = bool(require_ml)
        self.min_ml_proba = float(min_ml_proba)
        self.ml_model = None
        self.last_reason: str | None = None
        if ml_model_path:
            try:
                import joblib

                bundle = joblib.load(ml_model_path)
                self.ml_model = bundle.get("model") or bundle
            except Exception:
                self.ml_model = None

    @property
    def required_points(self) -> float:
        if self.cover_fees:
            return max(self.min_edge_points, self.fee_break_even * self.safety_mult)
        return self.min_edge_points

    @property
    def status_line(self) -> str:
        return (
            f"min_user={self.min_edge_points:.0f}pt "
            f"fee_BE={self.fee_break_even:.0f}pt "
            f"required={self.required_points:.0f}pt "
            f"cover_fees={self.cover_fees} "
            f"imb>={self.imbalance_ratio:.2f} "
            f"reason={'ON' if self.require_reasoning else 'off'} "
            f"ml={'ON' if self.ml_model else 'off'} "
            f"pos={self.position}"
        )

    def _bias(self, message: dict[str, Any]) -> tuple[str, float]:
        """Return (bias, imbalance_ratio). ratio = dominant/other side qty."""
        buy, sell, _ = depth_buy_sell_sums(message)
        if buy <= 0 and sell <= 0:
            return "flat", 1.0
        if buy > sell * self.imbalance_ratio:
            return "long", (buy / sell) if sell > 0 else 99.0
        if sell > buy * self.imbalance_ratio:
            return "short", (sell / buy) if buy > 0 else 99.0
        return "flat", (max(buy, sell) / max(1e-9, min(buy, sell)))

    def on_tick(
        self, now: datetime, ltp: float, message: dict[str, Any]
    ) -> SignalResult | None:
        self._tick_i += 1
        expected = self.atr.update(ltp)
        self.last_expected = expected
        if self._tick_i % self.every_n_ticks != 0:
            return None
        if expected is None:
            self.last_skip = "warming_atr"
            return None

        # Refresh fee BE occasionally with live price
        if self._tick_i % 200 == 0:
            self.fee_break_even = edge_thresholds_from_env(ltp).fee_break_even_points

        req = self.required_points
        bias, imb = self._bias(message)

        # --- manage open trade ---
        if self.position != "flat" and self.entry_price is not None:
            move = ltp - self.entry_price
            if self.position == "short":
                move = -move
            tgt = self.target_points or expected
            stop = self.stop_points or (expected * 0.5)
            if move >= tgt:
                side = self.position
                self.position = "flat"
                self.entry_price = None
                return SignalResult(
                    action="CLOSE",
                    position_after="flat",
                    price_delta=move,
                    net=expected,
                    net_delta=req,
                    prev_net_delta=None,
                    reason=f"minedge target hit move={move:.1f}>={tgt:.1f} was_{side}",
                )
            if move <= -stop:
                side = self.position
                self.position = "flat"
                self.entry_price = None
                return SignalResult(
                    action="CLOSE",
                    position_after="flat",
                    price_delta=move,
                    net=expected,
                    net_delta=req,
                    prev_net_delta=None,
                    reason=f"minedge stop hit move={move:.1f}<=-{stop:.1f} was_{side}",
                )
            self.last_skip = f"hold exp={expected:.1f} move={move:.1f}"
            return None

        # --- entries only if enough expected points + clear book bias ---
        if expected < req:
            self.last_skip = f"edge_too_small exp={expected:.1f}<req={req:.1f}"
            return None
        if bias == "flat":
            self.last_skip = f"weak_bias exp={expected:.1f} imb={imb:.2f}"
            return None

        ml_proba = None
        if self.ml_model is not None:
            try:
                import numpy as np

                feat = np.array([[expected, req, imb, float(ltp)]], dtype=float)
                proba = self.ml_model.predict_proba(feat)[0]
                ml_proba = float(proba[1]) if len(proba) > 1 else float(proba[0])
            except Exception:
                ml_proba = None
        if self.require_ml and (ml_proba is None or ml_proba < self.min_ml_proba):
            self.last_skip = f"ml_gate p={ml_proba}"
            return None

        if self.require_reasoning:
            from s5_reasoner import reason_entry

            trace = reason_entry(
                px=float(ltp),
                expected_pts=float(expected),
                required_pts=float(req),
                bias=bias,
                imb_ratio=float(imb),
                imbalance_threshold=self.imbalance_ratio,
                atr_ready=True,
                lots=self.reasoning_lots,
                ml_proba=ml_proba,
                min_ml_proba=self.min_ml_proba,
                min_score=self.reasoning_min_score,
            )
            self.last_reason = trace.line()
            want = "ENTER_LONG" if bias == "long" else "ENTER_SHORT"
            if trace.action != want or trace.score < self.reasoning_min_score:
                self.last_skip = f"reason_skip {trace.line()}"
                return None

        self.entry_price = ltp
        # Target at least required (fee-aware); use expected if larger
        self.target_points = max(req, expected * 0.85)
        self.stop_points = max(self.min_edge_points * 0.4, expected * 0.45)
        if bias == "long":
            self.position = "long"
            action: Action = "BUY"
            reason = (
                f"MINEDGE BUY exp_move={expected:.1f}pt >= required={req:.1f}pt "
                f"imb={imb:.2f} "
                f"(user_min={self.min_edge_points:.0f}, fee_BE={self.fee_break_even:.0f})"
            )
        else:
            self.position = "short"
            action = "SHORT"
            reason = (
                f"MINEDGE SHORT exp_move={expected:.1f}pt >= required={req:.1f}pt "
                f"imb={imb:.2f} "
                f"(user_min={self.min_edge_points:.0f}, fee_BE={self.fee_break_even:.0f})"
            )
        if self.last_reason:
            reason = f"{reason} | {self.last_reason}"
        return SignalResult(
            action=action,
            position_after=self.position,
            price_delta=None,
            net=expected,
            net_delta=req,
            prev_net_delta=None,
            reason=reason,
        )


def minedge_from_env() -> MinEdgeStrategy:
    """S5: fee-aware min edge (COVER_FEES + MIN_EDGE_POINTS)."""
    every = int(os.getenv("S5_EVERY_N_TICKS", "5"))
    window = int(os.getenv("S5_ATR_WINDOW", "120"))
    imb = float(os.getenv("S5_IMBALANCE_RATIO", "1.35"))
    reasoning = os.getenv("S5_REASONING", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "y",
    }
    min_score = float(os.getenv("S5_REASONING_MIN_SCORE", "0.45"))
    lots = float(os.getenv("S5_REASONING_LOTS", "1"))
    ml_path = os.getenv("S5_ML_MODEL_PATH", "").strip() or None
    require_ml = os.getenv("S5_REQUIRE_ML", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "y",
    }
    min_ml = float(os.getenv("S5_ML_MIN_PROBA", "0.55"))
    return MinEdgeStrategy(
        every_n_ticks=every,
        atr_window=window,
        imbalance_ratio=imb,
        require_reasoning=reasoning,
        reasoning_min_score=min_score,
        reasoning_lots=lots,
        ml_model_path=ml_path,
        require_ml=require_ml,
        min_ml_proba=min_ml,
    )


def min30_from_env() -> MinEdgeStrategy:
    """S6: trade when expected move >= S6_MIN_POINTS (default 30). No fee gate."""
    every = int(os.getenv("S6_EVERY_N_TICKS", os.getenv("S5_EVERY_N_TICKS", "5")))
    window = int(os.getenv("S6_ATR_WINDOW", os.getenv("S5_ATR_WINDOW", "120")))
    imb = float(os.getenv("S6_IMBALANCE_RATIO", os.getenv("S5_IMBALANCE_RATIO", "1.35")))
    min_pts = float(os.getenv("S6_MIN_POINTS", "30"))
    return MinEdgeStrategy(
        name="S6_MIN30",
        min_edge_points=min_pts,
        every_n_ticks=every,
        atr_window=window,
        imbalance_ratio=imb,
        cover_fees=False,
    )
