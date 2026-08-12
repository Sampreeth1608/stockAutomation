"""Multi-step reasoning for S5 min-edge (math → logic → science → planning).

Gates entries when expected move can beat fees and book imbalance is clear.
"""

from __future__ import annotations

from typing import Literal

from s8_reasoner import ReasonStep, ReasoningTrace, fee_be_points

PlanSide = Literal["long", "short"]


def reason_entry(
    *,
    px: float,
    expected_pts: float,
    required_pts: float,
    bias: str,
    imb_ratio: float,
    imbalance_threshold: float = 1.35,
    atr_ready: bool = True,
    lots: float = 1.0,
    ml_proba: float | None = None,
    min_ml_proba: float = 0.55,
    min_score: float = 0.45,
) -> ReasoningTrace:
    steps: list[ReasonStep] = []
    be = fee_be_points(px, lots)
    steps.append(
        ReasonStep("mathematics", "fee_be", be > 0, f"RT BE≈{be:.1f}pt", be)
    )
    steps.append(
        ReasonStep(
            "mathematics",
            "edge_cover",
            expected_pts >= required_pts,
            f"exp={expected_pts:.1f}≥req={required_pts:.1f}",
            expected_pts - required_pts,
        )
    )
    steps.append(
        ReasonStep(
            "mathematics",
            "vs_fee",
            expected_pts >= be,
            f"exp={expected_pts:.1f}≥fee_BE={be:.1f}",
            expected_pts - be,
        )
    )
    steps.append(ReasonStep("logic", "atr_ready", atr_ready, "ATR window warmed"))
    long_ok = bias == "long" and imb_ratio >= imbalance_threshold
    short_ok = bias == "short" and imb_ratio >= imbalance_threshold
    steps.append(
        ReasonStep(
            "logic",
            "book_bias",
            long_ok or short_ok,
            f"bias={bias} imb={imb_ratio:.2f} thr={imbalance_threshold:.2f}",
            imb_ratio,
        )
    )
    if ml_proba is None:
        steps.append(
            ReasonStep("science", "rules_only", True, "no S5 ML head — rules/ATR gate")
        )
    else:
        steps.append(
            ReasonStep(
                "science",
                "ml_edge",
                ml_proba >= min_ml_proba,
                f"P(edge_ok)={ml_proba:.3f}≥{min_ml_proba}",
                ml_proba,
            )
        )
    if not atr_ready or expected_pts < required_pts:
        action = "WAIT"
    elif long_ok:
        action = "ENTER_LONG"
    elif short_ok:
        action = "ENTER_SHORT"
    else:
        action = "SKIP"
    ok_n = sum(1 for s in steps if s.ok)
    score = ok_n / max(len(steps), 1)
    if action.startswith("ENTER") and score < min_score:
        action = "SKIP"
        steps.append(
            ReasonStep("planning", "min_score", False, f"score={score:.2f}<{min_score}")
        )
    else:
        steps.append(
            ReasonStep("planning", "plan", True, f"action={action} score={score:.2f}", score)
        )
    return ReasoningTrace(
        action=action,  # type: ignore[arg-type]
        score=score,
        steps=steps,
        summary=f"S5 minedge {action} exp={expected_pts:.1f} req={required_pts:.1f}",
    )


def reason_hold(
    *,
    side: PlanSide,
    move_pts: float,
    target_pts: float,
    stop_pts: float,
    expected_pts: float,
) -> ReasoningTrace:
    hit_tgt = move_pts >= target_pts
    hit_stop = move_pts <= -stop_pts
    steps = [
        ReasonStep("mathematics", "move", True, f"move={move_pts:+.1f}pt", move_pts),
        ReasonStep(
            "logic",
            "not_target",
            not hit_tgt,
            f"target={target_pts:.1f}",
            target_pts,
        ),
        ReasonStep(
            "logic",
            "not_stop",
            not hit_stop,
            f"stop={stop_pts:.1f}",
            stop_pts,
        ),
        ReasonStep(
            "science",
            "edge_still",
            expected_pts > 0,
            f"exp={expected_pts:.1f}",
            expected_pts,
        ),
    ]
    if hit_tgt or hit_stop:
        action = "EXIT"
    else:
        action = "HOLD"
    steps.append(ReasonStep("planning", "plan", True, f"action={action}"))
    score = sum(1 for s in steps if s.ok) / max(len(steps), 1)
    return ReasoningTrace(action=action, score=score, steps=steps, summary=f"S5 {action}")


def reason_exit(
    *,
    side: PlanSide,
    move_pts: float,
    target_pts: float,
    stop_pts: float,
) -> ReasoningTrace:
    hit_tgt = move_pts >= target_pts
    hit_stop = move_pts <= -stop_pts
    steps = [
        ReasonStep("mathematics", "move", True, f"move={move_pts:+.1f}pt", move_pts),
        ReasonStep("logic", "target", hit_tgt, f">={target_pts:.1f}", target_pts),
        ReasonStep("logic", "stop", hit_stop, f"<=-{stop_pts:.1f}", stop_pts),
    ]
    action = "EXIT" if (hit_tgt or hit_stop) else "HOLD"
    steps.append(ReasonStep("planning", "plan", True, f"action={action}"))
    score = sum(1 for s in steps if s.ok) / max(len(steps), 1)
    return ReasoningTrace(
        action=action,
        score=score,
        steps=steps,
        summary=f"S5 exit check {action} side={side}",
    )
