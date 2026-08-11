"""Multi-step reasoning engine for S8 (math → logic → science/ML → planning).

Modern-model style capabilities, implemented for Gold Petal trading (not a chat LLM):

  Mathematics   fee break-even, R:R, edge expectancy, imb deltas
  Programming   deterministic step graph (this module)
  Logic         boolean gates (IMB rise, NET sign, cooldown, loss-lock)
  Science       empirical NN proba + hold/exit heads (stats)
  Planning      ordered plan: SKIP | ENTER_LONG | ENTER_SHORT | HOLD | EXIT
  Multi-step    each decision emits a ReasoningTrace of named steps

Optional live gate: AlignS8Config.require_reasoning=True (S8_REASONING=true).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

PlanAction = Literal[
    "SKIP",
    "ENTER_LONG",
    "ENTER_SHORT",
    "HOLD",
    "EXIT",
    "WAIT",
]


@dataclass
class ReasonStep:
    domain: str  # mathematics | logic | science | planning | programming
    name: str
    ok: bool
    detail: str
    value: float | None = None


@dataclass
class ReasoningTrace:
    action: PlanAction
    score: float
    steps: list[ReasonStep] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "score": self.score,
            "summary": self.summary,
            "steps": [asdict(s) for s in self.steps],
        }

    def line(self) -> str:
        bits = [f"{s.domain[0].upper()}:{s.name}={'Y' if s.ok else 'N'}" for s in self.steps]
        return f"plan={self.action} score={self.score:.2f} | " + " ".join(bits)


def fee_be_points(ltp: float, lots: float = 100.0) -> float:
    """Rough Angel MCX goldpetal round-trip fee in points (math)."""
    # Align with learn_s8_align.fee_rt / lots → points
    notional = float(ltp) * float(lots)
    # simplified: ~₹50–60 per side-ish → use same structure as fee_rt if available
    try:
        from learn_s8_align import fee_rt

        fee_inr = fee_rt(ltp, lots)
        return float(fee_inr) / max(lots, 1e-9)
    except Exception:
        return 50.0 + notional * 0.0


def math_block(
    *,
    px: float,
    tp: float,
    sl: float,
    lots: float,
    imb: float,
    prev_imb: float,
) -> list[ReasonStep]:
    be = fee_be_points(px, lots)
    rr = (tp / sl) if sl > 0 else 0.0
    # expectancy proxy assuming p_win ~ 0.5 baseline, adjusted later by science
    edge_pts = 0.5 * tp - 0.5 * sl - be
    dimb = imb - prev_imb
    steps = [
        ReasonStep(
            "mathematics",
            "fee_be",
            be > 0,
            f"break-even≈{be:.1f}pt for lots={lots:.0f}",
            be,
        ),
        ReasonStep(
            "mathematics",
            "risk_reward",
            rr >= 1.0,
            f"TP/SL={tp:.0f}/{sl:.0f} R:R={rr:.2f}",
            rr,
        ),
        ReasonStep(
            "mathematics",
            "raw_edge",
            edge_pts > 0,
            f"0.5·TP−0.5·SL−BE={edge_pts:.1f}pt",
            edge_pts,
        ),
        ReasonStep(
            "mathematics",
            "imb_delta",
            dimb > 0,
            f"IMB {prev_imb:.1f}→{imb:.1f} (Δ{dimb:+.1f})",
            dimb,
        ),
    ]
    return steps


def logic_block(
    *,
    side: Literal["long", "short", "flat"],
    net: float,
    imb: float,
    min_imb: float,
    imb_rising: bool,
    require_rising_imb: bool,
    tbq_rising: bool,
    tsq_rising: bool,
    loss_locked: bool,
    in_cooldown: bool,
) -> list[ReasonStep]:
    steps = [
        ReasonStep("logic", "not_loss_locked", not loss_locked, "loss-lock gate"),
        ReasonStep("logic", "not_cooldown", not in_cooldown, "cooldown gate"),
        ReasonStep(
            "logic",
            "imb_floor",
            imb >= min_imb,
            f"imb {imb:.1f}≥{min_imb:.1f}",
            imb,
        ),
    ]
    if require_rising_imb:
        steps.append(
            ReasonStep("logic", "imb_rising", imb_rising, "require rising IMB")
        )
    if side == "long":
        steps.append(ReasonStep("logic", "net_long", net > 0, f"net={net:.0f}>0", net))
        steps.append(
            ReasonStep(
                "logic",
                "book_not_falling",
                True,  # rising book optional at logic layer
                f"tbq_rising={tbq_rising}",
            )
        )
    elif side == "short":
        steps.append(ReasonStep("logic", "net_short", net < 0, f"net={net:.0f}<0", net))
        steps.append(
            ReasonStep("logic", "book_not_falling", True, f"tsq_rising={tsq_rising}")
        )
    else:
        steps.append(ReasonStep("logic", "side_known", False, "side=flat — no entry"))
    return steps


def science_block(
    *,
    entry_proba: float | None,
    hold_proba: float | None,
    exit_soon_proba: float | None,
    min_entry_proba: float,
) -> list[ReasonStep]:
    steps: list[ReasonStep] = []
    if entry_proba is None:
        steps.append(
            ReasonStep(
                "science",
                "ml_entry",
                True,
                "no NN score (rules-only science pass)",
                None,
            )
        )
    else:
        steps.append(
            ReasonStep(
                "science",
                "ml_entry",
                entry_proba >= min_entry_proba,
                f"P(edge)={entry_proba:.2f}≥{min_entry_proba:.2f}",
                entry_proba,
            )
        )
    if hold_proba is not None:
        steps.append(
            ReasonStep(
                "science",
                "ml_hold",
                hold_proba >= 0.45,
                f"P(hold_ok)={hold_proba:.2f}",
                hold_proba,
            )
        )
    if exit_soon_proba is not None:
        steps.append(
            ReasonStep(
                "science",
                "ml_exit_soon",
                exit_soon_proba < 0.55,
                f"P(exit_soon)={exit_soon_proba:.2f} (want low)",
                exit_soon_proba,
            )
        )
    return steps


def plan_from_steps(
    *,
    candidate: Literal["long", "short", "flat"],
    steps: list[ReasonStep],
    entry_proba: float | None,
) -> ReasoningTrace:
    """Planning: combine step votes into ENTER / SKIP / WAIT."""
    hard = [s for s in steps if s.domain in {"logic", "mathematics"} and s.name in {
        "not_loss_locked",
        "not_cooldown",
        "imb_floor",
        "imb_rising",
        "net_long",
        "net_short",
        "side_known",
        "risk_reward",
    }]
    hard_fail = [s for s in hard if not s.ok]
    soft = [s for s in steps if s not in hard]
    soft_ok = sum(1 for s in soft if s.ok)
    soft_n = max(1, len(soft))
    score = soft_ok / soft_n
    if entry_proba is not None:
        score = 0.5 * score + 0.5 * float(entry_proba)

    if hard_fail:
        action: PlanAction = "SKIP"
        summary = "hard gate fail: " + ",".join(s.name for s in hard_fail)
    elif candidate == "flat":
        action = "WAIT"
        summary = "no NET side"
    elif score < 0.45:
        action = "SKIP"
        summary = f"soft score {score:.2f}<0.45"
    elif candidate == "long":
        action = "ENTER_LONG"
        summary = f"multi-step plan ENTER_LONG score={score:.2f}"
    else:
        action = "ENTER_SHORT"
        summary = f"multi-step plan ENTER_SHORT score={score:.2f}"

    steps.append(
        ReasonStep("planning", "decide", action.startswith("ENTER"), summary, score)
    )
    steps.append(
        ReasonStep(
            "programming",
            "trace_ok",
            True,
            f"steps={len(steps)} domains="
            + ",".join(sorted({s.domain for s in steps})),
        )
    )
    return ReasoningTrace(action=action, score=float(score), steps=steps, summary=summary)


def reason_entry(
    *,
    px: float,
    net: float,
    imb: float,
    prev_imb: float,
    tp: float,
    sl: float,
    min_imb: float,
    imb_rising: bool,
    require_rising_imb: bool,
    tbq_rising: bool,
    tsq_rising: bool,
    loss_locked: bool,
    in_cooldown: bool,
    lots: float = 100.0,
    entry_proba: float | None = None,
    hold_proba: float | None = None,
    exit_soon_proba: float | None = None,
    min_entry_proba: float = 0.55,
) -> ReasoningTrace:
    """Full multi-step reasoning for a flat→entry decision."""
    if net > 0:
        candidate: Literal["long", "short", "flat"] = "long"
    elif net < 0:
        candidate = "short"
    else:
        candidate = "flat"

    steps: list[ReasonStep] = []
    steps.extend(
        math_block(
            px=px, tp=tp, sl=sl, lots=lots, imb=imb, prev_imb=prev_imb
        )
    )
    steps.extend(
        logic_block(
            side=candidate,
            net=net,
            imb=imb,
            min_imb=min_imb,
            imb_rising=imb_rising,
            require_rising_imb=require_rising_imb,
            tbq_rising=tbq_rising,
            tsq_rising=tsq_rising,
            loss_locked=loss_locked,
            in_cooldown=in_cooldown,
        )
    )
    steps.extend(
        science_block(
            entry_proba=entry_proba,
            hold_proba=hold_proba,
            exit_soon_proba=exit_soon_proba,
            min_entry_proba=min_entry_proba,
        )
    )
    return plan_from_steps(
        candidate=candidate, steps=steps, entry_proba=entry_proba
    )


def reason_manage(
    *,
    side: Literal["long", "short"],
    move: float,
    tp: float,
    sl: float,
    tbq_falling: bool,
    tsq_falling: bool,
    hold_proba: float | None = None,
    exit_soon_proba: float | None = None,
) -> ReasoningTrace:
    """Multi-step hold/exit reasoning while in a trade."""
    steps: list[ReasonStep] = [
        ReasonStep(
            "mathematics",
            "pnl_path",
            move > 0,
            f"open P&L={move:+.1f}pt TP={tp:.0f} SL={sl:.0f}",
            move,
        ),
        ReasonStep(
            "mathematics",
            "sl_distance",
            move > -sl,
            f"above SL ({move:.1f} > {-sl:.1f})",
            move + sl,
        ),
    ]
    book_bad = tbq_falling if side == "long" else tsq_falling
    steps.append(
        ReasonStep(
            "logic",
            "supporting_book",
            not book_bad,
            "TBQ/TSQ support still intact" if not book_bad else "supporting book dropping",
        )
    )
    if hold_proba is not None:
        steps.append(
            ReasonStep(
                "science",
                "ml_hold",
                hold_proba >= 0.45,
                f"P(hold_ok)={hold_proba:.2f}",
                hold_proba,
            )
        )
    if exit_soon_proba is not None:
        steps.append(
            ReasonStep(
                "science",
                "ml_exit_soon",
                exit_soon_proba >= 0.55,
                f"P(exit_soon)={exit_soon_proba:.2f}",
                exit_soon_proba,
            )
        )

    want_exit = False
    why = []
    if move <= -sl:
        want_exit = True
        why.append("hit_sl")
    if move >= tp:
        want_exit = True
        why.append("hit_tp")
    if book_bad:
        want_exit = True
        why.append("book_drop")
    if exit_soon_proba is not None and exit_soon_proba >= 0.70 and move < tp * 0.5:
        want_exit = True
        why.append("ml_exit_soon")
    if hold_proba is not None and hold_proba < 0.35 and move > 0:
        # weak hold in profit → prefer exit planning later; soft
        why.append("weak_hold")

    action: PlanAction = "EXIT" if want_exit else "HOLD"
    score = 1.0 if want_exit else (hold_proba if hold_proba is not None else 0.6)
    summary = f"{action}: " + (",".join(why) if why else "path_ok")
    steps.append(ReasonStep("planning", "manage", True, summary, float(score)))
    steps.append(
        ReasonStep("programming", "trace_ok", True, f"manage steps={len(steps)}")
    )
    return ReasoningTrace(action=action, score=float(score), steps=steps, summary=summary)
