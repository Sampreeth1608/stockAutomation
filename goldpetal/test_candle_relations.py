"""OHLC / wick / prev-bar / higher-TF relation features."""

from __future__ import annotations

from backtest_hhhl_candles import Candle
from candle_relations import (
    FEATURE_COLUMNS,
    attach_completed_higher,
    labeled_rows,
    pair_features,
)


def _c(t: str, o: float, h: float, l: float, c: float) -> Candle:
    return Candle(t, o, h, l, c)


PREV = _c("2026-08-17 10:00:00", 100.0, 105.0, 99.0, 104.0)
CUR = _c("2026-08-17 11:00:00", 104.0, 120.0, 100.0, 110.0)
DAY = _c("2026-08-16 00:00:00", 90.0, 130.0, 80.0, 95.0)


def test_same_bar_ohlc_and_wicks() -> None:
    f = pair_features(PREV, CUR)
    assert f["o_h"] == 16.0
    assert f["o_l"] == 4.0
    assert f["o_c"] == 6.0
    assert f["h_c"] == 10.0
    assert f["l_c"] == 10.0
    assert f["upper"] == 10.0
    assert f["lower"] == 4.0
    assert f["body"] == 6.0
    assert f["green"] == 1.0
    assert 0.0 < f["close_loc"] < 1.0


def test_vs_prev_includes_requested_crosses() -> None:
    f = pair_features(PREV, CUR)
    assert f["c_pc"] == 6.0
    assert f["h_ph"] == 15.0
    assert f["l_pl"] == 1.0
    assert f["h_pl"] == 21.0
    assert f["l_ph"] == -5.0
    assert f["c_po"] == 10.0
    assert f["l_pc"] == -4.0
    assert f["o_pc"] == 0.0
    assert f["hh"] == 1.0
    assert f["ll"] == 0.0
    assert "o_h" in FEATURE_COLUMNS
    assert "l_ph" in FEATURE_COLUMNS
    assert "htf_c_c" in FEATURE_COLUMNS


def test_higher_tf_close_vs_yesterday() -> None:
    f = pair_features(PREV, CUR, higher=DAY)
    assert f["htf_present"] == 1.0
    assert f["htf_c_c"] == 15.0
    assert f["htf_c_h"] == 110.0 - 130.0
    none = pair_features(PREV, CUR, higher=None)
    assert none["htf_present"] == 0.0


def test_attach_uses_completed_higher_only() -> None:
    hours = [
        _c("2026-08-17 09:00:00", 100, 101, 99, 100.5),
        _c("2026-08-17 10:00:00", 100.5, 102, 100, 101),
        _c("2026-08-17 11:00:00", 101, 103, 100, 102),
    ]
    days = [_c("2026-08-16 00:00:00", 90, 110, 80, 95)]
    attached = attach_completed_higher(hours, days, 1440)
    assert attached[0] is not None
    assert attached[0].close == 95.0


def test_label_is_next_close() -> None:
    candles = [
        PREV,
        CUR,
        _c("2026-08-17 12:00:00", 110.0, 111.0, 109.0, 108.0),
    ]
    rows = labeled_rows(candles)
    assert len(rows) == 1
    assert rows[0]["y_up"] == 0
    assert rows[0]["next_pts"] == -2.0


def test_learn_script_fits_toy_bars() -> None:
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    from learn_candle_relations import run_tf

    ist = ZoneInfo("Asia/Kolkata")
    rows = []
    px = 100.0
    start = datetime(2026, 8, 17, 9, 0, tzinfo=ist)
    for i in range(48):
        ts = start + timedelta(hours=i)
        o = px
        h = o + 8
        l = o - 3
        c = o + (4 if i % 2 == 0 else -2)
        stamp = ts.isoformat(timespec="seconds")
        rows.append((stamp, o))
        rows.append(((ts.replace(minute=10)).isoformat(timespec="seconds"), h))
        rows.append(((ts.replace(minute=20)).isoformat(timespec="seconds"), l))
        rows.append(((ts.replace(minute=50)).isoformat(timespec="seconds"), c))
        px = c
    rep = run_tf(
        rows,
        tf="1h",
        higher=None,
        lots=1.0,
        fees=False,
        session=False,
        train_frac=0.7,
        long_p=0.55,
        short_p=0.45,
    )
    assert rep["rows"] >= 8
    assert "train_corr" in rep
    assert "error" not in rep or rep.get("test_n", 0) < 2


if __name__ == "__main__":
    test_same_bar_ohlc_and_wicks()
    test_vs_prev_includes_requested_crosses()
    test_higher_tf_close_vs_yesterday()
    test_attach_uses_completed_higher_only()
    test_label_is_next_close()
    test_learn_script_fits_toy_bars()
    print("ALL test_candle_relations OK")
