"""Paper S18 1h: wait for the hour to finish, then pack AND FLIP."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from s18_ohlc_vol_htf import S18Pack, VolBar
from strategy_s18 import S18OhlcVolHtfStrategy
from strategy_wick import wick_record_actions

IST = ZoneInfo("Asia/Kolkata")


def _s18() -> S18OhlcVolHtfStrategy:
    s = S18OhlcVolHtfStrategy(seed=False)
    s._day = VolBar("2026-08-16 00:00:00", 90.0, 130.0, 80.0, 95.0, 8000.0)
    return s


def _t(h: int, m: int, s: int = 0) -> datetime:
    return datetime(2026, 8, 17, h, m, s, tzinfo=IST)


def _msg(vol: float, bp: float = 80.0, sp: float = 40.0) -> dict:
    return {
        "volume_trade_for_the_day": vol,
        "total_buy_quantity": bp,
        "total_sell_quantity": sp,
    }


def test_forming_hour_does_not_trade() -> None:
    s = _s18()
    assert s.on_tick(_t(10, 5), 100.0, _msg(10)) is None
    assert s.on_tick(_t(10, 59), 110.0, _msg(20)) is None
    assert s.position == "flat"
    assert s.last_skip == "waiting_1h_close"


def test_first_closed_hour_needs_prev() -> None:
    s = _s18()
    s.on_tick(_t(9, 0), 100.0, _msg(100))
    s.on_tick(_t(9, 10), 105.0, _msg(400))
    s.on_tick(_t(9, 20), 99.0, _msg(700))
    s.on_tick(_t(9, 59), 104.0, _msg(1000))
    assert s.on_tick(_t(10, 0), 104.0, _msg(1000)) is None
    assert s.last_skip == "need_prev_1h"
    assert s._prev is not None
    assert s._prev.close == 104.0


def test_base_and_enters_long_at_second_close() -> None:
    s = _s18()
    s.on_tick(_t(9, 0), 100.0, _msg(100))
    s.on_tick(_t(9, 10), 105.0, _msg(400))
    s.on_tick(_t(9, 20), 99.0, _msg(700))
    s.on_tick(_t(9, 59), 104.0, _msg(1000))
    s.on_tick(_t(10, 0), 104.0, _msg(1000))
    s.on_tick(_t(10, 10), 120.0, _msg(1800))
    s.on_tick(_t(10, 59), 110.0, _msg(3000))
    result = s.on_tick(_t(11, 0), 110.0, _msg(3000))
    assert result is not None
    assert result.action == "BUY"
    assert s.position == "long"
    assert s.entry_price == 110.0
    assert "base:long" in result.reason
    assert wick_record_actions("flat", result) == [("BUY", "long")]


def test_weak_volume_skips_unless_pack_drops_vol() -> None:
    s = _s18()
    s._prev = VolBar("2026-08-17 09:00:00", 100.0, 105.0, 99.0, 104.0, 2000.0)
    s._prev_close_vol = 2000.0
    s._bar_key = datetime(2026, 8, 17, 10, 0, tzinfo=IST)
    s._bar_o = 104.0
    s._bar_h = 120.0
    s._bar_l = 103.0
    s._bar_c = 110.0
    s._bar_n = 4
    s._bar_last_vol = 2100.0
    assert s.on_tick(_t(11, 0), 110.0, _msg(2100)) is None
    assert s.position == "flat"
    assert "vol" in str(s.last_skip)

    d = _s18()
    d.pack = S18Pack(name="drop_vol", require_vol_up=False)
    d._prev = VolBar("2026-08-17 09:00:00", 100.0, 105.0, 99.0, 104.0, 2000.0)
    d._prev_close_vol = 2000.0
    d._bar_key = datetime(2026, 8, 17, 10, 0, tzinfo=IST)
    d._bar_o = 104.0
    d._bar_h = 120.0
    d._bar_l = 103.0
    d._bar_c = 110.0
    d._bar_n = 4
    d._bar_last_vol = 2100.0
    result = d.on_tick(_t(11, 0), 110.0, _msg(2100))
    assert result is not None
    assert result.action == "BUY"


def test_flatten_at_session_close() -> None:
    s = _s18()
    s.on_tick(_t(9, 0), 100.0, _msg(100))
    s.on_tick(_t(9, 59), 104.0, _msg(1000))
    s.on_tick(_t(10, 0), 104.0, _msg(1000))
    s.on_tick(_t(10, 10), 120.0, _msg(1800))
    s.on_tick(_t(10, 59), 110.0, _msg(3000))
    assert s.on_tick(_t(11, 0), 110.0, _msg(3000)) is not None
    assert s.position == "long"
    close = s.on_tick(_t(23, 30), 111.0, _msg(9000))
    assert close is not None
    assert close.action == "CLOSE"
    assert s.position == "flat"


def test_s18_from_env_name() -> None:
    s = S18OhlcVolHtfStrategy(seed=False)
    assert s.name == "S18_OHLC_VOL_HTF"
    assert "pack=" in s.status_line


if __name__ == "__main__":
    test_forming_hour_does_not_trade()
    test_first_closed_hour_needs_prev()
    test_base_and_enters_long_at_second_close()
    test_weak_volume_skips_unless_pack_drops_vol()
    test_flatten_at_session_close()
    test_s18_from_env_name()
    print("ALL test_strategy_s18 OK")
