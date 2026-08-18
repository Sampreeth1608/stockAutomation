"""Gold Petal front vs next month using ROLLOVER_DAYS."""

from __future__ import annotations

from datetime import datetime

from symbols import choose_goldpetal_contract, s13_roll_intent


def _row(symbol: str, token: str, expiry: str) -> dict:
    return {"symbol": symbol, "token": token, "expiry": expiry, "name": "GOLDPETAL"}


def test_choose_stays_front_until_window() -> None:
    aug = datetime(2026, 8, 31)
    sep = datetime(2026, 9, 30)
    rows = [
        (aug, _row("GOLDPETAL31AUG26FUT", "1", "31AUG2026")),
        (sep, _row("GOLDPETAL30SEP26FUT", "2", "30SEP2026")),
    ]
    c = choose_goldpetal_contract(rows, today=datetime(2026, 8, 24), rollover_days=5)
    assert c["symbol"] == "GOLDPETAL31AUG26FUT"
    assert c["rolled"] is False
    assert c["days_to_front_expiry"] == 7


def test_choose_switches_on_rollover_days() -> None:
    aug = datetime(2026, 8, 31)
    sep = datetime(2026, 9, 30)
    rows = [
        (aug, _row("GOLDPETAL31AUG26FUT", "1", "31AUG2026")),
        (sep, _row("GOLDPETAL30SEP26FUT", "2", "30SEP2026")),
    ]
    c = choose_goldpetal_contract(rows, today=datetime(2026, 8, 26), rollover_days=5)
    assert c["symbol"] == "GOLDPETAL30SEP26FUT"
    assert c["rolled"] is True
    assert c["days_to_front_expiry"] == 5


def test_s13_intent_last_front_then_switch() -> None:
    assert (
        s13_roll_intent(
            held_symbol="GOLDPETAL31AUG26FUT",
            trade_symbol="GOLDPETAL31AUG26FUT",
            rolled=False,
            days_to_front_expiry=7,
            rollover_days=5,
            in_position=True,
        )
        == "trade"
    )
    assert (
        s13_roll_intent(
            held_symbol="GOLDPETAL31AUG26FUT",
            trade_symbol="GOLDPETAL31AUG26FUT",
            rolled=False,
            days_to_front_expiry=6,
            rollover_days=5,
            in_position=True,
        )
        == "flatten_last_front"
    )
    assert (
        s13_roll_intent(
            held_symbol="GOLDPETAL31AUG26FUT",
            trade_symbol="GOLDPETAL31AUG26FUT",
            rolled=False,
            days_to_front_expiry=6,
            rollover_days=5,
            in_position=False,
        )
        == "block_last_front"
    )
    assert (
        s13_roll_intent(
            held_symbol="GOLDPETAL31AUG26FUT",
            trade_symbol="GOLDPETAL30SEP26FUT",
            rolled=True,
            days_to_front_expiry=5,
            rollover_days=5,
            in_position=True,
        )
        == "flatten_switch"
    )
    assert (
        s13_roll_intent(
            held_symbol="GOLDPETAL30SEP26FUT",
            trade_symbol="GOLDPETAL30SEP26FUT",
            rolled=True,
            days_to_front_expiry=5,
            rollover_days=5,
            in_position=True,
        )
        == "trade"
    )


if __name__ == "__main__":
    test_choose_stays_front_until_window()
    test_choose_switches_on_rollover_days()
    test_s13_intent_last_front_then_switch()
    print("ALL test_symbols OK")
