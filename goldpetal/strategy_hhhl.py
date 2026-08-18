"""S12_HHHL30 — Higher-high / lower-low on the *same* 30m candle.

Retired from paper (ENABLE_S12=false). Module kept for tests and old tape.
"""

Judgement (user rule): every action is taken on the candle that printed the
signal, in that candle's last minute — never on the next bar.

  Watch: during a 30m candle, if current high > previous high (or LL for short).
  LONG  entry: that same candle's last-minute close > open.
  LONG  exit:  a later candle with high < prev_high AND last-minute close < open.
  SHORT entry: same candle last-minute close < open after LL.
  SHORT exit:  a later candle with low > prev_low AND last-minute close > open.

If the *exit* candle also qualifies as a new long or short, re-enter on that
same candle (close the old trade, open the new side). A CLOSE-only does not
lock the confirm window, so a later last-minute tick can still enter.

No fallback on the first tick of the next bar (that was the 60-minute delay).
Defaults: 30m bars, min_range=5, no_flip=True.
Previous + in-progress candle OHLC seeded from ticks.db.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from strategy import Position, SignalResult
from quality_filters import hhll_break_ok

IST = ZoneInfo("Asia/Kolkata")


@dataclass
class HhhlConfig:
    bar_minutes: int = 30
    min_range: float = 5.0
    no_flip: bool = True
    allow_long: bool = True
    allow_short: bool = True
    # Confirm in the final N minutes of the candle (default: last 1 minute).
    confirm_minutes: int = 1
    # Close must finish this many points beyond prev high/low (false-break filter).
    min_close_beyond: float = 3.0
    # Reject if the break wick is most of the poke (close near the prior level).
    max_break_wick_frac: float = 0.6


class HhhlCandleStrategy:
    name = "S12_HHHL30"

    def __init__(self, cfg: HhhlConfig | None = None, *, seed: bool = True) -> None:
        self.cfg = cfg or HhhlConfig()
        self.position: Position = "flat"
        self.entry_price: float | None = None
        self.last_skip: str | None = None
        self.prev_o = self.prev_h = self.prev_l = self.prev_c = None
        self._bar_key: datetime | None = None
        self._bar_o = self._bar_h = self._bar_l = self._bar_c = None
        self._bar_n = 0
        # True only after we emitted a BUY/SHORT/CLOSE for this candle.
        self._decided_this_bar = False
        self._watching: str | None = None  # "hh" | "ll" | "both" | None
        if seed:
            self.seed_from_ticks()

    @property
    def status_line(self) -> str:
        c = self.cfg
        prev = (
            f"prevH={self.prev_h:.0f} prevL={self.prev_l:.0f}"
            if self.prev_h is not None and self.prev_l is not None
            else "prev=none"
        )
        return (
            f"TF={c.bar_minutes}m min_range={c.min_range:.0f} "
            f"beyond={c.min_close_beyond:.0f} "
            f"confirm={c.confirm_minutes}m no_flip={c.no_flip} "
            f"L={c.allow_long} S={c.allow_short} {prev} "
            f"watch={self._watching or '-'} skip={self.last_skip or '-'} "
            f"pos={self.position}"
        )

    def _floor_bar(self, ts: datetime) -> datetime:
        ts = ts.astimezone(IST)
        midnight = ts.replace(hour=0, minute=0, second=0, microsecond=0)
        mins = int((ts - midnight).total_seconds() // 60)
        block = (mins // max(1, self.cfg.bar_minutes)) * max(1, self.cfg.bar_minutes)
        return midnight + timedelta(minutes=block)

    def seed_from_ticks(self, db_path: Path | None = None) -> None:
        """Seed prev completed bar + hydrate the in-progress current bar from DB."""
        try:
            from mtf_bars import build_rich_bars, load_tick_rows

            db = db_path or (Path(__file__).resolve().parent / "data" / "ticks.db")
            if not db.exists():
                return
            rows = load_tick_rows(db)
            minutes = max(1, int(self.cfg.bar_minutes))
            bars = build_rich_bars(rows, f"{minutes}m", minutes)
            if not bars:
                return
            now = datetime.now(IST)
            cur_key = self._floor_bar(now)
            completed = []
            current = None
            for b in bars:
                try:
                    bt = datetime.strptime(str(b.time)[:19], "%Y-%m-%d %H:%M:%S").replace(
                        tzinfo=IST
                    )
                except ValueError:
                    continue
                if bt < cur_key:
                    completed.append(b)
                elif bt == cur_key:
                    current = b
            if self.prev_h is None and completed:
                b = completed[-1]
                self.prev_o = float(b.open)
                self.prev_h = float(b.high)
                self.prev_l = float(b.low)
                self.prev_c = float(b.close)
            if self._bar_key is None and current is not None:
                self._bar_key = cur_key
                self._bar_o = float(current.open)
                self._bar_h = float(current.high)
                self._bar_l = float(current.low)
                self._bar_c = float(current.close)
                self._bar_n = max(1, int(getattr(current, "n_ticks", 1) or 1))
                self._decided_this_bar = False
                self._update_watching()
        except Exception:
            return

    def seed_prev_from_ticks(self, db_path: Path | None = None) -> None:
        """Back-compat alias."""
        self.seed_from_ticks(db_path)

    def _in_confirm_window(self, now: datetime, bar_key: datetime) -> bool:
        """True in the last `confirm_minutes` of this candle."""
        bar_end = bar_key + timedelta(minutes=max(1, self.cfg.bar_minutes))
        start = bar_end - timedelta(minutes=max(1, self.cfg.confirm_minutes))
        return start <= now < bar_end

    def _update_watching(self) -> None:
        if self.prev_h is None or self.prev_l is None:
            self._watching = None
            return
        if self._bar_h is None or self._bar_l is None:
            return
        saw_hh = float(self._bar_h) > float(self.prev_h)
        saw_ll = float(self._bar_l) < float(self.prev_l)
        if saw_hh and saw_ll:
            self._watching = "both"
        elif saw_hh:
            self._watching = "hh"
        elif saw_ll:
            self._watching = "ll"

    def release_decision_lock(self) -> None:
        """Allow another confirm-window attempt (e.g. after entry gate reject)."""
        self._decided_this_bar = False

    def on_tick(
        self, now: datetime, ltp: float, message: dict[str, Any] | None = None
    ) -> SignalResult | None:
        del message  # OHLC from LTP only
        now = now.astimezone(IST)
        key = self._floor_bar(now)
        px = float(ltp)

        if self._bar_key is None:
            self._bar_key = key
            self._bar_o = self._bar_h = self._bar_l = self._bar_c = px
            self._bar_n = 1
            self._decided_this_bar = False
            self._watching = None
            return None

        if key != self._bar_key:
            # Candle ended. Do not decide on this new bar's first tick — that
            # would enter/exit on the *next* candle (60m delay). Missed last
            # minute of the closed bar = no trade on that bar.
            self.prev_o = float(self._bar_o or px)
            self.prev_h = float(self._bar_h or px)
            self.prev_l = float(self._bar_l or px)
            self.prev_c = float(self._bar_c or px)
            self._bar_key = key
            self._bar_o = self._bar_h = self._bar_l = self._bar_c = px
            self._bar_n = 1
            self._decided_this_bar = False
            self._watching = None
            return None

        # Same candle — update OHLC and watch for HH/LL breaks
        assert self._bar_h is not None and self._bar_l is not None
        self._bar_h = max(self._bar_h, px)
        self._bar_l = min(self._bar_l, px)
        self._bar_c = px
        self._bar_n += 1
        self._update_watching()

        if self._decided_this_bar:
            return None
        if self.prev_h is None or self.prev_l is None:
            self.last_skip = "need_prev_bar"
            return None
        if not self._in_confirm_window(now, key):
            self.last_skip = f"watching={self._watching or 'none'}"
            return None

        # Last minute(s): re-check every tick until we get a real signal.
        result = self._decide(
            float(self._bar_o or px),
            float(self._bar_h),
            float(self._bar_l),
            float(self._bar_c or px),
        )
        # Lock only after a new position. CLOSE must not block same-candle re-entry.
        if result is not None and result.action in {"BUY", "SHORT"}:
            self._decided_this_bar = True
        return result

    def on_bar_row(self, row: dict[str, Any]) -> SignalResult | None:
        """Offline / paper-sim path from a completed OHLC row."""
        return self._on_bar_close(
            float(row["open"]),
            float(row["high"]),
            float(row["low"]),
            float(row["close"]),
        )

    def _on_bar_close(self, o: float, h: float, l: float, c: float) -> SignalResult | None:
        """Bar-series API: each row is a finished candle vs previous finished candle."""
        if self.prev_h is None or self.prev_l is None:
            self.prev_o, self.prev_h, self.prev_l, self.prev_c = o, h, l, c
            return None
        result = self._decide(o, h, l, c)
        self.prev_o, self.prev_h, self.prev_l, self.prev_c = o, h, l, c
        return result

    def _open_if_flat(
        self,
        o: float,
        h: float,
        l: float,
        c: float,
        ph: float,
        pl: float,
        range_pts: float,
        *,
        reason_prefix: str = "",
    ) -> SignalResult | None:
        """Enter long/short from flat when this candle has HH+green or LL+red."""
        want_long = self.cfg.allow_long and h > ph and c > o
        want_short = self.cfg.allow_short and l < pl and c < o
        if range_pts < self.cfg.min_range:
            self.last_skip = f"min_range {range_pts:.1f}<{self.cfg.min_range}"
            return None
        if want_long and not want_short:
            ok, why = hhll_break_ok(
                side="long",
                o=o,
                h=h,
                l=l,
                c=c,
                prev_h=ph,
                prev_l=pl,
                min_close_beyond=self.cfg.min_close_beyond,
                max_break_wick_frac=self.cfg.max_break_wick_frac,
            )
            if not ok:
                self.last_skip = why
                return None
            self.position = "long"
            self.entry_price = c
            return SignalResult(
                action="BUY",
                position_after="long",
                price_delta=None,
                net=None,
                net_delta=None,
                prev_net_delta=None,
                reason=(
                    f"{reason_prefix}s12 long same-candle HH+green range={range_pts:.1f} "
                    f"H={h:.1f}>prevH={ph:.1f} C={c:.1f}>O={o:.1f}"
                ),
            )
        if want_short and not want_long:
            ok, why = hhll_break_ok(
                side="short",
                o=o,
                h=h,
                l=l,
                c=c,
                prev_h=ph,
                prev_l=pl,
                min_close_beyond=self.cfg.min_close_beyond,
                max_break_wick_frac=self.cfg.max_break_wick_frac,
            )
            if not ok:
                self.last_skip = why
                return None
            self.position = "short"
            self.entry_price = c
            return SignalResult(
                action="SHORT",
                position_after="short",
                price_delta=None,
                net=None,
                net_delta=None,
                prev_net_delta=None,
                reason=(
                    f"{reason_prefix}s12 short same-candle LL+red range={range_pts:.1f} "
                    f"L={l:.1f}<prevL={pl:.1f} C={c:.1f}<O={o:.1f}"
                ),
            )
        if self._watching == "hh" and not (c > o):
            self.last_skip = "hh_but_not_green_yet"
        elif self._watching == "ll" and not (c < o):
            self.last_skip = "ll_but_not_red_yet"
        else:
            self.last_skip = "no_signal"
        return None

    def _decide(self, o: float, h: float, l: float, c: float) -> SignalResult | None:
        if self.prev_h is None or self.prev_l is None:
            self.last_skip = "need_prev_bar"
            return None
        ph = float(self.prev_h)
        pl = float(self.prev_l)

        range_pts = h - l
        want_long = self.cfg.allow_long and h > ph and c > o
        want_short = self.cfg.allow_short and l < pl and c < o
        exit_long = h < ph and c < o
        exit_short = l > pl and c > o

        if self.position == "long":
            if exit_long or (want_short and not self.cfg.no_flip):
                self.position = "flat"
                self.entry_price = None
                reenter = self._open_if_flat(
                    o, h, l, c, ph, pl, range_pts,
                    reason_prefix="s12 re-enter after close ",
                )
                if reenter is not None:
                    return reenter
                return SignalResult(
                    action="CLOSE",
                    position_after="flat",
                    price_delta=None,
                    net=None,
                    net_delta=None,
                    prev_net_delta=None,
                    reason=f"s12 exit long same-candle LH+red range={range_pts:.1f}",
                )
            self.last_skip = "hold_long"
            return None

        if self.position == "short":
            if exit_short or (want_long and not self.cfg.no_flip):
                self.position = "flat"
                self.entry_price = None
                reenter = self._open_if_flat(
                    o, h, l, c, ph, pl, range_pts,
                    reason_prefix="s12 re-enter after close ",
                )
                if reenter is not None:
                    return reenter
                return SignalResult(
                    action="CLOSE",
                    position_after="flat",
                    price_delta=None,
                    net=None,
                    net_delta=None,
                    prev_net_delta=None,
                    reason=f"s12 exit short same-candle HL+green range={range_pts:.1f}",
                )
            self.last_skip = "hold_short"
            return None

        return self._open_if_flat(o, h, l, c, ph, pl, range_pts)


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "y"}


def hhhl_from_env() -> HhhlCandleStrategy:
    try:
        from dotenv import load_dotenv

        load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
    except Exception:
        pass
    cfg = HhhlConfig(
        bar_minutes=int(os.getenv("S12_BAR_MINUTES", "30")),
        min_range=float(os.getenv("S12_MIN_RANGE", "5")),
        no_flip=_env_flag("S12_NO_FLIP", True),
        allow_long=_env_flag("S12_ALLOW_LONG", True),
        allow_short=_env_flag("S12_ALLOW_SHORT", True),
        confirm_minutes=int(os.getenv("S12_CONFIRM_MINUTES", "1")),
        min_close_beyond=float(os.getenv("S12_MIN_CLOSE_BEYOND", "3")),
        max_break_wick_frac=float(os.getenv("S12_MAX_BREAK_WICK_FRAC", "0.6")),
    )
    return HhhlCandleStrategy(cfg)
