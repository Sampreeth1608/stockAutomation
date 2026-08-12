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
_ENV_LOADED = False


def _ensure_dotenv() -> None:
    """Load goldpetal/.env into process env (Streamlit does not do this alone)."""
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    env_path = ROOT / ".env"
    try:
        from dotenv import load_dotenv

        load_dotenv(env_path, override=False)
        # Desk secrets must come from the file if present (ignore stale shell exports).
        for key in (
            "DESK_AUTH",
            "DESK_PASSWORD",
            "DESK_PASSWORD_HASH",
            "DESK_TOTP_SECRET",
            "DESK_TOTP_REQUIRED",
        ):
            # force-refresh from file via side effect of later _desk_secret_from_file
            pass
    except Exception:
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                raw = line.strip()
                if not raw or raw.startswith("#") or "=" not in raw:
                    continue
                key, val = raw.split("=", 1)
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = val
    _ENV_LOADED = True


def _now() -> str:
    return datetime.now(IST).isoformat(timespec="seconds")


def _desk_secret_from_file(key: str) -> str:
    """Read a desk secret from .env file (wins over stale shell env)."""
    env_path = ROOT / ".env"
    if not env_path.exists():
        return ""
    for line in env_path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        k, val = raw.split("=", 1)
        if k.strip() == key:
            return val.strip().strip('"').strip("'")
    return ""


def desk_password_configured() -> bool:
    _ensure_dotenv()
    plain = _desk_secret_from_file("DESK_PASSWORD") or os.getenv("DESK_PASSWORD", "").strip()
    hashed = _desk_secret_from_file("DESK_PASSWORD_HASH") or os.getenv("DESK_PASSWORD_HASH", "").strip()
    return bool(plain or hashed)


def desk_totp_configured() -> bool:
    _ensure_dotenv()
    return bool(
        _desk_secret_from_file("DESK_TOTP_SECRET") or os.getenv("DESK_TOTP_SECRET", "").strip()
    )


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
    _ensure_dotenv()
    # Prefer .env file so stale exported shell vars cannot break login.
    plain = _desk_secret_from_file("DESK_PASSWORD") or os.getenv("DESK_PASSWORD", "").strip()
    if plain:
        return hmac.compare_digest(password.strip(), plain)
    hashed = _desk_secret_from_file("DESK_PASSWORD_HASH") or os.getenv(
        "DESK_PASSWORD_HASH", ""
    ).strip()
    if not hashed or ":" not in hashed:
        return False
    salt, expect = hashed.split(":", 1)
    got = _password_hash(password.strip(), salt)
    return hmac.compare_digest(got, expect)


def verify_totp(code: str) -> bool:
    _ensure_dotenv()
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
    """If DESK_AUTH=false, skip login. Default: require when password set."""
    _ensure_dotenv()
    flag = os.getenv("DESK_AUTH", "").strip().lower()
    if flag in {"0", "false", "no", "n", "off"}:
        return False
    if flag in {"1", "true", "yes", "y", "on"}:
        return True
    return desk_password_configured()


def dangerous_requires_totp() -> bool:
    """Live/restart/DRY_RUN=false require TOTP when DESK_TOTP_SECRET is set."""
    _ensure_dotenv()
    flag = os.getenv("DESK_TOTP_REQUIRED", "true").strip().lower()
    if flag in {"0", "false", "no", "n", "off"}:
        return False
    return desk_totp_configured()
