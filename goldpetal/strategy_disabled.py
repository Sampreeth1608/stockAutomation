"""No-op strategy stub — used when ENABLE_S* is false to save RAM."""

from __future__ import annotations

from typing import Any

from strategy import SignalResult


class DisabledStrategy:
    """Lightweight stand-in so the runner can skip loading unused strategy code."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.position = "flat"
        self.entry_price = None
        self.bias = "NEUTRAL"
        self.last_label = ""
        self.last_net = 0.0
        self.last_imb = 0.0
        self.last_skip = "disabled"
        self.enabled = False
        self._load_error = "ENABLE_* false"

    @property
    def status_line(self) -> str:
        return "DISABLED (not loaded — frees RAM)"

    def on_tick(self, *args: Any, **kwargs: Any) -> None:
        return None

    def release_decision_lock(self) -> None:
        return None

    @property
    def bar_debug(self) -> str:
        return "disabled"

    def maybe_signal(self, *args: Any, **kwargs: Any) -> None:
        return None

    def on_bar(self, *args: Any, **kwargs: Any) -> SignalResult:
        return SignalResult(
            action="HOLD",
            position_after="flat",
            price_delta=None,
            net=0.0,
            net_delta=None,
            prev_net_delta=None,
            reason="disabled",
        )

    def on_bar_row(self, *args: Any, **kwargs: Any) -> None:
        return None
