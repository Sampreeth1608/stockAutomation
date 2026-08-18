"""Paper S20 1h: wait for the hour to finish, then fade HL FLIP."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from s18_ohlc_vol_htf import VolBar
from strategy_s20 import S20FadeHlStrategy, s20_from_env
from strategy_wick import wick_record_actions

IST = ZoneInfo("Asia/Kolkata")


def _s20() -> S20FadeHlStrategy:
    return S20FadeHlStrategy(seed=False)


def _t(h: int, m: int, s: int = 0) -> datetime:
    return datetime(2026, 8, 17, h, m, s, tzinfo=IST)


def _msg(vol: float = 1000.0) -> dict:
    return {
        "volume_trade_for_the_day": vol,
        "total_buy_quantity": 80.0,
        "total_sell_quantity": 40.0,
    }


def test_forming_hour_does_not_trade() -> None:
    s = _s20()
    assert s.on_tick(_t(10, 5), 100.0, _msg()) is None
    assert s.on_tick(_t(10, 59), 110.0, _msg()) is None
    assert s.position == "flat"
    assert s.last_skip == "waiting_1h_close"


def test_first_closed_hour_needs_prev() -> None:
    s = _s20()
    s.on_tick(_t(9, 0), 100.0, _msg())
    s.on_tick(_t(9, 59), 104.0, _msg())
    assert s.on_tick(_t(10, 0), 104.0, _msg()) is None
    assert s.last_skip == "need_prev_1h"
    assert s._prev is not None
    assert s._prev.close == 104.0


def test_bounced_low_buys() -> None:
    s = _s20()
    s.on_tick(_t(9, 0), 100.0, _msg())
    s.on_tick(_t(9, 30), 110.0, _msg())
    s.on_tick(_t(9, 59), 104.0, _msg())
    s.on_tick(_t(10, 0), 104.0, _msg())
    s.on_tick(_t(10, 20), 90.0, _msg())
    s.on_tick(_t(10, 40), 109.0, _msg())
    s.on_tick(_t(10, 59), 108.0, _msg())
    result = s.on_tick(_t(11, 0), 108.0, _msg())
    assert result is not None
    assert result.action == "BUY"
    assert s.position == "long"
    assert s.entry_price == 108.0
    assert "bounce:LL" in result.reason
    assert wick_record_actions("flat", result) == [("BUY", "long")]


def test_knife_low_holds_flat() -> None:
    s = _s20()
    s._prev = VolBar("2026-08-17 09:00:00", 100.0, 110.0, 95.0, 105.0)
    s._bar_key = datetime(2026, 8, 17, 10, 0, tzinfo=IST)
    s._bar_o = 105.0
    s._bar_h = 106.0
    s._bar_l = 80.0
    s._bar_c = 82.0
    s._bar_n = 4
    assert s.on_tick(_t(11, 0), 82.0, _msg()) is None
    assert s.position == "flat"
    assert "knife" in str(s.last_skip)


def test_rejected_high_shorts_then_flips_long() -> None:
    s = _s20()
    s._prev = VolBar("2026-08-17 09:00:00", 100.0, 110.0, 95.0, 105.0)
    s._bar_key = datetime(2026, 8, 17, 10, 0, tzinfo=IST)
    s._bar_o = 110.0
    s._bar_h = 130.0
    s._bar_l = 100.0
    s._bar_c = 102.0
    s._bar_n = 4
    result = s.on_tick(_t(11, 0), 102.0, _msg())
    assert result is not None
    assert result.action == "SHORT"
    assert s.position == "short"
    s.on_tick(_t(11, 5), 102.0, _msg())
    s.on_tick(_t(11, 20), 80.0, _msg())
    s.on_tick(_t(11, 59), 108.0, _msg())
    flip = s.on_tick(_t(12, 0), 108.0, _msg())
    assert flip is not None
    assert flip.action == "BUY"
    assert s.position == "long"
    assert "bounce:LL" in flip.reason


def test_s20_from_env_name() -> None:
    s = S20FadeHlStrategy(seed=False)
    assert s.name == "S20_FADE_HL"
    assert s20_from_env is not None


if __name__ == "__main__":
    test_forming_hour_does_not_trade()
    test_first_closed_hour_needs_prev()
    test_bounced_low_buys()
    test_knife_low_holds_flat()
    test_rejected_high_shorts_then_flips_long()
    test_s20_from_env_name()
    print("ALL test_strategy_s20 OK")
