"""Tests: other Streamlit tabs must not overwrite the Desk tab."""

from __future__ import annotations

from operator_desk import streamlit_control_allowed, streamlit_write_blocked


def test_streamlit_write_blocked() -> None:
    res = streamlit_write_blocked("Capital")
    assert res["ok"] is False
    assert "lite desk" in res["error"]
    assert "8501" in res["error"]
    assert "8787" in res["error"]
    assert res["use"].endswith(":8501/")


def test_streamlit_may_only_stop() -> None:
    ok, _ = streamlit_control_allowed({"emergency_off": True})
    assert ok is True
    ok, _ = streamlit_control_allowed({"trading_enabled": False})
    assert ok is True
    ok, _ = streamlit_control_allowed({"live_unlocked": False})
    assert ok is True
    ok, why = streamlit_control_allowed({"emergency_off": False})
    assert ok is False
    assert "Clear" in why or "unlock" in why.lower()
    ok, _ = streamlit_control_allowed({"live_unlocked": True})
    assert ok is False
    ok, _ = streamlit_control_allowed({"trading_enabled": True})
    assert ok is False


if __name__ == "__main__":
    test_streamlit_write_blocked()
    print("ok blocked")
    test_streamlit_may_only_stop()
    print("ok stop only")
    print("ALL test_operator_desk OK")
