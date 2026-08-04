"""Tests for Angel One MCX futures charges + tax."""

from __future__ import annotations

import os

from charges import ChargeConfig, apply_charges_and_tax, round_trip_charges


def test_round_trip_has_brokerage_gst_ctt() -> None:
    cfg = ChargeConfig(
        brokerage_per_order=20.0,
        brokerage_promo=False,
        lot_size=1.0,
        turnover_mult=1.0,
    )
    fee = round_trip_charges(side="BUY", entry_price=14000.0, exit_price=14100.0, cfg=cfg)
    # 2 orders × ₹20 brokerage
    assert fee["brokerage"] == 40.0
    assert fee["gst"] > 0
    assert fee["ctt"] > 0  # sell leg
    assert fee["stamp"] > 0  # buy legs (entry buy + no stamp on sell; short would differ)
    assert fee["charges"] > 40.0


def test_promo_zero_brokerage() -> None:
    cfg = ChargeConfig(brokerage_per_order=0.0, lot_size=1.0, turnover_mult=1.0)
    fee = round_trip_charges(side="BUY", entry_price=14000.0, exit_price=14100.0, cfg=cfg)
    assert fee["brokerage"] == 0.0
    assert fee["charges"] > 0  # still statutory


def test_profit_after_tax() -> None:
    cfg = ChargeConfig(brokerage_per_order=20.0, tax_rate=0.30, turnover_mult=1.0, lot_size=1)
    # 100 points * ₹1 = ₹100 gross — covers ~₹50 fees → profit before tax
    out = apply_charges_and_tax(
        100.0, cfg, side="BUY", entry_price=14000.0, exit_price=14100.0
    )
    assert out["gross_pnl"] == 100.0
    assert out["charges"] > 40.0
    assert out["pnl_after_charges"] > 0
    assert out["tax"] > 0.0


def test_small_move_loses_to_fees() -> None:
    cfg = ChargeConfig(brokerage_per_order=20.0, tax_rate=0.30, turnover_mult=1.0, lot_size=1)
    # 30 points = ₹30 < ~₹50 fees
    out = apply_charges_and_tax(
        30.0, cfg, side="BUY", entry_price=14000.0, exit_price=14030.0
    )
    assert out["gross_pnl"] == 30.0
    assert out["pnl_after_charges"] < 0
    assert out["tax"] == 0.0


def test_build_trades_angel_schedule() -> None:
    os.environ["BROKERAGE_PER_ORDER"] = "20"
    os.environ["BROKERAGE_PROMO"] = "false"
    os.environ["TAX_RATE"] = "0.30"
    os.environ["TURNOVER_MULT"] = "1.0"
    os.environ["LOT_SIZE"] = "1"

    import tempfile
    from pathlib import Path

    from storage import build_trades, init_db, save_signal

    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db = Path(tmp.name)
    tmp.close()
    init_db(db)
    # 100 points * ₹1 = ₹100 gross — enough to beat fees
    save_signal(
        "t1", "GOLDPETAL", "BUY", "long", "in",
        None, 1, 1, True, strategy="S3_ML", cmp=14000.0, db_path=db,
    )
    save_signal(
        "t2", "GOLDPETAL", "CLOSE", "flat", "out",
        None, 0, None, True, strategy="S3_ML", cmp=14100.0, db_path=db,
    )
    t = build_trades(strategy="S3_ML", db_path=db)[0]
    assert t["gross_pnl"] == 100.0
    assert float(t["charges"]) >= 40.0
    assert "brokerage" in t
    assert t["pnl_after_tax"] == t["net_pnl"]
    assert float(t["pnl_after_charges"]) > 0


if __name__ == "__main__":
    test_round_trip_has_brokerage_gst_ctt()
    test_promo_zero_brokerage()
    test_profit_after_tax()
    test_small_move_loses_to_fees()
    test_build_trades_angel_schedule()
    print("ok")
