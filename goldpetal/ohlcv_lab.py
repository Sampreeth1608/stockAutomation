"""OHLCV Strategy Laboratory — research only. Not a paper book. Not live.

Feed finished bars (OHLC + volume) through a feature pass (price action,
volume, volatility), then seven named strategies, then a shared backtest
with two exit modes. Rank after Angel charges, tax excluded:

    expectancy + profit factor + after-charges drawdown + walk-forward
    stability. Win rate is secondary (40% × large winners can beat 70% ×
    small winners).

Fill at the signal bar's close. ATR targets also exit at close — we do
not fill the intra-bar high/low (that is look-ahead). Intraday tapes
flatten at the last bar of each calendar day.

Do not add these names to ALL_STRATEGY_NAMES / desk / ENABLE_*. Paper
them only if a later tape beats S16 and S18 after charges with enough
trades and the operator asks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from backtest_hhhl_candles import Candle, Trade, in_session, make_charge_cfg
from backtest_wick_candles import _tf_result_from_trades
from charges import ChargeConfig
from s18_ohlc_vol_htf import VolBar, _close_leg

LAB_NAME = "OHLCV_LAB"

FORMULA = (
    "Research lab, not a paper book. Finished OHLCV bars → features "
    "(range, close location, RVOL, 20-bar high/low, ATR, session VWAP, SMA) → "
    "seven strategies (volume breakout, climax reversal, pullback contraction, "
    "consolidation breakout, VWAP+volume, RVOL momentum, three-candle) → "
    "flip+EOD or 2ATR target / 1.5ATR trail. Fill at close. Rank after charges "
    "(expectancy, profit factor, drawdown, walk-forward); win% secondary."
)

EXIT_FLIP = "flip_eod"
EXIT_ATR = "atr"


@dataclass(frozen=True)
class LabParams:
    lookback: int = 20
    atr_n: int = 14
    box: int = 12
    breakout_rvol: float = 1.5
    close_loc: float = 0.75
    climax_rvol: float = 2.0
    climax_range_mult: float = 1.5
    impulse_rvol: float = 1.3
    impulse_range_mult: float = 1.2
    pullback_min: int = 2
    pullback_max: int = 4
    consol_range_frac: float = 0.7
    consol_vol_frac: float = 0.85
    consol_box_atr: float = 1.5
    consol_rvol: float = 1.5
    rvol_mom: float = 1.5
    tp_atr: float = 2.0
    trail_atr: float = 1.5


@dataclass(frozen=True)
class Feat:
    rng: float
    body: float
    close_loc: float
    green: bool
    red: bool
    avg_vol: float
    rvol: float
    avg_range: float
    prev_high_n: float
    prev_low_n: float
    atr: float
    sma: float
    vwap: float
    prev_close: float
    prev_vol: float


@dataclass
class LabMetrics:
    name: str
    family: str
    exit_mode: str
    n_trades: int
    n_long: int
    n_short: int
    after_charges: float
    expectancy: float
    profit_factor: float
    avg_win: float
    avg_loss: float
    max_dd: float
    win_rate: float
    wf_wins: int = 0
    wf_folds: int = 0
    n_bars: int = 0
    result: Any = None
    wf_rows: list[LabMetrics] | None = None

    @property
    def stability(self) -> str:
        if self.wf_folds <= 0:
            return "—"
        return f"{self.wf_wins}/{self.wf_folds}"


# name, family, one-line idea
STRATEGIES: tuple[tuple[str, str, str], ...] = (
    ("breakout", "breakout", "Volume-confirmed breakout of the prior 20-bar high/low"),
    ("climax", "reversal", "Extreme-volume climax, then fade the next bar's recovery"),
    ("pullback", "trend", "Impulse then 2–4 weak-volume pullback bars, then resume"),
    ("consol", "breakout", "Tight range + contracted volume, then expansion break"),
    ("vwap", "trend", "Intraday VWAP pullback with volume confirmation"),
    ("rvol_mom", "trend", "RVOL > 1.5 with close momentum near the bar extreme"),
    ("three", "trend", "Strong candle, small pullback, breakout with rising volume"),
)


def rvol_class(rvol: float) -> str:
    if rvol < 0.7:
        return "low"
    if rvol < 1.3:
        return "normal"
    if rvol < 2.0:
        return "high"
    return "extreme"


def after_charges_inr(result: Any) -> float:
    """Gross minus Angel charges. Tax excluded."""
    return float(result.gross_pnl_inr) - float(result.fees_inr)


def session_bars(
    bars: list[VolBar],
    *,
    market_open: str = "09:00",
    market_close: str = "23:30",
) -> list[VolBar]:
    """Keep MCX session bars so a 20-bar lookback is 20 session candles."""
    out: list[VolBar] = []
    for b in bars:
        if in_session(
            Candle(b.time, b.open, b.high, b.low, b.close),
            open_hhmm=market_open,
            close_hhmm=market_close,
        ):
            out.append(b)
    return out


def _close_loc(high: float, low: float, close: float) -> float:
    rng = float(high) - float(low)
    if rng <= 1e-12:
        return 0.5
    return (float(close) - float(low)) / rng


def build_features(bars: list[VolBar], params: LabParams | None = None) -> list[Feat | None]:
    p = params or LabParams()
    n = len(bars)
    n_lb = max(1, int(p.lookback))
    n_atr = max(1, int(p.atr_n))
    out: list[Feat | None] = [None] * n
    if n < 2:
        return out

    trs = [0.0] * n
    for i in range(1, n):
        h, l, pc = bars[i].high, bars[i].low, bars[i - 1].close
        trs[i] = max(h - l, abs(h - pc), abs(l - pc))
    atrs = [0.0] * n
    atr_ready = [False] * n
    if n > n_atr:
        atrs[n_atr] = sum(trs[1 : n_atr + 1]) / float(n_atr)
        atr_ready[n_atr] = True
        for i in range(n_atr + 1, n):
            atrs[i] = (atrs[i - 1] * (n_atr - 1) + trs[i]) / float(n_atr)
            atr_ready[i] = True

    cum_tpv = 0.0
    cum_vol = 0.0
    prev_day = ""
    for i, b in enumerate(bars):
        if b.day != prev_day:
            cum_tpv = 0.0
            cum_vol = 0.0
            prev_day = b.day
        typical = (b.high + b.low + b.close) / 3.0
        cum_tpv += typical * float(b.volume)
        cum_vol += float(b.volume)
        vwap = (cum_tpv / cum_vol) if cum_vol > 1e-12 else b.close
        if i < n_lb or not atr_ready[i]:
            continue
        window = bars[i - n_lb : i]
        avg_vol = sum(x.volume for x in window) / float(n_lb)
        avg_range = sum((x.high - x.low) for x in window) / float(n_lb)
        rvol = (float(b.volume) / avg_vol) if avg_vol > 1e-12 else 0.0
        sma = sum(x.close for x in bars[i - n_lb + 1 : i + 1]) / float(n_lb)
        rng = float(b.high) - float(b.low)
        out[i] = Feat(
            rng=rng,
            body=abs(float(b.close) - float(b.open)),
            close_loc=_close_loc(b.high, b.low, b.close),
            green=b.close > b.open,
            red=b.close < b.open,
            avg_vol=avg_vol,
            rvol=rvol,
            avg_range=avg_range,
            prev_high_n=max(x.high for x in window),
            prev_low_n=min(x.low for x in window),
            atr=atrs[i],
            sma=sma,
            vwap=vwap,
            prev_close=bars[i - 1].close,
            prev_vol=float(bars[i - 1].volume),
        )
    return out


def decide_breakout(
    bars: list[VolBar], feats: list[Feat | None], i: int, p: LabParams, st: dict[str, Any]
) -> tuple[str | None, str]:
    del st
    f = feats[i]
    if f is None:
        return None, "warmup"
    b = bars[i]
    if b.close > f.prev_high_n and f.rvol >= p.breakout_rvol and f.close_loc >= p.close_loc:
        return "long", "breakout:long C>Nhigh RVOL close-high"
    if b.close < f.prev_low_n and f.rvol >= p.breakout_rvol and f.close_loc <= (1.0 - p.close_loc):
        return "short", "breakout:short C<Nlow RVOL close-low"
    return None, "breakout:no"


def decide_climax(
    bars: list[VolBar], feats: list[Feat | None], i: int, p: LabParams, st: dict[str, Any]
) -> tuple[str | None, str]:
    f = feats[i]
    if f is None:
        st["pending"] = None
        return None, "warmup"
    b = bars[i]
    pending = st.get("pending")
    signal: tuple[str | None, str] = (None, "climax:wait")
    if pending is not None:
        side, mid = pending
        st["pending"] = None
        if side == "long" and b.close > mid:
            signal = ("long", "climax:long recover above midpoint")
        elif side == "short" and b.close < mid:
            signal = ("short", "climax:short reject below midpoint")
        else:
            signal = (None, "climax:no recover")
        if signal[0] is not None:
            return signal
    large = f.rng >= p.climax_range_mult * f.avg_range and f.avg_range > 1e-12
    if large and f.rvol >= p.climax_rvol and f.red and f.close_loc <= (1.0 - p.close_loc):
        st["pending"] = ("long", (b.high + b.low) / 2.0)
        return None, "climax:bearish setup wait"
    if large and f.rvol >= p.climax_rvol and f.green and f.close_loc >= p.close_loc:
        st["pending"] = ("short", (b.high + b.low) / 2.0)
        return None, "climax:bullish setup wait"
    return signal if pending is not None else (None, "climax:no")


def _impulse_long(b: VolBar, f: Feat, p: LabParams) -> bool:
    return (
        f.green
        and f.rvol >= p.impulse_rvol
        and f.rng >= p.impulse_range_mult * f.avg_range
        and b.close > f.sma
        and f.body >= 0.5 * f.rng
    )


def _impulse_short(b: VolBar, f: Feat, p: LabParams) -> bool:
    return (
        f.red
        and f.rvol >= p.impulse_rvol
        and f.rng >= p.impulse_range_mult * f.avg_range
        and b.close < f.sma
        and f.body >= 0.5 * f.rng
    )


def decide_pullback(
    bars: list[VolBar], feats: list[Feat | None], i: int, p: LabParams, st: dict[str, Any]
) -> tuple[str | None, str]:
    f = feats[i]
    if f is None:
        st["phase"] = None
        return None, "warmup"
    b = bars[i]
    phase = st.get("phase")
    impulse_vol = float(st.get("impulse_vol") or 0.0)
    pb_count = int(st.get("pb_count") or 0)

    def reset() -> None:
        st["phase"] = None
        st["impulse_vol"] = 0.0
        st["pb_count"] = 0

    def arm(side: str) -> None:
        st["phase"] = side
        st["impulse_vol"] = float(b.volume)
        st["pb_count"] = 0

    if phase == "long_pb":
        if f.green and p.pullback_min <= pb_count <= p.pullback_max:
            reset()
            return "long", f"pullback:long after {pb_count} weak reds"
        if f.red and float(b.volume) < impulse_vol and pb_count < p.pullback_max:
            st["pb_count"] = pb_count + 1
            return None, "pullback:long counting"
        reset()
        if _impulse_long(b, f, p):
            arm("long_pb")
            return None, "pullback:re-arm long"
        if _impulse_short(b, f, p):
            arm("short_pb")
            return None, "pullback:re-arm short"
        return None, "pullback:reset"
    if phase == "short_pb":
        if f.red and p.pullback_min <= pb_count <= p.pullback_max:
            reset()
            return "short", f"pullback:short after {pb_count} weak greens"
        if f.green and float(b.volume) < impulse_vol and pb_count < p.pullback_max:
            st["pb_count"] = pb_count + 1
            return None, "pullback:short counting"
        reset()
        if _impulse_short(b, f, p):
            arm("short_pb")
            return None, "pullback:re-arm short"
        if _impulse_long(b, f, p):
            arm("long_pb")
            return None, "pullback:re-arm long"
        return None, "pullback:reset"
    if _impulse_long(b, f, p):
        arm("long_pb")
        return None, "pullback:impulse long"
    if _impulse_short(b, f, p):
        arm("short_pb")
        return None, "pullback:impulse short"
    return None, "pullback:no"


def decide_consol(
    bars: list[VolBar], feats: list[Feat | None], i: int, p: LabParams, st: dict[str, Any]
) -> tuple[str | None, str]:
    del st
    f = feats[i]
    box = max(2, int(p.box))
    if f is None or i < box:
        return None, "warmup"
    window = bars[i - box : i]
    box_high = max(x.high for x in window)
    box_low = min(x.low for x in window)
    mean_range = sum((x.high - x.low) for x in window) / float(box)
    mean_vol = sum(x.volume for x in window) / float(box)
    width = box_high - box_low
    tight = width <= p.consol_box_atr * f.atr and f.atr > 1e-12
    quiet = mean_range <= p.consol_range_frac * f.avg_range and f.avg_range > 1e-12
    dry = mean_vol <= p.consol_vol_frac * f.avg_vol and f.avg_vol > 1e-12
    if not (tight and quiet and dry):
        return None, "consol:not tight"
    b = bars[i]
    large = f.rng >= f.avg_range
    if b.close > box_high and f.rvol >= p.consol_rvol and large:
        return "long", "consol:long break high + volume"
    if b.close < box_low and f.rvol >= p.consol_rvol and large:
        return "short", "consol:short break low + volume"
    return None, "consol:no break"


def decide_vwap(
    bars: list[VolBar], feats: list[Feat | None], i: int, p: LabParams, st: dict[str, Any]
) -> tuple[str | None, str]:
    del p, st
    f = feats[i]
    if f is None:
        return None, "warmup"
    b = bars[i]
    if i == 0 or bars[i - 1].day != b.day:
        return None, "vwap:first bar of day"
    vol_up = float(b.volume) > f.prev_vol
    if b.close > f.vwap and b.low <= f.vwap and f.green and vol_up:
        return "long", "vwap:long pullback to VWAP vol-up"
    if b.close < f.vwap and b.high >= f.vwap and f.red and vol_up:
        return "short", "vwap:short pullback to VWAP vol-up"
    return None, "vwap:no"


def decide_rvol_mom(
    bars: list[VolBar], feats: list[Feat | None], i: int, p: LabParams, st: dict[str, Any]
) -> tuple[str | None, str]:
    del st
    f = feats[i]
    if f is None:
        return None, "warmup"
    b = bars[i]
    if (
        f.rvol >= p.rvol_mom
        and b.close > f.prev_close
        and b.close > f.sma
        and f.close_loc >= p.close_loc
    ):
        return "long", f"rvol_mom:long {rvol_class(f.rvol)}"
    if (
        f.rvol >= p.rvol_mom
        and b.close < f.prev_close
        and b.close < f.sma
        and f.close_loc <= (1.0 - p.close_loc)
    ):
        return "short", f"rvol_mom:short {rvol_class(f.rvol)}"
    return None, "rvol_mom:no"


def decide_three(
    bars: list[VolBar], feats: list[Feat | None], i: int, p: LabParams, st: dict[str, Any]
) -> tuple[str | None, str]:
    del p, st
    if i < 2:
        return None, "warmup"
    f = feats[i]
    f1 = feats[i - 2]
    f2 = feats[i - 1]
    if f is None or f1 is None or f2 is None:
        return None, "warmup"
    c1, c2, c3 = bars[i - 2], bars[i - 1], bars[i]
    # Long: strong green, smaller pullback, break c1 high with rising volume
    if (
        f1.green
        and f1.body >= 0.6 * f1.rng
        and f1.rng > f1.avg_range
        and f2.rng < f1.rng
        and (f2.red or c2.close < c1.close)
        and float(c2.volume) <= float(c1.volume) * 1.1
        and f.green
        and c3.close > c1.high
        and float(c3.volume) > float(c2.volume)
    ):
        return "long", "three:long continuation"
    if (
        f1.red
        and f1.body >= 0.6 * f1.rng
        and f1.rng > f1.avg_range
        and f2.rng < f1.rng
        and (f2.green or c2.close > c1.close)
        and float(c2.volume) <= float(c1.volume) * 1.1
        and f.red
        and c3.close < c1.low
        and float(c3.volume) > float(c2.volume)
    ):
        return "short", "three:short continuation"
    return None, "three:no"


DECIDERS: dict[str, Callable[..., tuple[str | None, str]]] = {
    "breakout": decide_breakout,
    "climax": decide_climax,
    "pullback": decide_pullback,
    "consol": decide_consol,
    "vwap": decide_vwap,
    "rvol_mom": decide_rvol_mom,
    "three": decide_three,
}


def _is_last_of_day(bars: list[VolBar], i: int) -> bool:
    if i >= len(bars) - 1:
        return True
    return bars[i + 1].day != bars[i].day


def simulate_lab(
    bars: list[VolBar],
    strategy: str,
    *,
    exit_mode: str = EXIT_FLIP,
    tf: str | None = None,
    lots: float = 100.0,
    fees: bool = True,
    flatten_eod: bool = True,
    params: LabParams | None = None,
    entry_days: set[str] | None = None,
    charge_cfg: ChargeConfig | None = None,
) -> Any:
    """FLIP or ATR-exit simulator. Fill at close. Optional day window for WF."""
    if strategy not in DECIDERS:
        raise ValueError(f"unknown lab strategy {strategy!r}")
    if exit_mode not in {EXIT_FLIP, EXIT_ATR}:
        raise ValueError(f"unknown exit_mode {exit_mode!r}")
    p = params or LabParams()
    feats = build_features(bars, p)
    decide = DECIDERS[strategy]
    st: dict[str, Any] = {}
    cfg = charge_cfg or make_charge_cfg(fees=fees, lots=lots)
    book = tf or f"{strategy}:{exit_mode}"
    trades: list[Trade] = []
    side: str | None = None
    entry_px = 0.0
    entry_time = ""
    entry_i = -1
    entry_atr = 0.0
    ext_px = 0.0

    def close_trade(exit_c: VolBar) -> None:
        nonlocal side, entry_px, entry_time
        assert side is not None
        trades.append(
            _close_leg(
                tf=book,
                side=side,
                entry_time=entry_time,
                entry_px=entry_px,
                exit_c=exit_c,
                cfg=cfg,
            )
        )
        side = None

    def open_trade(cur: VolBar, want: str, atr: float, i: int) -> None:
        nonlocal side, entry_px, entry_time, entry_i, entry_atr, ext_px
        side = "LONG" if want == "long" else "SHORT"
        entry_px = cur.close
        entry_time = cur.time
        entry_i = i
        entry_atr = float(atr)
        ext_px = cur.high if side == "LONG" else cur.low

    for i, cur in enumerate(bars):
        f = feats[i]
        want, _why = decide(bars, feats, i, p, st) if f is not None else (None, "warmup")
        scoring = entry_days is None or cur.day in entry_days
        if not scoring:
            if side is not None:
                close_trade(cur)
            continue
        last = bool(flatten_eod) and _is_last_of_day(bars, i)
        if last:
            if side is not None:
                close_trade(cur)
            continue
        if side is not None and exit_mode == EXIT_ATR and i > entry_i and f is not None:
            ext_px = max(ext_px, cur.high) if side == "LONG" else min(ext_px, cur.low)
            atr = entry_atr
            if atr > 1e-12:
                if side == "LONG":
                    tp = entry_px + p.tp_atr * atr
                    trail = ext_px - p.trail_atr * atr
                    if cur.close >= tp or cur.close <= trail:
                        close_trade(cur)
                else:
                    tp = entry_px - p.tp_atr * atr
                    trail = ext_px + p.trail_atr * atr
                    if cur.close <= tp or cur.close >= trail:
                        close_trade(cur)
        atr_now = float(f.atr) if f is not None else 0.0
        if side == "LONG":
            if want == "short":
                close_trade(cur)
                open_trade(cur, "short", atr_now, i)
            continue
        if side == "SHORT":
            if want == "long":
                close_trade(cur)
                open_trade(cur, "long", atr_now, i)
            continue
        if want == "long":
            open_trade(cur, "long", atr_now, i)
        elif want == "short":
            open_trade(cur, "short", atr_now, i)

    if side is not None and bars:
        close_trade(bars[-1])
    if entry_days is not None:
        trades = [t for t in trades if t.entry_time[:10] in entry_days]
    return _tf_result_from_trades(book, len(bars), trades)


def trade_after_charges(t: Trade) -> float:
    return float(t.gross_pnl_inr) - float(t.fees_inr)


def max_dd_after_charges(trades: list[Trade]) -> float:
    equity = 0.0
    peak = 0.0
    dd = 0.0
    for t in trades:
        equity += trade_after_charges(t)
        peak = max(peak, equity)
        dd = max(dd, peak - equity)
    return dd


def profit_factor(trades: list[Trade]) -> float:
    wins = sum(trade_after_charges(t) for t in trades if trade_after_charges(t) > 0)
    losses = sum(trade_after_charges(t) for t in trades if trade_after_charges(t) < 0)
    if losses >= -1e-12:
        return 99.99 if wins > 1e-12 else 0.0
    return wins / abs(losses)


def score_result(
    result: Any,
    *,
    name: str,
    family: str = "",
    exit_mode: str = EXIT_FLIP,
    wf_wins: int = 0,
    wf_folds: int = 0,
) -> LabMetrics:
    trades: list[Trade] = list(result.trades)
    n = len(trades)
    ac = after_charges_inr(result)
    ac_list = [trade_after_charges(t) for t in trades]
    win_acs = [x for x in ac_list if x > 0]
    loss_acs = [x for x in ac_list if x < 0]
    wr = (len(win_acs) / n) if n else 0.0
    return LabMetrics(
        name=name,
        family=family,
        exit_mode=exit_mode,
        n_trades=n,
        n_long=int(result.n_long),
        n_short=int(result.n_short),
        after_charges=ac,
        expectancy=(ac / n) if n else 0.0,
        profit_factor=profit_factor(trades),
        avg_win=(sum(win_acs) / len(win_acs)) if win_acs else 0.0,
        avg_loss=(sum(loss_acs) / len(loss_acs)) if loss_acs else 0.0,
        max_dd=max_dd_after_charges(trades),
        win_rate=wr,
        wf_wins=wf_wins,
        wf_folds=wf_folds,
        n_bars=int(result.n_bars),
        result=result,
    )


def unique_days(bars: list[VolBar]) -> list[str]:
    out: list[str] = []
    got: set[str] = set()
    for b in bars:
        if b.day not in got:
            got.add(b.day)
            out.append(b.day)
    return out


def day_folds(bars: list[VolBar], n_folds: int = 3) -> list[set[str]]:
    days = unique_days(bars)
    if not days:
        return []
    n_folds = max(1, int(n_folds))
    if len(days) < n_folds:
        return [set(days)]
    folds: list[set[str]] = []
    n = len(days)
    for k in range(n_folds):
        a = k * n // n_folds
        b = (k + 1) * n // n_folds
        folds.append(set(days[a:b]))
    return folds


def walk_forward(
    bars: list[VolBar],
    strategy: str,
    *,
    exit_mode: str = EXIT_FLIP,
    n_folds: int = 3,
    **kwargs: Any,
) -> tuple[list[LabMetrics], int, int]:
    """Score each chronological day-fold. Warmup bars before the fold update state."""
    kwargs = {k: v for k, v in kwargs.items() if k not in {"tf", "entry_days"}}
    folds = day_folds(bars, n_folds=n_folds)
    rows: list[LabMetrics] = []
    for i, days in enumerate(folds):
        if not days:
            continue
        last = max(days)
        window = [b for b in bars if b.day <= last]
        res = simulate_lab(
            window,
            strategy,
            exit_mode=exit_mode,
            entry_days=days,
            tf=f"{strategy}:{exit_mode}:wf{i + 1}",
            **kwargs,
        )
        rows.append(
            score_result(
                res,
                name=f"{strategy}:{exit_mode}:wf{i + 1}",
                exit_mode=exit_mode,
            )
        )
    wins = sum(1 for r in rows if r.after_charges > 0)
    return rows, wins, len(rows)


def selected_strategies(names: list[str] | None) -> tuple[tuple[str, str, str], ...]:
    """Filter STRATEGIES, keeping lab order (breakout → … → three)."""
    if not names:
        return STRATEGIES
    want: list[str] = []
    for item in names:
        want.extend(x.strip() for x in str(item).split(",") if x.strip())
    known = {row[0] for row in STRATEGIES}
    unknown = [n for n in want if n not in known]
    if unknown:
        raise ValueError(
            f"unknown lab strategy {unknown!r}; choose from {[n for n, _, _ in STRATEGIES]}"
        )
    pick = set(want)
    return tuple(row for row in STRATEGIES if row[0] in pick)


def run_lab_book(
    bars: list[VolBar],
    strategy: str,
    *,
    exit_mode: str = EXIT_FLIP,
    family: str = "",
    n_folds: int = 3,
    **kwargs: Any,
) -> LabMetrics:
    sim_kwargs = {k: v for k, v in kwargs.items() if k != "n_folds"}
    res = simulate_lab(bars, strategy, exit_mode=exit_mode, **sim_kwargs)
    wf_kwargs = {k: v for k, v in sim_kwargs.items() if k not in {"tf", "entry_days"}}
    wf_rows, wf_wins, wf_folds = walk_forward(
        bars, strategy, exit_mode=exit_mode, n_folds=n_folds, **wf_kwargs
    )
    fam = family or next((f for n, f, _ in STRATEGIES if n == strategy), "")
    metrics = score_result(
        res,
        name=strategy,
        family=fam,
        exit_mode=exit_mode,
        wf_wins=wf_wins,
        wf_folds=wf_folds,
    )
    metrics.wf_rows = wf_rows
    return metrics
