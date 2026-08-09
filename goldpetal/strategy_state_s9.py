"""S9_STATE30 — 27-state TBQ/TSQ/Price machine on N-minute bars.

Leaves S8 untouched. Built from state-transition study:
  - Enter LONG on true-up confirm: TBQ+ TSQ- Price+  (B+S-P+)
  - Optional: B+S+P+ if NET>0 (both books grow + price up)
  - Hold while bull-continue family AND NET>0
  - Exit: TP/SL (data-driven defaults 26/16 on 30m) or NET flip soft
  - SHORT disabled by default (bear confirms failed on bullish sample)

Extensible: add states to enter_long_states / exit_long_states / hooks
without rewriting the bar engine. Future: shorts, multi-TF confirm, depth.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Literal
from zoneinfo import ZoneInfo

from almost_equal import clamp, sign_px, sign_rel
from strategy import Position, SignalResult

IST = ZoneInfo("Asia/Kolkata")
Bias = Literal["BULL", "BEAR", "NEUTRAL"]


def state_code_from_levels(
    tbq_c: float,
    tbq_p: float,
    tsq_c: float,
    tsq_p: float,
    px_c: float,
    px_p: float,
    *,
    equal_pct: float,
    equal_px_pct: float,
    equal_pts: float | None,
) -> str:
    """Build TBQ/TSQ/P state using almost-equal bands (not exact =)."""
    b = sign_rel(tbq_c, tbq_p, equal_pct)
    s = sign_rel(tsq_c, tsq_p, equal_pct)
    p = sign_px(px_c, px_p, equal_px_pct=equal_px_pct, equal_pts=equal_pts)
    return f"TBQ{b}_TSQ{s}_P{p}"


# Back-compat alias used by tests (absolute eps on deltas)
def state_code(dtbq: float, dtsq: float, dpx: float, eps_qty: float, eps_px: float) -> str:
    def _s(d: float, eps: float) -> str:
        if d > eps:
            return "+"
        if d < -eps:
            return "-"
        return "="

    return f"TBQ{_s(dtbq, eps_qty)}_TSQ{_s(dtsq, eps_qty)}_P{_s(dpx, eps_px)}"


def short_label(code: str) -> str:
    if code in {"", "START", "WARMUP"}:
        return code
    parts = code.split("_")
    if len(parts) != 3:
        return code
    return (
        parts[0].replace("TBQ", "B")
        + parts[1].replace("TSQ", "S")
        + parts[2]
    )


# Families from study (extensible)
ENTER_LONG_DEFAULT = frozenset(
    {
        "TBQ+_TSQ-_P+",
        "TBQ+_TSQ+_P+",
        "TBQ+_TSQ=_P+",  # almost-equal study candidate
        "TBQ+_TSQ+_P=",
    }
)
CONTINUE_LONG_DEFAULT = frozenset(
    {
        "TBQ+_TSQ-_P+",
        "TBQ+_TSQ=_P+",
        "TBQ=_TSQ-_P+",
        "TBQ+_TSQ+_P+",
        "TBQ+_TSQ-_P=",
        "TBQ+_TSQ-_P-",  # bull pullback — hold, don't flip
        "TBQ+_TSQ+_P=",
    }
)
# H/L × Volume confirms from 30m study
HLV_LONG_CONFIRM_DEFAULT = frozenset({"H+_V+", "L+_V+"})
HLV_LONG_VETO_DEFAULT = frozenset({"H+_V-"})
ENTER_SHORT_DEFAULT = frozenset({"TBQ-_TSQ+_P-"})  # off unless allow_short
CONTINUE_SHORT_DEFAULT = frozenset(
    {
        "TBQ-_TSQ+_P-",
        "TBQ-_TSQ=_P-",
        "TBQ=_TSQ+_P-",
        "TBQ-_TSQ-_P-",
        "TBQ-_TSQ+_P=",
        "TBQ-_TSQ+_P+",
    }
)


@dataclass
class StateS9Config:
    bar_minutes: int = 30
    tp_points: float = 26.0
    sl_points: float = 16.0
    # Almost-equal: qty/vol relative (3–9%), price tighter fraction / optional pts
    equal_pct: float = 0.05
    equal_px_pct: float = 0.0005
    equal_pts: float | None = None
    # legacy absolute eps (only used by old state_code helper / tests)
    eps_qty: float = 1.0
    eps_px: float = 0.5
    min_imb_pct: float = 5.0  # soft NET quality gate
    require_net_sign: bool = True  # long only if NET>0
    allow_short: bool = False  # study: shorts failed on current sample
    enter_long_states: frozenset[str] = field(default_factory=lambda: ENTER_LONG_DEFAULT)
    continue_long_states: frozenset[str] = field(
        default_factory=lambda: CONTINUE_LONG_DEFAULT
    )
    enter_short_states: frozenset[str] = field(default_factory=lambda: ENTER_SHORT_DEFAULT)
    continue_short_states: frozenset[str] = field(
        default_factory=lambda: CONTINUE_SHORT_DEFAULT
    )
    # Exit long if we leave continue family for this many bars
    break_bars: int = 1
    # High/Low × Volume filters (from hl_vol_study).
    # VM 30m replay @100 lots: NO_HLV sum₹≈+12k vs HLV_GATE ≈+0.4k — default OFF.
    require_hlv_confirm: bool = False
    hlv_mode: str = "any"  # any = H+V+ OR L+V+ ; both = need both
    hlv_confirm_states: frozenset[str] = field(
        default_factory=lambda: HLV_LONG_CONFIRM_DEFAULT
    )
    hlv_veto_states: frozenset[str] = field(default_factory=lambda: HLV_LONG_VETO_DEFAULT)
    # 3-bar volume expansion (study: INC_DEC|UP → higher huge-next rate on 30m).
    # Default OFF — must beat TBQ-only (+₹12k) in paper-sim before enabling.
    require_vol_expansion: bool = False
    vol_expansion_stacks: frozenset[str] = field(
        default_factory=lambda: frozenset({"INC_DEC"})
    )
    vol_exp_require_up: bool = True
    vol_exp_require_h_plus: bool = False
    vol_trend_lookback: int = 2  # close vs close N bars ago


class StateS9Strategy:
    """Bar-close state machine. Feed ticks via on_tick; decides on bar boundary."""

    name = "S9_STATE30"

    def __init__(self, cfg: StateS9Config | None = None) -> None:
        self.cfg = cfg or StateS9Config()
        self.position: Position = "flat"
        self.entry_price: float | None = None
        self.last_state: str = "WARMUP"
        self.prev_state: str = "WARMUP"
        self.last_label: str = "WARMUP"
        self.last_net: float = 0.0
        self.last_imb: float = 0.0
        self.last_tbq: float = 0.0
        self.last_tsq: float = 0.0
        self.last_close: float | None = None
        self.last_hv: str = "START"
        self.last_lv: str = "START"
        self.last_vol_stack: str = "START"
        self.last_px_trend: str = "NA"
        self.break_count: int = 0
        self.last_skip: str | None = None

        # bar builder
        self._bar_key: datetime | None = None
        self._o = self._h = self._l = self._c = None
        self._tbq_o = self._tsq_o = self._tbq_c = self._tsq_c = 0.0
        self._vol_c: float | None = None
        self._n = 0
        self._prev_tbq: float | None = None
        self._prev_tsq: float | None = None
        self._prev_close: float | None = None
        self._prev_high: float | None = None
        self._prev_low: float | None = None
        self._prev_bar_vol: float | None = None
        self._prev_prev_bar_vol: float | None = None
        self._prev_cum_vol: float | None = None
        self._close_hist: list[float] = []

        # hooks for future extensions: fn(bar_dict, strategy) -> SignalResult|None
        self.extra_entry_filters: list[Callable[..., bool]] = []
        self.extra_exit_hooks: list[Callable[..., SignalResult | None]] = []

    @property
    def status_line(self) -> str:
        c = self.cfg
        return (
            f"TF={c.bar_minutes}m TP={c.tp_points:.0f} SL={c.sl_points:.0f} "
            f"short={c.allow_short} net_gate={c.require_net_sign} "
            f"hlv={c.require_hlv_confirm}/{c.hlv_mode} "
            f"volx={c.require_vol_expansion} "
            f"state={self.last_label} stack={self.last_vol_stack}|{self.last_px_trend} "
            f"pos={self.position}"
        )

    def _floor(self, ts: datetime) -> datetime:
        ts = ts.astimezone(IST)
        midnight = ts.replace(hour=0, minute=0, second=0, microsecond=0)
        mins = int((ts - midnight).total_seconds() // 60)
        block = (mins // self.cfg.bar_minutes) * self.cfg.bar_minutes
        return midnight + timedelta(minutes=block)

    def _open(self, side: Position, px: float) -> None:
        self.position = side
        self.entry_price = float(px)
        self.break_count = 0

    def _close(self) -> None:
        self.position = "flat"
        self.entry_price = None
        self.break_count = 0

    def _hlv_ok(self) -> tuple[bool, str]:
        """Long confirm via H+V+ / L+V+; veto H+V-."""
        if not self.cfg.require_hlv_confirm:
            return True, "hlv_off"
        if self.last_hv in self.cfg.hlv_veto_states:
            return False, f"hlv_veto {self.last_hv}"
        conf_h = self.last_hv in self.cfg.hlv_confirm_states
        conf_l = self.last_lv in self.cfg.hlv_confirm_states
        mode = (self.cfg.hlv_mode or "any").lower()
        if mode == "both":
            ok = conf_h and conf_l
        else:
            ok = conf_h or conf_l
        if not ok:
            return False, f"hlv_need_confirm hv={self.last_hv} lv={self.last_lv}"
        return True, f"hlv_ok hv={self.last_hv} lv={self.last_lv}"

    def _vol_stack_label(self, cv: float, pv: float | None, ppv: float | None) -> str:
        if pv is None or ppv is None:
            return "START"
        s1 = sign_rel(cv, float(pv), self.cfg.equal_pct)
        s2 = sign_rel(float(pv), float(ppv), self.cfg.equal_pct)
        words = {"+": "INC", "-": "DEC", "=": "FLAT"}
        if s1 == "-" and s2 == "-":
            return "DEC_DEC"
        if s1 == "+" and s2 == "+":
            return "INC_INC"
        if s1 == "=" and s2 == "=":
            return "FLAT_FLAT"
        return f"{words[s1]}_{words[s2]}"

    def _px_trend_label(self, c: float) -> str:
        lb = max(1, int(self.cfg.vol_trend_lookback))
        if len(self._close_hist) < lb:
            return "NA"
        anchor = self._close_hist[-lb]
        s = sign_px(
            c,
            float(anchor),
            equal_px_pct=self.cfg.equal_px_pct,
            equal_pts=self.cfg.equal_pts,
        )
        return {"+": "UP", "-": "DOWN", "=": "FLAT"}[s]

    def _vol_exp_ok(self) -> tuple[bool, str]:
        """Optional INC_DEC (+ UP) expansion confirm from vol_trend_study."""
        if not self.cfg.require_vol_expansion:
            return True, "volx_off"
        stack = self.last_vol_stack
        if stack not in self.cfg.vol_expansion_stacks:
            return False, f"volx_need_stack got={stack}"
        if self.cfg.vol_exp_require_up and self.last_px_trend != "UP":
            return False, f"volx_need_UP got={self.last_px_trend}"
        if self.cfg.vol_exp_require_h_plus and not self.last_hv.startswith("H+"):
            return False, f"volx_need_H+ got={self.last_hv}"
        return True, f"volx_ok {stack}|{self.last_px_trend}"

    def _on_bar_close(
        self,
        bar_time: datetime,
        o: float,
        h: float,
        l: float,
        c: float,
        tbq_o: float,
        tsq_o: float,
        tbq_c: float,
        tsq_c: float,
        n_ticks: int,
        cum_vol: float | None = None,
        bar_volume: float | None = None,
    ) -> SignalResult | None:
        net = tbq_c - tsq_c
        imb = abs(net) / max(tbq_c, tsq_c, 1e-9) * 100.0
        self.last_net = net
        self.last_imb = imb
        self.last_tbq = tbq_c
        self.last_tsq = tsq_c
        self.last_close = c

        # bar volume from cumulative day volume if needed
        if bar_volume is None:
            if cum_vol is not None and self._prev_cum_vol is not None:
                bar_volume = max(0.0, float(cum_vol) - float(self._prev_cum_vol))
            else:
                bar_volume = 0.0
        bar_volume = float(bar_volume)

        if self._prev_tbq is None:
            st = "START"
            self.last_hv = "START"
            self.last_lv = "START"
            self.last_vol_stack = "START"
            self.last_px_trend = "NA"
        else:
            st = state_code_from_levels(
                tbq_c,
                self._prev_tbq,
                tsq_c,
                self._prev_tsq,
                c,
                float(self._prev_close or c),
                equal_pct=self.cfg.equal_pct,
                equal_px_pct=self.cfg.equal_px_pct,
                equal_pts=self.cfg.equal_pts,
            )
            hs = sign_px(
                h,
                float(self._prev_high if self._prev_high is not None else h),
                equal_px_pct=self.cfg.equal_px_pct,
                equal_pts=self.cfg.equal_pts,
            )
            ls = sign_px(
                l,
                float(self._prev_low if self._prev_low is not None else l),
                equal_px_pct=self.cfg.equal_px_pct,
                equal_pts=self.cfg.equal_pts,
            )
            vs = sign_rel(
                bar_volume,
                float(self._prev_bar_vol if self._prev_bar_vol is not None else 0.0),
                self.cfg.equal_pct,
            )
            self.last_hv = f"H{hs}_V{vs}"
            self.last_lv = f"L{ls}_V{vs}"
            self.last_vol_stack = self._vol_stack_label(
                bar_volume, self._prev_bar_vol, self._prev_prev_bar_vol
            )
            self.last_px_trend = self._px_trend_label(c)

        self.prev_state = self.last_state
        self.last_state = st
        self.last_label = short_label(st)

        # shift history after labels computed
        self._prev_prev_bar_vol = self._prev_bar_vol
        self._prev_tbq, self._prev_tsq, self._prev_close = tbq_c, tsq_c, c
        self._prev_high, self._prev_low = h, l
        self._prev_bar_vol = bar_volume
        self._close_hist.append(c)
        if len(self._close_hist) > 8:
            self._close_hist = self._close_hist[-8:]
        if cum_vol is not None:
            self._prev_cum_vol = float(cum_vol)

        bar = {
            "time": bar_time.isoformat(timespec="seconds"),
            "open": o,
            "high": h,
            "low": l,
            "close": c,
            "tbq_open": tbq_o,
            "tsq_open": tsq_o,
            "tbq_close": tbq_c,
            "tsq_close": tsq_c,
            "net": net,
            "imb_pct": imb,
            "n_ticks": n_ticks,
            "bar_volume": bar_volume,
            "state": st,
            "label": self.last_label,
            "hv": self.last_hv,
            "lv": self.last_lv,
            "vol_stack": self.last_vol_stack,
            "px_trend": self.last_px_trend,
            "prev_state": self.prev_state,
        }

        # --- manage open ---
        if self.position != "flat" and self.entry_price is not None:
            for hook in self.extra_exit_hooks:
                extra = hook(bar, self)
                if extra is not None:
                    return extra
            return self._manage(c, st, net)

        if st == "START":
            self.last_skip = "warmup"
            return None

        # --- entries ---
        if st in self.cfg.enter_long_states:
            if self.cfg.require_net_sign and net <= 0:
                self.last_skip = "long_blocked_net<=0"
                return None
            if self.cfg.min_imb_pct > 0 and imb < self.cfg.min_imb_pct:
                self.last_skip = f"long_blocked_imb<{self.cfg.min_imb_pct}"
                return None
            ok_hlv, hlv_why = self._hlv_ok()
            if not ok_hlv:
                self.last_skip = hlv_why
                return None
            ok_volx, volx_why = self._vol_exp_ok()
            if not ok_volx:
                self.last_skip = volx_why
                return None
            for filt in self.extra_entry_filters:
                if not filt(bar, self, "long"):
                    self.last_skip = "extra_entry_filter_long"
                    return None
            self._open("long", c)
            return SignalResult(
                action="BUY",
                position_after="long",
                price_delta=None,
                net=net,
                net_delta=None,
                prev_net_delta=None,
                reason=(
                    f"S9 enter_long {self.last_label} {hlv_why} {volx_why} "
                    f"net={net:.0f} imb={imb:.1f}% "
                    f"TP={self.cfg.tp_points:.0f} SL={self.cfg.sl_points:.0f}"
                ),
            )

        if self.cfg.allow_short and st in self.cfg.enter_short_states:
            if self.cfg.require_net_sign and net >= 0:
                self.last_skip = "short_blocked_net>=0"
                return None
            for filt in self.extra_entry_filters:
                if not filt(bar, self, "short"):
                    self.last_skip = "extra_entry_filter_short"
                    return None
            self._open("short", c)
            return SignalResult(
                action="SHORT",
                position_after="short",
                price_delta=None,
                net=net,
                net_delta=None,
                prev_net_delta=None,
                reason=(
                    f"S9 enter_short {self.last_label} net={net:.0f} "
                    f"imb={imb:.1f}% TP={self.cfg.tp_points:.0f} SL={self.cfg.sl_points:.0f}"
                ),
            )

        self.last_skip = f"wait state={self.last_label}"
        return None

    def _manage(self, c: float, st: str, net: float) -> SignalResult | None:
        assert self.entry_price is not None
        ep = float(self.entry_price)
        side = self.position
        move = (c - ep) if side == "long" else (ep - c)

        def done(reason: str) -> SignalResult:
            self._close()
            return SignalResult(
                action="CLOSE",
                position_after="flat",
                price_delta=(c - ep) if side == "long" else (ep - c),
                net=net,
                net_delta=None,
                prev_net_delta=None,
                reason=reason,
            )

        if move >= self.cfg.tp_points:
            return done(f"tp +{move:.1f}>={self.cfg.tp_points:.0f} state={self.last_label}")
        if move <= -self.cfg.sl_points:
            return done(f"sl {move:.1f}<=-{self.cfg.sl_points:.0f} state={self.last_label}")

        if side == "long":
            if self.cfg.require_net_sign and net < 0:
                return done(f"net_flip_exit net={net:.0f} state={self.last_label}")
            if st not in self.cfg.continue_long_states:
                self.break_count += 1
                if self.break_count >= self.cfg.break_bars:
                    return done(
                        f"state_break_exit {self.last_label} "
                        f"not_in_continue_long x{self.break_count}"
                    )
            else:
                self.break_count = 0
        elif side == "short":
            if self.cfg.require_net_sign and net > 0:
                return done(f"net_flip_exit net={net:.0f} state={self.last_label}")
            if st not in self.cfg.continue_short_states:
                self.break_count += 1
                if self.break_count >= self.cfg.break_bars:
                    return done(
                        f"state_break_exit {self.last_label} "
                        f"not_in_continue_short x{self.break_count}"
                    )
            else:
                self.break_count = 0
        return None

    def on_tick(
        self, now: datetime, ltp: float, message: dict[str, Any]
    ) -> SignalResult | None:
        """Accumulate into bar; emit signal only when a bar closes."""
        tbq = message.get("total_buy_quantity")
        tsq = message.get("total_sell_quantity")
        if tbq is None or tsq is None or ltp is None:
            self.last_skip = "incomplete_tick"
            return None
        try:
            tbq_f, tsq_f, px = float(tbq), float(tsq), float(ltp)
        except (TypeError, ValueError):
            return None
        vol_raw = message.get("volume_trade_for_the_day")
        try:
            vol_f = float(vol_raw) if vol_raw is not None else None
        except (TypeError, ValueError):
            vol_f = None

        key = self._floor(now)
        sig: SignalResult | None = None

        if self._bar_key is None:
            self._bar_key = key

        if key != self._bar_key:
            if self._o is not None and self._c is not None:
                sig = self._on_bar_close(
                    self._bar_key,
                    float(self._o),
                    float(self._h),
                    float(self._l),
                    float(self._c),
                    float(self._tbq_o),
                    float(self._tsq_o),
                    float(self._tbq_c),
                    float(self._tsq_c),
                    self._n,
                    cum_vol=self._vol_c,
                )
            self._bar_key = key
            self._o = self._h = self._l = self._c = px
            self._tbq_o = tbq_f
            self._tsq_o = tsq_f
            self._tbq_c = tbq_f
            self._tsq_c = tsq_f
            self._vol_c = vol_f
            self._n = 1
            return sig

        if self._o is None:
            self._o = self._h = self._l = self._c = px
            self._tbq_o = tbq_f
            self._tsq_o = tsq_f
            self._tbq_c = tbq_f
            self._tsq_c = tsq_f
            self._vol_c = vol_f
            self._n = 1
            return None

        self._h = max(float(self._h), px)
        self._l = min(float(self._l), px)
        self._c = px
        self._tbq_c = tbq_f
        self._tsq_c = tsq_f
        if vol_f is not None:
            self._vol_c = vol_f
        self._n += 1
        return None

    def on_bar_row(self, row: dict[str, Any]) -> SignalResult | None:
        """Offline/paper path: one completed bar row (from mtf export)."""
        try:
            ts = datetime.strptime(str(row["time"]), "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=IST
            )
        except Exception:
            ts = datetime.now(IST)
        cum = row.get("volume_close")
        try:
            cum_f = float(cum) if cum is not None else None
        except (TypeError, ValueError):
            cum_f = None
        bv = row.get("bar_volume")
        try:
            bv_f = float(bv) if bv is not None else None
        except (TypeError, ValueError):
            bv_f = None
        return self._on_bar_close(
            ts,
            float(row["open"]),
            float(row["high"]),
            float(row["low"]),
            float(row["close"]),
            float(row.get("tbq_open", row.get("tbq_close", 0))),
            float(row.get("tsq_open", row.get("tsq_close", 0))),
            float(row["tbq_close"]),
            float(row["tsq_close"]),
            int(row.get("n_ticks", 0) or 0),
            cum_vol=cum_f,
            bar_volume=bv_f,
        )


def state_s9_from_env() -> StateS9Strategy:
    try:
        from dotenv import load_dotenv

        load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
    except Exception:
        pass

    def _f(name: str, default: float) -> float:
        return float(os.getenv(name, str(default)))

    def _b(name: str, default: bool) -> bool:
        raw = os.getenv(name)
        if raw is None:
            return default
        return raw.strip().lower() in {"1", "true", "yes", "y"}

    def _states(name: str, default: frozenset[str]) -> frozenset[str]:
        raw = os.getenv(name, "").strip()
        if not raw:
            return default
        # accept short labels B+S-P+ or full TBQ+_TSQ-_P+
        out = set()
        for part in raw.split(","):
            p = part.strip()
            if not p:
                continue
            if p.startswith("TBQ"):
                out.add(p)
            elif p.startswith("B") and "S" in p and "P" in p:
                # B+S-P+ → TBQ+_TSQ-_P+
                try:
                    b = p[1]
                    s = p[3]
                    pr = p[5]
                    out.add(f"TBQ{b}_TSQ{s}_P{pr}")
                except IndexError:
                    continue
            else:
                out.add(p)
        return frozenset(out) if out else default

    eq_pct = clamp(_f("S9_EQUAL_PCT", 0.05), 0.03, 0.09)
    eq_pts_raw = os.getenv("S9_EQUAL_PTS", "").strip()
    eq_pts = float(eq_pts_raw) if eq_pts_raw else None
    cfg = StateS9Config(
        bar_minutes=int(_f("S9_BAR_MINUTES", 30)),
        tp_points=_f("S9_TP_POINTS", 26.0),
        sl_points=_f("S9_SL_POINTS", 16.0),
        equal_pct=eq_pct,
        equal_px_pct=_f("S9_EQUAL_PX_PCT", 0.0005),
        equal_pts=eq_pts,
        eps_qty=_f("S9_EPS_QTY", 1.0),
        eps_px=_f("S9_EPS_PX", 0.5),
        min_imb_pct=_f("S9_MIN_IMB_PCT", 5.0),
        require_net_sign=_b("S9_REQUIRE_NET_SIGN", True),
        allow_short=_b("S9_ALLOW_SHORT", False),
        break_bars=int(_f("S9_BREAK_BARS", 1)),
        enter_long_states=_states("S9_ENTER_LONG", ENTER_LONG_DEFAULT),
        continue_long_states=_states("S9_CONTINUE_LONG", CONTINUE_LONG_DEFAULT),
        enter_short_states=_states("S9_ENTER_SHORT", ENTER_SHORT_DEFAULT),
        continue_short_states=_states("S9_CONTINUE_SHORT", CONTINUE_SHORT_DEFAULT),
        require_hlv_confirm=_b("S9_REQUIRE_HLV", False),
        hlv_mode=os.getenv("S9_HLV_MODE", "any").strip().lower() or "any",
        require_vol_expansion=_b("S9_REQUIRE_VOL_EXPANSION", False),
        vol_exp_require_up=_b("S9_VOL_EXP_REQUIRE_UP", True),
        vol_exp_require_h_plus=_b("S9_VOL_EXP_REQUIRE_H_PLUS", False),
        vol_trend_lookback=int(_f("S9_VOL_TREND_LOOKBACK", 2)),
    )
    return StateS9Strategy(cfg)
