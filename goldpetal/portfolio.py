"""Strategy portfolio: only trade strategies when the regime suits them."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from amise_slots import (
    AMISE_ENABLE,
    AMISE_SLOT_BOOKS,
    FIRST_AMISE_N,
    allocated_slots,
    enable_key,
    is_amise_slot,
    slot_name,
)
from regime import Regime


# Default: based on observed paper results — S2 is noisy in chop; S1 likes structure;
# S3 needs model edge; nothing trades in wide spreads.
DEFAULT_ALLOWED: dict[Regime, set[str]] = {
    "TREND": {
        "S1_NETDELTA",
        "S2_BALANCE",
        "S3_ML",
        "S4_OVERNIGHT",
        "S5_MINEDGE",
        "S6_MIN30",
        "FLOW_BRAIN",
        "OVERNIGHT_GAP",
        "S8_NET_ZIGZAG",
        "S9_STATE30",
        "S10_LEGACY30",
        "S11_DISCOVERED",
        "S13_HHHL_DAY",
        "S16_HHHL_WICK_1H",
        "S18_OHLC_VOL_HTF",
        "S19_BODY_CLOSE_1H",
        "S20_FADE_HL",
        *AMISE_SLOT_BOOKS,
    },
    # S8/S10 bar-zigzag paper path had no regime filter in MTF sim — allow in CHOP too.
    # S5 has its own ATR/fee gate — keep it eligible in CHOP/QUIET so a smooth
    # 100–200pt Gold drift is not blocked while the short-window regime says QUIET.
    "CHOP": {
        "S4_OVERNIGHT",
        "S5_MINEDGE",
        "FLOW_BRAIN",
        "OVERNIGHT_GAP",
        "S8_NET_ZIGZAG",
        "S10_LEGACY30",
        "S13_HHHL_DAY",
        "S16_HHHL_WICK_1H",
        "S18_OHLC_VOL_HTF",
        "S19_BODY_CLOSE_1H",
        "S20_FADE_HL",
        *AMISE_SLOT_BOOKS,
    },
    "QUIET": {
        "S1_NETDELTA",
        "S2_BALANCE",
        "S4_OVERNIGHT",
        "S5_MINEDGE",
        "S6_MIN30",
        "FLOW_BRAIN",
        "OVERNIGHT_GAP",
        "S8_NET_ZIGZAG",
        "S9_STATE30",
        "S10_LEGACY30",
        "S11_DISCOVERED",
        "S13_HHHL_DAY",
        "S16_HHHL_WICK_1H",
        "S18_OHLC_VOL_HTF",
        "S19_BODY_CLOSE_1H",
        "S20_FADE_HL",
        *AMISE_SLOT_BOOKS,
    },
    "WIDE_SPREAD": {"OVERNIGHT_GAP", "S16_HHHL_WICK_1H"},
    "UNKNOWN": {
        "S1_NETDELTA",
        "S2_BALANCE",
        "S3_ML",
        "S4_OVERNIGHT",
        "S5_MINEDGE",
        "S6_MIN30",
        "FLOW_BRAIN",
        "OVERNIGHT_GAP",
        "S8_NET_ZIGZAG",
        "S9_STATE30",
        "S10_LEGACY30",
        "S11_DISCOVERED",
        "S13_HHHL_DAY",
        "S16_HHHL_WICK_1H",
        "S18_OHLC_VOL_HTF",
        "S19_BODY_CLOSE_1H",
        "S20_FADE_HL",
        *AMISE_SLOT_BOOKS,
    },
}


@dataclass
class PortfolioConfig:
    """Which strategies may open/hold in each regime."""

    enabled: set[str] = field(
        default_factory=lambda: {"S1_NETDELTA", "S2_BALANCE", "S3_ML"}
    )
    allowed: dict[Regime, set[str]] = field(
        default_factory=lambda: {k: set(v) for k, v in DEFAULT_ALLOWED.items()}
    )
    flatten_when_blocked: bool = False

    def is_enabled(self, strategy: str) -> bool:
        return strategy in self.enabled

    def _in_regime(self, strategy: str, regime: Regime) -> bool:
        allowed = self.allowed.get(regime, set())
        if strategy in allowed:
            return True
        # S25+ share S21's regime map so a new named slot can trade the same day.
        if is_amise_slot(strategy):
            return "S21_AMISE" in allowed
        return False

    def allows(self, strategy: str, regime: Regime) -> bool:
        """Every enabled book may open. Market regime no longer gates entries."""
        del regime
        return strategy in self.enabled

    def should_flatten(self, strategy: str, regime: Regime) -> bool:
        """Never dump for regime. Market regime was removed."""
        del strategy, regime
        return False


def portfolio_from_env() -> PortfolioConfig:
    """Load enable flags from env.

    Slim paper default: S5, S8, S13, S16, S18, S19, overnight gap (S4 off — Angel/ticks daily
    swing pick was S13). S11 pack ML is off the hot path (ENABLE_S11 default
    false) — AMISE factory holds the research ML. S18 stays paper until 40% WR% AC.
    S19 paper 1h body+close is on (Live after 40%). Overnight gap papers; Live tab
    after closed trades and WR% AC ≥ 40 — you Arm. S20 stays off until you ask.
    AMISE slots S21+ ENABLE after Lab Approve.
    """
    def on(key: str, default: str) -> bool:
        return os.getenv(key, default).strip().lower() in {"1", "true", "yes", "y"}

    enabled: set[str] = set()
    if on("ENABLE_S1", "false"):
        enabled.add("S1_NETDELTA")
    if on("ENABLE_S2", "false"):
        enabled.add("S2_BALANCE")
    if on("ENABLE_S3", "false"):
        enabled.add("S3_ML")
    if on("ENABLE_S4", "false"):
        enabled.add("S4_OVERNIGHT")
    if on("ENABLE_S5", "true"):
        enabled.add("S5_MINEDGE")
    if on("ENABLE_S6", "false"):
        enabled.add("S6_MIN30")
    if on("ENABLE_FLOW_BRAIN", "false"):
        enabled.add("FLOW_BRAIN")
    if on("ENABLE_OVERNIGHT_GAP", "true"):
        enabled.add("OVERNIGHT_GAP")
    if on("ENABLE_S8", "true"):
        enabled.add("S8_NET_ZIGZAG")
    if on("ENABLE_S9", "false"):
        enabled.add("S9_STATE30")
    if on("ENABLE_S10", "false"):
        enabled.add("S10_LEGACY30")
    if on("ENABLE_S11", "false"):
        enabled.add("S11_DISCOVERED")
    if on("ENABLE_S13", "true"):
        enabled.add("S13_HHHL_DAY")
    if on("ENABLE_S16", "true"):
        enabled.add("S16_HHHL_WICK_1H")
    if on("ENABLE_S18", "true"):
        enabled.add("S18_OHLC_VOL_HTF")
    if on("ENABLE_S19", "true"):
        enabled.add("S19_BODY_CLOSE_1H")
    if on("ENABLE_S20", "false"):
        enabled.add("S20_FADE_HL")
    for name, key in AMISE_ENABLE.items():
        if on(key, "false"):
            enabled.add(name)
    for name in allocated_slots():
        if on(enable_key(name), "false"):
            enabled.add(name)
    for key, val in list(os.environ.items()):
        if not key.startswith("ENABLE_S") or key in AMISE_ENABLE.values():
            continue
        try:
            n = int(key.replace("ENABLE_S", "", 1))
        except ValueError:
            continue
        if n < FIRST_AMISE_N:
            continue
        if str(val or "").strip().lower() in {"1", "true", "yes", "y"}:
            enabled.add(slot_name(n))

    if not enabled:
        enabled = {
            "S5_MINEDGE",
            "S8_NET_ZIGZAG",
            "S13_HHHL_DAY",
            "S16_HHHL_WICK_1H",
            "S18_OHLC_VOL_HTF",
            "S19_BODY_CLOSE_1H",
            "OVERNIGHT_GAP",
        }

    flatten = on("FLATTEN_ON_BAD_REGIME", "false")
    return PortfolioConfig(enabled=enabled, flatten_when_blocked=flatten)
