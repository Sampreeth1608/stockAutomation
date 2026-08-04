"""Trading charges + tax for paper PnL.

Env (in .env):
  TRADE_CHARGE_PER_SIDE=20      # ₹ per order leg (entry and exit each)
  TRADE_ROUND_TRIP_CHARGE=      # optional override: flat ₹ per closed trade
  TAX_RATE=0.30                 # 30% on profit after charges (losses: no tax credit here)
  LOT_SIZE=1                    # multiplier on price-diff PnL
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ChargeConfig:
    charge_per_side: float = 20.0
    round_trip_charge: float | None = None
    tax_rate: float = 0.30
    lot_size: float = 1.0

    def round_trip_cost(self) -> float:
        if self.round_trip_charge is not None:
            return float(self.round_trip_charge)
        return float(self.charge_per_side) * 2.0


def charges_from_env() -> ChargeConfig:
    try:
        from dotenv import load_dotenv

        load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
    except Exception:
        pass

    per_side = float(os.getenv("TRADE_CHARGE_PER_SIDE", "20"))
    raw_rt = os.getenv("TRADE_ROUND_TRIP_CHARGE", "").strip()
    rt = float(raw_rt) if raw_rt else None
    tax = float(os.getenv("TAX_RATE", "0.30"))
    lot = float(os.getenv("LOT_SIZE", "1"))
    return ChargeConfig(
        charge_per_side=per_side,
        round_trip_charge=rt,
        tax_rate=tax,
        lot_size=lot,
    )


def apply_charges_and_tax(
    gross_pnl: float,
    cfg: ChargeConfig | None = None,
) -> dict[str, float]:
    """Return gross, charges, after_charges, tax, after_tax."""
    cfg = cfg or charges_from_env()
    gross = float(gross_pnl) * float(cfg.lot_size)
    charges = cfg.round_trip_cost()
    after_charges = gross - charges
    # Tax only on positive profit after charges (simple paper model)
    tax = max(0.0, after_charges) * float(cfg.tax_rate)
    after_tax = after_charges - tax
    return {
        "gross_pnl": round(gross, 2),
        "charges": round(charges, 2),
        "pnl_after_charges": round(after_charges, 2),
        "tax": round(tax, 2),
        "pnl_after_tax": round(after_tax, 2),
    }
