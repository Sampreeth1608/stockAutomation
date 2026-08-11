"""Tests for almost-equal state signs."""

from __future__ import annotations

from almost_equal import sign_px, sign_rel


def test_qty_almost_equal_5pct():
    assert sign_rel(105, 100, 0.05) == "="
    assert sign_rel(106, 100, 0.05) == "+"
    assert sign_rel(94, 100, 0.05) == "-"


def test_price_band_not_5pct():
    # 5% of 10000 = 500 pts would wrongly flatten; px band is tighter
    assert sign_px(10004, 10000, equal_px_pct=0.0005) == "="  # 4 pts < 5
    assert sign_px(10010, 10000, equal_px_pct=0.0005) == "+"
    assert sign_px(10003, 10000, equal_pts=5) == "="
    assert sign_px(10000, 10010, equal_pts=5) == "-"


if __name__ == "__main__":
    test_qty_almost_equal_5pct()
    test_price_band_not_5pct()
    print("ok")
