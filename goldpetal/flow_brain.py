"""FLOW_BRAIN: live LTP + TBQ + TSQ pressure. New book. Not S7_HOURLY. Not S16.

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
Gate packs sit on top of that state. The desk pack reuses S5 fee-cover,
S8 NET/IMB/book-drop, S16/S19 1m structure, and S20 fade-block.
Fill at LTP. Rank after Angel charges, tax excluded.

ENABLE_FLOW_BRAIN defaults false. Paper-only. Stay DRY_RUN. Not live.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

from backtest_hhhl_candles import Candle, Trade, make_charge_cfg
from backtest_wick_candles import _tf_result_from_trades
from charges import ChargeConfig, apply_charges_and_tax
from edge import PointATR, fee_break_even_points
from quality_filters import s8_quality_ok
from s16_hhhl_wick import s16_bar_decision
from s18_ohlc_vol_htf import VolBar
from s19_body_close import s19_bar_decision
from s20_fade_hl import fade_bounce_decision
from strategy_s8_align import net_imbalance

BOOK = "FLOW_BRAIN"
LAB_NAME = BOOK

FORMULA = (
    "Tick LTP + TBQ + TSQ. Pressure = 5s change in cumulative buy/sell qty. "
    "Imbalance = (dTBQ−dTSQ)/(dTBQ+dTSQ). LONG when price up, buy flow up, "
    "and both are expanding. SHORT the mirror. No trade on absorption "
    "(flow without price). Gate packs: confirm N seconds, min-hold, "
    "cooldown, no-flip, persist until opposite, min flow/price/ATR. "
    "v1 is the unfiltered 8k-trade tape. desk pack adds S5 fee-cover, "
    "S8 NET+rising IMB%+book-drop+TP/SL, S19 1m body+close, S16 1m "
    "agreement, S20 fade-block. Fill at LTP. Flatten session close. "
    "Not S7_HOURLY. Not S16 paper. Not live."
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

SCALE_RANK: dict[str, int] = {
    "none": 0,
    "small": 1,
    "medium": 2,
    "large": 3,
    "extreme": 4,
}


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
    net: float = 0.0
    imb_pct: float = 0.0
    imb_rising: bool = False
    expected_pts: float = 0.0
    s19_1m: Want | None = None
    s16_1m: Want | None = None
    s20_1m: Want | None = None
    tbq_drop_pct: float = 0.0
    tsq_drop_pct: float = 0.0


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
        self._pattr = PointATR(window=100)
        self._gate_prev_tbq: float | None = None
        self._gate_prev_tsq: float | None = None
        self._gate_prev_imb: float | None = None
        self._m_key: int | None = None
        self._m_o: float | None = None
        self._m_h: float | None = None
        self._m_l: float | None = None
        self._m_c: float | None = None
        self._closed_1m: deque[Candle] = deque(maxlen=8)
        self.s19_1m: Want | None = None
        self.s16_1m: Want | None = None
        self.s20_1m: Want | None = None

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

    def _reset_session_state(self) -> None:
        self.tr.clear()
        self._minute_key = None
        self._prev_close = None
        self._pattr = PointATR(window=100)
        self._gate_prev_tbq = None
        self._gate_prev_tsq = None
        self._gate_prev_imb = None
        self._m_key = None
        self._m_o = self._m_h = self._m_l = self._m_c = None
        self._closed_1m.clear()
        self.s19_1m = self.s16_1m = self.s20_1m = None

    def _as_vol(self, c: Candle) -> VolBar:
        return VolBar(time=c.time, open=c.open, high=c.high, low=c.low, close=c.close)

    def _roll_htf_minute(self, t: float, ltp: float) -> None:
        key = int(t // 60)
        if self._m_key is None:
            self._m_key = key
            self._m_o = self._m_h = self._m_l = self._m_c = float(ltp)
            return
        if key == self._m_key:
            assert self._m_h is not None and self._m_l is not None
            self._m_h = max(self._m_h, ltp)
            self._m_l = min(self._m_l, ltp)
            self._m_c = float(ltp)
            return
        if (
            self._m_o is not None
            and self._m_h is not None
            and self._m_l is not None
            and self._m_c is not None
        ):
            cur = Candle(
                time=datetime.fromtimestamp(self._m_key * 60).strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
                open=self._m_o,
                high=self._m_h,
                low=self._m_l,
                close=self._m_c,
            )
            if self._closed_1m:
                prev = self._closed_1m[-1]
                self.s19_1m, _ = s19_bar_decision(self._as_vol(prev), self._as_vol(cur))
                self.s16_1m, _ = s16_bar_decision(prev, cur, min_wick_gap=0.0)
                self.s20_1m, _ = fade_bounce_decision(prev, cur)
            self._closed_1m.append(cur)
        self._m_key = key
        self._m_o = self._m_h = self._m_l = self._m_c = float(ltp)

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
                self._reset_session_state()
        self.buf.append((t, ltp, tbq, tsq))
        cut = t - self.keep_s
        while self.buf and self.buf[0][0] < cut:
            self.buf.popleft()
        self._roll_minute(t, ltp)
        self._roll_htf_minute(t, ltp)
        expected = self._pattr.update(ltp)
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
        net, imb_pct = net_imbalance(tbq, tsq)
        if self._gate_prev_imb is None:
            imb_rising = False
        else:
            imb_rising = imb_pct > self._gate_prev_imb + 1e-9
        tbq_drop_pct = 0.0
        tsq_drop_pct = 0.0
        if self._gate_prev_tbq is not None and tbq < self._gate_prev_tbq:
            tbq_drop_pct = (self._gate_prev_tbq - tbq) / max(self._gate_prev_tbq, 1e-9) * 100.0
        if self._gate_prev_tsq is not None and tsq < self._gate_prev_tsq:
            tsq_drop_pct = (self._gate_prev_tsq - tsq) / max(self._gate_prev_tsq, 1e-9) * 100.0
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
            net=net,
            imb_pct=imb_pct,
            imb_rising=imb_rising,
            expected_pts=float(expected or 0.0),
            s19_1m=self.s19_1m,
            s16_1m=self.s16_1m,
            s20_1m=self.s20_1m,
            tbq_drop_pct=tbq_drop_pct,
            tsq_drop_pct=tsq_drop_pct,
        )
        self._gate_prev_tbq = float(tbq)
        self._gate_prev_tsq = float(tsq)
        self._gate_prev_imb = float(imb_pct)
        self.last = snap
        return snap


@dataclass
class FlowGates:
    """Entry/exit filters on top of the pressure state. v1 is the 8k-trade tape."""

    name: str = "v1"
    decide_every_s: float = 1.0
    min_flow_imb: float = 0.12
    min_price_pts: float = 1.0
    absorb_atr_frac: float = 0.03
    min_hold_s: float = 20.0
    cooldown_s: float = 15.0
    confirm_s: float = 0.0
    min_scale: str = "none"
    min_atr: float = 0.0
    min_atr_frac: float = 0.0
    require_imb_vel: bool = False
    require_expanding: bool = True
    allow_flip: bool = True
    persist_until_opposite: bool = False
    keep_s: float = 180.0
    require_net_sign: bool = False
    min_imb_pct: float = 0.0
    require_rising_imb_level: bool = False
    require_s19_1m: bool = False
    require_s19_agree: bool = False
    require_s16_agree: bool = False
    block_s20_against: bool = False
    fee_cover: bool = False
    min_edge_pts: float = 20.0
    edge_safety: float = 1.25
    lots: float = 100.0
    tp_pts: float = 0.0
    sl_pts: float = 0.0
    book_drop_min_pct: float = 0.0
    book_drop_persist: int = 1
    protect_profit_pts: float = 25.0
    scale_exits_to_expected: bool = False

    def brain(self) -> FlowBrain:
        return FlowBrain(
            keep_s=self.keep_s,
            decide_every_s=self.decide_every_s,
            min_flow_imb=self.min_flow_imb,
            min_price_pts=self.min_price_pts,
            absorb_atr_frac=self.absorb_atr_frac,
        )


def gated_want(
    snap: FlowState,
    g: FlowGates,
    *,
    lots: float | None = None,
) -> Want | None:
    """Apply quality gates to a continuation want.

    v1 keeps the engine's expanding-only want. Confirm/persist packs drop
    the acceleration flicker (5s vs 10s equalizes in a steady trend) and
    trade continuation that still clears the floors. desk adds S5/S8/S16/S19/S20.
    """
    if g.require_expanding:
        want = snap.want
    elif snap.state == BULL_CONT and snap.flow_imb_5s > 0:
        want = "long"
    elif snap.state == BEAR_CONT and snap.flow_imb_5s < 0:
        want = "short"
    else:
        want = None
    if want is None:
        return None
    if SCALE_RANK.get(snap.scale, 0) < SCALE_RANK.get(g.min_scale, 0):
        return None
    if snap.atr < float(g.min_atr):
        return None
    if g.min_atr_frac > 0 and snap.atr > 0:
        if abs(snap.d_ltp_5s) < float(g.min_atr_frac) * snap.atr:
            return None
    if g.require_imb_vel:
        # Reject fading flow. Zero velocity (steady pressure) is allowed.
        if want == "long" and snap.imb_vel < 0:
            return None
        if want == "short" and snap.imb_vel > 0:
            return None
    if g.require_net_sign:
        if want == "long" and snap.net <= 0:
            return None
        if want == "short" and snap.net >= 0:
            return None
    if g.min_imb_pct > 0 or g.require_rising_imb_level:
        rising = snap.imb_rising if g.require_rising_imb_level else None
        ok, _why = s8_quality_ok(
            snap.imb_pct,
            min_imb_pct=float(g.min_imb_pct) if g.min_imb_pct > 0 else 0.0,
            rising=rising if g.require_rising_imb_level else None,
        )
        if not ok:
            return None
    if g.require_s19_1m:
        if snap.s19_1m is None or snap.s19_1m != want:
            return None
    if g.require_s19_agree and snap.s19_1m is not None and snap.s19_1m != want:
        return None
    if g.require_s16_agree and snap.s16_1m is not None and snap.s16_1m != want:
        return None
    if g.block_s20_against and snap.s20_1m is not None and snap.s20_1m != want:
        return None
    if g.fee_cover:
        # Cover uses the pack's lot assumption (desk=100), not the settle
        # lot. Otherwise 1-lot vs 100-lot backtests take different trades.
        n = float(g.lots)
        be = fee_break_even_points(snap.ltp, force_fees=True, lots=n)
        req = max(float(g.min_edge_pts), be * float(g.edge_safety))
        if float(snap.expected_pts or 0.0) < req:
            return None
    return want


GATE_PACKS: dict[str, FlowGates] = {
    "v1": FlowGates(name="v1"),
    "confirm10": FlowGates(
        name="confirm10",
        decide_every_s=2.0,
        confirm_s=10.0,
        min_hold_s=60.0,
        cooldown_s=30.0,
        require_expanding=False,
        persist_until_opposite=True,
    ),
    "hold_opp": FlowGates(
        name="hold_opp",
        decide_every_s=5.0,
        min_hold_s=120.0,
        cooldown_s=60.0,
        require_expanding=False,
        allow_flip=False,
        persist_until_opposite=True,
    ),
    "quality": FlowGates(
        name="quality",
        decide_every_s=5.0,
        min_flow_imb=0.28,
        min_price_pts=4.0,
        confirm_s=15.0,
        min_hold_s=180.0,
        cooldown_s=120.0,
        min_scale="small",
        min_atr=5.0,
        min_atr_frac=0.12,
        require_imb_vel=True,
        require_expanding=False,
        allow_flip=False,
        persist_until_opposite=True,
    ),
    "desk": FlowGates(
        # 548k VM: confirm10 76t / 22.4% / −₹25k. AND-stack 0 trades.
        # Retune: confirm10 + no-flip + S8 net + HTF agree-if-set + 25/10.
        # Next tape: 2 LONG / 100% / +₹484.5 after charges. n=2 is not a go.
        name="desk",
        decide_every_s=2.0,
        confirm_s=10.0,
        min_hold_s=60.0,
        cooldown_s=30.0,
        min_flow_imb=0.18,
        min_price_pts=2.0,
        require_expanding=False,
        allow_flip=False,
        persist_until_opposite=True,
        require_net_sign=True,
        require_s19_agree=True,
        require_s16_agree=True,
        block_s20_against=True,
        fee_cover=True,
        min_edge_pts=5.0,
        edge_safety=1.25,
        tp_pts=25.0,
        sl_pts=10.0,
        book_drop_min_pct=0.25,
        book_drop_persist=1,
        protect_profit_pts=8.0,
    ),
    "c10_hold": FlowGates(
        # confirm10 + no-flip only. Isolates hold_opp's no-flip on the 76-trade loop.
        name="c10_hold",
        decide_every_s=2.0,
        confirm_s=10.0,
        min_hold_s=60.0,
        cooldown_s=30.0,
        require_expanding=False,
        persist_until_opposite=True,
        allow_flip=False,
    ),
    "desk_nonet": FlowGates(
        # desk without S8 NET sign. If this blows up, NET was the 2-trade filter.
        name="desk_nonet",
        decide_every_s=2.0,
        confirm_s=10.0,
        min_hold_s=60.0,
        cooldown_s=30.0,
        min_flow_imb=0.18,
        min_price_pts=2.0,
        require_expanding=False,
        allow_flip=False,
        persist_until_opposite=True,
        require_net_sign=False,
        require_s19_agree=True,
        require_s16_agree=True,
        block_s20_against=True,
        fee_cover=True,
        min_edge_pts=5.0,
        edge_safety=1.25,
        tp_pts=25.0,
        sl_pts=10.0,
        book_drop_min_pct=0.25,
        book_drop_persist=1,
        protect_profit_pts=8.0,
    ),
    "desk_nohtf": FlowGates(
        # desk without S16/S19/S20 1m agree. If this stays ~2 trades, NET+fee is the cut.
        name="desk_nohtf",
        decide_every_s=2.0,
        confirm_s=10.0,
        min_hold_s=60.0,
        cooldown_s=30.0,
        min_flow_imb=0.18,
        min_price_pts=2.0,
        require_expanding=False,
        allow_flip=False,
        persist_until_opposite=True,
        require_net_sign=True,
        require_s19_agree=False,
        require_s16_agree=False,
        block_s20_against=False,
        fee_cover=True,
        min_edge_pts=5.0,
        edge_safety=1.25,
        tp_pts=25.0,
        sl_pts=10.0,
        book_drop_min_pct=0.25,
        book_drop_persist=1,
        protect_profit_pts=8.0,
    ),
}


def kill_long(snap: FlowState, persist: bool) -> bool:
    """Flat-exit without a reverse. Persist ignores decay; opposite is handled by want."""
    if persist:
        return snap.state == ABSORB_BUY
    return snap.state in {BEAR_CONT, ABSORB_BUY} or (
        snap.decaying and snap.state != BULL_CONT
    )


def kill_short(snap: FlowState, persist: bool) -> bool:
    """Flat-exit without a reverse. Persist ignores decay; opposite is handled by want."""
    if persist:
        return snap.state == ABSORB_SELL
    return snap.state in {BULL_CONT, ABSORB_SELL} or (
        snap.decaying and snap.state != BEAR_CONT
    )


def after_charges_win_rate(result: Any) -> float:
    trades = list(getattr(result, "trades", []) or [])
    n = len(trades)
    if n == 0:
        return 0.0
    wins = sum(1 for t in trades if (t.gross_pnl_inr - t.fees_inr) > 0)
    return wins / n


def s5_targets(g: FlowGates | None, snap: FlowState) -> tuple[float, float]:
    """S5-style stops. Default: use the pack's tp/sl.

    scale_exits_to_expected lifts TP/SL to PointATR (S5 min-edge). On the
    548k tape that made TP 100+ pts (session range) so exits never fired.
    Desk keeps fixed 25/10.
    """
    if g is None:
        return 0.0, 0.0
    tp = float(g.tp_pts)
    sl = float(g.sl_pts)
    if g.scale_exits_to_expected:
        exp = float(snap.expected_pts or 0.0)
        if tp > 0:
            tp = max(tp, exp * 0.85)
        sl = max(float(g.min_edge_pts) * 0.4, exp * 0.45)
    elif g.fee_cover and tp > 0:
        tp = max(tp, float(g.min_edge_pts))
        if sl <= 0:
            sl = max(float(g.min_edge_pts) * 0.4, tp * 0.4)
    return tp, sl


def open_pnl_pts(side: str, entry_px: float, ltp: float) -> float:
    if side in {"LONG", "long"}:
        return float(ltp) - float(entry_px)
    return float(entry_px) - float(ltp)


def manage_open(
    *,
    side: str,
    snap: FlowState,
    entry_px: float,
    held: float,
    min_hold: float,
    g: FlowGates | None,
    drop_streak: int,
    tp_pts: float | None = None,
    sl_pts: float | None = None,
) -> tuple[str | None, int]:
    """S5 TP/SL and S8 book-drop+protect. None means keep managing via want/kill."""
    if g is None:
        return None, 0
    move = open_pnl_pts(side, entry_px, snap.ltp)
    tp = float(g.tp_pts if tp_pts is None else tp_pts)
    sl = float(g.sl_pts if sl_pts is None else sl_pts)
    if tp > 0 and move >= tp:
        return "tp", 0
    if sl > 0 and move <= -sl:
        return "sl", 0
    drop_need = float(g.book_drop_min_pct)
    if drop_need <= 0 or held < min_hold:
        return None, 0
    protect = float(g.protect_profit_pts)
    if protect > 0 and move >= protect:
        return None, 0
    drop_pct = snap.tbq_drop_pct if side in {"LONG", "long"} else snap.tsq_drop_pct
    if drop_pct >= drop_need:
        streak = drop_streak + 1
    else:
        streak = 0
    if streak >= max(1, int(g.book_drop_persist)):
        return "book_drop", 0
    return None, streak


GATE_PACK_ORDER = (
    "v1",
    "confirm10",
    "c10_hold",
    "hold_opp",
    "quality",
    "desk",
    "desk_nonet",
    "desk_nohtf",
)


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
    tf: str | None = None,
    lots: float = 100.0,
    fees: bool = True,
    session_filter: bool = True,
    market_open: str = "09:00",
    market_close: str = "23:30",
    charge_cfg: ChargeConfig | None = None,
    min_hold_s: float = 20.0,
    cooldown_s: float = 15.0,
    brain: FlowBrain | None = None,
    gates: FlowGates | None = None,
) -> Any:
    """Walk ticks. Fill at LTP. Flatten at session end / last sample.

    gates=None keeps v1 (8k-trade tape). Packs own hold/cooldown/brain.
    """
    g = gates
    cfg = charge_cfg or make_charge_cfg(fees=fees, lots=lots)
    if g is not None:
        eng = brain or g.brain()
        hold = float(g.min_hold_s)
        cool = float(g.cooldown_s)
        confirm_s = float(g.confirm_s)
        allow_flip = bool(g.allow_flip)
        persist = bool(g.persist_until_opposite)
        label = tf or f"fb_{g.name}"
    else:
        eng = brain or FlowBrain()
        hold = float(min_hold_s)
        cool = float(cooldown_s)
        confirm_s = 0.0
        allow_flip = True
        persist = False
        label = tf or "tick:flow_brain"
    trades: list[Trade] = []
    side: str | None = None
    entry_px = 0.0
    entry_time = ""
    entry_t = 0.0
    last_exit_t = -1e18
    last_dt: datetime | None = None
    last_px = 0.0
    n_decisions = 0
    pending_want: Want | None = None
    pending_since = 0.0
    drop_streak = 0
    entry_tp = 0.0
    entry_sl = 0.0

    def close_now(dt: datetime, px: float) -> None:
        nonlocal side, entry_px, entry_time, entry_t, last_exit_t, pending_want, pending_since, drop_streak
        assert side is not None
        trades.append(
            _close_px(
                tf=label,
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
        pending_want = None
        pending_since = 0.0
        drop_streak = 0

    def take_want(snap: FlowState) -> Want | None:
        if g is None:
            return snap.want
        return gated_want(snap, g, lots=lots)

    def fill(new_side: str, snap: FlowState, when: datetime) -> None:
        nonlocal side, entry_px, entry_time, entry_t, entry_tp, entry_sl, pending_want, pending_since
        side = new_side
        entry_px = snap.ltp
        entry_time = when.strftime("%Y-%m-%d %H:%M:%S")
        entry_t = snap.t
        entry_tp, entry_sl = s5_targets(g, snap)
        pending_want = None
        pending_since = 0.0

    for dt, ltp, tbq, tsq in samples:
        last_dt, last_px = dt, float(ltp)
        sess_ok = (not session_filter) or _in_session(dt, market_open, market_close)
        if side is not None and session_filter and not sess_ok:
            close_now(dt, float(ltp))
            eng.buf.clear()
            pending_want = None
            continue
        if session_filter and not sess_ok:
            continue
        snap = eng.push(dt.timestamp(), float(ltp), float(tbq), float(tsq))
        if snap is None:
            continue
        n_decisions += 1
        t = snap.t
        raw_want = snap.want
        want = take_want(snap)
        if side == "LONG":
            held = t - entry_t
            managed, drop_streak = manage_open(
                side=side,
                snap=snap,
                entry_px=entry_px,
                held=held,
                min_hold=hold,
                g=g,
                drop_streak=drop_streak,
                tp_pts=entry_tp,
                sl_pts=entry_sl,
            )
            if managed is not None:
                close_now(dt, snap.ltp)
                continue
            opposite = raw_want == "short" or want == "short"
            if opposite and held >= hold:
                close_now(dt, snap.ltp)
                if allow_flip and want == "short":
                    fill("SHORT", snap, dt)
            elif kill_long(snap, persist) and held >= hold:
                close_now(dt, snap.ltp)
            continue
        if side == "SHORT":
            held = t - entry_t
            managed, drop_streak = manage_open(
                side=side,
                snap=snap,
                entry_px=entry_px,
                held=held,
                min_hold=hold,
                g=g,
                drop_streak=drop_streak,
                tp_pts=entry_tp,
                sl_pts=entry_sl,
            )
            if managed is not None:
                close_now(dt, snap.ltp)
                continue
            opposite = raw_want == "long" or want == "long"
            if opposite and held >= hold:
                close_now(dt, snap.ltp)
                if allow_flip and want == "long":
                    fill("LONG", snap, dt)
            elif kill_short(snap, persist) and held >= hold:
                close_now(dt, snap.ltp)
            continue
        if t - last_exit_t < cool:
            pending_want = None
            pending_since = 0.0
            continue
        if want is None:
            pending_want = None
            pending_since = 0.0
            continue
        if confirm_s > 0:
            if pending_want != want:
                pending_want = want
                pending_since = t
                continue
            if t - pending_since < confirm_s:
                continue
        if want == "long":
            fill("LONG", snap, dt)
        elif want == "short":
            fill("SHORT", snap, dt)

    if side is not None and last_dt is not None:
        close_now(last_dt, last_px)
    result = _tf_result_from_trades(label, n_decisions, trades)
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


def scratchy_then_trend_samples() -> list[tuple[datetime, float, float, float]]:
    """Toy tape used for the +₹165 / 1-lot desk row: 25m chop, then 8m expansion."""
    ltp, tbq, tsq = 2340.0, 50_000.0, 50_000.0
    out: list[tuple[datetime, float, float, float]] = []
    t0 = datetime(2026, 8, 17, 10, 0, 0)
    i = 0
    for _minute in range(25):
        for _s in range(30):
            ltp += 0.3
            tbq += 80
            tsq += 20
            out.append((t0 + timedelta(seconds=i), ltp, tbq, tsq))
            i += 1
        for _s in range(30):
            ltp -= 0.3
            tbq += 20
            tsq += 80
            out.append((t0 + timedelta(seconds=i), ltp, tbq, tsq))
            i += 1
    for _s in range(480):
        ltp += 1.0
        tbq += 400
        tsq += 10
        out.append((t0 + timedelta(seconds=i), ltp, tbq, tsq))
        i += 1
    return out
