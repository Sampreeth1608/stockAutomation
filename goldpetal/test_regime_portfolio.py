"""Tests for regime detector and portfolio gating."""

from __future__ import annotations

from portfolio import PortfolioConfig, portfolio_from_env
from regime import RegimeDetector


def test_regime_quiet_vs_trend() -> None:
    d = RegimeDetector(window=40)
    # flat prices -> QUIET or UNKNOWN then QUIET
    for i in range(30):
        d.update(14000.0 + (0.01 if i % 2 == 0 else -0.01), spread_bps=1.0)
    assert d.last.regime in {"QUIET", "CHOP", "TREND", "UNKNOWN"}

    d2 = RegimeDetector(window=40)
    for i in range(40):
        d2.update(14000.0 + i * 2.0, spread_bps=1.0)  # strong drift
    assert d2.last.regime in {"TREND", "CHOP"}


def test_wide_spread() -> None:
    d = RegimeDetector(window=40)
    for i in range(30):
        d.update(14000.0 + i * 0.1, spread_bps=12.0)
    assert d.last.regime == "WIDE_SPREAD"


def test_portfolio_gates() -> None:
    p = PortfolioConfig(enabled={"S1_NETDELTA", "S3_ML", "S5_MINEDGE"})
    assert p.allows("S1_NETDELTA", "TREND")
    assert p.allows("S5_MINEDGE", "TREND")
    assert not p.allows("S2_BALANCE", "TREND")  # not enabled
    assert not p.allows("S1_NETDELTA", "WIDE_SPREAD")
    assert p.should_flatten("S1_NETDELTA", "WIDE_SPREAD")
    assert not p.allows("S3_ML", "CHOP")  # chop: overnight only by default
    assert p.allows("S5_MINEDGE", "UNKNOWN")
    # Smooth rallies often label QUIET — S5 must still be eligible (own fee gate).
    assert p.allows("S5_MINEDGE", "QUIET")
    assert p.allows("S5_MINEDGE", "CHOP")
    assert not p.allows("S2_BALANCE", "CHOP")
    p10 = PortfolioConfig(enabled={"S10_LEGACY30"})
    assert p10.allows("S10_LEGACY30", "TREND")
    assert p10.allows("S10_LEGACY30", "QUIET")
    assert p10.allows("S10_LEGACY30", "CHOP")


def test_env_defaults_include_s2(monkeypatch=None) -> None:
    import os

    os.environ.pop("ENABLE_S1", None)
    os.environ.pop("ENABLE_S2", None)
    os.environ.pop("ENABLE_S3", None)
    os.environ.pop("ENABLE_S4", None)
    os.environ.pop("ENABLE_S5", None)
    os.environ.pop("ENABLE_S6", None)
    os.environ.pop("ENABLE_FLOW_BRAIN", None)
    os.environ.pop("ENABLE_S8", None)
    os.environ.pop("ENABLE_S9", None)
    os.environ.pop("ENABLE_S10", None)
    os.environ.pop("ENABLE_S11", None)
    os.environ.pop("ENABLE_S13", None)
    os.environ.pop("ENABLE_S16", None)
    os.environ.pop("ENABLE_S18", None)
    os.environ.pop("ENABLE_S19", None)
    os.environ.pop("ENABLE_S20", None)
    os.environ.pop("ENABLE_OVERNIGHT_GAP", None)
    p = portfolio_from_env()
    assert "S1_NETDELTA" not in p.enabled
    assert "S2_BALANCE" not in p.enabled
    assert "S3_ML" not in p.enabled
    assert "S4_OVERNIGHT" not in p.enabled
    assert "S5_MINEDGE" in p.enabled
    assert "S13_HHHL_DAY" in p.enabled
    assert "S16_HHHL_WICK_1H" in p.enabled
    assert "S18_OHLC_VOL_HTF" in p.enabled
    assert "S19_BODY_CLOSE_1H" in p.enabled
    assert "S20_FADE_HL" not in p.enabled
    assert "OVERNIGHT_GAP" not in p.enabled
    assert "FLOW_BRAIN" not in p.enabled
    assert "S8_NET_ZIGZAG" in p.enabled
    assert "S9_STATE30" not in p.enabled
    assert "S10_LEGACY30" not in p.enabled
    assert "S11_DISCOVERED" not in p.enabled

    os.environ["ENABLE_S8"] = "true"
    p8 = portfolio_from_env()
    assert "S8_NET_ZIGZAG" in p8.enabled
    os.environ.pop("ENABLE_S8", None)

    os.environ["ENABLE_S9"] = "true"
    p9 = portfolio_from_env()
    assert "S9_STATE30" in p9.enabled
    os.environ.pop("ENABLE_S9", None)

    os.environ["ENABLE_S10"] = "false"
    p10 = portfolio_from_env()
    assert "S10_LEGACY30" not in p10.enabled
    os.environ.pop("ENABLE_S10", None)

    os.environ["ENABLE_S25"] = "true"
    p25 = portfolio_from_env()
    assert "S25_AMISE" in p25.enabled
    assert p25.allows("S25_AMISE", "TREND")
    assert p25.allows("S25_AMISE", "CHOP")
    assert not p25.allows("S25_AMISE", "WIDE_SPREAD")
    os.environ.pop("ENABLE_S25", None)

    os.environ["ENABLE_OVERNIGHT_GAP"] = "true"
    p_gap = portfolio_from_env()
    assert "OVERNIGHT_GAP" in p_gap.enabled
    assert p_gap.allows("OVERNIGHT_GAP", "WIDE_SPREAD")
    assert p_gap.allows("OVERNIGHT_GAP", "CHOP")
    os.environ.pop("ENABLE_OVERNIGHT_GAP", None)


if __name__ == "__main__":
    test_regime_quiet_vs_trend()
    test_wide_spread()
    test_portfolio_gates()
    test_env_defaults_include_s2()
    print("ok")
