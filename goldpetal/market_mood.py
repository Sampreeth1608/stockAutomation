"""Shared Gold Petal market mood — one brain in front of the books.

Books are not "wrong." They each see a thin rule (1h close, 30m zigzag, depth).
None of them is the skip filter you use: heat, cooldown, sudden burst, quiet,
fall-start. Copying that into every strategy would make them all trade the
same and all lose together when the mood read is late.

This module classifies the tape once. The desk always shows it. Paper books
do not change until MOOD_GATE=true (default false). Flattening opens needs
MOOD_FLATTEN=true as well. Learning ≠ deploy. Keep DRY_RUN=true.
"""

from __future__ import annotations

import os
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Deque, Literal
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
# Daily/overnight books: show mood, never flatten, skip tick-window entry gate.
MOOD_EXEMPT_BOOKS = frozenset({"S13_HHHL_DAY", "S4_OVERNIGHT"})
Mood = Literal[
    "UNKNOWN",
    "QUIET",
    "HEAT",
    "COOL",
    "BURST",
    "FALL_START",
    "RISE_START",
]


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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def mood_gate_on() -> bool:
    return (os.getenv("MOOD_GATE") or "false").strip().lower() in {"1", "true", "yes", "y"}


def mood_flatten_on() -> bool:
    return (os.getenv("MOOD_FLATTEN") or "false").strip().lower() in {"1", "true", "yes", "y"}


def _imb(tbq: float, tsq: float) -> float:
    tot = float(tbq) + float(tsq)
    if tot <= 1e-12:
        return 0.0
    return (float(tbq) - float(tsq)) / tot


def _empty(reason: str = "warming_up") -> MoodState:
    gate = mood_gate_on()
    flat = mood_flatten_on()
    return MoodState(
        mood="UNKNOWN",
        direction="flat",
        heat=0.0,
        velocity=0.0,
        range_pts=0.0,
        imb=0.0,
        imb_delta=0.0,
        allow_long=True,
        allow_short=True,
        flatten_long=False,
        flatten_short=False,
        label="mood warming up",
        reason=reason,
        n_samples=0,
        gate_on=gate,
        flatten_on=flat,
    )


def classify_samples(
    samples: list[tuple[float, float, float]],
    *,
    gate: bool | None = None,
    flatten: bool | None = None,
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
    # Burst vs earlier rolling ranges of the same length.
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
    if mood in {"FALL_START", "COOL"} and direction == "down":
        allow_long = False
        flatten_long = True
    if mood in {"RISE_START", "COOL"} and direction == "up":
        allow_short = False
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
    )


def mood_blocks_entry(
    state: MoodState, side: str, *, strategy: str = ""
) -> tuple[bool, str]:
    """True = do not open. Ignored unless MOOD_GATE is on. S13/S4 skip."""
    if strategy in MOOD_EXEMPT_BOOKS:
        return False, "mood_exempt"
    if not state.gate_on:
        return False, "mood_observe"
    act = str(side or "").strip().lower()
    if act in {"buy", "long"} and not state.allow_long:
        return True, f"mood={state.mood} block_long"
    if act in {"short", "sell"} and not state.allow_short:
        return True, f"mood={state.mood} block_short"
    return False, "mood_ok"


def mood_wants_flatten(
    state: MoodState, position: str, *, strategy: str = ""
) -> tuple[bool, str]:
    """True = flatten open. Needs MOOD_GATE and MOOD_FLATTEN. Never S13/S4."""
    if strategy in MOOD_EXEMPT_BOOKS:
        return False, "mood_exempt"
    if not state.gate_on or not state.flatten_on:
        return False, "mood_no_flatten"
    pos = str(position or "").strip().lower()
    if pos == "long" and state.flatten_long:
        return True, f"mood={state.mood} flatten_long"
    if pos == "short" and state.flatten_short:
        return True, f"mood={state.mood} flatten_short"
    return False, "mood_hold"


class MoodDetector:
    """Streaming tape → mood. Feed every tick. Desk uses snapshot_mood(db)."""

    def __init__(self, window: int = 80) -> None:
        self.window = max(16, int(window))
        self._buf: Deque[tuple[float, float, float]] = deque(maxlen=self.window)
        self.last: MoodState = _empty()

    def update(self, ltp: float, tbq: float = 0.0, tsq: float = 0.0) -> MoodState:
        self._buf.append((float(ltp), float(tbq or 0.0), float(tsq or 0.0)))
        self.last = classify_samples(list(self._buf))
        return self.last


def snapshot_mood(db: Path | None = None, *, limit: int = 240) -> MoodState:
    from desk_data import resolve_desk_db
    from storage import connect, init_db

    path = db or resolve_desk_db()
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
                (int(limit),),
            )
        )
    rows.reverse()
    samples = [
        (float(r["ltp"]), float(r["bp"] or 0.0), float(r["sp"] or 0.0))
        for r in rows
    ]
    return classify_samples(samples)


def mood_desk_payload(db: Path | None = None) -> dict[str, Any]:
    st = snapshot_mood(db)
    d = st.to_dict()
    d["ok"] = True
    d["ts_ist"] = datetime.now(IST).isoformat(timespec="seconds")
    d["note"] = (
        "One mood for all books. Observe only unless MOOD_GATE=true. "
        "MOOD_FLATTEN=true is required to dump opens. Does not ENABLE. "
        "Does not change S13/S16 formulas. Keep DRY_RUN=true."
    )
    return d
