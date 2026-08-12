"""Desk authentication: password gate + TOTP for dangerous actions."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
ROOT = Path(__file__).resolve().parents[1]
CONTROL = ROOT / "data" / "control"
AUDIT_PATH = CONTROL / "desk_audit.jsonl"


def _now() -> str:
    return datetime.now(IST).isoformat(timespec="seconds")


def desk_password_configured() -> bool:
    return bool(os.getenv("DESK_PASSWORD", "").strip() or os.getenv("DESK_PASSWORD_HASH", "").strip())


def desk_totp_configured() -> bool:
    return bool(os.getenv("DESK_TOTP_SECRET", "").strip())


def _password_hash(password: str, salt: str) -> str:
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        120_000,
    )
    return digest.hex()


def verify_password(password: str) -> bool:
    """Accept DESK_PASSWORD (plain) or DESK_PASSWORD_HASH=salt:hex."""
    plain = os.getenv("DESK_PASSWORD", "").strip()
    if plain:
        return hmac.compare_digest(password, plain)
    hashed = os.getenv("DESK_PASSWORD_HASH", "").strip()
    if not hashed or ":" not in hashed:
        # No password configured → deny if auth required; caller decides.
        return False
    salt, expect = hashed.split(":", 1)
    got = _password_hash(password, salt)
    return hmac.compare_digest(got, expect)


def verify_totp(code: str) -> bool:
    secret = os.getenv("DESK_TOTP_SECRET", "").strip().replace(" ", "")
    if not secret:
        return False
    try:
        import pyotp
    except ImportError:
        return False
    totp = pyotp.TOTP(secret)
    return bool(totp.verify(str(code).strip(), valid_window=1))


def make_password_hash(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(8)
    return f"{salt}:{_password_hash(password, salt)}"


def audit(event: str, *, ok: bool, detail: dict[str, Any] | None = None) -> None:
    CONTROL.mkdir(parents=True, exist_ok=True)
    row = {
        "ts_ist": _now(),
        "event": event,
        "ok": bool(ok),
        "detail": detail or {},
    }
    with AUDIT_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, default=str) + "\n")


def auth_required() -> bool:
    """If DESK_AUTH=false, skip login (not recommended). Default: require when password set."""
    flag = os.getenv("DESK_AUTH", "").strip().lower()
    if flag in {"0", "false", "no", "n", "off"}:
        return False
    if flag in {"1", "true", "yes", "y", "on"}:
        return True
    return desk_password_configured()


def dangerous_requires_totp() -> bool:
    """Live/restart/DRY_RUN=false require TOTP when DESK_TOTP_SECRET is set."""
    flag = os.getenv("DESK_TOTP_REQUIRED", "true").strip().lower()
    if flag in {"0", "false", "no", "n", "off"}:
        return False
    return desk_totp_configured()
