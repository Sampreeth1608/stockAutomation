"""Strategy portfolio: only trade strategies when the regime suits them."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from regime import Regime


# Default: based on observed paper results — S2 is noisy in chop; S1 likes structure;
# S3 needs model edge; nothing trades in wide spreads.
DEFAULT_ALLOWED: dict[Regime, set[str]] = {
    "TREND": {"S1_NETDELTA", "S3_ML", "S4_OVERNIGHT", "S5_MINEDGE"},
    "CHOP": {"S4_OVERNIGHT"},  # S5 skips chop by edge size usually
    "QUIET": {"S1_NETDELTA", "S4_OVERNIGHT"},
    "WIDE_SPREAD": set(),
    "UNKNOWN": {"S1_NETDELTA", "S3_ML", "S4_OVERNIGHT", "S5_MINEDGE"},
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

    ENABLE_S1=true/false, ENABLE_S2=..., ENABLE_S3=...
    FLATTEN_ON_BAD_REGIME=true
    """
    enabled: set[str] = set()
    if os.getenv("ENABLE_S1", "true").strip().lower() in {"1", "true", "yes", "y"}:
        enabled.add("S1_NETDELTA")
    if os.getenv("ENABLE_S2", "false").strip().lower() in {"1", "true", "yes", "y"}:
        # default false — currently loses in chop
        enabled.add("S2_BALANCE")
    else:
        # keep explicit false as default for S2 unless user enables
        pass
    if os.getenv("ENABLE_S3", "true").strip().lower() in {"1", "true", "yes", "y"}:
        enabled.add("S3_ML")
    if os.getenv("ENABLE_S4", "true").strip().lower() in {"1", "true", "yes", "y"}:
        enabled.add("S4_OVERNIGHT")
    if os.getenv("ENABLE_S5", "true").strip().lower() in {"1", "true", "yes", "y"}:
        enabled.add("S5_MINEDGE")

    # If user set none of the vars oddly empty, fall back
    if not enabled:
        enabled = {"S1_NETDELTA", "S3_ML", "S4_OVERNIGHT", "S5_MINEDGE"}

    flatten = os.getenv("FLATTEN_ON_BAD_REGIME", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "y",
    }
    return PortfolioConfig(enabled=enabled, flatten_when_blocked=flatten)
