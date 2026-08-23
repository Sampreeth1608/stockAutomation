"""Weekend and listed holidays are closed — no Angel orders."""

from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo

from market_session import listed_holidays, session_open

IST = ZoneInfo("Asia/Kolkata")


def test_sunday_is_closed() -> None:
    sunday = datetime(2026, 8, 23, 12, 10, tzinfo=IST)
    assert session_open(sunday) is False


def test_monday_session_is_open() -> None:
    monday = datetime(2026, 8, 24, 12, 10, tzinfo=IST)
    os.environ.pop("MARKET_HOLIDAYS", None)
    assert session_open(monday) is True


def test_listed_holiday_is_closed() -> None:
    monday = datetime(2026, 8, 24, 12, 10, tzinfo=IST)
    os.environ["MARKET_HOLIDAYS"] = "2026-08-24"
    try:
        assert "2026-08-24" in listed_holidays()
        assert session_open(monday) is False
    finally:
        os.environ.pop("MARKET_HOLIDAYS", None)


if __name__ == "__main__":
    test_sunday_is_closed()
    test_monday_session_is_open()
    test_listed_holiday_is_closed()
    print("ALL test_market_session OK")
