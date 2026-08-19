"""S7 flow brain: live LTP + TBQ + TSQ pressure. New book. Not S16.

Angel TBQ/TSQ are session cumulatives. Pressure is the *change* over a
few seconds (new buy qty vs new sell qty), not whether the totals fall.

Every second the engine classifies:

  bull_cont   price up + buy flow up     → continuation long candidate
  bull_weak   price up + buy flow fading → exhaustion / no new long
  bear_cont   price down + sell flow up  → continuation short candidate
  bear_weak   price down + sell flow fading
  absorb_buy  buy flow up, price stuck
  absorb_sell sell flow up, price stuck
  mixed

Expansion = price response growing AND flow imbalance growing.
Decay     = the opposite.

Trade only continuation+expansion. Flatten on decay / opposite / EOD.
Fill at LTP. Rank after Angel charges, tax excluded.

ENABLE_S7 defaults false. Paper-only. Stay DRY_RUN. Not live.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from backtest_hhhl_candles import Trade, make_charge_cfg
from backtest_wick_candles import _tf_result_from_trades
from charges import ChargeConfig, apply_charges_and_tax

BOOK = "S7_FLOW_BRAIN"
LAB_NAME = BOOK

FORMULA = (
    "Tick LTP + TBQ + TSQ. Pressure = 5s change in cumulative buy/sell qty. "
    "Imbalance = (dTBQ−dTSQ)/(dTBQ+dTSQ). LONG when price up, buy flow up, "
    "and both are expanding. SHORT the mirror. No trade on absorption "
    "(flow without price). Exit on decay or opposite continuation. "
    "Fill at LTP. Flatten session close. Not S16. Not live."
)

BULL_CONT = "bull_cont"
BULL_WEAK = "bull_weak"
BEAR_CONT = "bear_cont"
BEAR_WEAK = "bear_weak"
ABSORB_BUY = "absorb_buy"
ABSORB_SELL = "absorb_sell"
MIXED = "mixed"

Scale = Literal["none", "small", "medium", "large", "extreme"]
Want = Literal["long", "short"]


def move_scale(pts: float, atr: float) -> Scale:
    a = float(atr) if float(atr) > 1e-9 else 10.0
    r = abs(float(pts)) / a
    if r < 0.10:
        return "none"
    if r < 0.30:
        return "small"
    if r < 0.70:
        return "medium"
    if r < 1.50:
        return "large"
    return "extreme"


def flow_imbalance(d_tbq: float, d_tsq: float) -> float:
    den = abs(d_tbq) + abs(d_tsq)
    if den <= 1e-9:
        return 0.0
    return (d_tbq - d_tsq) / den


@dataclass
class FlowState:
    t: float
    ltp: float
    tbq: float
    tsq: float
    d_ltp_5s: float
    d_tbq_5s: float
    d_tsq_5s: float
    d_ltp_10s: float
    flow_imb_5s: float
    flow_imb_10s: float
    imb_vel: float
    price_vel: float
    atr: float
    state: str
    expanding: bool
    decaying: bool
    scale: Scale
    want: Want | None
    why: str


class FlowBrain:
    """Rolling tick windows → pressure state + want."""

    def __init__(
        self,
        *,
        keep_s: float = 180.0,
        decide_every_s: float = 1.0,
        min_flow_imb: float = 0.12,
        min_price_pts: float = 1.0,
        absorb_atr_frac: float = 0.03,
        atr_bars: int = 20,
    ) -> None:
        self.keep_s = float(keep_s)
        self.decide_every_s = float(decide_every_s)
        self.min_flow_imb = float(min_flow_imb)
        self.min_price_pts = float(min_price_pts)
        self.absorb_atr_frac = float(absorb_atr_frac)
        self.atr_bars = max(5, int(atr_bars))
        self.buf: deque[tuple[float, float, float, float]] = deque()
        self._last_decide = -1e18
        self._minute_key: int | None = None
        self._min_h: float | None = None
        self._min_l: float | None = None
        self._prev_close: float | None = None
        self.tr: deque[float] = deque(maxlen=self.atr_bars)
        self.last: FlowState | None = None

    def _atr(self) -> float:
        if not self.tr:
            return 0.0
        return sum(self.tr) / len(self.tr)

    def _roll_minute(self, t: float, ltp: float) -> None:
        key = int(t // 60)
        if self._minute_key is None:
            self._minute_key = key
            self._min_h = self._min_l = ltp
            return
        if key == self._minute_key:
            assert self._min_h is not None and self._min_l is not None
            self._min_h = max(self._min_h, ltp)
            self._min_l = min(self._min_l, ltp)
            return
        if self._min_h is not None and self._min_l is not None:
            hi, lo = self._min_h, self._min_l
            prev = self._prev_close if self._prev_close is not None else hi
            tr = max(hi - lo, abs(hi - prev), abs(lo - prev))
            if tr > 0:
                self.tr.append(tr)
            self._prev_close = ltp
        self._minute_key = key
        self._min_h = self._min_l = ltp

    def _at(self, t_target: float) -> tuple[float, float, float, float] | None:
        found: tuple[float, float, float, float] | None = None
        for row in self.buf:
            if row[0] <= t_target:
                found = row
            else:
                break
        return found

    def _window(self, now: tuple[float, float, float, float], dt: float) -> tuple[float, float, float]:
        past = self._at(now[0] - dt)
        if past is None:
            return 0.0, 0.0, 0.0
        return now[1] - past[1], now[2] - past[2], now[3] - past[3]

    def push(self, t: float, ltp: float, tbq: float, tsq: float) -> FlowState | None:
        if self.buf:
            _pt, _pl, pb, ps = self.buf[-1]
            if tbq + tsq + 1 < 0.45 * (pb + ps) and (pb + ps) > 1000:
                self.buf.clear()
                self.tr.clear()
                self._minute_key = None
                self._prev_close = None
        self.buf.append((t, ltp, tbq, tsq))
        cut = t - self.keep_s
        while self.buf and self.buf[0][0] < cut:
            self.buf.popleft()
        self._roll_minute(t, ltp)
        if t - self._last_decide < self.decide_every_s:
            return None
        if len(self.buf) < 8:
            return None
        self._last_decide = t
        now = self.buf[-1]
        d_ltp_5, d_tbq_5, d_tsq_5 = self._window(now, 5.0)
        d_ltp_10, d_tbq_10, d_tsq_10 = self._window(now, 10.0)
        imb5 = flow_imbalance(d_tbq_5, d_tsq_5)
        imb10 = flow_imbalance(d_tbq_10, d_tsq_10)
        imb_vel = imb5 - imb10
        price_vel = d_ltp_5
        atr = self._atr()
        min_px = max(self.min_price_pts, 0.05 * atr if atr > 0 else self.min_price_pts)
        absorb_px = max(0.5, self.absorb_atr_frac * atr) if atr > 0 else 1.0
        buy_up = d_tbq_5 > 0 and imb5 >= self.min_flow_imb
        sell_up = d_tsq_5 > 0 and imb5 <= -self.min_flow_imb
        px_up = d_ltp_5 >= min_px
        px_dn = d_ltp_5 <= -min_px
        px_flat = abs(d_ltp_5) < absorb_px
        strong_flow = abs(imb5) >= self.min_flow_imb and (abs(d_tbq_5) + abs(d_tsq_5)) > 0

        if px_flat and buy_up and strong_flow:
            st = ABSORB_BUY
        elif px_flat and sell_up and strong_flow:
            st = ABSORB_SELL
        elif px_up and buy_up:
            st = BULL_CONT
        elif px_up and not buy_up:
            st = BULL_WEAK
        elif px_dn and sell_up:
            st = BEAR_CONT
        elif px_dn and not sell_up:
            st = BEAR_WEAK
        else:
            st = MIXED

        expanding = abs(d_ltp_5) > abs(d_ltp_10) * 0.55 and abs(imb5) > abs(imb10)
        decaying = (st in {BULL_WEAK, BEAR_WEAK}) or (imb5 * d_ltp_5 < 0)
        scale = move_scale(d_ltp_10, atr if atr > 0 else 10.0)
        want: Want | None = None
        why = st
        if st == BULL_CONT and expanding and imb5 > 0:
            want = "long"
            why = "bull_cont+expand"
        elif st == BEAR_CONT and expanding and imb5 < 0:
            want = "short"
            why = "bear_cont+expand"
        elif st in {ABSORB_BUY, ABSORB_SELL}:
            why = st
        snap = FlowState(
            t=t,
            ltp=ltp,
            tbq=tbq,
            tsq=tsq,
            d_ltp_5s=d_ltp_5,
            d_tbq_5s=d_tbq_5,
            d_tsq_5s=d_tsq_5,
            d_ltp_10s=d_ltp_10,
            flow_imb_5s=imb5,
            flow_imb_10s=imb10,
            imb_vel=imb_vel,
            price_vel=price_vel,
            atr=atr,
            state=st,
            expanding=expanding,
            decaying=decaying,
            scale=scale,
            want=want,
            why=why,
        )
        self.last = snap
        return snap


def classify_message(brain: FlowBrain, now: datetime, ltp: float, message: dict[str, Any]) -> FlowState | None:
    tbq = float(message.get("total_buy_quantity") or 0.0)
    tsq = float(message.get("total_sell_quantity") or 0.0)
    t = now.timestamp()
    return brain.push(t, float(ltp), tbq, tsq)


def _close_px(
    *,
    tf: str,
    side: str,
    entry_time: str,
    entry_px: float,
    exit_time: str,
    exit_px: float,
    cfg: ChargeConfig,
) -> Trade:
    if side == "LONG":
        pts = exit_px - entry_px
        order_side = "BUY"
    else:
        pts = entry_px - exit_px
        order_side = "SELL"
    settled = apply_charges_and_tax(
        pts,
        cfg,
        side=order_side,
        entry_price=entry_px,
        exit_price=exit_px,
    )
    return Trade(
        tf=tf,
        side=side,
        entry_time=entry_time,
        entry_px=entry_px,
        exit_time=exit_time,
        exit_px=exit_px,
        gross_pts=float(pts) * float(cfg.lot_size),
        gross_pnl_inr=float(settled["gross_pnl"]),
        after_tax_pnl_inr=float(settled["pnl_after_tax"]),
        fees_inr=float(settled["charges"]),
        lots=float(cfg.lot_size),
    )


def _in_session(dt: datetime, market_open: str, market_close: str) -> bool:
    if dt.weekday() >= 5:
        return False
    hhmm = dt.strftime("%H:%M")
    return market_open <= hhmm <= market_close


def simulate_flow_brain(
    samples: list[tuple[datetime, float, float, float]],
    *,
    tf: str = "tick:s7_flow",
    lots: float = 100.0,
    fees: bool = True,
    session_filter: bool = True,
    market_open: str = "09:00",
    market_close: str = "23:30",
    charge_cfg: ChargeConfig | None = None,
    min_hold_s: float = 20.0,
    cooldown_s: float = 15.0,
    brain: FlowBrain | None = None,
) -> Any:
    """Walk ticks. Fill at LTP. Flatten at session end / last sample."""
    cfg = charge_cfg or make_charge_cfg(fees=fees, lots=lots)
    eng = brain or FlowBrain()
    trades: list[Trade] = []
    side: str | None = None
    entry_px = 0.0
    entry_time = ""
    entry_t = 0.0
    last_exit_t = -1e18
    last_dt: datetime | None = None
    last_px = 0.0
    n_decisions = 0

    def close_now(dt: datetime, px: float) -> None:
        nonlocal side, entry_px, entry_time, entry_t, last_exit_t
        assert side is not None
        trades.append(
            _close_px(
                tf=tf,
                side=side,
                entry_time=entry_time,
                entry_px=entry_px,
                exit_time=dt.strftime("%Y-%m-%d %H:%M:%S"),
                exit_px=px,
                cfg=cfg,
            )
        )
        last_exit_t = dt.timestamp()
        side = None

    for dt, ltp, tbq, tsq in samples:
        last_dt, last_px = dt, float(ltp)
        sess_ok = (not session_filter) or _in_session(dt, market_open, market_close)
        if side is not None and session_filter and not sess_ok:
            close_now(dt, float(ltp))
            eng.buf.clear()
            continue
        if session_filter and not sess_ok:
            continue
        snap = eng.push(dt.timestamp(), float(ltp), float(tbq), float(tsq))
        if snap is None:
            continue
        n_decisions += 1
        t = snap.t
        want = snap.want
        if side == "LONG":
            held = t - entry_t
            kill = snap.state in {BEAR_CONT, ABSORB_BUY} or (
                snap.decaying and snap.state != BULL_CONT
            )
            if want == "short" and held >= min_hold_s:
                close_now(dt, snap.ltp)
                side = "SHORT"
                entry_px = snap.ltp
                entry_time = dt.strftime("%Y-%m-%d %H:%M:%S")
                entry_t = t
            elif kill and held >= min_hold_s:
                close_now(dt, snap.ltp)
            continue
        if side == "SHORT":
            held = t - entry_t
            kill = snap.state in {BULL_CONT, ABSORB_SELL} or (
                snap.decaying and snap.state != BEAR_CONT
            )
            if want == "long" and held >= min_hold_s:
                close_now(dt, snap.ltp)
                side = "LONG"
                entry_px = snap.ltp
                entry_time = dt.strftime("%Y-%m-%d %H:%M:%S")
                entry_t = t
            elif kill and held >= min_hold_s:
                close_now(dt, snap.ltp)
            continue
        if t - last_exit_t < cooldown_s:
            continue
        if want == "long":
            side = "LONG"
            entry_px = snap.ltp
            entry_time = dt.strftime("%Y-%m-%d %H:%M:%S")
            entry_t = t
        elif want == "short":
            side = "SHORT"
            entry_px = snap.ltp
            entry_time = dt.strftime("%Y-%m-%d %H:%M:%S")
            entry_t = t

    if side is not None and last_dt is not None:
        close_now(last_dt, last_px)
    result = _tf_result_from_trades(tf, n_decisions, trades)
    return result


def samples_from_tick_rows(rows: list[Any]) -> list[tuple[datetime, float, float, float]]:
    from mtf_bars import parse_ts, tick_metrics

    out: list[tuple[datetime, float, float, float]] = []
    for row in rows:
        m = tick_metrics(row)
        ltp = m.get("ltp")
        if ltp is None:
            continue
        dt = parse_ts(row["received_at"])
        out.append((dt, float(ltp), float(m.get("tbq") or 0.0), float(m.get("tsq") or 0.0)))
    return out
