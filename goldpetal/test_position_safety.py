"""Tests for restart / orphan / EOD safety helpers."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from position_safety import (
    OpenPosition,
    apply_position_to_strategy,
    in_eod_flatten_window,
    intraday_open_for_flatten,
    startup_reconcile,
)

IST = ZoneInfo("Asia/Kolkata")


class _Fake:
    def __init__(self, name: str) -> None:
        self.name = name
        self.position = "flat"
        self.entry_price = None
        self.entry_date = None
        self.saved = False
        self.target_points = None
        self.stop_points = None
        self.required_points = 50.0

    def _save_state(self) -> None:
        self.saved = True

    @property
    def status_line(self) -> str:
        return f"pos={self.position}"


class _Disabled:
    name = "S5_MINEDGE"
    position = "flat"
    entry_price = None
    enabled = False

    @property
    def status_line(self) -> str:
        return "DISABLED (not loaded — frees RAM)"


def test_apply_restore_s5() -> None:
    s = _Fake("S5_MINEDGE")
    ok = apply_position_to_strategy(
        s,
        OpenPosition("S5_MINEDGE", "short", 15300.0, "t", "SHORT", 15300.0),
    )
    assert ok
    assert s.position == "short"
    assert s.entry_price == 15300.0
    assert s.target_points == 50.0
    assert s.saved is True


def test_apply_restore_s13_sets_entry_date() -> None:
    s = _Fake("S13_HHHL_DAY")
    ok = apply_position_to_strategy(
        s,
        OpenPosition(
            "S13_HHHL_DAY",
            "long",
            15462.0,
            "2026-08-14T23:20:00+05:30",
            "BUY",
            15462.0,
        ),
    )
    assert ok
    assert s.position == "long"
    assert s.entry_date == "2026-08-14"
    assert s.saved is True


def test_apply_restore_s4_sets_entry_date() -> None:
    s = _Fake("S4_OVERNIGHT")
    ok = apply_position_to_strategy(
        s,
        OpenPosition(
            "S4_OVERNIGHT",
            "short",
            15400.0,
            "2026-08-14T23:20:00+05:30",
            "SHORT",
            15400.0,
        ),
    )
    assert ok
    assert s.position == "short"
    assert s.entry_date == "2026-08-14"
    assert s.saved is True


def test_disabled_not_restorable() -> None:
    s = _Disabled()
    ok = apply_position_to_strategy(
        s,
        OpenPosition("S5_MINEDGE", "long", 1.0, "t", "BUY", 1.0),
    )
    assert not ok


def test_s12_not_eod_flattened() -> None:
    class _S12:
        name = "S12_HHHL30"
        position = "long"
        entry_price = 15000.0

    class _S5:
        name = "S5_MINEDGE"
        position = "short"
        entry_price = 15100.0

    rows = intraday_open_for_flatten({"S12_HHHL30": _S12(), "S5_MINEDGE": _S5()})
    names = {r["strategy"] for r in rows}
    assert "S5_MINEDGE" in names
    assert "S12_HHHL30" not in names


def test_s14_s15_not_eod_flattened() -> None:
    class _W:
        def __init__(self, name: str) -> None:
            self.name = name
            self.position = "long"
            self.entry_price = 15400.0

    class _S5:
        name = "S5_MINEDGE"
        position = "short"
        entry_price = 15100.0

    rows = intraday_open_for_flatten(
        {
            "S14_WICK30_STRICT": _W("S14_WICK30_STRICT"),
            "S15_WICK30_NOWICK": _W("S15_WICK30_NOWICK"),
            "S5_MINEDGE": _S5(),
        }
    )
    names = {r["strategy"] for r in rows}
    assert "S5_MINEDGE" in names
    assert "S14_WICK30_STRICT" not in names
    assert "S15_WICK30_NOWICK" not in names


def test_s4_not_eod_flattened() -> None:
    class _S4:
        name = "S4_OVERNIGHT"
        position = "long"
        entry_price = 15400.0

    class _S5:
        name = "S5_MINEDGE"
        position = "short"
        entry_price = 15100.0

    rows = intraday_open_for_flatten(
        {"S4_OVERNIGHT": _S4(), "S5_MINEDGE": _S5()}
    )
    names = {r["strategy"] for r in rows}
    assert "S5_MINEDGE" in names
    assert "S4_OVERNIGHT" not in names


def test_s16_is_eod_flattened() -> None:
    class _S16:
        name = "S16_HHHL_WICK_1H"
        position = "long"
        entry_price = 15400.0

    class _S5:
        name = "S5_MINEDGE"
        position = "short"
        entry_price = 15100.0

    rows = intraday_open_for_flatten(
        {"S16_HHHL_WICK_1H": _S16(), "S5_MINEDGE": _S5()}
    )
    names = {r["strategy"] for r in rows}
    assert "S5_MINEDGE" in names
    assert "S16_HHHL_WICK_1H" in names


def test_s18_is_eod_flattened() -> None:
    class _S18:
        name = "S18_OHLC_VOL_HTF"
        position = "short"
        entry_price = 15400.0

    class _S5:
        name = "S5_MINEDGE"
        position = "short"
        entry_price = 15100.0

    rows = intraday_open_for_flatten(
        {"S18_OHLC_VOL_HTF": _S18(), "S5_MINEDGE": _S5()}
    )
    names = {r["strategy"] for r in rows}
    assert "S5_MINEDGE" in names
    assert "S18_OHLC_VOL_HTF" in names


def test_s19_is_eod_flattened() -> None:
    class _S19:
        name = "S19_BODY_CLOSE_1H"
        position = "long"
        entry_price = 15400.0

    class _S5:
        name = "S5_MINEDGE"
        position = "short"
        entry_price = 15100.0

    rows = intraday_open_for_flatten(
        {"S19_BODY_CLOSE_1H": _S19(), "S5_MINEDGE": _S5()}
    )
    names = {r["strategy"] for r in rows}
    assert "S5_MINEDGE" in names
    assert "S19_BODY_CLOSE_1H" in names


def test_s20_is_eod_flattened() -> None:
    class _S20:
        name = "S20_FADE_HL"
        position = "long"
        entry_price = 15400.0

    class _S5:
        name = "S5_MINEDGE"
        position = "short"
        entry_price = 15100.0

    rows = intraday_open_for_flatten(
        {"S20_FADE_HL": _S20(), "S5_MINEDGE": _S5()}
    )
    names = {r["strategy"] for r in rows}
    assert "S5_MINEDGE" in names
    assert "S20_FADE_HL" in names


def test_daily_amise_skips_eod_flatten_and_restores_overnight() -> None:
    class _Daily:
        name = "S21_AMISE"
        position = "long"
        holds_overnight = True
        entry_price = 15400.0

    class _Intra:
        name = "S22_AMISE"
        position = "short"
        holds_overnight = False
        entry_price = 15400.0

    class _S5:
        name = "S5_MINEDGE"
        position = "short"
        entry_price = 15100.0

    rows = intraday_open_for_flatten(
        {"S21_AMISE": _Daily(), "S22_AMISE": _Intra(), "S5_MINEDGE": _S5()}
    )
    names = {r["strategy"] for r in rows}
    assert "S5_MINEDGE" in names
    assert "S22_AMISE" in names
    assert "S21_AMISE" not in names

    import position_safety as ps

    def fake_last(name: str):
        if name == "S21_AMISE":
            return OpenPosition(
                name, "long", 15400.0, "2026-08-17T22:00:00", "BUY", 15400.0
            )
        return None

    orig = ps.last_open_position
    ps.last_open_position = fake_last  # type: ignore[assignment]
    try:
        daily = _Daily()
        daily.position = "flat"
        res = startup_reconcile(
            {"S21_AMISE": daily},
            mode="restore",
            now=datetime(2026, 8, 18, 9, 5, tzinfo=IST),
        )
        assert daily.position == "long"
        assert len(res["restored"]) == 1
        assert res["closes"] == []
    finally:
        ps.last_open_position = orig  # type: ignore[assignment]


def test_eod_window() -> None:
    # MARKET_CLOSE 23:30, last 5 minutes → 23:25–23:30
    assert in_eod_flatten_window(
        datetime(2026, 8, 12, 23, 27, tzinfo=IST),
        market_close="23:30",
        minutes=5,
    )
    assert not in_eod_flatten_window(
        datetime(2026, 8, 12, 23, 20, tzinfo=IST),
        market_close="23:30",
        minutes=5,
    )
    # weekend
    assert not in_eod_flatten_window(
        datetime(2026, 8, 15, 23, 27, tzinfo=IST),  # Saturday
        market_close="23:30",
        minutes=5,
    )


def test_startup_reconcile_restore(monkeypatch_signals=None) -> None:
    # Unit-level: monkey via injecting fake last_open by patching module
    import position_safety as ps

    calls: list = []

    def fake_last(name: str):
        if name == "S8_NET_ZIGZAG":
            return OpenPosition(name, "long", 15366.0, "2026-08-11T16:30:00", "BUY", 15366.0)
        return None

    orig = ps.last_open_position
    ps.last_open_position = fake_last  # type: ignore[assignment]
    try:
        s8 = _Fake("S8_NET_ZIGZAG")
        res = startup_reconcile({"S8_NET_ZIGZAG": s8}, mode="restore")
        assert s8.position == "long"
        assert len(res["restored"]) == 1
        assert res["closes"] == []
        res2 = startup_reconcile({"S8_NET_ZIGZAG": s8}, mode="close")
        assert len(res2["closes"]) == 1
    finally:
        ps.last_open_position = orig  # type: ignore[assignment]
    del calls


def test_s16_overnight_is_closed_not_restored() -> None:
    import position_safety as ps

    def fake_last(name: str):
        if name == "S16_HHHL_WICK_1H":
            return OpenPosition(
                name, "long", 15400.0, "2026-08-17T22:00:00", "BUY", 15400.0
            )
        return None

    orig = ps.last_open_position
    ps.last_open_position = fake_last  # type: ignore[assignment]
    try:
        s16 = _Fake("S16_HHHL_WICK_1H")
        res = startup_reconcile(
            {"S16_HHHL_WICK_1H": s16},
            mode="restore",
            now=datetime(2026, 8, 18, 9, 5, tzinfo=IST),
        )
        assert s16.position == "flat"
        assert len(res["closes"]) == 1
        assert res["restored"] == []
    finally:
        ps.last_open_position = orig  # type: ignore[assignment]


def test_s18_overnight_is_closed_not_restored() -> None:
    import position_safety as ps

    def fake_last(name: str):
        if name == "S18_OHLC_VOL_HTF":
            return OpenPosition(
                name, "short", 15400.0, "2026-08-17T22:00:00", "SHORT", 15400.0
            )
        return None

    orig = ps.last_open_position
    ps.last_open_position = fake_last  # type: ignore[assignment]
    try:
        s18 = _Fake("S18_OHLC_VOL_HTF")
        res = startup_reconcile(
            {"S18_OHLC_VOL_HTF": s18},
            mode="restore",
            now=datetime(2026, 8, 18, 9, 5, tzinfo=IST),
        )
        assert s18.position == "flat"
        assert len(res["closes"]) == 1
        assert res["restored"] == []
    finally:
        ps.last_open_position = orig  # type: ignore[assignment]


def test_s19_overnight_is_closed_not_restored() -> None:
    import position_safety as ps

    def fake_last(name: str):
        if name == "S19_BODY_CLOSE_1H":
            return OpenPosition(
                name, "long", 15400.0, "2026-08-17T22:00:00", "BUY", 15400.0
            )
        return None

    orig = ps.last_open_position
    ps.last_open_position = fake_last  # type: ignore[assignment]
    try:
        s19 = _Fake("S19_BODY_CLOSE_1H")
        res = startup_reconcile(
            {"S19_BODY_CLOSE_1H": s19},
            mode="restore",
            now=datetime(2026, 8, 18, 9, 5, tzinfo=IST),
        )
        assert s19.position == "flat"
        assert len(res["closes"]) == 1
        assert res["restored"] == []
    finally:
        ps.last_open_position = orig  # type: ignore[assignment]


def test_s20_overnight_is_closed_not_restored() -> None:
    import position_safety as ps

    def fake_last(name: str):
        if name == "S20_FADE_HL":
            return OpenPosition(
                name, "long", 15400.0, "2026-08-17T22:00:00", "BUY", 15400.0
            )
        return None

    orig = ps.last_open_position
    ps.last_open_position = fake_last  # type: ignore[assignment]
    try:
        s20 = _Fake("S20_FADE_HL")
        res = startup_reconcile(
            {"S20_FADE_HL": s20},
            mode="restore",
            now=datetime(2026, 8, 18, 9, 5, tzinfo=IST),
        )
        assert s20.position == "flat"
        assert len(res["closes"]) == 1
        assert res["restored"] == []
    finally:
        ps.last_open_position = orig  # type: ignore[assignment]


if __name__ == "__main__":
    test_apply_restore_s5()
    print("ok apply")
    test_apply_restore_s13_sets_entry_date()
    print("ok s13 restore")
    test_apply_restore_s4_sets_entry_date()
    print("ok s4 restore")
    test_disabled_not_restorable()
    print("ok disabled")
    test_eod_window()
    print("ok eod")
    test_s12_not_eod_flattened()
    print("ok s12 skip flatten")
    test_s14_s15_not_eod_flattened()
    print("ok s14/s15 skip flatten")
    test_s16_is_eod_flattened()
    print("ok s16 eod flatten")
    test_s18_is_eod_flattened()
    print("ok s18 eod flatten")
    test_s19_is_eod_flattened()
    print("ok s19 eod flatten")
    test_s20_is_eod_flattened()
    print("ok s20 eod flatten")
    test_daily_amise_skips_eod_flatten_and_restores_overnight()
    print("ok daily AMISE overnight")
    test_s4_not_eod_flattened()
    print("ok s4 skip flatten")
    test_startup_reconcile_restore()
    print("ok reconcile")
    test_s16_overnight_is_closed_not_restored()
    print("ok s16 overnight close")
    test_s18_overnight_is_closed_not_restored()
    print("ok s18 overnight close")
    test_s19_overnight_is_closed_not_restored()
    print("ok s19 overnight close")
    test_s20_overnight_is_closed_not_restored()
    print("ok s20 overnight close")
    print("ALL test_position_safety OK")
