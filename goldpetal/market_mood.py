"""Shared Gold Petal market state.

Desk header does not show live Fit / TRENDING until Enable regime.
That writes MOOD_GATE and FLATTEN_ON_BAD_REGIME. Type RESTART on Engine.
Tape is read on 80 ticks plus 5m…3h, the session day, and a week.
Enable uses that stack to fit books. S13/overnight and S16 1h formula
are never vetoed. Keep DRY_RUN=true.
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
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


@dataclass(frozen=True)
class HorizonSpec:
    key: str
    label: str
    kind: str  # ticks | minutes | day | week
    minutes: int = 0


# Fast 80-tick tape plus the operator rungs, session day, and week.
HORIZON_SPECS: tuple[HorizonSpec, ...] = (
    HorizonSpec("ticks80", "80 ticks", "ticks", 0),
    HorizonSpec("5m", "5 min", "minutes", 5),
    HorizonSpec("15m", "15 min", "minutes", 15),
    HorizonSpec("30m", "30 min", "minutes", 30),
    HorizonSpec("45m", "45 min", "minutes", 45),
    HorizonSpec("1h", "1h", "minutes", 60),
    HorizonSpec("1h15", "1h15", "minutes", 75),
    HorizonSpec("1h30", "1h30", "minutes", 90),
    HorizonSpec("1h45", "1h45", "minutes", 105),
    HorizonSpec("2h", "2h", "minutes", 120),
    HorizonSpec("2h15", "2h15", "minutes", 135),
    HorizonSpec("2h30", "2h30", "minutes", 150),
    HorizonSpec("2h45", "2h45", "minutes", 165),
    HorizonSpec("3h", "3h", "minutes", 180),
    HorizonSpec("day", "day", "day", 1440),
    HorizonSpec("week", "week", "week", 10080),
)
HORIZON_KEYS: tuple[str, ...] = tuple(h.key for h in HORIZON_SPECS)
LAYER_SAMPLES = 80
# Newest N ticks in a window — no COUNT(*) of the week on every /api/mood.
WINDOW_ROW_CAP = 2500
_MOOD_CACHE_SEC = 8.0
_mood_lock = threading.Lock()
_mood_cache: dict[str, tuple[float, dict[str, Any]]] = {}
FAST_HORIZONS: tuple[str, ...] = ("ticks80", "5m", "15m")
STRUCT_HORIZONS: tuple[str, ...] = ("2h", "3h", "day", "week")
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
    clear_mood_cache()
    return out


def clear_mood_cache() -> None:
    """Drop the 8s desk mood snapshot. Enable regime and tests call this."""
    with _mood_lock:
        _mood_cache.clear()


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
    layers: list[dict[str, Any]] = field(default_factory=list)
    alignment: str = "warming"
    structure: str = ""

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
    layers: dict[str, MoodState] | None = None,
    alignment: str = "warming",
    structure_dir: str = "flat",
) -> list[dict[str, Any]]:
    """Strategy Manager: which paper book belongs in this tape. Not an order."""
    side = _preferred_side(direction, mood, regime)
    rows: list[dict[str, Any]] = []
    for name in fit_books():
        rows.append(
            _fit_from_layers(
                name,
                mood=mood,
                regime=regime,
                preferred_side=side,
                layers=layers,
                alignment=alignment,
                structure_dir=structure_dir,
            )
        )
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


def _home_horizons(name: str) -> tuple[str, ...]:
    if name in MOOD_EXEMPT_BOOKS:
        return ("day", "week")
    if name in FORMULA_GATE_BOOKS:
        return ("1h", "1h15", "1h30")
    if name in {"S5_MINEDGE", "S8_NET_ZIGZAG", "FLOW_BRAIN"}:
        return ("ticks80", "5m", "15m")
    if name in {"S18_OHLC_VOL_HTF", "S19_BODY_CLOSE_1H"}:
        return ("1h", "1h15", "1h30", "2h")
    if name == "S20_FADE_HL":
        return ("15m", "30m", "45m", "1h")
    if is_amise_slot(name):
        genome = load_slot_genome(name)
        tf = str(getattr(genome, "timeframe", "") or "").strip().lower() if genome else ""
        if tf in HORIZON_KEYS:
            idx = HORIZON_KEYS.index(tf)
            left = HORIZON_KEYS[max(1, idx - 1)]
            right = HORIZON_KEYS[min(len(HORIZON_KEYS) - 2, idx + 1)]
            return (left, tf, right)
        return ("15m", "1h", "3h")
    return ("15m", "1h", "3h")


def _ready_states(layers: dict[str, MoodState] | None, keys: tuple[str, ...]) -> list[MoodState]:
    if not layers:
        return []
    out: list[MoodState] = []
    for key in keys:
        st = layers.get(key)
        if st is None:
            continue
        if st.n_samples >= 8 and st.mood != "UNKNOWN":
            out.append(st)
    return out


def _majority_dir(states: list[MoodState]) -> str:
    votes = {"up": 0, "down": 0}
    for st in states:
        if st.direction in votes:
            votes[st.direction] += 1
    if votes["up"] > votes["down"] and votes["up"] > 0:
        return "up"
    if votes["down"] > votes["up"] and votes["down"] > 0:
        return "down"
    return "flat"


def _home_state(states: list[MoodState]) -> MoodState | None:
    if not states:
        return None
    burst = [s for s in states if s.mood == "BURST"]
    if burst:
        return burst[0]
    return states[-1]


def _fit_from_layers(
    name: str,
    *,
    mood: Mood,
    regime: Regime,
    preferred_side: str,
    layers: dict[str, MoodState] | None,
    alignment: str,
    structure_dir: str,
) -> dict[str, Any]:
    if name in MOOD_EXEMPT_BOOKS or name in FORMULA_GATE_BOOKS:
        return _fit_one(name, mood=mood, regime=regime, preferred_side=preferred_side)
    homes = _ready_states(layers, _home_horizons(name))
    local = _home_state(homes)
    use_mood = local.mood if local else mood
    use_regime = local.regime if local else regime
    use_dir = local.direction if local else (
        "up" if preferred_side == "long" else ("down" if preferred_side == "short" else "flat")
    )
    use_side = _preferred_side(use_dir, use_mood, use_regime)
    home_dir = _majority_dir(homes) if homes else use_dir
    if (
        name not in OWN_GATE_BOOKS
        and home_dir in {"up", "down"}
        and structure_dir in {"up", "down"}
        and home_dir != structure_dir
    ):
        return {
            "strategy": name,
            "weight": 0.14,
            "stance": "stand_down",
            "preferred_side": "none",
            "why": (
                f"home {home_dir} fights day/week {structure_dir} — wait for alignment"
            ),
        }
    row = _fit_one(name, mood=use_mood, regime=use_regime, preferred_side=use_side)
    if alignment == "aligned" and local is not None:
        row = dict(row)
        row["why"] = f"{row.get('why') or use_regime} · {local.regime} on home TF"
    return row


def classify_samples(
    samples: list[tuple[float, float, float]],
    *,
    gate: bool | None = None,
    flatten: bool | None = None,
    prev_regime: str = "",
    with_fits: bool = True,
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
    fits = (
        book_fits(mood=mood, regime=regime, direction=direction)
        if with_fits
        else []
    )
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


def downsample_samples(
    samples: list[tuple[float, float, float]], n: int = LAYER_SAMPLES
) -> list[tuple[float, float, float]]:
    """Keep first→last shape with ~n points. Used so 3h/day/week are not last-N-ticks."""
    if n <= 0 or len(samples) <= n:
        return list(samples)
    last_i = len(samples) - 1
    out: list[tuple[float, float, float]] = []
    prev = -1
    for k in range(n):
        i = int(round(k * last_i / (n - 1)))
        if i == prev:
            continue
        out.append(samples[i])
        prev = i
    if out[-1] != samples[-1]:
        out.append(samples[-1])
    return out


def _parse_received(raw: str) -> datetime | None:
    t = str(raw or "").strip()
    if not t:
        return None
    try:
        dt = datetime.fromisoformat(t.replace(" ", "T").replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    return dt.astimezone(IST)


def _iso(ts: datetime) -> str:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=IST)
    return ts.astimezone(IST).isoformat(timespec="seconds")


def _session_open(now: datetime) -> datetime:
    raw = (os.getenv("MARKET_OPEN") or "09:00").strip() or "09:00"
    try:
        hh, mm = raw.split(":", 1)
        hour, minute = int(hh), int(mm)
    except ValueError:
        hour, minute = 9, 0
    open_dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if now < open_dt:
        open_dt -= timedelta(days=1)
    while open_dt.weekday() >= 5:
        open_dt -= timedelta(days=1)
    return open_dt


def tape_now(db: Path | None = None) -> datetime:
    from desk_data import resolve_desk_db
    from storage import connect, init_db

    path = db or resolve_desk_db()
    if Path(path).is_file():
        init_db(path)
        with connect(path) as conn:
            row = conn.execute(
                "SELECT received_at FROM ticks ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if row is not None:
            parsed = _parse_received(str(row["received_at"] or ""))
            if parsed is not None:
                return parsed
    return datetime.now(IST)


def _tick_samples_from_rows(rows: list[Any]) -> list[tuple[float, float, float]]:
    return [
        (float(r["ltp"]), float(r["bp"] or 0.0), float(r["sp"] or 0.0))
        for r in rows
    ]


def _tick_mood_samples_on(conn: Any, *, limit: int = 80) -> list[tuple[float, float, float]]:
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
    return _tick_samples_from_rows(rows)


def _tick_mood_window_on(
    conn: Any,
    *,
    since: datetime,
    target: int = LAYER_SAMPLES,
) -> list[tuple[float, float, float]]:
    """Newest WINDOW_ROW_CAP ticks in [since, tape], then downsample. No COUNT(*)."""
    target = max(8, int(target))
    cap = max(target * 12, WINDOW_ROW_CAP)
    rows = list(
        conn.execute(
            """
            SELECT ltp, bp, sp FROM ticks
            WHERE ltp IS NOT NULL AND received_at >= ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (_iso(since), cap),
        )
    )
    if not rows:
        return []
    rows.reverse()
    return downsample_samples(_tick_samples_from_rows(rows), target)


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
        return _tick_mood_samples_on(conn, limit=limit)


def tick_mood_window(
    db: Path | None = None,
    *,
    since: datetime,
    target: int = LAYER_SAMPLES,
) -> list[tuple[float, float, float]]:
    """~target samples from the newest ticks in [since, tape]. Desk-poll cheap."""
    from desk_data import resolve_desk_db
    from storage import connect, init_db

    path = db or resolve_desk_db()
    if not Path(path).is_file():
        return []
    init_db(path)
    with connect(path) as conn:
        return _tick_mood_window_on(conn, since=since, target=target)


def _since_for(spec: HorizonSpec, now: datetime) -> datetime | None:
    if spec.kind == "ticks":
        return None
    if spec.kind == "day":
        return _session_open(now)
    if spec.kind == "week":
        return now - timedelta(days=7)
    return now - timedelta(minutes=max(1, spec.minutes))


def _layer_row(spec: HorizonSpec, st: MoodState) -> dict[str, Any]:
    ready = st.n_samples >= 8 and st.mood != "UNKNOWN"
    return {
        "key": spec.key,
        "label": spec.label,
        "minutes": spec.minutes,
        "mood": st.mood,
        "regime": st.regime,
        "direction": st.direction,
        "n_samples": st.n_samples,
        "ready": ready,
        "text": st.label,
    }


def classify_horizons(
    db: Path | None = None,
    *,
    gate: bool | None = None,
    flatten: bool | None = None,
) -> dict[str, MoodState]:
    from desk_data import resolve_desk_db
    from storage import connect, init_db

    gate_on = mood_gate_on() if gate is None else bool(gate)
    flatten_on = mood_flatten_on() if flatten is None else bool(flatten)
    empty = {spec.key: _empty("need ≥8 ticks") for spec in HORIZON_SPECS}
    path = db or resolve_desk_db()
    if not Path(path).is_file():
        return empty
    init_db(path)
    with connect(path) as conn:
        now = datetime.now(IST)
        row = conn.execute(
            "SELECT received_at FROM ticks ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row is not None:
            parsed = _parse_received(str(row["received_at"] or ""))
            if parsed is not None:
                now = parsed
        out: dict[str, MoodState] = {}
        ticks_raw = _tick_mood_samples_on(conn, limit=LAYER_SAMPLES)
        for spec in HORIZON_SPECS:
            if spec.kind == "ticks":
                raw = ticks_raw
            else:
                since = _since_for(spec, now)
                raw = (
                    _tick_mood_window_on(conn, since=since)
                    if since is not None
                    else []
                )
            out[spec.key] = classify_samples(
                raw, gate=gate_on, flatten=flatten_on, with_fits=False
            )
        return out


def blend_layers(
    layers: dict[str, MoodState],
    *,
    gate: bool | None = None,
    flatten: bool | None = None,
    prev_regime: str = "",
) -> MoodState:
    """80-tick feel + higher-TF structure. Fits follow each book's home rungs."""
    fast = layers.get("ticks80") or _empty("need ≥8 ticks")
    gate_on = mood_gate_on() if gate is None else bool(gate)
    flatten_on = mood_flatten_on() if flatten is None else bool(flatten)
    fast_ready = _ready_states(layers, FAST_HORIZONS)
    struct_ready = _ready_states(layers, STRUCT_HORIZONS)
    fast_dir = _majority_dir(fast_ready) if fast_ready else fast.direction
    struct_dir = _majority_dir(struct_ready)
    if not fast_ready and not struct_ready:
        alignment = "warming"
    elif fast_dir in {"up", "down"} and struct_dir in {"up", "down"} and fast_dir != struct_dir:
        alignment = "fighting"
    elif fast_dir in {"up", "down"} and struct_dir == fast_dir:
        alignment = "aligned"
    elif struct_ready:
        alignment = "mixed"
    else:
        alignment = "fast-only"
    day = layers.get("day")
    week = layers.get("week")
    h1 = layers.get("1h")
    struct_bits = []
    for st in (h1, day, week):
        if st is not None and st.n_samples >= 8 and st.mood != "UNKNOWN":
            struct_bits.append(f"{st.regime} {st.direction}")
    structure = " · ".join(struct_bits) if struct_bits else (fast.regime or "")
    fits = book_fits(
        mood=fast.mood,
        regime=fast.regime,
        direction=fast.direction,
        layers=layers,
        alignment=alignment,
        structure_dir=struct_dir,
    )
    reason = fast.reason
    extra = [alignment]
    if structure:
        extra.append("HTF " + structure)
    reason = fast.reason + " · " + " · ".join(extra)
    layer_rows = [_layer_row(spec, layers.get(spec.key) or _empty()) for spec in HORIZON_SPECS]
    st = MoodState(
        mood=fast.mood,
        direction=fast.direction,
        heat=fast.heat,
        velocity=fast.velocity,
        range_pts=fast.range_pts,
        imb=fast.imb,
        imb_delta=fast.imb_delta,
        allow_long=fast.allow_long,
        allow_short=fast.allow_short,
        flatten_long=fast.flatten_long,
        flatten_short=fast.flatten_short,
        label=fast.label,
        reason=reason,
        n_samples=fast.n_samples,
        gate_on=gate_on,
        flatten_on=flatten_on,
        regime=fast.regime,
        transition=fast.transition or prev_regime,
        direction_score=fast.direction_score,
        trend_strength=fast.trend_strength,
        momentum=fast.momentum,
        volatility=fast.volatility,
        buy_pressure=fast.buy_pressure,
        compression=fast.compression,
        breakout_probability=fast.breakout_probability,
        reversal_probability=fast.reversal_probability,
        confidence=fast.confidence,
        fits=fits,
        layers=layer_rows,
        alignment=alignment,
        structure=structure,
    )
    return st


class MoodDetector:
    """Streaming 80-tick tape + slower 5m…week stack. Desk uses snapshot_mood(db)."""

    def __init__(self, window: int = 80) -> None:
        self.window = max(16, int(window))
        self._buf: Deque[tuple[float, float, float]] = deque(maxlen=self.window)
        self._layer_cache: dict[str, MoodState] = {}
        self.last: MoodState = _empty()

    def _blend_fast(self, fast: MoodState) -> MoodState:
        if not self._layer_cache:
            return fast
        merged = dict(self._layer_cache)
        merged["ticks80"] = fast
        return blend_layers(
            merged,
            gate=fast.gate_on,
            flatten=fast.flatten_on,
            prev_regime=fast.regime,
        )

    def update(self, ltp: float, tbq: float = 0.0, tsq: float = 0.0) -> MoodState:
        prev = self.last.regime
        self._buf.append((float(ltp), float(tbq or 0.0), float(tsq or 0.0)))
        fast = classify_samples(
            list(self._buf),
            prev_regime=prev,
            with_fits=not self._layer_cache,
        )
        self.last = self._blend_fast(fast)
        return self.last

    def refresh_layers(self, db: Path | None = None) -> MoodState:
        """Re-read 5m…week from the tape. Seed + every ~50 ticks. One SQLite pass."""
        fast = (
            classify_samples(list(self._buf), with_fits=True)
            if self._buf
            else _empty()
        )
        try:
            self._layer_cache = classify_horizons(db)
        except Exception:
            self.last = fast
            return self.last
        self.last = self._blend_fast(fast)
        return self.last

    def seed_from_db(self, db: Path | None = None) -> MoodState:
        """Match the desk snapshot so restart catch-up sees the live regime."""
        samples = tick_mood_samples(db, limit=self.window)
        self._buf.clear()
        self._buf.extend(samples)
        return self.refresh_layers(db)


def snapshot_mood(db: Path | None = None, *, limit: int = 240) -> MoodState:
    del limit
    layers = classify_horizons(db)
    return blend_layers(layers)


def _mood_cache_key(db: Path | None, gate_on: bool) -> str:
    try:
        from desk_data import resolve_desk_db

        path = str(Path(db or resolve_desk_db()).resolve())
    except Exception:
        path = str(db or "")
    return f"{path}|{int(gate_on)}"


def mood_desk_payload(db: Path | None = None) -> dict[str, Any]:
    """Desk / AMISE snapshot. Cached ~8s so the 5s poll cannot starve /api/desk + /api/tape."""
    gate_on = mood_gate_on()
    key = _mood_cache_key(db, gate_on)
    now = time.monotonic()
    with _mood_lock:
        hit = _mood_cache.get(key)
        if hit is not None and now - hit[0] < _MOOD_CACHE_SEC:
            return dict(hit[1])
    st = snapshot_mood(db)
    d = st.to_dict()
    d["ok"] = True
    d["ts_ist"] = datetime.now(IST).isoformat(timespec="seconds")
    if st.gate_on:
        d["note"] = (
            "Market regime is ON. Unfit books will not open. "
            "Fit uses 80 ticks + 5m…3h + day + week; a book that fights the day/week stands down. "
            "Held 1h shorts stay until the next hour close unless MOOD_FLATTEN=true. "
            "S13/overnight and S16 1h formula never veto. "
            "Does not ENABLE. Does not change S13/S16 formulas. Keep DRY_RUN=true."
        )
    else:
        d["note"] = (
            "Market regime is OFF. Books trade their formulas. "
            "The 80-tick + 5m…3h + day + week stack still calculates in RAM. "
            "AMISE always uses that stack to invent. Enable only gates your books. "
            "Live labels stay off the GOLD LTP row until Enable regime. "
            "AMISE may still observe the tape. Type RESTART after you toggle. "
            "Does not ENABLE. Keep DRY_RUN=true."
        )
    with _mood_lock:
        _mood_cache[key] = (time.monotonic(), d)
    return dict(d)
