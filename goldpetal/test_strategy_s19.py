"""Paper S19 1h: wait for the hour to finish, then aligned body+close FLIP."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from s18_ohlc_vol_htf import VolBar
from strategy_s19 import S19BodyCloseStrategy, s19_from_env
from strategy_wick import wick_record_actions

IST = ZoneInfo("Asia/Kolkata")


def _s19() -> S19BodyCloseStrategy:
    return S19BodyCloseStrategy(seed=False)


def _t(h: int, m: int, s: int = 0) -> datetime:
    return datetime(2026, 8, 17, h, m, s, tzinfo=IST)


def _msg(vol: float = 1000.0) -> dict:
    return {
        "volume_trade_for_the_day": vol,
        "total_buy_quantity": 80.0,
        "total_sell_quantity": 40.0,
    }


def test_forming_hour_does_not_trade() -> None:
    s = _s19()
    assert s.on_tick(_t(10, 5), 100.0, _msg()) is None
    assert s.on_tick(_t(10, 59), 110.0, _msg()) is None
    assert s.position == "flat"
    assert s.last_skip == "waiting_1h_close"


def test_first_closed_hour_needs_prev() -> None:
    s = _s19()
    s.on_tick(_t(9, 0), 100.0, _msg())
    s.on_tick(_t(9, 59), 104.0, _msg())
    assert s.on_tick(_t(10, 0), 104.0, _msg()) is None
    assert s.last_skip == "need_prev_1h"
    assert s._prev is not None
    assert s._prev.close == 104.0


def test_aligned_green_up_close_buys() -> None:
    s = _s19()
    s.on_tick(_t(9, 0), 100.0, _msg())
    s.on_tick(_t(9, 59), 104.0, _msg())
    s.on_tick(_t(10, 0), 104.0, _msg())
    s.on_tick(_t(10, 10), 120.0, _msg())
    s.on_tick(_t(10, 59), 110.0, _msg())
    result = s.on_tick(_t(11, 0), 110.0, _msg())
    assert result is not None
    assert result.action == "BUY"
    assert s.position == "long"
    assert s.entry_price == 110.0
    assert "aligned:long" in result.reason
    assert wick_record_actions("flat", result) == [("BUY", "long")]


def test_mixed_green_down_close_holds() -> None:
    s = _s19()
    s._prev = VolBar("2026-08-17 09:00:00", 100.0, 105.0, 99.0, 104.0)
    s._bar_key = datetime(2026, 8, 17, 10, 0, tzinfo=IST)
    s._bar_o = 90.0
    s._bar_h = 110.0
    s._bar_l = 89.0
    s._bar_c = 100.0
    s._bar_n = 4
    assert s.on_tick(_t(11, 0), 100.0, _msg()) is None
    assert s.position == "flat"
    assert "mixed" in str(s.last_skip)


def test_hold_long_through_mixed_then_flip_short() -> None:
    s = _s19()
    s.on_tick(_t(9, 0), 100.0, _msg())
    s.on_tick(_t(9, 59), 104.0, _msg())
    s.on_tick(_t(10, 0), 104.0, _msg())
    s.on_tick(_t(10, 59), 110.0, _msg())
    assert s.on_tick(_t(11, 0), 110.0, _msg()) is not None
    assert s.position == "long"
    # Gap the next hour open below prev close so mixed green+down-close is possible.
    s._bar_o = 90.0
    s._bar_h = 110.0
    s._bar_l = 89.0
    s._bar_c = 100.0
    mixed = s.on_tick(_t(12, 0), 100.0, _msg())
    assert mixed is None
    assert s.position == "long"
    assert "mixed" in str(s.last_skip)
    # Next hour aligned red down-close vs held 100.
    s.on_tick(_t(12, 1), 100.0, _msg())
    s.on_tick(_t(12, 59), 85.0, _msg())
    flip = s.on_tick(_t(13, 0), 85.0, _msg())
    assert flip is not None
    assert flip.action == "SHORT"
    assert s.position == "short"
    assert "FLIP" in flip.reason
    assert wick_record_actions("long", flip) == [("CLOSE", "flat"), ("SHORT", "short")]


def test_flatten_at_session_close() -> None:
    s = _s19()
    s.on_tick(_t(9, 0), 100.0, _msg())
    s.on_tick(_t(9, 59), 104.0, _msg())
    s.on_tick(_t(10, 0), 104.0, _msg())
    s.on_tick(_t(10, 59), 110.0, _msg())
    assert s.on_tick(_t(11, 0), 110.0, _msg()) is not None
    close = s.on_tick(_t(23, 30), 111.0, _msg())
    assert close is not None
    assert close.action == "CLOSE"
    assert s.position == "flat"


def test_s19_from_env_name() -> None:
    s = S19BodyCloseStrategy(seed=False)
    assert s.name == "S19_BODY_CLOSE_1H"
    assert "aligned body+close" in s.status_line
    assert "pack=" not in s.status_line
    assert s19_from_env is not None


if __name__ == "__main__":
    test_forming_hour_does_not_trade()
    test_first_closed_hour_needs_prev()
    test_aligned_green_up_close_buys()
    test_mixed_green_down_close_holds()
    test_hold_long_through_mixed_then_flip_short()
    test_flatten_at_session_close()
    test_s19_from_env_name()
    print("ALL test_strategy_s19 OK")
