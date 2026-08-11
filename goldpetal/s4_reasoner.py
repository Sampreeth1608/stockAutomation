"""Multi-step reasoning for S4 overnight (math → logic → science → planning).

Gates delivery entries near close using day-bias + ML gap prob + fee math.
"""

from __future__ import annotations

from typing import Literal

from s8_reasoner import ReasonStep, ReasoningTrace, fee_be_points

PlanSide = Literal["long", "short"]


def reason_entry(
    *,
    px: float,
    prob_bullish: float,
    bias: str,
    buy_prob: float = 0.58,
    short_prob: float = 0.42,
    in_entry_window: bool = True,
    already_in: bool = False,
    entered_today: bool = False,
    lots: float = 1.0,
    expected_gap_pts: float | None = None,
    ml_proba: float | None = None,
    min_score: float = 0.45,
) -> ReasoningTrace:
    steps: list[ReasonStep] = []
    be = fee_be_points(px, lots)
    gap = float(expected_gap_pts) if expected_gap_pts is not None else abs(prob_bullish - 0.5) * 40.0
    steps.append(
        ReasonStep("mathematics", "fee_be", be > 0, f"overnight BE≈{be:.1f}pt", be)
    )
    steps.append(
        ReasonStep(
            "mathematics",
            "gap_vs_fee",
            gap >= be * 0.5,
            f"expected_gap≈{gap:.1f}pt vs BE={be:.1f}",
            gap,
        )
    )
    steps.append(
        ReasonStep("logic", "flat", not already_in, "no open overnight position")
    )
    steps.append(
        ReasonStep("logic", "not_entered_today", not entered_today, "one entry per day")
    )
    steps.append(
        ReasonStep("logic", "entry_window", in_entry_window, "near market close window")
    )
    bull = bias.upper() == "BULLISH" and prob_bullish >= buy_prob
    bear = bias.upper() == "BEARISH" and prob_bullish <= short_prob
    steps.append(
        ReasonStep(
            "logic",
            "bias_threshold",
            bull or bear,
            f"bias={bias} P={prob_bullish:.3f} buy>={buy_prob} short<={short_prob}",
            prob_bullish,
        )
    )
    if ml_proba is not None:
        steps.append(
            ReasonStep(
                "science",
                "ml_gap",
                (ml_proba >= buy_prob) if bull else (ml_proba <= short_prob) if bear else False,
                f"ML P(gap_up)={ml_proba:.3f}",
                ml_proba,
            )
        )
    else:
        steps.append(
            ReasonStep("science", "heuristic_or_bias", True, "using day-bias / heuristic blend")
        )
    # Planning
    if already_in or entered_today or not in_entry_window:
        action = "SKIP"
    elif bull:
        action = "ENTER_LONG"
    elif bear:
        action = "ENTER_SHORT"
    else:
        action = "WAIT"
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
        summary=f"S4 overnight {action} P={prob_bullish:.3f} bias={bias}",
    )


def reason_hold(
    *,
    side: PlanSide,
    held_overnight: bool,
    next_session: bool,
    open_pnl_pts: float = 0.0,
) -> ReasoningTrace:
    steps = [
        ReasonStep("logic", "in_position", True, f"side={side}"),
        ReasonStep(
            "logic",
            "hold_through_night",
            held_overnight and not next_session,
            "carry until next open exit window",
        ),
        ReasonStep(
            "mathematics",
            "open_pnl",
            True,
            f"open_pnl={open_pnl_pts:+.1f}pt",
            open_pnl_pts,
        ),
    ]
    action = "EXIT" if next_session else "HOLD"
    steps.append(ReasonStep("planning", "plan", True, f"action={action}"))
    score = sum(1 for s in steps if s.ok) / max(len(steps), 1)
    return ReasoningTrace(action=action, score=score, steps=steps, summary=f"S4 {action}")


def reason_exit(
    *,
    side: PlanSide,
    next_session: bool,
    in_exit_window: bool,
    open_pnl_pts: float = 0.0,
) -> ReasoningTrace:
    steps = [
        ReasonStep("logic", "next_session", next_session, "new IST day after entry"),
        ReasonStep("logic", "exit_window", in_exit_window, "minutes after open"),
        ReasonStep(
            "mathematics",
            "realized_proxy",
            True,
            f"open_pnl={open_pnl_pts:+.1f}pt",
            open_pnl_pts,
        ),
    ]
    action = "EXIT" if (next_session and in_exit_window) else "HOLD"
    steps.append(ReasonStep("planning", "plan", True, f"action={action}"))
    score = sum(1 for s in steps if s.ok) / max(len(steps), 1)
    return ReasoningTrace(action=action, score=score, steps=steps, summary=f"S4 {action}")
