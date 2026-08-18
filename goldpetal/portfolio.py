"""Strategy portfolio: only trade strategies when the regime suits them."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

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
        "S8_NET_ZIGZAG",
        "S9_STATE30",
        "S10_LEGACY30",
        "S11_DISCOVERED",
        "S13_HHHL_DAY",
        "S16_HHHL_WICK_1H",
        "S18_OHLC_VOL_HTF",
        "S19_BODY_CLOSE_1H",
    },
    # S8/S10 bar-zigzag paper path had no regime filter in MTF sim — allow in CHOP too.
    # S5 has its own ATR/fee gate — keep it eligible in CHOP/QUIET so a smooth
    # 100–200pt Gold drift is not blocked while the short-window regime says QUIET.
    "CHOP": {
        "S4_OVERNIGHT",
        "S5_MINEDGE",
        "S8_NET_ZIGZAG",
        "S10_LEGACY30",
        "S13_HHHL_DAY",
        "S16_HHHL_WICK_1H",
        "S18_OHLC_VOL_HTF",
        "S19_BODY_CLOSE_1H",
    },
    "QUIET": {
        "S1_NETDELTA",
        "S2_BALANCE",
        "S4_OVERNIGHT",
        "S5_MINEDGE",
        "S6_MIN30",
        "S8_NET_ZIGZAG",
        "S9_STATE30",
        "S10_LEGACY30",
        "S11_DISCOVERED",
        "S13_HHHL_DAY",
        "S16_HHHL_WICK_1H",
        "S18_OHLC_VOL_HTF",
        "S19_BODY_CLOSE_1H",
    },
    "WIDE_SPREAD": set(),
    "UNKNOWN": {
        "S1_NETDELTA",
        "S2_BALANCE",
        "S3_ML",
        "S4_OVERNIGHT",
        "S5_MINEDGE",
        "S6_MIN30",
        "S8_NET_ZIGZAG",
        "S9_STATE30",
        "S10_LEGACY30",
        "S11_DISCOVERED",
        "S13_HHHL_DAY",
        "S16_HHHL_WICK_1H",
        "S18_OHLC_VOL_HTF",
        "S19_BODY_CLOSE_1H",
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
    flatten_when_blocked: bool = True

    def is_enabled(self, strategy: str) -> bool:
        return strategy in self.enabled

    def allows(self, strategy: str, regime: Regime) -> bool:
        if strategy not in self.enabled:
            return False
        return strategy in self.allowed.get(regime, set())

    def should_flatten(self, strategy: str, regime: Regime) -> bool:
        """True if open position should be closed because regime no longer fits."""
        if not self.flatten_when_blocked:
            return False
        if strategy not in self.enabled:
            return True
        return strategy not in self.allowed.get(regime, set())


def portfolio_from_env() -> PortfolioConfig:
    """Load enable flags from env.

    Slim paper default: S5, S8, S11, S13, S16, S18, S19 (S4 off — Angel/ticks daily
    swing pick was S13). S18/S19 stay paper-only.
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
    if on("ENABLE_S8", "true"):
        enabled.add("S8_NET_ZIGZAG")
    if on("ENABLE_S9", "false"):
        enabled.add("S9_STATE30")
    if on("ENABLE_S10", "false"):
        enabled.add("S10_LEGACY30")
    if on("ENABLE_S11", "true"):
        enabled.add("S11_DISCOVERED")
    if on("ENABLE_S13", "true"):
        enabled.add("S13_HHHL_DAY")
    if on("ENABLE_S16", "true"):
        enabled.add("S16_HHHL_WICK_1H")
    if on("ENABLE_S18", "true"):
        enabled.add("S18_OHLC_VOL_HTF")
    if on("ENABLE_S19", "true"):
        enabled.add("S19_BODY_CLOSE_1H")

    if not enabled:
        enabled = {
            "S5_MINEDGE",
            "S8_NET_ZIGZAG",
            "S11_DISCOVERED",
            "S13_HHHL_DAY",
            "S16_HHHL_WICK_1H",
            "S18_OHLC_VOL_HTF",
            "S19_BODY_CLOSE_1H",
        }

    flatten = on("FLATTEN_ON_BAD_REGIME", "true")
    return PortfolioConfig(enabled=enabled, flatten_when_blocked=flatten)
