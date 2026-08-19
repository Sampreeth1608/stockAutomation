"""Human trade capture — record what YOU see, then optionally send live.

Manual entries use timing, context, and a skip filter that coded rules
do not have. Press BUY / SHORT / NO TRADE / CLOSE; we store the tape
around the click (1m/3m/5m/15m/1h candles, recent ticks with LTP/TBQ/TSQ,
book, OI, VWAP) and fill 5s–5m outcomes from ticks. Sittings of 30m, 1h,
3h, or the whole day all count. Angel only if live is already armed and
you type YOU — see you_trade. This module never sets DRY_RUN=false.
Learning may paper-ENABLE a mimic after knowledge is good; live still
needs Unlock + LIVE.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from desk_data import resolve_desk_db
from export_full_ticks import _depth_side
from mtf_bars import build_rich_bars, tick_metrics
from storage import connect, init_db, latest_ltp

IST = ZoneInfo("Asia/Kolkata")
ROOT = Path(__file__).resolve().parent
CAPTURE_DIR = ROOT / "data" / "human_capture"
EXAMPLES_PATH = CAPTURE_DIR / "examples.json"
HORIZONS_SEC: tuple[int, ...] = (5, 10, 30, 60, 300)
HORIZON_LABEL = {5: "5s", 10: "10s", 30: "30s", 60: "1m", 300: "5m"}
LOOKBACK_1M = 80
LOOKBACK_TICK_SEC = 120
RECENT_TICK_LIMIT = 16000
TICK_WINDOW = 120
ACTIONS = frozenset({"buy", "short", "no_trade", "close"})
TF_PACK: tuple[tuple[str, int, int], ...] = (
    ("1m", 1, 80),
    ("3m", 3, 40),
    ("5m", 5, 40),
    ("15m", 15, 20),
    ("1h", 60, 16),
)
DEFAULT_MARKET_OPEN = "09:00"
DEFAULT_MARKET_CLOSE = "23:30"
YOU_SESSION_GAP_MIN = 45

_lock = threading.Lock()


def _now() -> datetime:
    return datetime.now(IST)


def _now_iso() -> str:
    return _now().isoformat(timespec="seconds")


def _aware(now: datetime) -> datetime:
    if now.tzinfo is None:
        return now.replace(tzinfo=IST)
    return now.astimezone(IST)


def market_window() -> tuple[str, str]:
    open_s = (os.getenv("MARKET_OPEN") or DEFAULT_MARKET_OPEN).strip() or DEFAULT_MARKET_OPEN
    close_s = (os.getenv("MARKET_CLOSE") or DEFAULT_MARKET_CLOSE).strip() or DEFAULT_MARKET_CLOSE
    return open_s, close_s


def _parse_hhmm(value: str) -> tuple[int, int]:
    parts = str(value or "").strip().split(":")
    if len(parts) != 2:
        return 9, 0
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return 9, 0


def session_status(now: datetime | None = None) -> dict[str, Any]:
    """Same Gold Petal window as the bot: Mon–Fri MARKET_OPEN–MARKET_CLOSE IST."""
    clock = _aware(now or _now())
    open_s, close_s = market_window()
    oh, om = _parse_hhmm(open_s)
    ch, cm = _parse_hhmm(close_s)
    start = clock.replace(hour=oh, minute=om, second=0, microsecond=0)
    end = clock.replace(hour=ch, minute=cm, second=0, microsecond=0)
    weekend = clock.weekday() >= 5
    open_ok = (not weekend) and (start <= clock <= end)
    if weekend:
        label = f"weekend — session is Mon–Fri {open_s}–{close_s} IST"
    elif open_ok:
        label = f"session open {open_s}–{close_s} IST"
    else:
        label = (
            f"market closed — session is Mon–Fri {open_s}–{close_s} IST "
            f"(now {clock.strftime('%a %H:%M')})"
        )
    return {
        "open": open_ok,
        "weekend": weekend,
        "open_hhmm": open_s,
        "close_hhmm": close_s,
        "now_ist": clock.isoformat(timespec="seconds"),
        "label": label,
    }


def is_session_open(now: datetime | None = None) -> bool:
    return bool(session_status(now).get("open"))


def _parse_ts(raw: str) -> datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    return dt.astimezone(IST)


def _empty_store() -> dict[str, Any]:
    return {"updated_at_ist": _now_iso(), "examples": []}


def load_examples(path: Path = EXAMPLES_PATH) -> list[dict[str, Any]]:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(json.dumps(_empty_store(), indent=2), encoding="utf-8")
        return []
    with _lock:
        raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        return list(raw)
    return list(raw.get("examples") or [])


def save_examples(items: list[dict[str, Any]], path: Path = EXAMPLES_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"updated_at_ist": _now_iso(), "examples": items}
    with _lock:
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def signed_imb(buy: float, sell: float) -> float:
    tot = float(buy) + float(sell)
    if tot <= 1e-12:
        return 0.0
    return (float(buy) - float(sell)) / tot


def _bar_vol(b: Any) -> float:
    v = getattr(b, "bar_volume", None)
    if v is None:
        v = getattr(b, "volume", None)
    return float(v or 0.0)


def _bar_oi(b: Any) -> float:
    v = getattr(b, "oi_close", None)
    if v is None:
        v = getattr(b, "oi", None)
    return float(v or 0.0)


def _bar_tbq(b: Any) -> float:
    v = getattr(b, "tbq_close", None)
    if v is None:
        v = getattr(b, "tbq", None)
    return float(v or 0.0)


def _bar_tsq(b: Any) -> float:
    v = getattr(b, "tsq_close", None)
    if v is None:
        v = getattr(b, "tsq", None)
    return float(v or 0.0)


def _book_side(msg: dict[str, Any], side: str) -> list[dict[str, float | None]]:
    out: list[dict[str, float | None]] = []
    for px, qty in _depth_side(msg, side):
        out.append(
            {
                "price": round(float(px), 2) if px is not None else None,
                "qty": round(float(qty), 2) if qty is not None else None,
            }
        )
    return out


def recent_tick_rows(db: Path, *, limit: int = RECENT_TICK_LIMIT) -> list[Any]:
    init_db(db)
    with connect(db) as conn:
        rows = list(
            conn.execute(
                """
                SELECT id, received_at, ltp, volume, bp, sp, raw_json
                FROM ticks
                WHERE ltp IS NOT NULL
                ORDER BY id DESC
                LIMIT ?
                """,
                (int(limit),),
            )
        )
    rows.reverse()
    return rows


def ltp_at_or_after(db: Path, when_iso: str) -> float | None:
    init_db(db)
    with connect(db) as conn:
        row = conn.execute(
            """
            SELECT ltp FROM ticks
            WHERE received_at >= ? AND ltp IS NOT NULL
            ORDER BY id ASC
            LIMIT 1
            """,
            (when_iso,),
        ).fetchone()
    if row is None or row["ltp"] is None:
        return None
    return float(row["ltp"])


def _bar_row(b: Any, prev: Any | None) -> dict[str, Any]:
    rng = float(b.high) - float(b.low)
    body = abs(float(b.close) - float(b.open))
    upper = float(b.high) - max(float(b.open), float(b.close))
    lower = min(float(b.open), float(b.close)) - float(b.low)
    vol = _bar_vol(b)
    prev_vol = _bar_vol(prev) if prev is not None else 0.0
    oi = _bar_oi(b)
    prev_oi = _bar_oi(prev) if prev is not None else 0.0
    return {
        "time": b.time,
        "open": round(float(b.open), 2),
        "high": round(float(b.high), 2),
        "low": round(float(b.low), 2),
        "close": round(float(b.close), 2),
        "volume": round(vol, 2),
        "oi": round(oi, 2),
        "tbq": round(_bar_tbq(b), 2),
        "tsq": round(_bar_tsq(b), 2),
        "n_ticks": int(getattr(b, "n_ticks", 0) or 0),
        "bull": bool(b.close > b.open),
        "bear": bool(b.close < b.open),
        "hh": bool(prev is not None and b.high > prev.high),
        "hl": bool(prev is not None and b.low > prev.low),
        "hc": bool(prev is not None and b.close > prev.close),
        "lh": bool(prev is not None and b.high < prev.high),
        "ll": bool(prev is not None and b.low < prev.low),
        "lc": bool(prev is not None and b.close < prev.close),
        "vol_up": bool(prev is not None and vol > prev_vol > 0),
        "oi_up": bool(prev is not None and oi > prev_oi > 0),
        "body_frac": round(body / rng, 3) if rng > 1e-12 else 0.0,
        "upper_wick_frac": round(upper / rng, 3) if rng > 1e-12 else 0.0,
        "lower_wick_frac": round(lower / rng, 3) if rng > 1e-12 else 0.0,
    }


def _naive_long(row: dict[str, Any] | None) -> bool:
    if not row:
        return False
    return bool(row.get("bull") and row.get("hh") and row.get("hc") and row.get("vol_up"))


def _naive_short(row: dict[str, Any] | None) -> bool:
    if not row:
        return False
    return bool(row.get("bear") and row.get("ll") and row.get("lc") and row.get("vol_up"))


def snapshot_market(
    db: Path | None = None,
    *,
    tick_limit: int = RECENT_TICK_LIMIT,
) -> dict[str, Any]:
    """Market state at click time. Not an order."""
    path = db or resolve_desk_db()
    rows = recent_tick_rows(path, limit=tick_limit)
    now = _now()
    last = rows[-1] if rows else None
    last_m = tick_metrics(last) if last is not None else {}
    ltp = float(last_m["ltp"]) if last_m.get("ltp") is not None else latest_ltp(path)
    last_ts = _parse_ts(str(last["received_at"] or "")) if last is not None else None
    clock = last_ts or now

    cutoff_ticks = clock - timedelta(seconds=LOOKBACK_TICK_SEC)
    ticks_win: list[dict[str, Any]] = []
    ltp_then: float | None = None
    first_id = None
    last_id = None
    for row in rows:
        try:
            rid = int(row["id"])
        except (KeyError, TypeError, ValueError):
            rid = None
        if rid is not None:
            first_id = rid if first_id is None else min(first_id, rid)
            last_id = rid if last_id is None else max(last_id, rid)
        ts = _parse_ts(str(row["received_at"] or ""))
        if ts is None or ts < cutoff_ticks:
            continue
        m = tick_metrics(row)
        px = m.get("ltp")
        if px is None:
            continue
        if ltp_then is None:
            ltp_then = float(px)
        ticks_win.append(
            {
                "id": rid,
                "time": m.get("time"),
                "ltp": round(float(px), 2),
                "tbq": round(float(m.get("tbq") or 0), 2),
                "tsq": round(float(m.get("tsq") or 0), 2),
                "ltq": round(float(m.get("ltq") or 0), 2),
                "oi": round(float(m.get("oi") or 0), 2) if m.get("oi") is not None else None,
                "volume": round(float(m.get("volume") or 0), 2) if m.get("volume") is not None else None,
                "buy5": round(float(m.get("buy5_sum") or 0), 2),
                "sell5": round(float(m.get("sell5_sum") or 0), 2),
            }
        )
    vel = None
    if ltp is not None and ltp_then is not None and LOOKBACK_TICK_SEC:
        vel = (float(ltp) - float(ltp_then)) / float(LOOKBACK_TICK_SEC)

    packed_tf: dict[str, list[dict[str, Any]]] = {}
    last_tf: dict[str, Any] = {}
    for tf_name, minutes, keep in TF_PACK:
        bars = build_rich_bars(rows, tf_name, minutes) if rows else []
        use = bars[-keep:]
        packed = []
        for i, b in enumerate(use):
            prev = use[i - 1] if i else (bars[-keep - 1] if len(bars) > keep else None)
            packed.append(_bar_row(b, prev))
        packed_tf[tf_name] = packed
        last_tf[tf_name] = packed[-1] if packed else None
    packed_1m = packed_tf.get("1m") or []
    last_1m = last_tf.get("1m")
    last_1h = last_tf.get("1h")
    prev_1h = None
    bars_1h_pack = packed_tf.get("1h") or []
    if len(bars_1h_pack) >= 2:
        prev_1h = bars_1h_pack[-2]
    use_1m = packed_1m
    last_tick_full = None
    if last is not None:
        lm = tick_metrics(last)
        last_tick_full = {
            "id": int(last["id"]) if "id" in last.keys() and last["id"] is not None else None,
            "time": lm.get("time"),
            "ltp": round(float(lm["ltp"]), 2) if lm.get("ltp") is not None else None,
            "tbq": round(float(lm.get("tbq") or 0), 2),
            "tsq": round(float(lm.get("tsq") or 0), 2),
            "ltq": round(float(lm.get("ltq") or 0), 2),
            "oi": lm.get("oi"),
            "volume": lm.get("volume"),
            "buy5": round(float(lm.get("buy5_sum") or 0), 2),
            "sell5": round(float(lm.get("sell5_sum") or 0), 2),
            "net": round(float(lm.get("net") or 0), 2),
            "imb_pct": lm.get("imb_pct"),
        }

    session_vwap = None
    if packed_1m:
        tpv = 0.0
        vol = 0.0
        day = str(packed_1m[-1].get("time") or "")[:10]
        for b in packed_1m:
            if str(b.get("time") or "")[:10] != day:
                continue
            typical = (float(b["high"]) + float(b["low"]) + float(b["close"])) / 3.0
            bar_vol = float(b.get("volume") or 0)
            tpv += typical * bar_vol
            vol += bar_vol
        if vol > 1e-12:
            session_vwap = tpv / vol

    tbq = float(last_m.get("tbq") or 0.0)
    tsq = float(last_m.get("tsq") or 0.0)
    buy5 = float(last_m.get("buy5_sum") or 0.0)
    sell5 = float(last_m.get("sell5_sum") or 0.0)
    coded_1h_long = _naive_long(last_1h)
    coded_1h_short = _naive_short(last_1h)
    coded_1m_long = _naive_long(last_1m)
    coded_1m_short = _naive_short(last_1m)

    last_msg: dict[str, Any] = {}
    if last is not None and last["raw_json"]:
        try:
            parsed = json.loads(last["raw_json"])
            if isinstance(parsed, dict):
                last_msg = parsed
        except (TypeError, json.JSONDecodeError):
            last_msg = {}
    bids = _book_side(last_msg, "buy")
    asks = _book_side(last_msg, "sell")
    bid1 = bids[0]["price"] if bids else None
    ask1 = asks[0]["price"] if asks else None
    spread = (
        round(float(ask1) - float(bid1), 2)
        if bid1 is not None and ask1 is not None
        else None
    )

    return {
        "captured_at_ist": _now_iso(),
        "db_path": str(path),
        "n_ticks_loaded": len(rows),
        "ltp": round(float(ltp), 2) if ltp is not None else None,
        "last_tick_at": str(last["received_at"]) if last is not None else "",
        "last_tick": last_tick_full,
        "tape_span": {
            "first_id": first_id,
            "last_id": last_id,
            "n": len(rows),
            "window_sec": LOOKBACK_TICK_SEC,
        },
        "tbq": tbq,
        "tsq": tsq,
        "imb": round(signed_imb(tbq, tsq), 4),
        "buy5": buy5,
        "sell5": sell5,
        "bids": bids,
        "asks": asks,
        "bid1": bid1,
        "ask1": ask1,
        "spread": spread,
        "depth_imb": round(signed_imb(buy5, sell5), 4),
        "oi": last_m.get("oi"),
        "session_volume": last_m.get("volume"),
        "ltp_velocity_30s": round(float(vel), 6) if vel is not None else None,
        "vwap": round(float(session_vwap), 2) if session_vwap is not None else None,
        "vwap_gap": (
            round(float(ltp) - float(session_vwap), 2)
            if ltp is not None and session_vwap is not None
            else None
        ),
        "ticks": ticks_win[-TICK_WINDOW:],
        "ticks_30s": ticks_win[-80:],
        "n_ticks_30s": len(ticks_win),
        "n_ticks_window": len(ticks_win),
        "bars_1m": packed_tf.get("1m") or [],
        "bars_3m": packed_tf.get("3m") or [],
        "bars_5m": packed_tf.get("5m") or [],
        "bars_15m": packed_tf.get("15m") or [],
        "bars_1h": packed_tf.get("1h") or [],
        "last_1m": last_1m,
        "last_3m": last_tf.get("3m"),
        "last_5m": last_tf.get("5m"),
        "last_15m": last_tf.get("15m"),
        "last_1h": last_1h,
        "prev_1h": prev_1h,
        "coded": {
            "rule": "bull + higher high + higher close + volume up (naive; not S16 wick, not S18 day)",
            "h1_long": coded_1h_long,
            "h1_short": coded_1h_short,
            "m1_long": coded_1m_long,
            "m1_short": coded_1m_short,
        },
        "weekday": clock.strftime("%A"),
        "hhmm": clock.strftime("%H:%M"),
    }


def _vs_coded(action: str, coded: dict[str, Any]) -> str:
    if action == "buy":
        if coded.get("h1_long"):
            return "you_buy_rule_also"
        return "you_buy_rule_missed"
    if action == "short":
        if coded.get("h1_short"):
            return "you_short_rule_also"
        return "you_short_rule_missed"
    if action == "close":
        return "you_closed"
    if coded.get("h1_long") or coded.get("h1_short"):
        return "you_skipped_rule_would_take"
    return "you_skipped_rule_quiet"


def _you_session_id(items: list[dict[str, Any]], now: datetime) -> str:
    """Same sitting if the last click was today and within YOU_SESSION_GAP_MIN."""
    clock = _aware(now)
    for ex in items:
        if ex.get("action") == "close":
            continue
        prev = _parse_ts(str(ex.get("created_at_ist") or ex.get("entry_at") or ""))
        if prev is None:
            continue
        gap = (clock - prev).total_seconds()
        if clock.date() == prev.date() and 0 <= gap <= YOU_SESSION_GAP_MIN * 60:
            sid = str(ex.get("you_session_id") or "").strip()
            if sid:
                return sid
        break
    return uuid.uuid4().hex[:8]


def record_human(
    action: str,
    *,
    confidence: int = 3,
    note: str = "",
    db: Path | None = None,
    path: Path = EXAMPLES_PATH,
    snapshot: dict[str, Any] | None = None,
    now: datetime | None = None,
    places_order: bool = False,
    live: bool = False,
) -> dict[str, Any]:
    act = str(action or "").strip().lower().replace("-", "_").replace(" ", "_")
    if act in {"long", "buy_long"}:
        act = "buy"
    if act in {"sell", "short_sell"}:
        act = "short"
    if act in {"skip", "pass", "hold", "no"}:
        act = "no_trade"
    if act in {"flatten", "square", "exit"}:
        act = "close"
    if act not in ACTIONS:
        raise ValueError("action must be buy, short, no_trade, or close")
    sess = session_status(now)
    if not sess["open"] and act != "close":
        raise RuntimeError(
            "market closed — capture only Mon–Fri "
            f"{sess['open_hhmm']}–{sess['close_hhmm']} IST"
        )
    conf = max(1, min(5, int(confidence or 3)))
    snap = snapshot if snapshot is not None else snapshot_market(db)
    ltp = snap.get("ltp")
    if ltp is None:
        raise RuntimeError("no LTP on the tape — start the feed first")
    coded = snap.get("coded") or {}
    clock = _now() if now is None else _aware(now)
    items = load_examples(path)
    example = {
        "id": uuid.uuid4().hex[:10],
        "you_session_id": _you_session_id(items, clock),
        "created_at_ist": clock.isoformat(timespec="seconds"),
        "action": act,
        "confidence": conf,
        "note": str(note or "")[:400],
        "entry_px": float(ltp),
        "entry_at": snap.get("last_tick_at") or (
            _now_iso() if now is None else _aware(now).isoformat(timespec="seconds")
        ),
        "vs_coded": _vs_coded(act, coded),
        "coded": coded,
        "snapshot": snap,
        "session": sess,
        "outcomes": {},
        "settled": False,
        "places_order": bool(places_order),
        "paper": False,
        "live": bool(live),
    }
    items.insert(0, example)
    save_examples(items[:2000], path=path)
    return example


def mark_example_order(
    example_id: str,
    *,
    queued: bool,
    order: dict[str, Any] | None = None,
    path: Path = EXAMPLES_PATH,
) -> None:
    items = load_examples(path)
    for ex in items:
        if str(ex.get("id") or "") != str(example_id):
            continue
        ex["places_order"] = bool(queued)
        ex["live"] = bool(queued)
        if order is not None:
            ex["order"] = order
        break
    save_examples(items, path=path)


def _horizon_iso(entry_at: str, sec: int) -> str | None:
    ts = _parse_ts(entry_at)
    if ts is None:
        return None
    return (ts + timedelta(seconds=int(sec))).isoformat(timespec="seconds")


def _signed_pts(action: str, entry: float, px: float) -> dict[str, float]:
    long_pts = float(px) - float(entry)
    short_pts = float(entry) - float(px)
    if action == "buy":
        taken = long_pts
    elif action == "short":
        taken = short_pts
    else:
        taken = 0.0
    return {
        "ltp": round(float(px), 2),
        "long_pts": round(long_pts, 2),
        "short_pts": round(short_pts, 2),
        "taken_pts": round(taken, 2),
        "taken_inr_100lots": round(taken * 100.0, 2),
    }


def settle_open(
    db: Path | None = None,
    *,
    path: Path = EXAMPLES_PATH,
    now: datetime | None = None,
) -> int:
    """Fill 5s/10s/30s/1m/5m marks from later ticks. Not a fill. Not a trade."""
    db_path = db or resolve_desk_db()
    items = load_examples(path)
    clock = now or _now()
    changed = 0
    for ex in items:
        if ex.get("settled"):
            continue
        entry_at = str(ex.get("entry_at") or "")
        entry_px = float(ex.get("entry_px") or 0)
        action = str(ex.get("action") or "")
        outcomes = dict(ex.get("outcomes") or {})
        start = _parse_ts(entry_at)
        if start is None or entry_px <= 0:
            continue
        dirty = False
        all_done = True
        for sec in HORIZONS_SEC:
            label = HORIZON_LABEL[sec]
            if label in outcomes:
                continue
            due = start + timedelta(seconds=sec)
            if clock < due:
                all_done = False
                continue
            when = _horizon_iso(entry_at, sec)
            px = ltp_at_or_after(db_path, when) if when else None
            if px is None:
                all_done = False
                continue
            outcomes[label] = _signed_pts(action, entry_px, px)
            dirty = True
        if dirty:
            ex["outcomes"] = outcomes
            changed += 1
        if all_done and len(outcomes) >= len(HORIZONS_SEC):
            ex["settled"] = True
            changed += 1
    if changed:
        save_examples(items, path=path)
    return changed


def _win(ex: dict[str, Any], horizon: str = "1m") -> bool | None:
    if ex.get("action") in {"no_trade", "close"}:
        return None
    mark = (ex.get("outcomes") or {}).get(horizon) or {}
    pts = mark.get("taken_pts")
    if pts is None:
        return None
    return float(pts) > 0


def capture_summary(items: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = items if items is not None else load_examples()
    n = len(rows)
    buys = [r for r in rows if r.get("action") == "buy"]
    shorts = [r for r in rows if r.get("action") == "short"]
    skips = [r for r in rows if r.get("action") == "no_trade"]
    closes = [r for r in rows if r.get("action") == "close"]
    taken = buys + shorts

    def wr(group: list[dict[str, Any]], horizon: str) -> float | None:
        marks = [_win(r, horizon) for r in group]
        known = [x for x in marks if x is not None]
        if not known:
            return None
        return sum(1 for x in known if x) / float(len(known))

    vs = {
        "you_buy_rule_also": 0,
        "you_buy_rule_missed": 0,
        "you_short_rule_also": 0,
        "you_short_rule_missed": 0,
        "you_skipped_rule_would_take": 0,
        "you_skipped_rule_quiet": 0,
        "you_closed": 0,
    }
    for r in rows:
        key = str(r.get("vs_coded") or "")
        if key in vs:
            vs[key] += 1
    return {
        "n": n,
        "n_buy": len(buys),
        "n_short": len(shorts),
        "n_no_trade": len(skips),
        "n_close": len(closes),
        "n_taken": len(taken),
        "win_rate_1m": wr(taken, "1m"),
        "win_rate_5m": wr(taken, "5m"),
        "win_rate_30s": wr(taken, "30s"),
        "vs_coded": vs,
        "note": (
            "NO TRADE is the selection filter. Coded 1h rule is naive "
            "HH+HC+volume-up — the thing your brain is usually stricter than. "
            "Tape (LTP/TBQ/TSQ/candles/ticks) is always stored. Angel only if "
            "you type YOU after live is armed. 30m / 1h / 3h / whole-day sittings "
            "all count. This store does not set DRY_RUN=false."
        ),
    }


def example_public(ex: dict[str, Any]) -> dict[str, Any]:
    snap = ex.get("snapshot") or {}
    last_1m = snap.get("last_1m") or {}
    last_1h = snap.get("last_1h") or {}
    return {
        "id": ex.get("id"),
        "you_session_id": ex.get("you_session_id"),
        "created_at_ist": ex.get("created_at_ist"),
        "action": ex.get("action"),
        "confidence": ex.get("confidence"),
        "note": ex.get("note"),
        "entry_px": ex.get("entry_px"),
        "vs_coded": ex.get("vs_coded"),
        "hhmm": snap.get("hhmm"),
        "ltp": snap.get("ltp"),
        "tbq": snap.get("tbq"),
        "tsq": snap.get("tsq"),
        "imb": snap.get("imb"),
        "bid1": snap.get("bid1"),
        "ask1": snap.get("ask1"),
        "spread": snap.get("spread"),
        "oi": snap.get("oi"),
        "vwap_gap": snap.get("vwap_gap"),
        "ltp_velocity_30s": snap.get("ltp_velocity_30s"),
        "last_1m": last_1m,
        "last_5m": snap.get("last_5m") or {},
        "last_1h": last_1h,
        "last_tick": snap.get("last_tick") or {},
        "coded": ex.get("coded"),
        "outcomes": ex.get("outcomes") or {},
        "settled": bool(ex.get("settled")),
        "places_order": bool(ex.get("places_order")),
        "live": bool(ex.get("live")),
        "n_bars_1m": len(snap.get("bars_1m") or []),
        "n_ticks_30s": snap.get("n_ticks_30s") or 0,
        "n_ticks_window": snap.get("n_ticks_window") or snap.get("n_ticks_30s") or 0,
    }


def capture_desk_payload(
    *,
    db: Path | None = None,
    path: Path = EXAMPLES_PATH,
    settle: bool = True,
) -> dict[str, Any]:
    db_path = db or resolve_desk_db()
    if settle:
        try:
            settle_open(db_path, path=path)
        except Exception:
            pass
    items = load_examples(path)
    ltp = latest_ltp(db_path)
    sess = session_status()
    tape_now: dict[str, Any] = {"ltp": ltp, "tbq": None, "tsq": None}
    try:
        from storage import latest_ticks

        rows = latest_ticks(limit=1, db_path=db_path)
        if rows:
            m = tick_metrics(rows[0])
            tape_now = {
                "ltp": m.get("ltp") if m.get("ltp") is not None else ltp,
                "tbq": m.get("tbq"),
                "tsq": m.get("tsq"),
                "oi": m.get("oi"),
                "buy5": m.get("buy5_sum"),
                "sell5": m.get("sell5_sum"),
                "time": m.get("time"),
            }
            if tape_now["ltp"] is not None:
                ltp = tape_now["ltp"]
    except Exception:
        pass
    try:
        from you_trade import you_live_status

        live = you_live_status()
    except Exception as exc:
        live = {"would_place": False, "why": str(exc), "dry_run": True}
    try:
        from you_learn import deploy_status, learn_status, maybe_auto_paper

        learn = learn_status(items)
        deployed = deploy_status()
        if deployed.get("slot"):
            learn["deploy"] = deployed
        if settle and path == EXAMPLES_PATH and learn.get("knowledge_good"):
            try:
                dep = maybe_auto_paper(examples_path=path)
                learn = dict(dep.get("learn") or learn)
                if dep.get("slot") or deployed.get("slot"):
                    learn["deploy"] = {
                        **deployed,
                        "slot": dep.get("slot") or deployed.get("slot"),
                        "deployed": bool(dep.get("deployed")),
                        "already": bool(dep.get("already")),
                        "restart_needed": bool(dep.get("restart_needed")),
                        "note": dep.get("note") or learn.get("note"),
                    }
            except Exception as exc:
                learn["deploy_error"] = str(exc)
    except Exception as exc:
        learn = {"ready": False, "note": str(exc)}
    return {
        "ok": True,
        "ts_ist": _now_iso(),
        "places_order": bool(live.get("would_place")),
        "live_blocked": not bool(live.get("would_place")),
        "ltp": ltp,
        "tape_now": tape_now,
        "db_path": str(db_path),
        "session": sess,
        "live": live,
        "learn": learn,
        "summary": capture_summary(items),
        "recent": [example_public(x) for x in items[:40]],
        "note": (
            "Press BUY / SHORT / NO TRADE / CLOSE while Gold Petal is open "
            f"(Mon–Fri {sess['open_hhmm']}–{sess['close_hhmm']} IST). "
            "Every click stores LTP, TBQ, TSQ, book, OI, 1m–1h candles, and recent ticks. "
            "Sit 30m, 1h, 3h, or the whole day — all count. "
            "Angel only after Paper off + LIVE + Unlock + Restart, then type YOU on the click. "
            "When knowledge is good the mimic papers itself in your hours. Never DRY_RUN=false."
        ),
    }
