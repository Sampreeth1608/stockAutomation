"""Tests for desk env bridge + auth helpers."""

from __future__ import annotations

import os
from pathlib import Path

from analytics.desk_auth import make_password_hash, verify_password
from analytics.env_bridge import (
    apply_env_patch,
    apply_strategy_enables,
    read_env,
    write_env_updates,
)


def test_write_env_whitelist(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("ENABLE_S4=false\nSECRET=keep\n", encoding="utf-8")
    res = write_env_updates(
        {"ENABLE_S4": "true", "DRY_RUN": "true", "SECRET": "hack", "LIVE_MAX_LOTS": "5"},
        path=env,
    )
    assert res["ok"]
    assert res["applied"]["ENABLE_S4"] == "true"
    assert res["applied"]["LIVE_MAX_LOTS"] == "5"
    assert "SECRET" in res["skipped"]
    text = env.read_text(encoding="utf-8")
    assert "SECRET=keep" in text
    assert "ENABLE_S4=true" in text
    assert "DRY_RUN=true" in text


def test_strategy_enables(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("", encoding="utf-8")
    res = apply_strategy_enables(
        ["S4_OVERNIGHT", "S13_HHHL_DAY"],
        known=["S4_OVERNIGHT", "S5_MINEDGE", "S13_HHHL_DAY"],
        path=env,
    )
    assert res["ok"]
    snap = read_env(env)
    assert snap["ENABLE_S4"] == "true"
    assert snap["ENABLE_S5"] == "false"
    assert snap["ENABLE_S13"] == "true"


def test_apply_s11_pack_path(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    res = apply_env_patch(
        {"S11_PACK_PATH": "data/discover/packs/demo.json", "ENABLE_S11": "true"},
        path=env,
    )
    assert res["ok"]
    assert read_env(env)["S11_PACK_PATH"] == "data/discover/packs/demo.json"


def test_password_hash_roundtrip() -> None:
    hashed = make_password_hash("desk-secret")
    os.environ.pop("DESK_PASSWORD", None)
    os.environ["DESK_PASSWORD_HASH"] = hashed
    assert verify_password("desk-secret")
    assert not verify_password("wrong")
    os.environ.pop("DESK_PASSWORD_HASH", None)


if __name__ == "__main__":
    from tempfile import TemporaryDirectory

    with TemporaryDirectory() as td:
        root = Path(td)
        test_write_env_whitelist(root)
        print("ok whitelist")
        (root / "e2").mkdir()
        test_strategy_enables(root / "e2")
        print("ok enables")
        (root / "e3").mkdir()
        test_apply_s11_pack_path(root / "e3")
        print("ok s11")
    test_password_hash_roundtrip()
    print("ok password")
    print("ALL test_desk_ops OK")
