"""S11: discovered multi-model strategy pack (weekend approval → paper).

Loads a pack JSON written by discover_strategies.py (model path + thresholds
+ optional imbalance gate). Off until ENABLE_S11 + S11_PACK_PATH are set.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from strategy_ml import MLStrategy


class DiscoveredStrategy(MLStrategy):
    """Same live scoring as S3, but configured from a discovery pack."""

    name = "S11_DISCOVERED"

    def __init__(
        self,
        pack_path: Path | None = None,
        *,
        min_imb: float = 0.0,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.pack_path = pack_path
        self.min_imb = float(min_imb)
        self.pack_id = ""
        if pack_path is not None:
            self.pack_id = pack_path.stem

    @property
    def status_line(self) -> str:
        base = super().status_line
        pack = self.pack_path or "?"
        return f"{base} pack={pack} min_imb={self.min_imb:.2f}"

    def maybe_signal(self, now):  # type: ignore[no-untyped-def]
        result = super().maybe_signal(now)
        if result is None or result.action not in {"BUY", "SHORT"}:
            return result
        if self.min_imb <= 0:
            return result
        # Re-check last buffer row imbalance if available
        if not self.buffer:
            return result
        try:
            import pandas as pd

            from ml_features import build_features

            feat = build_features(pd.DataFrame(list(self.buffer)))
            if feat.empty or "imb_l1" not in feat.columns:
                return result
            imb = float(feat["imb_l1"].iloc[-1])
        except Exception:
            return result
        if result.action == "BUY" and imb < self.min_imb:
            self.position = "flat"
            self.last_skip = f"s11_imb_gate imb={imb:.3f}<{self.min_imb}"
            return None
        if result.action == "SHORT" and imb > -self.min_imb:
            self.position = "flat"
            self.last_skip = f"s11_imb_gate imb={imb:.3f}>-{self.min_imb}"
            return None
        result.reason = f"{result.reason} | imb={imb:.3f}"
        return result


def discovered_from_env() -> DiscoveredStrategy:
    pack_raw = os.getenv("S11_PACK_PATH", "").strip()
    if not pack_raw:
        # Disabled placeholder — enabled=False via missing model
        s = DiscoveredStrategy(model_path=Path("/nonexistent/s11.joblib"))
        s.enabled = False
        s._load_error = "S11_PACK_PATH not set"
        return s
    pack_path = Path(pack_raw)
    if not pack_path.exists():
        s = DiscoveredStrategy(pack_path=pack_path, model_path=Path("/nonexistent/s11.joblib"))
        s.enabled = False
        s._load_error = f"pack missing: {pack_path}"
        return s
    try:
        pack = json.loads(pack_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        s = DiscoveredStrategy(pack_path=pack_path, model_path=Path("/nonexistent/s11.joblib"))
        s.enabled = False
        s._load_error = f"pack read failed: {exc}"
        return s
    model_path = Path(str(pack.get("model_path") or ""))
    return DiscoveredStrategy(
        pack_path=pack_path,
        model_path=model_path if model_path.exists() else None,
        buy_prob=float(pack.get("buy_prob", 0.58)),
        short_prob=float(pack.get("short_prob", 0.42)),
        min_hold_sec=float(pack.get("min_hold_sec", 30)),
        every_n_ticks=int(pack.get("every_n_ticks", 5)),
        min_imb=float(pack.get("min_imb", 0.0)),
    )
