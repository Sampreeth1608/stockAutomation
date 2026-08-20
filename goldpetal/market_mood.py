"""Shared Gold Petal market state.

Desk always shows Fit. Market regime is off until you press Enable regime.
That writes MOOD_GATE and FLATTEN_ON_BAD_REGIME. Type RESTART on Engine.
S13/overnight and S16 1h formula are never vetoed. Keep DRY_RUN=true.
"""

from __future__ import annotations

import os
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Deque, Literal
from zoneinfo import ZoneInfo

from amise_slots import amise_books_now, is_amise_slot, load_slot_genome, slot_is_fade

IST = ZoneInfo("Asia/Kolkata")
# Daily/overnight books: show mood, never flatten, skip tick-window entry gate.
MOOD_EXEMPT_BOOKS = frozenset({"S13_HHHL_DAY", "S4_OVERNIGHT", "OVERNIGHT_GAP"})
# Tick books with their own edge/book gates. Mood still prefers a side in
# heat/fall/rise and still stands them down in a burst. Quiet/range/cool
# must not freeze S5/S8 — portfolio already allows them in QUIET/CHOP.
OWN_GATE_BOOKS = frozenset({"S5_MINEDGE", "S8_NET_ZIGZAG"})
# S16 1h close-vs-prev (wick on down close, HH/LL on up close, FLIP). Mood
# Fit is display only — stand-down / prefers-side must not skip a 1h signal.
FORMULA_GATE_BOOKS = frozenset({"S16_HHHL_WICK_1H"})
CORE_FIT_BOOKS: tuple[str, ...] = (
    "S5_MINEDGE",
    "S8_NET_ZIGZAG",
    "S13_HHHL_DAY",
    "S16_HHHL_WICK_1H",
    "S18_OHLC_VOL_HTF",
    "S19_BODY_CLOSE_1H",
    "S20_FADE_HL",
    "FLOW_BRAIN",
    "OVERNIGHT_GAP",
)
FIT_BOOKS: tuple[str, ...] = CORE_FIT_BOOKS + (
    "S21_AMISE",
    "S22_AMISE",
    "S23_AMISE",
    "S24_AMISE",
)


def fit_books() -> tuple[str, ...]:
    return CORE_FIT_BOOKS + amise_books_now()
Mood = Literal[
    "UNKNOWN",
    "QUIET",
    "HEAT",
    "COOL",
    "BURST",
    "FALL_START",
    "RISE_START",
]
Regime = Literal[
    "UNKNOWN",
    "CALM",
    "RANGE",
    "ACCUMULATION",
    "BREAKOUT",
    "BREAKDOWN",
    "TRENDING",
    "EXHAUSTION",
    "COOLDOWN",
    "BURST",
]
Stance = Literal["trade", "stand_down", "hold_swing"]


def mood_gate_on() -> bool:
    return (os.getenv("MOOD_GATE") or "false").strip().lower() in {"1", "true", "yes", "y"}


def set_regime_gate(on: bool, *, path: Path | None = None) -> dict[str, Any]:
    """Desk Enable regime button. Off = formulas trade. On = Fit + TREND/CHOP/WIDE_SPREAD.
    Does not set DRY_RUN=false. Does not Arm live. Type RESTART after this.
    """
    from analytics.env_bridge import write_env_updates

    val = "true" if on else "false"
    res = write_env_updates(
        {"MOOD_GATE": val, "FLATTEN_ON_BAD_REGIME": val},
        path=path,
    )
    if res.get("ok"):
        os.environ["MOOD_GATE"] = val
        os.environ["FLATTEN_ON_BAD_REGIME"] = val
    out = dict(res)
    out["gate_on"] = on if res.get("ok") else mood_gate_on()
    out["note"] = (
        "Market regime ON. Unfit books will not open. Type RESTART on Engine."
        if on
        else "Market regime OFF. Books trade their formulas. Type RESTART on Engine."
    )
    return out


def mood_flatten_on() -> bool:
    return (os.getenv("MOOD_FLATTEN") or "false").strip().lower() in {"1", "true", "yes", "y"}


def mood_fit_min() -> float:
    raw = (os.getenv("MOOD_FIT_MIN") or "0.40").strip()
    try:
        return min(0.95, max(0.05, float(raw)))
    except ValueError:
        return 0.40


def _imb(tbq: float, tsq: float) -> float:
    tot = float(tbq) + float(tsq)
    if tot <= 1e-12:
        return 0.0
    return (float(tbq) - float(tsq)) / tot


def _clip(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, float(x)))


@dataclass
class MoodState:
    mood: Mood
    direction: str  # up | down | flat
    heat: float  # 0–1
    velocity: float
    range_pts: float
    imb: float
    imb_delta: float
    allow_long: bool
    allow_short: bool
    flatten_long: bool
    flatten_short: bool
    label: str
    reason: str
    n_samples: int
    gate_on: bool
    flatten_on: bool
    regime: Regime = "UNKNOWN"
    transition: str = ""
    direction_score: float = 0.0
    trend_strength: float = 0.0
    momentum: float = 0.0
    volatility: float = 0.0
    buy_pressure: float = 0.5
    compression: float = 0.0
    breakout_probability: float = 0.0
    reversal_probability: float = 0.0
    confidence: float = 0.0
    fits: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def fit_for(self, strategy: str) -> dict[str, Any] | None:
        name = str(strategy or "")
        for row in self.fits:
            if row.get("strategy") == name:
                return row
        return None


def _empty(reason: str = "warming_up") -> MoodState:
    gate = mood_gate_on()
    flat = mood_flatten_on()
    st = MoodState(
        mood="UNKNOWN",
        direction="flat",
        heat=0.0,
        velocity=0.0,
        range_pts=0.0,
        imb=0.0,
        imb_delta=0.0,
        allow_long=False,
        allow_short=False,
        flatten_long=False,
        flatten_short=False,
        label="mood warming up",
        reason=reason,
        n_samples=0,
        gate_on=gate,
        flatten_on=flat,
        regime="UNKNOWN",
        transition="warming up",
        confidence=0.0,
        fits=_swing_only_fits("warming up", warming=True),
    )
    return st


def _swing_only_fits(why: str, *, warming: bool = False) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for name in fit_books():
        if name in MOOD_EXEMPT_BOOKS:
            out.append(
                {
                    "strategy": name,
                    "weight": 0.7,
                    "stance": "hold_swing",
                    "preferred_side": "none",
                    "why": "daily swing — tick regime does not dump",
                }
            )
        elif name in FORMULA_GATE_BOOKS:
            out.append(
                {
                    "strategy": name,
                    "weight": 0.82,
                    "stance": "trade",
                    "preferred_side": "none",
                    "why": "1h close vs prev — wick/HHLL FLIP. Mood does not veto",
                }
            )
        elif warming:
            out.append(
                {
                    "strategy": name,
                    "weight": 0.12,
                    "stance": "stand_down",
                    "preferred_side": "none",
                    "why": why,
                }
            )
        else:
            out.append(
                {
                    "strategy": name,
                    "weight": 0.5,
                    "stance": "trade",
                    "preferred_side": "none",
                    "why": why,
                }
            )
    return out


def _regime_for(
    mood: Mood,
    *,
    rng: float,
    d_imb: float,
    first_half: float,
) -> Regime:
    if mood == "BURST":
        return "BURST"
    if mood == "FALL_START":
        return "BREAKDOWN"
    if mood == "RISE_START":
        return "BREAKOUT"
    if mood == "HEAT":
        return "TRENDING"
    if mood == "COOL":
        return "COOLDOWN" if abs(first_half) >= 14.0 else "EXHAUSTION"
    if mood == "QUIET":
        if rng < 6.0 and abs(d_imb) >= 0.06:
            return "ACCUMULATION"
        if rng < 5.0:
            return "CALM"
        return "RANGE"
    return "UNKNOWN"


def _preferred_side(direction: str, mood: Mood, regime: Regime) -> str:
    if mood == "FALL_START" or regime == "BREAKDOWN" or (
        regime == "TRENDING" and direction == "down"
    ):
        return "short"
    if mood == "RISE_START" or regime == "BREAKOUT" or (
        regime == "TRENDING" and direction == "up"
    ):
        return "long"
    return "none"


def book_fits(
    *,
    mood: Mood,
    regime: Regime,
    direction: str,
) -> list[dict[str, Any]]:
    """Strategy Manager: which paper book belongs in this tape. Not an order."""
    side = _preferred_side(direction, mood, regime)
    rows: list[dict[str, Any]] = []
    for name in fit_books():
        rows.append(_fit_one(name, mood=mood, regime=regime, preferred_side=side))
    return rows


def _fit_one(
    name: str,
    *,
    mood: Mood,
    regime: Regime,
    preferred_side: str,
) -> dict[str, Any]:
    if name in MOOD_EXEMPT_BOOKS:
        return {
            "strategy": name,
            "weight": 0.7,
            "stance": "hold_swing",
            "preferred_side": "none",
            "why": (
                "daily / overnight hold — tick regime does not dump or rewrite "
                "S13 or OVERNIGHT_GAP"
            ),
        }
    if name in FORMULA_GATE_BOOKS:
        return {
            "strategy": name,
            "weight": 0.82,
            "stance": "trade",
            "preferred_side": "none",
            "why": (
                "1h close vs prev — down close wick (upper SHORT / lower BUY), "
                "up close HH/LL. Mood does not veto. FLIP"
            ),
        }

    trend_book = name in {
        "S8_NET_ZIGZAG",
        "S18_OHLC_VOL_HTF",
        "S19_BODY_CLOSE_1H",
        "FLOW_BRAIN",
    }
    vol_book = name == "S5_MINEDGE"
    fade_book = name == "S20_FADE_HL"
    amise_book = is_amise_slot(name)
    if amise_book:
        if load_slot_genome(name) is None:
            return {
                "strategy": name,
                "weight": 0.08,
                "stance": "stand_down",
                "preferred_side": "none",
                "why": "empty AMISE slot — waiting for Lab Approve",
            }
        fade_book = slot_is_fade(name)
        if not fade_book:
            trend_book = True

    if regime == "BURST" or mood == "BURST":
        return {
            "strategy": name,
            "weight": 0.10,
            "stance": "stand_down",
            "preferred_side": "none",
            "why": "sudden burst — wait",
        }
    if regime in {"COOLDOWN", "EXHAUSTION"} or mood == "COOL":
        if fade_book:
            return {
                "strategy": name,
                "weight": 0.70,
                "stance": "trade",
                "preferred_side": "none",
                "why": "cooldown — fade may take the mean (S20 stays off until you ENABLE)",
            }
        if name in OWN_GATE_BOOKS:
            return {
                "strategy": name,
                "weight": 0.62,
                "stance": "trade",
                "preferred_side": "none",
                "why": "cooldown — S5 minedge / S8 book still decide",
            }
        return {
            "strategy": name,
            "weight": 0.16,
            "stance": "stand_down",
            "preferred_side": "none",
            "why": "cooldown / exhaustion — do not chase",
        }
    if regime in {"CALM", "RANGE", "ACCUMULATION"} or mood == "QUIET":
        if fade_book:
            return {
                "strategy": name,
                "weight": 0.72,
                "stance": "trade",
                "preferred_side": "none",
                "why": "range — fade book may trade; S20 is not a paper book until you ENABLE",
            }
        if name in OWN_GATE_BOOKS:
            return {
                "strategy": name,
                "weight": 0.70,
                "stance": "trade",
                "preferred_side": "none",
                "why": "quiet/range — S5 minedge / S8 book still decide",
            }
        if vol_book:
            return {
                "strategy": name,
                "weight": 0.22,
                "stance": "stand_down",
                "preferred_side": "none",
                "why": "low profile — min-edge has no move",
            }
        return {
            "strategy": name,
            "weight": 0.24,
            "stance": "stand_down",
            "preferred_side": "none",
            "why": "range / low profile — trend book stands down",
        }
    if fade_book and regime in {"TRENDING", "BREAKOUT", "BREAKDOWN"}:
        return {
            "strategy": name,
            "weight": 0.18,
            "stance": "stand_down",
            "preferred_side": "none",
            "why": "trend / break — fade book stands down",
        }
    if trend_book or vol_book:
        if regime in {"TRENDING", "BREAKOUT", "BREAKDOWN"} or mood in {
            "HEAT",
            "FALL_START",
            "RISE_START",
        }:
            return {
                "strategy": name,
                "weight": 0.86 if trend_book else 0.78,
                "stance": "trade",
                "preferred_side": preferred_side,
                "why": f"{regime.lower()} — {name.split('_')[0]} may trade with the tape",
            }
    return {
        "strategy": name,
        "weight": 0.45,
        "stance": "trade",
        "preferred_side": preferred_side,
        "why": "mixed tape",
    }


def classify_samples(
    samples: list[tuple[float, float, float]],
    *,
    gate: bool | None = None,
    flatten: bool | None = None,
    prev_regime: str = "",
) -> MoodState:
    """samples oldest→newest: (ltp, tbq, tsq). Snapshot classifier. Not an order."""
    gate_on = mood_gate_on() if gate is None else bool(gate)
    flatten_on = mood_flatten_on() if flatten is None else bool(flatten)
    if len(samples) < 8:
        st = _empty("need ≥8 ticks")
        st.n_samples = len(samples)
        st.gate_on = gate_on
        st.flatten_on = flatten_on
        return st

    px = [float(s[0]) for s in samples]
    tbq = [float(s[1]) for s in samples]
    tsq = [float(s[2]) for s in samples]
    n = len(px)
    last = px[-1]
    first = px[0]
    mid = px[n // 2]
    ret = last - first
    second_half = last - mid
    first_half = mid - first
    vel = (px[-1] - px[max(0, n - 8)]) / max(1.0, min(7.0, n - 1))
    window = px[-min(20, n) :]
    rng = max(window) - min(window)
    chunk = 8
    ranges: list[float] = []
    i = 0
    while i + chunk <= n:
        part = px[i : i + chunk]
        ranges.append(max(part) - min(part))
        i += chunk
    earlier = ranges[:-1] if len(ranges) >= 2 else []
    med_earlier = 0.0
    if earlier:
        srt = sorted(earlier)
        med_earlier = srt[len(srt) // 2]
    last_chunk_rng = ranges[-1] if ranges else rng
    burst = last_chunk_rng >= 12.0 and last_chunk_rng >= 2.4 * max(med_earlier, 2.0)

    imb_now = _imb(tbq[-1], tsq[-1])
    imb_then = _imb(tbq[0], tsq[0])
    d_imb = imb_now - imb_then
    tbq_up = tbq[-1] > tbq[max(0, n - 8)]
    tsq_up = tsq[-1] > tsq[max(0, n - 8)]
    falling = ret <= -12.0 and second_half < -4.0 and vel < -0.4
    rising = ret >= 12.0 and second_half > 4.0 and vel > 0.4
    sell_book = d_imb <= -0.04 or (tsq_up and not tbq_up)
    buy_book = d_imb >= 0.04 or (tbq_up and not tsq_up)
    cooling = abs(second_half) < abs(first_half) * 0.35 and abs(first_half) >= 10.0 and abs(vel) < 0.35
    heat = abs(ret) >= 18.0 and abs(vel) >= 0.55 and not cooling

    mood: Mood = "QUIET"
    reason = f"ret={ret:.1f} vel={vel:.2f}/tick rng={rng:.1f} d_imb={d_imb:.3f}"
    if burst:
        mood = "BURST"
        reason = f"burst range {last_chunk_rng:.1f} vs med {med_earlier:.1f} · " + reason
    elif falling and sell_book:
        mood = "FALL_START"
        reason = "price down + sell book · " + reason
    elif rising and buy_book:
        mood = "RISE_START"
        reason = "price up + buy book · " + reason
    elif cooling:
        mood = "COOL"
        reason = "move dying · " + reason
    elif heat:
        mood = "HEAT"
        reason = "sustained move · " + reason
    elif falling:
        mood = "FALL_START"
        reason = "price down (book mixed) · " + reason
    elif rising:
        mood = "RISE_START"
        reason = "price up (book mixed) · " + reason
    else:
        mood = "QUIET"
        reason = "low profile · " + reason

    if ret > 4:
        direction = "up"
    elif ret < -4:
        direction = "down"
    else:
        direction = "flat"
    heat_score = min(1.0, max(0.0, abs(ret) / 40.0 + abs(vel) / 3.0))

    allow_long = True
    allow_short = True
    flatten_long = False
    flatten_short = False
    if mood == "FALL_START" or (mood == "COOL" and direction == "down"):
        allow_long = False
    if mood == "RISE_START" or (mood == "COOL" and direction == "up"):
        allow_short = False
    if mood in {"FALL_START", "COOL"} and direction == "down":
        flatten_long = True
    if mood in {"RISE_START", "COOL"} and direction == "up":
        flatten_short = True
    if mood == "BURST":
        allow_long = False
        allow_short = False
    if mood == "HEAT" and direction == "down":
        allow_long = False
    if mood == "HEAT" and direction == "up":
        allow_short = False

    labels = {
        "UNKNOWN": "mood unknown",
        "QUIET": "low profile — no chase",
        "HEAT": "heat — trend on",
        "COOL": "cooldown — do not chase the last tick",
        "BURST": "sudden burst — wait",
        "FALL_START": "fall starting — no new longs",
        "RISE_START": "rise starting — no new shorts",
    }
    regime = _regime_for(mood, rng=rng, d_imb=d_imb, first_half=first_half)
    prev = str(prev_regime or "").strip().upper()
    if prev and prev not in {"", "UNKNOWN"} and prev != regime:
        transition = f"{prev} → {regime}"
    else:
        transition = regime
    vol = _clip(rng / 30.0)
    compression = _clip(1.0 - rng / 25.0) if not burst else 0.05
    breakout_p = _clip(
        (0.55 if burst or mood in {"RISE_START", "FALL_START"} else 0.2)
        + (0.25 if compression < 0.35 and abs(vel) > 0.4 else 0.0)
    )
    reversal_p = _clip(0.65 if mood == "COOL" else (0.35 if burst else 0.18))
    fits = book_fits(mood=mood, regime=regime, direction=direction)
    return MoodState(
        mood=mood,
        direction=direction,
        heat=round(heat_score, 3),
        velocity=round(vel, 4),
        range_pts=round(rng, 2),
        imb=round(imb_now, 4),
        imb_delta=round(d_imb, 4),
        allow_long=allow_long,
        allow_short=allow_short,
        flatten_long=flatten_long,
        flatten_short=flatten_short,
        label=labels[mood],
        reason=reason,
        n_samples=n,
        gate_on=gate_on,
        flatten_on=flatten_on,
        regime=regime,
        transition=transition,
        direction_score=round(_clip(ret / 40.0, -1.0, 1.0), 3),
        trend_strength=round(heat_score, 3),
        momentum=round(_clip(abs(vel) / 2.0), 3),
        volatility=round(vol, 3),
        buy_pressure=round(_clip((imb_now + 1.0) / 2.0), 3),
        compression=round(compression, 3),
        breakout_probability=round(breakout_p, 3),
        reversal_probability=round(reversal_p, 3),
        confidence=round(_clip(n / 80.0), 3),
        fits=fits,
    )


def mood_blocks_entry(
    state: MoodState, side: str, *, strategy: str = ""
) -> tuple[bool, str]:
    """True = do not open. Off unless Enable regime (MOOD_GATE). S13/S16 skip."""
    if strategy in MOOD_EXEMPT_BOOKS:
        return False, "mood_exempt"
    if strategy in FORMULA_GATE_BOOKS:
        return False, "s16_1h_formula"
    if not state.gate_on:
        return False, "mood_observe"
    act = str(side or "").strip().lower()
    fit = state.fit_for(strategy) if strategy else None
    if fit and fit.get("stance") == "stand_down":
        return True, (
            f"mood={state.mood} {state.regime} {strategy} stand_down "
            f"w={float(fit.get('weight') or 0):.2f}"
        )
    if fit and float(fit.get("weight") or 1.0) < mood_fit_min():
        return True, (
            f"mood={state.mood} {state.regime} {strategy} low_fit "
            f"w={float(fit.get('weight') or 0):.2f}"
        )
    if fit and act in {"buy", "long"} and fit.get("preferred_side") == "short":
        return True, f"mood={state.mood} {state.regime} {strategy} prefers_short"
    if fit and act in {"short", "sell"} and fit.get("preferred_side") == "long":
        return True, f"mood={state.mood} {state.regime} {strategy} prefers_long"
    if act in {"buy", "long"} and not state.allow_long:
        return True, f"mood={state.mood} {state.regime} {strategy} block_long"
    if act in {"short", "sell"} and not state.allow_short:
        return True, f"mood={state.mood} {state.regime} {strategy} block_short"
    return False, "mood_ok"


def mood_wants_flatten(
    state: MoodState, position: str, *, strategy: str = ""
) -> tuple[bool, str]:
    """True = flatten open. Needs Enable regime plus MOOD_FLATTEN. Never S13/S16."""
    if strategy in MOOD_EXEMPT_BOOKS:
        return False, "mood_exempt"
    if strategy in FORMULA_GATE_BOOKS:
        return False, "s16_1h_formula"
    if not state.gate_on or not state.flatten_on:
        return False, "mood_no_flatten"
    pos = str(position or "").strip().lower()
    if pos == "long" and state.flatten_long:
        return True, f"mood={state.mood} {state.regime} flatten_long"
    if pos == "short" and state.flatten_short:
        return True, f"mood={state.mood} {state.regime} flatten_short"
    return False, "mood_hold"


class MoodDetector:
    """Streaming tape → market state. Feed every tick. Desk uses snapshot_mood(db)."""

    def __init__(self, window: int = 80) -> None:
        self.window = max(16, int(window))
        self._buf: Deque[tuple[float, float, float]] = deque(maxlen=self.window)
        self.last: MoodState = _empty()

    def update(self, ltp: float, tbq: float = 0.0, tsq: float = 0.0) -> MoodState:
        prev = self.last.regime
        self._buf.append((float(ltp), float(tbq or 0.0), float(tsq or 0.0)))
        self.last = classify_samples(list(self._buf), prev_regime=prev)
        return self.last

    def seed_from_db(self, db: Path | None = None) -> MoodState:
        """Match the desk snapshot so restart catch-up sees the live regime."""
        samples = tick_mood_samples(db, limit=self.window)
        self._buf.clear()
        self._buf.extend(samples)
        self.last = classify_samples(list(self._buf))
        return self.last


def tick_mood_samples(
    db: Path | None = None, *, limit: int = 80
) -> list[tuple[float, float, float]]:
    from desk_data import resolve_desk_db
    from storage import connect, init_db

    path = db or resolve_desk_db()
    if not Path(path).is_file():
        return []
    init_db(path)
    with connect(path) as conn:
        rows = list(
            conn.execute(
                """
                SELECT ltp, bp, sp FROM ticks
                WHERE ltp IS NOT NULL
                ORDER BY id DESC
                LIMIT ?
                """,
                (max(8, int(limit)),),
            )
        )
    rows.reverse()
    return [
        (float(r["ltp"]), float(r["bp"] or 0.0), float(r["sp"] or 0.0))
        for r in rows
    ]


def snapshot_mood(db: Path | None = None, *, limit: int = 240) -> MoodState:
    samples = tick_mood_samples(db, limit=limit)
    return classify_samples(samples)


def mood_desk_payload(db: Path | None = None) -> dict[str, Any]:
    st = snapshot_mood(db)
    d = st.to_dict()
    d["ok"] = True
    d["ts_ist"] = datetime.now(IST).isoformat(timespec="seconds")
    d["note"] = (
        "One market state for all books, plus a fit per book. "
        "Gate is on: a book that does not fit will not open. "
        "Held 1h shorts stay until the next hour close unless MOOD_FLATTEN=true. "
        "S13/S4 ignore this tick window. "
        "Does not ENABLE. Does not change S13/S16 formulas. "
        "Does not auto-replace a champion. Keep DRY_RUN=true."
    )
    return d
