"""Tests for charges + tax on paper PnL."""

from __future__ import annotations

import os

from charges import ChargeConfig, apply_charges_and_tax


def test_profit_taxed() -> None:
    cfg = ChargeConfig(charge_per_side=20, tax_rate=0.30, lot_size=1)
    # gross 100, fees 40, after 60, tax 18, after_tax 42
    out = apply_charges_and_tax(100.0, cfg)
    assert out["gross_pnl"] == 100.0
    assert out["charges"] == 40.0
    assert out["pnl_after_charges"] == 60.0
    assert out["tax"] == 18.0
    assert out["pnl_after_tax"] == 42.0


def test_loss_no_tax() -> None:
    cfg = ChargeConfig(charge_per_side=20, tax_rate=0.30)
    out = apply_charges_and_tax(-10.0, cfg)
    assert out["gross_pnl"] == -10.0
    assert out["charges"] == 40.0
    assert out["pnl_after_charges"] == -50.0
    assert out["tax"] == 0.0
    assert out["pnl_after_tax"] == -50.0


def test_round_trip_override() -> None:
    cfg = ChargeConfig(charge_per_side=20, round_trip_charge=55, tax_rate=0.3)
    out = apply_charges_and_tax(100.0, cfg)
    assert out["charges"] == 55.0


def test_build_trades_applies_charges() -> None:
    os.environ["TRADE_CHARGE_PER_SIDE"] = "20"
    os.environ["TAX_RATE"] = "0.30"
    os.environ.pop("TRADE_ROUND_TRIP_CHARGE", None)

    import tempfile
    from pathlib import Path

    from storage import build_trades, init_db, save_signal

    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db = Path(tmp.name)
    tmp.close()
    init_db(db)
    save_signal(
        "t1", "GOLDPETAL", "BUY", "long", "in",
        None, 1, 1, True, strategy="S3_ML", cmp=100.0, db_path=db,
    )
    save_signal(
        "t2", "GOLDPETAL", "CLOSE", "flat", "out",
        None, 0, None, True, strategy="S3_ML", cmp=200.0, db_path=db,
    )
    t = build_trades(strategy="S3_ML", db_path=db)[0]
    assert t["gross_pnl"] == 100.0
    assert t["charges"] == 40.0
    assert t["pnl_after_charges"] == 60.0
    assert t["tax"] == 18.0
    assert t["pnl_after_tax"] == 42.0
    assert t["net_pnl"] == 42.0


if __name__ == "__main__":
    test_profit_taxed()
    test_loss_no_tax()
    test_round_trip_override()
    test_build_trades_applies_charges()
    print("ok")
