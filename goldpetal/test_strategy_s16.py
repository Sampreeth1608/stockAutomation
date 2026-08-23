"""Live S16 1h: wait for the hour to finish, then close-vs-prev FLIP."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from strategy_s16 import S16Config, S16HhhlWickStrategy
from strategy_wick import wick_record_actions

IST = ZoneInfo("Asia/Kolkata")


def _s16() -> S16HhhlWickStrategy:
    return S16HhhlWickStrategy(S16Config(bar_minutes=60, min_wick_gap=0.0), seed=False)


def _t(h: int, m: int, s: int = 0) -> datetime:
    return datetime(2026, 8, 17, h, m, s, tzinfo=IST)


def test_forming_hour_does_not_trade() -> None:
    s = _s16()
    assert s.on_tick(_t(10, 5), 100.0) is None
    assert s.on_tick(_t(10, 59), 110.0) is None
    assert s.position == "flat"
    assert s.last_skip == "waiting_1h_close"


def test_first_closed_hour_needs_prev() -> None:
    s = _s16()
    s.on_tick(_t(9, 0), 100.0)
    s.on_tick(_t(9, 10), 105.0)
    s.on_tick(_t(9, 20), 99.0)
    s.on_tick(_t(9, 59), 104.0)
    assert s.on_tick(_t(10, 0), 100.0) is None
    assert s.last_skip == "need_prev_1h"
    assert s._prev is not None
    assert s._prev.close == 104.0


def test_up_close_hh_green_enters_long_at_close() -> None:
    s = _s16()
    s.on_tick(_t(9, 0), 100.0)
    s.on_tick(_t(9, 10), 105.0)
    s.on_tick(_t(9, 20), 99.0)
    s.on_tick(_t(9, 59), 104.0)
    s.on_tick(_t(10, 0), 100.0)  # store 09:00 as prev
    s.on_tick(_t(10, 10), 120.0)
    s.on_tick(_t(10, 59), 110.0)
    result = s.on_tick(_t(11, 0), 110.0)
    assert result is not None
    assert result.action == "BUY"
    assert s.position == "long"
    assert s.entry_price == 110.0
    assert "C>prev → HH+green" in result.reason


def test_up_close_without_hhhl_skips_even_with_wick() -> None:
    s = _s16()
    s.on_tick(_t(9, 0), 100.0)
    s.on_tick(_t(9, 10), 120.0)
    s.on_tick(_t(9, 59), 104.0)
    s.on_tick(_t(10, 0), 104.0)
    s.on_tick(_t(10, 10), 104.5)  # no HH vs prevH=120
    s.on_tick(_t(10, 20), 90.0)  # long lower wick, ignored on up close
    s.on_tick(_t(10, 59), 104.2)
    result = s.on_tick(_t(11, 0), 104.2)
    assert result is None
    assert s.position == "flat"
    assert "no HH/LL" in str(s.last_skip)


def test_down_close_wick_flips() -> None:
    s = _s16()
    s.on_tick(_t(9, 0), 100.0)
    s.on_tick(_t(9, 10), 105.0)
    s.on_tick(_t(9, 59), 104.0)
    s.on_tick(_t(10, 0), 100.0)
    s.on_tick(_t(10, 10), 120.0)
    s.on_tick(_t(10, 59), 110.0)
    first = s.on_tick(_t(11, 0), 110.0)
    assert first is not None and first.action == "BUY"
    s.on_tick(_t(11, 10), 140.0)
    s.on_tick(_t(11, 30), 70.0)
    s.on_tick(_t(11, 59), 80.0)
    second = s.on_tick(_t(12, 0), 80.0)
    assert second is not None
    assert second.action == "SHORT"
    assert s.position == "short"
    assert s.entry_price == 80.0
    assert "FLIP" in second.reason
    assert wick_record_actions("long", second) == [
        ("CLOSE", "flat"),
        ("SHORT", "short"),
    ]


def test_equal_close_skips() -> None:
    s = _s16()
    s.on_tick(_t(9, 0), 100.0)
    s.on_tick(_t(9, 59), 104.0)
    s.on_tick(_t(10, 0), 104.0)
    s.on_tick(_t(10, 59), 104.0)
    result = s.on_tick(_t(11, 0), 104.0)
    assert result is None
    assert "C=prev" in str(s.last_skip)


def test_on_bar_row_uses_prev() -> None:
    s = _s16()
    assert (
        s.on_bar_row(
            {"time": "2026-08-17 09:00:00", "open": 100, "high": 105, "low": 99, "close": 104}
        )
        is None
    )
    result = s.on_bar_row(
        {"time": "2026-08-17 10:00:00", "open": 100, "high": 120, "low": 100, "close": 110}
    )
    assert result is not None
    assert result.action == "BUY"
    assert s.entry_price == 110.0


def test_flatten_at_market_close() -> None:
    import position_safety as ps

    orig = ps.is_session_intraday
    ps.is_session_intraday = lambda name: str(name) == "S16_HHHL_WICK_1H"  # type: ignore[assignment]
    try:
        s = _s16()
        s.on_tick(_t(9, 0), 100.0)
        s.on_tick(_t(9, 59), 104.0)
        s.on_tick(_t(10, 0), 100.0)
        s.on_tick(_t(10, 10), 120.0)
        s.on_tick(_t(10, 59), 110.0)
        buy = s.on_tick(_t(11, 0), 110.0)
        assert buy is not None and buy.action == "BUY"
        close = s.on_tick(_t(23, 30), 111.0)
        assert close is not None and close.action == "CLOSE"
        assert s.position == "flat"
        assert "session close" in (close.reason or "")
    finally:
        ps.is_session_intraday = orig  # type: ignore[assignment]


def test_overnight_leftover_closes_at_next_open() -> None:
    import position_safety as ps

    orig = ps.is_session_intraday
    ps.is_session_intraday = lambda name: str(name) == "S16_HHHL_WICK_1H"  # type: ignore[assignment]
    try:
        s = _s16()
        s.position = "long"
        s.entry_price = 110.0
        s.entry_date = "2026-08-16"
        close = s.on_tick(_t(9, 0), 112.0)
        assert close is not None and close.action == "CLOSE"
        assert s.position == "flat"
        assert "overnight leftover" in (close.reason or "")
    finally:
        ps.is_session_intraday = orig  # type: ignore[assignment]


def test_yesterday_prev_does_not_fill_first_hour() -> None:
    from backtest_hhhl_candles import Candle

    s = _s16()
    s._prev = Candle("2026-08-16 22:00:00", 90.0, 140.0, 80.0, 80.0)
    s.on_tick(_t(9, 0), 100.0)
    s.on_tick(_t(9, 10), 105.0)
    s.on_tick(_t(9, 20), 99.0)
    s.on_tick(_t(9, 59), 104.0)
    assert s.on_tick(_t(10, 0), 100.0) is None
    assert s.position == "flat"
    assert s.last_skip == "need_prev_1h"
    assert s._prev is not None
    assert s._prev.time.startswith("2026-08-17 09:00")
    assert s._prev.close == 104.0


def test_preopen_prev_does_not_fill_first_hour() -> None:
    from backtest_hhhl_candles import Candle

    s = _s16()
    s._prev = Candle("2026-08-17 08:00:00", 90.0, 140.0, 80.0, 80.0)
    s.on_tick(_t(9, 0), 100.0)
    s.on_tick(_t(9, 10), 105.0)
    s.on_tick(_t(9, 20), 99.0)
    s.on_tick(_t(9, 59), 104.0)
    assert s.on_tick(_t(10, 0), 100.0) is None
    assert s.position == "flat"
    assert s.last_skip == "need_prev_1h"


def test_second_hour_picks_side_after_stale_prev() -> None:
    from backtest_hhhl_candles import Candle

    s = _s16()
    s._prev = Candle("2026-08-16 22:00:00", 90.0, 140.0, 80.0, 80.0)
    s.on_tick(_t(9, 0), 100.0)
    s.on_tick(_t(9, 10), 105.0)
    s.on_tick(_t(9, 20), 99.0)
    s.on_tick(_t(9, 59), 104.0)
    assert s.on_tick(_t(10, 0), 100.0) is None
    s.on_tick(_t(10, 10), 120.0)
    s.on_tick(_t(10, 59), 110.0)
    result = s.on_tick(_t(11, 0), 110.0)
    assert result is not None
    assert result.action == "BUY"
    assert "C>prev → HH+green" in result.reason
    assert s.entry_price == 110.0


def test_seed_skips_preopen_and_yesterday() -> None:
    import sqlite3
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "ticks.db"
        con = sqlite3.connect(str(db))
        con.execute(
            "CREATE TABLE ticks (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "received_at TEXT NOT NULL, ltp REAL)"
        )
        con.executemany(
            "INSERT INTO ticks (received_at, ltp) VALUES (?, ?)",
            [
                ("2026-08-16T23:00:00+05:30", 90.0),
                ("2026-08-16T23:59:00+05:30", 80.0),
                ("2026-08-17T08:00:00+05:30", 90.0),
                ("2026-08-17T08:59:00+05:30", 80.0),
                ("2026-08-17T09:00:00+05:30", 100.0),
                ("2026-08-17T09:10:00+05:30", 101.0),
            ],
        )
        con.commit()
        con.close()
        s = _s16()
        s.seed_from_ticks(db, now=_t(9, 15))
        assert s._prev is None
        s.seed_from_ticks(db, now=datetime(2026, 8, 17, 0, 30, tzinfo=IST))
        assert s._prev is None


def test_seed_keeps_todays_closed_session_hour() -> None:
    import sqlite3
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "ticks.db"
        con = sqlite3.connect(str(db))
        con.execute(
            "CREATE TABLE ticks (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "received_at TEXT NOT NULL, ltp REAL)"
        )
        con.executemany(
            "INSERT INTO ticks (received_at, ltp) VALUES (?, ?)",
            [
                ("2026-08-17T09:00:00+05:30", 100.0),
                ("2026-08-17T09:10:00+05:30", 105.0),
                ("2026-08-17T09:59:00+05:30", 104.0),
                ("2026-08-17T10:00:00+05:30", 100.0),
                ("2026-08-17T10:10:00+05:30", 101.0),
            ],
        )
        con.commit()
        con.close()
        s = _s16()
        s.seed_from_ticks(db, now=_t(10, 15))
        assert s._prev is not None
        assert s._prev.time.startswith("2026-08-17 09:00")
        assert s._prev.close == 104.0
        assert s.on_tick(_t(10, 15), 101.0) is None
        assert s.position == "flat"


def _ticks_db(rows: list[tuple[str, float]]):
    import sqlite3
    import tempfile
    from pathlib import Path

    td = tempfile.TemporaryDirectory()
    db = Path(td.name) / "ticks.db"
    con = sqlite3.connect(str(db))
    con.execute(
        "CREATE TABLE ticks (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "received_at TEXT NOT NULL, token TEXT, ltp REAL, raw_json TEXT DEFAULT '{}')"
    )
    con.executemany("INSERT INTO ticks (received_at, ltp) VALUES (?, ?)", rows)
    con.commit()
    con.close()
    return td, db


def test_restart_after_4pm_catches_up_closed_hour() -> None:
    """Restart at 16:07 must still decide 15:00 vs 14:00 (missed 16:00 tick)."""
    td, db = _ticks_db(
        [
            ("2026-08-17T14:00:00+05:30", 100.0),
            ("2026-08-17T14:10:00+05:30", 105.0),
            ("2026-08-17T14:20:00+05:30", 99.0),
            ("2026-08-17T14:59:00+05:30", 104.0),
            ("2026-08-17T15:00:00+05:30", 100.0),
            ("2026-08-17T15:10:00+05:30", 120.0),
            ("2026-08-17T15:59:00+05:30", 110.0),
            ("2026-08-17T16:00:00+05:30", 110.0),
            ("2026-08-17T16:07:00+05:30", 111.0),
        ]
    )
    try:
        s = _s16()
        s.seed_from_ticks(db, now=_t(16, 7))
        result = s.on_tick(_t(16, 7), 111.0)
        assert result is not None
        assert result.action == "BUY"
        assert "C>prev → HH+green" in result.reason
        assert "restart catch-up" in result.reason
        assert s.position == "long"
        assert s.entry_price == 110.0
        assert s.on_tick(_t(16, 20), 112.0) is None
        assert s.position == "long"
    finally:
        td.cleanup()


def test_restart_catchup_skips_when_formula_has_no_side() -> None:
    td, db = _ticks_db(
        [
            ("2026-08-17T14:00:00+05:30", 100.0),
            ("2026-08-17T14:10:00+05:30", 120.0),
            ("2026-08-17T14:59:00+05:30", 104.0),
            ("2026-08-17T15:00:00+05:30", 104.0),
            ("2026-08-17T15:10:00+05:30", 104.5),
            ("2026-08-17T15:20:00+05:30", 90.0),
            ("2026-08-17T15:59:00+05:30", 104.2),
            ("2026-08-17T16:07:00+05:30", 104.2),
        ]
    )
    try:
        s = _s16()
        s.seed_from_ticks(db, now=_t(16, 7))
        assert s.on_tick(_t(16, 7), 104.2) is None
        assert s.position == "flat"
        assert "no HH/LL" in str(s.last_skip)
    finally:
        td.cleanup()


def test_restart_does_not_repeat_already_recorded_close() -> None:
    from storage import init_db, save_signal

    td, db = _ticks_db(
        [
            ("2026-08-17T14:00:00+05:30", 100.0),
            ("2026-08-17T14:10:00+05:30", 105.0),
            ("2026-08-17T14:59:00+05:30", 104.0),
            ("2026-08-17T15:00:00+05:30", 100.0),
            ("2026-08-17T15:10:00+05:30", 120.0),
            ("2026-08-17T15:59:00+05:30", 110.0),
            ("2026-08-17T16:07:00+05:30", 111.0),
        ]
    )
    try:
        init_db(db)
        save_signal(
            time_label="2026-08-17T16:00:05+05:30",
            symbol="GOLDPETAL",
            action="BUY",
            position_after="long",
            reason="live 16:00 close",
            price_delta=None,
            net=0.0,
            net_delta=None,
            dry_run=True,
            strategy="S16_HHHL_WICK_1H",
            cmp=110.0,
            db_path=db,
        )
        s = _s16()
        s.seed_from_ticks(db, now=_t(16, 7))
        assert s.position == "long"
        assert s.on_tick(_t(16, 7), 111.0) is None
        assert s.position == "long"
        assert s.entry_price == 110.0
    finally:
        td.cleanup()


def test_day_state_restart_keeps_forming_hour() -> None:
    import tempfile
    from pathlib import Path

    live = _s16()
    live.on_tick(_t(10, 5), 100.0)
    live.on_tick(_t(10, 30), 108.0)
    path = Path(tempfile.mkdtemp()) / "s16_day.json"
    live.persist_day_state(path)
    restarted = _s16()
    restarted.seed_from_day_state(path, now=_t(10, 40))
    assert restarted._prev is None
    assert restarted._bar_o == 100.0
    assert restarted._bar_h == 108.0
    assert restarted.on_tick(_t(10, 50), 107.0) is None
    assert restarted.last_skip == "waiting_1h_close"
    assert restarted.position == "flat"


def test_day_state_restart_catches_up_closed_hour() -> None:
    import tempfile
    from pathlib import Path

    live = _s16()
    live.on_tick(_t(14, 0), 100.0)
    live.on_tick(_t(14, 10), 105.0)
    live.on_tick(_t(14, 20), 99.0)
    live.on_tick(_t(14, 59), 104.0)
    live.on_tick(_t(15, 0), 100.0)
    live.on_tick(_t(15, 10), 120.0)
    live.on_tick(_t(15, 59), 110.0)
    path = Path(tempfile.mkdtemp()) / "s16_day.json"
    live.persist_day_state(path)
    restarted = _s16()
    restarted.seed_from_day_state(path, now=_t(16, 7))
    result = restarted.on_tick(_t(16, 7), 111.0)
    assert result is not None
    assert result.action == "BUY"
    assert "C>prev → HH+green" in result.reason
    assert "restart catch-up" in result.reason
    assert restarted.position == "long"
    assert restarted.entry_price == 110.0


if __name__ == "__main__":
    test_forming_hour_does_not_trade()
    test_first_closed_hour_needs_prev()
    test_up_close_hh_green_enters_long_at_close()
    test_up_close_without_hhhl_skips_even_with_wick()
    test_down_close_wick_flips()
    test_equal_close_skips()
    test_on_bar_row_uses_prev()
    test_flatten_at_market_close()
    test_overnight_leftover_closes_at_next_open()
    test_yesterday_prev_does_not_fill_first_hour()
    test_preopen_prev_does_not_fill_first_hour()
    test_second_hour_picks_side_after_stale_prev()
    test_seed_skips_preopen_and_yesterday()
    test_seed_keeps_todays_closed_session_hour()
    test_restart_after_4pm_catches_up_closed_hour()
    test_restart_catchup_skips_when_formula_has_no_side()
    test_restart_does_not_repeat_already_recorded_close()
    test_day_state_restart_keeps_forming_hour()
    test_day_state_restart_catches_up_closed_hour()
    print("ALL test_strategy_s16 OK")
