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
_RESOLVED_ENV_PATH: Path | None = None

_DESK_KEYS = (
    "DESK_AUTH",
    "DESK_PASSWORD",
    "DESK_PASSWORD_HASH",
    "DESK_TOTP_SECRET",
    "DESK_TOTP_REQUIRED",
)


def _candidate_env_paths() -> list[Path]:
    """Possible .env locations (desk may run from a repo checkout ≠ ~/goldpetal)."""
    out: list[Path] = []
    seen: set[Path] = set()
    for raw in (
        os.getenv("GP_ENV_PATH", "").strip(),
        os.getenv("DESK_ENV_PATH", "").strip(),
    ):
        if not raw:
            continue
        p = Path(raw).expanduser().resolve()
        if p not in seen:
            seen.add(p)
            out.append(p)
    for p in (
        (ROOT / ".env").resolve(),
        (Path.home() / "goldpetal" / ".env").resolve(),
    ):
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def env_path() -> Path:
    """Path used for desk secrets (first existing candidate, else ROOT/.env)."""
    global _RESOLVED_ENV_PATH
    if _RESOLVED_ENV_PATH is not None:
        return _RESOLVED_ENV_PATH
    for p in _candidate_env_paths():
        if p.is_file():
            _RESOLVED_ENV_PATH = p
            return p
    _RESOLVED_ENV_PATH = (ROOT / ".env").resolve()
    return _RESOLVED_ENV_PATH


def _parse_env_file(path: Path) -> dict[str, str]:
    """Parse .env like python-dotenv (comments, quotes, last-wins)."""
    if not path.is_file():
        return {}
    try:
        from dotenv import dotenv_values

        raw = dotenv_values(path)
        return {k: (v or "").strip() for k, v in raw.items() if k}
    except Exception:
        out: dict[str, str] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            raw = line.strip()
            if not raw or raw.startswith("#") or "=" not in raw:
                continue
            if raw.lower().startswith("export "):
                raw = raw[7:].strip()
            key, val = raw.split("=", 1)
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            # strip unquoted inline comments
            if " #" in val and not (val.startswith('"') or val.startswith("'")):
                val = val.split(" #", 1)[0].rstrip()
            if key:
                out[key] = val
        return out


def _ensure_dotenv() -> None:
    """Load desk .env; DESK_* always refreshed from the resolved file."""
    global _ENV_LOADED
    path = env_path()
    parsed = _parse_env_file(path)
    try:
        from dotenv import load_dotenv

        load_dotenv(path, override=False)
    except Exception:
        for key, val in parsed.items():
            if key and key not in os.environ:
                os.environ[key] = val
    # Desk secrets / flags: file wins over stale shell exports.
    for key in _DESK_KEYS:
        if key in parsed:
            os.environ[key] = parsed[key]
    _ENV_LOADED = True


def _now() -> str:
    return datetime.now(IST).isoformat(timespec="seconds")


def _desk_secret_from_file(key: str) -> str:
    """Read a desk secret; search all candidate .env paths (non-empty wins)."""
    for path in _candidate_env_paths():
        if not path.is_file():
            continue
        val = _parse_env_file(path).get(key, "").strip()
        if val:
            global _RESOLVED_ENV_PATH
            _RESOLVED_ENV_PATH = path
            return val
    return ""


def desk_password_configured() -> bool:
    _ensure_dotenv()
    plain = _desk_secret_from_file("DESK_PASSWORD") or os.getenv("DESK_PASSWORD", "").strip()
    hashed = _desk_secret_from_file("DESK_PASSWORD_HASH") or os.getenv("DESK_PASSWORD_HASH", "").strip()
    return bool(plain or hashed)


def desk_password_hint() -> dict[str, Any]:
    """Safe debug hint for the login screen (never includes the secret)."""
    _ensure_dotenv()
    path = env_path()
    plain = _desk_secret_from_file("DESK_PASSWORD")
    hashed = _desk_secret_from_file("DESK_PASSWORD_HASH")
    return {
        "env_path": str(path),
        "env_exists": path.is_file(),
        "password_len": len(plain) if plain else 0,
        "has_hash": bool(hashed),
        "mode": "plain" if plain else ("hash" if hashed else "none"),
    }


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


def _plain_match(got: str, expect: str) -> bool:
    """Length-safe constant-time compare (hmac.compare_digest raises on len mismatch)."""
    a = got.encode("utf-8")
    b = expect.encode("utf-8")
    if len(a) != len(b):
        return False
    return hmac.compare_digest(a, b)


def verify_password(password: str) -> bool:
    """Accept DESK_PASSWORD (plain) or DESK_PASSWORD_HASH=salt:hex."""
    _ensure_dotenv()
    got = (password or "").strip()
    # Prefer .env file so stale exported shell vars cannot break login.
    plain = _desk_secret_from_file("DESK_PASSWORD") or os.getenv("DESK_PASSWORD", "").strip()
    if plain:
        return _plain_match(got, plain)
    hashed = _desk_secret_from_file("DESK_PASSWORD_HASH") or os.getenv(
        "DESK_PASSWORD_HASH", ""
    ).strip()
    if not hashed or ":" not in hashed:
        return False
    salt, expect = hashed.split(":", 1)
    digest = _password_hash(got, salt)
    return _plain_match(digest, expect)


def verify_totp(code: str) -> bool:
    _ensure_dotenv()
    secret = (
        _desk_secret_from_file("DESK_TOTP_SECRET") or os.getenv("DESK_TOTP_SECRET", "")
    ).strip().replace(" ", "")
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
    """Login only when a password is actually configured.

    DESK_AUTH=true with no DESK_PASSWORD must not block the desk. Bind-on-localhost
    and TOTP for Arm live stay on their own flags.
    """
    _ensure_dotenv()
    flag = (
        _desk_secret_from_file("DESK_AUTH") or os.getenv("DESK_AUTH", "")
    ).strip().lower()
    if flag in {"0", "false", "no", "n", "off"}:
        return False
    return desk_password_configured()


def dangerous_requires_totp() -> bool:
    """Live/restart/DRY_RUN=false require TOTP when DESK_TOTP_SECRET is set."""
    _ensure_dotenv()
    flag = (
        _desk_secret_from_file("DESK_TOTP_REQUIRED")
        or os.getenv("DESK_TOTP_REQUIRED", "true")
    ).strip().lower()
    if flag in {"0", "false", "no", "n", "off"}:
        return False
    return desk_totp_configured()
