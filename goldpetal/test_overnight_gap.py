"""Overnight gap: close → next-open tape, paper until 40% WR% AC, then Live tab."""

from __future__ import annotations

import sqlite3
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from control_state import ALL_STRATEGY_NAMES, SLIM_PAPER_STRATEGIES
from live_readiness import (
    LIVE_ELIGIBLE_BOOKS,
    NEVER_LIVE_BOOKS,
    PAPER_ONLY_BOOKS,
    book_may_go_live,
)
from overnight_gap import (
    BOOK,
    DEFAULT_CLOSE,
    DEFAULT_EXIT_MINUTES,
    DEFAULT_OPEN,
    GapRead,
    in_entry_window,
    in_exit_window,
    parse_hhmm,
    score_session,
    score_ticks,
)
from strategy_overnight_gap import OvernightGapStrategy

ROOT = Path(__file__).resolve().parent
IST = ZoneInfo("Asia/Kolkata")
CLOSE = parse_hhmm(DEFAULT_CLOSE)
OPEN = parse_hhmm(DEFAULT_OPEN)


def _st(tmp: Path) -> OvernightGapStrategy:
    return OvernightGapStrategy(
        seed=False,
        state_path=tmp / "overnight_gap_state.json",
        db_path=tmp / "no-ticks.db",
    )


def _samples(now: datetime, *, drift: float, tbq: float, tsq: float) -> list:
    open_ts = now.replace(hour=9, minute=0, second=0, microsecond=0)
    out = []
    t = open_ts
    i = 0
    while t <= now:
        px = 90_000.0 + i * drift
        out.append((t, px, tbq, tsq))
        t += timedelta(minutes=5)
        i += 1
    return out


def test_entry_last_fifteen_minutes() -> None:
    assert in_entry_window(
        datetime(2026, 8, 20, 23, 20, tzinfo=IST),
        market_close=CLOSE,
        minutes_before=15,
    )
    assert in_entry_window(
        datetime(2026, 8, 20, 23, 15, tzinfo=IST),
        market_close=CLOSE,
        minutes_before=15,
    )
    assert not in_entry_window(
        datetime(2026, 8, 20, 22, 0, tzinfo=IST),
        market_close=CLOSE,
        minutes_before=15,
    )
    assert not in_entry_window(
        datetime(2026, 8, 20, 23, 31, tzinfo=IST),
        market_close=CLOSE,
        minutes_before=15,
    )


def test_exit_next_morning_window() -> None:
    assert in_exit_window(
        datetime(2026, 8, 21, 9, 0, tzinfo=IST),
        market_open=OPEN,
        minutes_after=5,
    )
    assert in_exit_window(
        datetime(2026, 8, 21, 9, 2, tzinfo=IST),
        market_open=OPEN,
        minutes_after=5,
    )
    assert in_exit_window(
        datetime(2026, 8, 21, 9, 5, tzinfo=IST),
        market_open=OPEN,
        minutes_after=5,
    )
    assert not in_exit_window(
        datetime(2026, 8, 21, 8, 59, tzinfo=IST),
        market_open=OPEN,
        minutes_after=5,
    )
    assert not in_exit_window(
        datetime(2026, 8, 21, 9, 6, tzinfo=IST),
        market_open=OPEN,
        minutes_after=5,
    )
    assert DEFAULT_EXIT_MINUTES == 5


def test_bullish_day_buys() -> None:
    read = score_session(
        open_px=90_000.0,
        high=91_200.0,
        low=89_800.0,
        close=91_050.0,
        late_open=90_700.0,
        late_close=91_050.0,
        late_imb=0.40,
        n=200,
    )
    assert read is not None
    assert read.bias == "BULLISH"
    assert read.prob_up >= 0.58


def test_bearish_day_shorts() -> None:
    read = score_session(
        open_px=90_000.0,
        high=90_200.0,
        low=88_700.0,
        close=88_850.0,
        late_open=89_300.0,
        late_close=88_850.0,
        late_imb=-0.40,
        n=200,
    )
    assert read is not None
    assert read.bias == "BEARISH"
    assert read.prob_up <= 0.42


def test_mixed_tape_skips() -> None:
    read = score_session(
        open_px=90_000.0,
        high=90_100.0,
        low=89_900.0,
        close=90_000.0,
        late_open=90_000.0,
        late_close=90_000.0,
        late_imb=0.0,
        n=200,
    )
    assert read is not None
    assert read.bias == "NEUTRAL"
    assert 0.42 < read.prob_up < 0.58


def test_unreadable_day() -> None:
    assert (
        score_session(
            open_px=90_000.0,
            high=91_000.0,
            low=89_000.0,
            close=90_500.0,
            late_open=90_400.0,
            late_close=90_500.0,
            late_imb=0.2,
            n=8,
        )
        is None
    )


def test_ticks_flat_is_neutral() -> None:
    open_ts = datetime(2026, 8, 20, 9, 0, tzinfo=IST)
    now = datetime(2026, 8, 20, 23, 20, tzinfo=IST)
    ticks = []
    t = open_ts
    while t <= now:
        ticks.append((t, 90_000.0, 100.0, 100.0))
        t += timedelta(minutes=5)
    read = score_ticks(ticks, now=now)
    assert read is not None
    assert read.n >= 20
    assert read.bias == "NEUTRAL"


def test_buy_in_close_window() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        st = _st(tmp)
        now = datetime(2026, 8, 20, 23, 20, tzinfo=IST)
        samples = _samples(now, drift=12.0, tbq=220.0, tsq=70.0)
        st._day = "2026-08-20"
        st._hydrated_day = "2026-08-20"
        st.samples = samples[:-1]
        px = samples[-1][1]
        out = st.on_tick(
            now,
            px,
            {"total_buy_quantity": 220, "total_sell_quantity": 70},
        )
        assert out is not None
        assert out.action == "BUY"
        assert out.position_after == "long"
        assert st.entry_date == "2026-08-20"
        assert BOOK in (out.reason or "")


def test_short_in_close_window() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        st = _st(tmp)
        now = datetime(2026, 8, 20, 23, 20, tzinfo=IST)
        samples = _samples(now, drift=-12.0, tbq=70.0, tsq=220.0)
        st._day = "2026-08-20"
        st._hydrated_day = "2026-08-20"
        st.samples = samples[:-1]
        px = samples[-1][1]
        out = st.on_tick(
            now,
            px,
            {"total_buy_quantity": 70, "total_sell_quantity": 220},
        )
        assert out is not None
        assert out.action == "SHORT"
        assert out.position_after == "short"


def test_mixed_close_window_holds() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        st = _st(tmp)
        now = datetime(2026, 8, 20, 23, 20, tzinfo=IST)
        samples = _samples(now, drift=0.0, tbq=100.0, tsq=100.0)
        st._day = "2026-08-20"
        st._hydrated_day = "2026-08-20"
        st.samples = samples[:-1]
        out = st.on_tick(
            now,
            90_000.0,
            {"total_buy_quantity": 100, "total_sell_quantity": 100},
        )
        assert out is None
        assert st.last_skip and st.last_skip.startswith("neutral_")
        assert st.position == "flat"


def test_no_entry_before_close_window() -> None:
    with tempfile.TemporaryDirectory() as td:
        st = _st(Path(td))
        now = datetime(2026, 8, 20, 22, 0, tzinfo=IST)
        out = st.on_tick(now, 90_000.0, {"total_buy_quantity": 200, "total_sell_quantity": 50})
        assert out is None
        assert st.last_skip == "wait_close_window"


def test_exit_next_morning() -> None:
    with tempfile.TemporaryDirectory() as td:
        st = _st(Path(td))
        st.position = "long"
        st.entry_price = 90_800.0
        st.entry_date = "2026-08-20"
        now = datetime(2026, 8, 21, 9, 2, tzinfo=IST)
        out = st.on_tick(now, 91_100.0, {})
        assert out is not None
        assert out.action == "CLOSE"
        assert st.position == "flat"
        assert st.entry_date is None


def test_same_day_open_does_not_exit() -> None:
    with tempfile.TemporaryDirectory() as td:
        st = _st(Path(td))
        st.position = "long"
        st.entry_price = 90_800.0
        st.entry_date = "2026-08-21"
        now = datetime(2026, 8, 21, 9, 2, tzinfo=IST)
        out = st.on_tick(now, 91_100.0, {})
        assert out is None
        assert st.position == "long"
        assert st.last_skip == "in_overnight_hold"


def test_friday_entry_exits_monday() -> None:
    with tempfile.TemporaryDirectory() as td:
        st = _st(Path(td))
        st.position = "short"
        st.entry_price = 90_800.0
        st.entry_date = "2026-08-21"
        now = datetime(2026, 8, 24, 9, 2, tzinfo=IST)
        out = st.on_tick(now, 90_400.0, {})
        assert out is not None
        assert out.action == "CLOSE"


def test_rollover_blocks_entry() -> None:
    with tempfile.TemporaryDirectory() as td:
        st = _st(Path(td))
        st.set_contract(
            {
                "symbol": "GOLD26AUG",
                "rolled": False,
                "days_to_front_expiry": 1,
                "rollover_days": 5,
            }
        )
        now = datetime(2026, 8, 20, 23, 20, tzinfo=IST)
        out = st.on_tick(now, 91_000.0, {"total_buy_quantity": 200, "total_sell_quantity": 50})
        assert out is None
        assert st.last_skip == "rollover_block_last_front"


def test_hydrate_uses_session_high_low() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "ticks.db"
        con = sqlite3.connect(str(db))
        con.execute(
            "CREATE TABLE ticks (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "received_at TEXT, ltp REAL, bp REAL, sp REAL)"
        )
        rows = [
            ("2026-08-20 09:00:00", 90_000.0, 100.0, 100.0),
            ("2026-08-20 12:00:00", 92_000.0, 180.0, 80.0),
            ("2026-08-20 15:00:00", 89_500.0, 80.0, 180.0),
        ]
        t = datetime(2026, 8, 20, 22, 45, tzinfo=IST)
        px = 90_400.0
        while t <= datetime(2026, 8, 20, 23, 20, tzinfo=IST):
            rows.append(
                (t.strftime("%Y-%m-%d %H:%M:%S"), px, 210.0, 70.0)
            )
            px += 25.0
            t += timedelta(minutes=1)
        con.executemany(
            "INSERT INTO ticks (received_at, ltp, bp, sp) VALUES (?, ?, ?, ?)",
            rows,
        )
        con.commit()
        con.close()
        st = OvernightGapStrategy(
            seed=False,
            state_path=tmp / "state.json",
            db_path=db,
        )
        now = datetime(2026, 8, 20, 23, 20, tzinfo=IST)
        out = st.on_tick(
            now,
            px,
            {"total_buy_quantity": 210, "total_sell_quantity": 70},
        )
        assert st.last_read is not None
        assert isinstance(st.last_read, GapRead)
        assert st.last_read.high >= 91_900.0
        assert st.last_read.low <= 89_600.0
        assert out is not None
        assert out.action == "BUY"


def test_enable_default_true() -> None:
    import os

    from portfolio import portfolio_from_env

    os.environ.pop("ENABLE_OVERNIGHT_GAP", None)
    p = portfolio_from_env()
    assert "OVERNIGHT_GAP" in p.enabled
    env = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "ENABLE_OVERNIGHT_GAP=true" in env
    portfolio = (ROOT / "portfolio.py").read_text(encoding="utf-8")
    assert 'on("ENABLE_OVERNIGHT_GAP", "true")' in portfolio
    panel = (ROOT / "control_panel.py").read_text(encoding="utf-8")
    assert "ensure_overnight_gap_enable" in panel


def test_wide_spread_allows() -> None:
    from portfolio import DEFAULT_ALLOWED, PortfolioConfig

    assert BOOK in DEFAULT_ALLOWED["WIDE_SPREAD"]
    p = PortfolioConfig(enabled={BOOK})
    assert p.allows(BOOK, "WIDE_SPREAD")
    assert p.allows(BOOK, "CHOP")
    assert p.allows(BOOK, "TREND")
    assert p.allows(BOOK, "QUIET")


def test_live_eligible_without_40() -> None:
    assert BOOK not in PAPER_ONLY_BOOKS
    assert BOOK not in NEVER_LIVE_BOOKS
    assert BOOK in LIVE_ELIGIBLE_BOOKS
    assert book_may_go_live(
        BOOK,
        summaries={BOOK: {"closed": 0, "win_rate_after_charges": 0.0}},
    ) is True
    assert book_may_go_live(
        BOOK,
        summaries={BOOK: {"closed": 20, "win_rate_after_charges": 39.9}},
    ) is True


def test_mood_exempt() -> None:
    from market_mood import MOOD_EXEMPT_BOOKS, classify_samples, mood_blocks_entry

    fall = []
    px, tbq, tsq = 15466.0, 8000.0, 5000.0
    for _i in range(24):
        px -= 4.0
        tbq -= 20.0
        tsq += 80.0
        fall.append((px, tbq, tsq))
    st = classify_samples(fall, gate=True, flatten=True)
    blocked, why = mood_blocks_entry(st, "BUY", strategy=BOOK)
    assert blocked is False
    assert why == "mood_removed"
    assert BOOK in MOOD_EXEMPT_BOOKS
    fit = st.fit_for(BOOK)
    assert fit is not None
    assert fit["stance"] == "hold_swing"


def test_on_desk_and_slim() -> None:
    assert BOOK in ALL_STRATEGY_NAMES
    assert BOOK in SLIM_PAPER_STRATEGIES


def test_eod_skip_and_restore() -> None:
    from position_safety import EOD_FLATTEN_SKIP, INTRADAY_RESTORE, SESSION_CLOSE_OVERNIGHT

    assert BOOK in EOD_FLATTEN_SKIP
    assert BOOK in INTRADAY_RESTORE
    assert BOOK not in SESSION_CLOSE_OVERNIGHT


def test_not_s7_and_wired() -> None:
    src = (ROOT / "run_strategy.py").read_text(encoding="utf-8")
    assert "from strategy_s7" not in src
    assert '_load("S7' not in src
    assert "from strategy_overnight import" not in src
    assert "overnight_gap_from_env" in src
    assert "emit_overnight_gap_if_changed" in src
    start = src.index("def emit_overnight_gap_if_changed")
    end = src.index("def emit_flow_brain_if_changed")
    block = src[start:end]
    assert "mood_blocks_entry" not in block
    assert "_may_enter" not in block
    assert "WIDE_SPREAD" not in block
    assert "allow_new_entry" in block


def test_disabled_test_does_not_import_module_at_top() -> None:
    src = (ROOT / "test_strategy_disabled.py").read_text(encoding="utf-8")
    head = src.split("class ")[0] if "class " in src else src.split("def test_run_strategy")[0]
    assert "from strategy_overnight_gap import" not in head
    run_head = (ROOT / "run_strategy.py").read_text(encoding="utf-8").split("def run_once")[0]
    assert "from strategy_overnight_gap import" not in run_head


if __name__ == "__main__":
    test_entry_last_fifteen_minutes()
    test_exit_next_morning_window()
    test_bullish_day_buys()
    test_bearish_day_shorts()
    test_mixed_tape_skips()
    test_unreadable_day()
    test_ticks_flat_is_neutral()
    test_buy_in_close_window()
    test_short_in_close_window()
    test_mixed_close_window_holds()
    test_no_entry_before_close_window()
    test_exit_next_morning()
    test_same_day_open_does_not_exit()
    test_friday_entry_exits_monday()
    test_rollover_blocks_entry()
    test_hydrate_uses_session_high_low()
    test_enable_default_true()
    test_live_eligible_without_40()
    test_mood_exempt()
    test_on_desk_and_slim()
    test_wide_spread_allows()
    test_eod_skip_and_restore()
    test_not_s7_and_wired()
    test_disabled_test_does_not_import_module_at_top()
    print("ALL test_overnight_gap OK")
