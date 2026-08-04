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
    assert not p.allows("S2_BALANCE", "CHOP")


def test_env_defaults_include_s2(monkeypatch=None) -> None:
    import os

    os.environ.pop("ENABLE_S1", None)
    os.environ.pop("ENABLE_S2", None)
    os.environ.pop("ENABLE_S3", None)
    os.environ.pop("ENABLE_S4", None)
    os.environ.pop("ENABLE_S5", None)
    os.environ.pop("ENABLE_S6", None)
    p = portfolio_from_env()
    assert "S1_NETDELTA" in p.enabled
    assert "S2_BALANCE" in p.enabled
    assert "S3_ML" in p.enabled
    assert "S4_OVERNIGHT" in p.enabled
    assert "S5_MINEDGE" in p.enabled
    assert "S6_MIN30" in p.enabled


if __name__ == "__main__":
    test_regime_quiet_vs_trend()
    test_wide_spread()
    test_portfolio_gates()
    test_env_defaults_include_s2()
    print("ok")
