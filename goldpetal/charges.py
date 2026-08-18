"""Angel One MCX futures charges for paper PnL (Gold Petal).

Defaults match Angel One commodity futures tariff (non-agri MCX):
  - Brokerage: ₹20 / executed order (set BROKERAGE_PROMO=true for ₹0 while offer lasts)
  - MCX txn charges (futures): 0.00210% of turnover
  - CTT (non-agri): 0.01% on SELL turnover only
  - SEBI: ₹10 / crore of turnover (= 0.0001%)
  - Stamp duty: 0.002% on BUY turnover
  - GST: 18% on (brokerage + exchange txn + SEBI)

Gold Petal contract (MCX official):
  - Trading unit / lot: 1 gram
  - Quotation / base value: ₹ per 1 gram
  - Tick: ₹1 per 1 gram → 1 point ≈ ₹1 PnL per lot
  - Turnover per lot ≈ price * LOT_SIZE * TURNOVER_MULT (default mult=1.0)

Tax on trading profit (user request): TAX_RATE (default 30%) on profit after charges.

IGNORE_FEES=true (default): paper PnL and strategy gates ignore all fees/tax so
strategies optimize gross points. Set IGNORE_FEES=false to restore Angel schedule.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "y"}


def ignore_fees_enabled() -> bool:
    """Global switch: no fees/tax in paper math and no fee cover gates."""
    try:
        from dotenv import load_dotenv

        load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
    except Exception:
        pass
    # Default ON — operator asked to remove fees/charges for all strategies.
    return _env_flag("IGNORE_FEES", True)


def paper_lots() -> float:
    """How many lots the paper tape / learner / fee gates use.

    LOT_SIZE=1 is the 1g Gold Petal contract (₹1 per point per lot).
    PAPER_LOTS=100 is the paper size on the desk. Live stays LIVE_MAX_LOTS.
    """
    try:
        from dotenv import load_dotenv

        load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
    except Exception:
        pass
    raw = os.getenv("PAPER_LOTS", "100")
    try:
        n = float(raw)
    except (TypeError, ValueError):
        n = 100.0
    return max(1.0, n)


@dataclass(frozen=True)
class ChargeConfig:
    brokerage_per_order: float = 20.0
    brokerage_promo: bool = False  # ₹0 brokerage while promo active
    mcx_txn_rate: float = 0.0000210  # 0.00210%
    ctt_sell_rate: float = 0.0001  # 0.01% non-agri sell
    sebi_rate: float = 0.000001  # ₹10/crore = 0.0001%
    stamp_buy_rate: float = 0.00002  # 0.002% on buy
    gst_rate: float = 0.18
    tax_rate: float = 0.30
    lot_size: float = 1.0
    # Quote is ₹/1g, lot=1g → multiplier 1.0 (1 point = ₹1)
    turnover_mult: float = 1.0
    ignore_fees: bool = False


def zero_charge_config(*, lot_size: float = 1.0, turnover_mult: float = 1.0) -> ChargeConfig:
    return ChargeConfig(
        brokerage_per_order=0.0,
        brokerage_promo=True,
        mcx_txn_rate=0.0,
        ctt_sell_rate=0.0,
        sebi_rate=0.0,
        stamp_buy_rate=0.0,
        gst_rate=0.0,
        tax_rate=0.0,
        lot_size=lot_size,
        turnover_mult=turnover_mult,
        ignore_fees=True,
    )


def charges_from_env() -> ChargeConfig:
    try:
        from dotenv import load_dotenv

        load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
    except Exception:
        pass

    lot_size = float(os.getenv("LOT_SIZE", "1"))
    turnover_mult = float(os.getenv("TURNOVER_MULT", "1.0"))
    if ignore_fees_enabled():
        return zero_charge_config(lot_size=lot_size, turnover_mult=turnover_mult)

    return angel_charges_from_env(lot_size=lot_size, turnover_mult=turnover_mult)


def angel_charges_from_env(
    *,
    lot_size: float | None = None,
    turnover_mult: float | None = None,
) -> ChargeConfig:
    """Real Angel MCX fee + tax schedule for *post-trade* reporting.

    Ignores IGNORE_FEES so journals/Streamlit still show charges after a trade
    even when strategies ride fee-free (IGNORE_FEES=true).
    """
    try:
        from dotenv import load_dotenv

        load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
    except Exception:
        pass

    ls = float(os.getenv("LOT_SIZE", "1") if lot_size is None else lot_size)
    tm = float(os.getenv("TURNOVER_MULT", "1.0") if turnover_mult is None else turnover_mult)
    promo = _env_flag("BROKERAGE_PROMO", False)
    brokerage = float(os.getenv("BROKERAGE_PER_ORDER", "20"))
    if promo:
        brokerage = 0.0

    return ChargeConfig(
        brokerage_per_order=brokerage,
        brokerage_promo=promo,
        mcx_txn_rate=float(os.getenv("MCX_TXN_RATE", "0.0000210")),
        ctt_sell_rate=float(os.getenv("CTT_SELL_RATE", "0.0001")),
        sebi_rate=float(os.getenv("SEBI_RATE", "0.000001")),
        stamp_buy_rate=float(os.getenv("STAMP_BUY_RATE", "0.00002")),
        gst_rate=float(os.getenv("GST_RATE", "0.18")),
        tax_rate=float(os.getenv("TAX_RATE", "0.30")),
        lot_size=ls,
        turnover_mult=tm,
        ignore_fees=False,
    )



def _leg_charges(
    *,
    side: str,
    price: float,
    cfg: ChargeConfig,
) -> dict[str, float]:
    """Charges for one executed order leg (BUY or SELL)."""
    turnover = abs(float(price)) * cfg.lot_size * cfg.turnover_mult
    brokerage = float(cfg.brokerage_per_order)
    txn = turnover * cfg.mcx_txn_rate
    sebi = turnover * cfg.sebi_rate
    stamp = turnover * cfg.stamp_buy_rate if side.upper() == "BUY" else 0.0
    ctt = turnover * cfg.ctt_sell_rate if side.upper() == "SELL" else 0.0
    gst = cfg.gst_rate * (brokerage + txn + sebi)
    total = brokerage + txn + sebi + stamp + ctt + gst
    return {
        "turnover": round(turnover, 4),
        "brokerage": round(brokerage, 4),
        "txn": round(txn, 6),
        "sebi": round(sebi, 6),
        "stamp": round(stamp, 6),
        "ctt": round(ctt, 6),
        "gst": round(gst, 6),
        "leg_total": round(total, 4),
    }


def round_trip_charges(
    *,
    side: str,
    entry_price: float,
    exit_price: float,
    cfg: ChargeConfig | None = None,
) -> dict[str, float]:
    """Full round-trip fees for a BUY→CLOSE or SHORT→CLOSE paper trade."""
    cfg = cfg or charges_from_env()
    # Long: buy then sell. Short: sell then buy.
    if side.upper() == "BUY":
        open_leg = _leg_charges(side="BUY", price=entry_price, cfg=cfg)
        close_leg = _leg_charges(side="SELL", price=exit_price, cfg=cfg)
    else:
        open_leg = _leg_charges(side="SELL", price=entry_price, cfg=cfg)
        close_leg = _leg_charges(side="BUY", price=exit_price, cfg=cfg)

    total = open_leg["leg_total"] + close_leg["leg_total"]
    return {
        "entry_turnover": open_leg["turnover"],
        "exit_turnover": close_leg["turnover"],
        "brokerage": round(open_leg["brokerage"] + close_leg["brokerage"], 4),
        "txn": round(open_leg["txn"] + close_leg["txn"], 6),
        "sebi": round(open_leg["sebi"] + close_leg["sebi"], 6),
        "stamp": round(open_leg["stamp"] + close_leg["stamp"], 6),
        "ctt": round(open_leg["ctt"] + close_leg["ctt"], 6),
        "gst": round(open_leg["gst"] + close_leg["gst"], 6),
        "charges": round(total, 2),
    }


def apply_charges_and_tax(
    gross_pnl_points: float,
    cfg: ChargeConfig | None = None,
    *,
    side: str = "BUY",
    entry_price: float | None = None,
    exit_price: float | None = None,
) -> dict[str, float]:
    """Convert point PnL + Angel fee schedule → after-tax take-home.

    gross_pnl_points: exit-entry (BUY) or entry-exit (SHORT) in quote points.
    For Gold Petal, ₹ PnL ≈ points * lot_size * turnover_mult
    (same multiplier as turnover).
    """
    cfg = cfg or charges_from_env()
    gross = float(gross_pnl_points) * cfg.lot_size * cfg.turnover_mult

    if entry_price is not None and exit_price is not None:
        fee = round_trip_charges(
            side=side, entry_price=entry_price, exit_price=exit_price, cfg=cfg
        )
        charges = float(fee["charges"])
        detail = {k: fee[k] for k in ("brokerage", "txn", "sebi", "stamp", "ctt", "gst")}
    else:
        # Fallback: 2 × brokerage only
        charges = cfg.brokerage_per_order * 2.0
        detail = {
            "brokerage": charges,
            "txn": 0.0,
            "sebi": 0.0,
            "stamp": 0.0,
            "ctt": 0.0,
            "gst": 0.0,
        }

    after_charges = gross - charges
    tax = max(0.0, after_charges) * cfg.tax_rate
    after_tax = after_charges - tax
    return {
        "gross_pnl": round(gross, 2),
        "charges": round(charges, 2),
        "pnl_after_charges": round(after_charges, 2),
        "tax": round(tax, 2),
        "pnl_after_tax": round(after_tax, 2),
        **{k: round(float(v), 4) for k, v in detail.items()},
    }
