"""HTTP login, session cookie, CSRF, and bind lock for the HTML desk on 8501.

Streamlit already uses analytics.desk_auth. The station on 8501 did not.
This module is that gate: cookie after password, CSRF on POSTs, optional TOTP
on Arm live / DRY_RUN=false / restart, and a refuse of --host 0.0.0.0.
"""

from __future__ import annotations

import os
import secrets
import threading
import time
from dataclasses import dataclass
from http.cookies import SimpleCookie
from typing import Any, Mapping

from analytics.desk_auth import (
    audit,
    auth_required,
    dangerous_requires_totp,
    desk_password_configured,
    desk_password_hint,
    verify_password,
    verify_totp,
)

COOKIE_NAME = "gp_desk"
CSRF_HEADER = "X-GP-CSRF"
TOTP_HEADER = "X-GP-TOTP"
MAX_BODY_BYTES = 262_144
SESSION_IDLE_SEC = 12 * 3600
LOGIN_FAIL_MAX = 8
LOGIN_FAIL_WINDOW_SEC = 300

LOGIN_PATHS = frozenset({"/login", "/login.html"})
PUBLIC_GET = frozenset(
    {
        "/login",
        "/login.html",
        "/api/desk/auth",
        "/manifest.webmanifest",
        "/manifest.json",
    }
)
PUBLIC_POST = frozenset({"/api/desk/login"})
HTML_PATHS = frozenset(
    {
        "/",
        "/index.html",
        "/station",
        "/station.html",
        "/lite",
        "/lite.html",
        "/controls",
        "/full",
        "/full.html",
        "/s14-sheet",
        "/s14-sheet.html",
    }
)
PUBLIC_BIND_HOSTS = frozenset({"0.0.0.0", "::", "[::]", "*", "::0"})

_LOCK = threading.Lock()
_SESSIONS: dict[str, "Session"] = {}
_FAILS: dict[str, list[float]] = {}


@dataclass
class Session:
    sid: str
    csrf: str
    created: float
    last_seen: float


@dataclass
class Access:
    allow: bool
    status: int = 200
    error: str = ""
    kind: str = "json"  # json | login_page | redirect
    location: str = ""
    session: Session | None = None


@dataclass
class LoginResult:
    ok: bool
    status: int = 200
    error: str = ""
    session: Session | None = None


def security_headers() -> dict[str, str]:
    return {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
        "Cache-Control": "no-store, no-cache, must-revalidate",
        "Pragma": "no-cache",
        "Content-Security-Policy": (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self'"
        ),
    }


def public_bind_blocked(host: str) -> str | None:
    h = (host or "").strip().lower()
    if h not in PUBLIC_BIND_HOSTS:
        return None
    flag = os.getenv("DESK_BIND_PUBLIC", "").strip().lower()
    if flag in {"1", "true", "yes", "y", "on"}:
        return None
    return (
        "Refusing to bind the desk on a public address "
        f"({host}). Use --host 127.0.0.1 and the Mac IAP tunnel. "
        "DESK_BIND_PUBLIC=true overrides (do not)."
    )


def desk_http_start_error() -> str | None:
    """Never refuse boot for a missing password. Public bind is a separate check."""
    return None


def desk_http_start_warning() -> str | None:
    if auth_required():
        return None
    return (
        "HTML desk login is off (no DESK_PASSWORD). "
        "IAP + bind 127.0.0.1 stay. Arm live still needs LIVE"
        + (" and TOTP" if dangerous_requires_totp() else "")
        + "."
    )


def cookie_header(sid: str, *, clear: bool = False) -> str:
    if clear or not sid:
        return f"{COOKIE_NAME}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"
    return (
        f"{COOKIE_NAME}={sid}; Path=/; HttpOnly; SameSite=Strict; "
        f"Max-Age={int(SESSION_IDLE_SEC)}"
    )


def session_from_headers(headers: Mapping[str, str] | None) -> Session | None:
    if not headers:
        return None
    raw = ""
    for key, val in headers.items():
        if str(key).lower() == "cookie":
            raw = str(val or "")
            break
    if not raw:
        return None
    jar = SimpleCookie()
    try:
        jar.load(raw)
    except Exception:
        return None
    morsel = jar.get(COOKIE_NAME)
    if morsel is None:
        return None
    sid = str(morsel.value or "").strip()
    if not sid:
        return None
    now = time.time()
    with _LOCK:
        sess = _SESSIONS.get(sid)
        if sess is None:
            return None
        if now - sess.last_seen > SESSION_IDLE_SEC:
            _SESSIONS.pop(sid, None)
            return None
        sess.last_seen = now
        return sess


def create_session() -> Session:
    now = time.time()
    sess = Session(
        sid=secrets.token_urlsafe(32),
        csrf=secrets.token_urlsafe(32),
        created=now,
        last_seen=now,
    )
    with _LOCK:
        _SESSIONS[sess.sid] = sess
        _prune_sessions_locked(now)
    return sess


def drop_session(sid: str) -> None:
    with _LOCK:
        _SESSIONS.pop(sid, None)


def reset_sessions_for_tests() -> None:
    with _LOCK:
        _SESSIONS.clear()
        _FAILS.clear()


def _prune_sessions_locked(now: float) -> None:
    dead = [sid for sid, sess in _SESSIONS.items() if now - sess.last_seen > SESSION_IDLE_SEC]
    for sid in dead:
        _SESSIONS.pop(sid, None)


def header_value(headers: Mapping[str, str] | None, name: str) -> str:
    if not headers:
        return ""
    want = name.lower()
    for key, val in headers.items():
        if str(key).lower() == want:
            return str(val or "").strip()
    return ""


def csrf_ok(session: Session, headers: Mapping[str, str] | None, data: dict[str, Any] | None) -> bool:
    got = header_value(headers, CSRF_HEADER)
    if not got and isinstance(data, dict):
        got = str(data.get("csrf") or data.get("csrf_token") or "").strip()
    if not got or not session.csrf:
        return False
    if len(got) != len(session.csrf):
        return False
    return secrets.compare_digest(got, session.csrf)


def totp_code(headers: Mapping[str, str] | None, data: dict[str, Any] | None) -> str:
    got = header_value(headers, TOTP_HEADER)
    if got:
        return got
    if isinstance(data, dict):
        return str(data.get("totp") or data.get("otp") or "").strip()
    return ""


def path_requires_totp(path: str, data: dict[str, Any] | None) -> bool:
    if not dangerous_requires_totp():
        return False
    p = path or ""
    body = data or {}
    if p in {"/api/bot/restart", "/api/bot/start"}:
        return True
    if p == "/api/desk/arm":
        mode = str(body.get("mode") or "").strip().lower()
        confirm = str(body.get("confirm") or "").strip()
        return mode == "live" or confirm == "LIVE"
    if p == "/api/live":
        return bool(body.get("unlocked"))
    if p == "/api/live/env":
        dry_raw = body.get("dry_run")
        if dry_raw is False:
            return True
        return str(dry_raw).strip().lower() in {"false", "0", "no", "n", "off"}
    if p == "/api/capture":
        return bool(body.get("place") or body.get("send") or body.get("live"))
    return False


def safe_next(raw: str) -> str:
    n = (raw or "/").strip() or "/"
    if not n.startswith("/") or n.startswith("//") or "\\" in n:
        return "/"
    return n


def _login_blocked(ip: str, now: float) -> bool:
    rec = _FAILS.get(ip) or []
    rec = [t for t in rec if now - t < LOGIN_FAIL_WINDOW_SEC]
    _FAILS[ip] = rec
    return len(rec) >= LOGIN_FAIL_MAX


def _login_fail(ip: str, now: float) -> None:
    rec = _FAILS.get(ip) or []
    rec = [t for t in rec if now - t < LOGIN_FAIL_WINDOW_SEC]
    rec.append(now)
    _FAILS[ip] = rec


def _login_clear(ip: str) -> None:
    _FAILS.pop(ip, None)


def try_login(*, password: str, ip: str) -> LoginResult:
    if not auth_required():
        return LoginResult(ok=True, session=None)
    now = time.time()
    key = (ip or "unknown").strip() or "unknown"
    with _LOCK:
        if _login_blocked(key, now):
            audit("desk_http_login", ok=False, detail={"reason": "rate", "ip": key})
            return LoginResult(ok=False, status=429, error="too many login attempts — wait a few minutes")
    got = (password or "").strip()
    if not got:
        with _LOCK:
            _login_fail(key, now)
        audit("desk_http_login", ok=False, detail={"reason": "empty", "ip": key})
        return LoginResult(ok=False, status=401, error="password required")
    if not desk_password_configured():
        audit("desk_http_login", ok=False, detail={"reason": "not_configured", "ip": key})
        return LoginResult(ok=False, status=503, error="DESK_PASSWORD is not set")
    if not verify_password(got):
        with _LOCK:
            _login_fail(key, now)
        hint = desk_password_hint()
        audit(
            "desk_http_login",
            ok=False,
            detail={"reason": "mismatch", "typed_len": len(got), "expect_len": hint.get("password_len"), "ip": key},
        )
        return LoginResult(ok=False, status=401, error="wrong password")
    with _LOCK:
        _login_clear(key)
    sess = create_session()
    audit("desk_http_login", ok=True, detail={"ip": key})
    return LoginResult(ok=True, session=sess)


def auth_status_payload() -> dict[str, Any]:
    hint = desk_password_hint()
    return {
        "ok": True,
        "auth_required": auth_required(),
        "password_configured": desk_password_configured(),
        "totp_required": dangerous_requires_totp(),
        "env_path": hint.get("env_path"),
        "env_exists": hint.get("env_exists"),
        "password_len": hint.get("password_len"),
        "mode": hint.get("mode"),
    }


def session_payload(session: Session | None) -> dict[str, Any]:
    required = auth_required()
    return {
        "ok": True,
        "auth_required": required,
        "csrf": (session.csrf if session else ""),
        "totp_required": dangerous_requires_totp() if required else False,
        "lock": bool(required and session),
    }


def check_access(
    *,
    method: str,
    path: str,
    headers: Mapping[str, str] | None,
    data: dict[str, Any] | None = None,
) -> Access:
    method_u = (method or "GET").upper()
    p = path or "/"
    if not auth_required():
        if method_u in {"POST", "PUT", "PATCH", "DELETE"} and path_requires_totp(p, data):
            code = totp_code(headers, data)
            if not code or not verify_totp(code):
                audit("desk_http_totp", ok=False, detail={"path": p, "login": "off"})
                return Access(
                    allow=False,
                    status=403,
                    error="authenticator OTP required for this action",
                    kind="json",
                )
        return Access(allow=True, kind="")
    if method_u == "GET" and p in LOGIN_PATHS:
        sess = session_from_headers(headers)
        if sess is not None:
            return Access(allow=False, status=302, kind="redirect", location="/", session=sess)
        return Access(allow=False, status=200, kind="login_page")
    if method_u == "GET" and p in PUBLIC_GET:
        return Access(allow=True, kind="")
    if method_u == "POST" and p in PUBLIC_POST:
        return Access(allow=True, kind="")
    sess = session_from_headers(headers)
    if sess is None:
        if method_u == "GET" and p in HTML_PATHS:
            nxt = safe_next(p)
            loc = "/login" if nxt in {"/", ""} else f"/login?next={nxt}"
            return Access(allow=False, status=302, kind="redirect", location=loc)
        return Access(allow=False, status=401, error="login required", kind="json")
    if method_u in {"POST", "PUT", "PATCH", "DELETE"} and p not in PUBLIC_POST:
        if not csrf_ok(sess, headers, data):
            return Access(
                allow=False,
                status=403,
                error="CSRF required — reload the desk after login",
                kind="json",
                session=sess,
            )
        if path_requires_totp(p, data):
            code = totp_code(headers, data)
            if not code or not verify_totp(code):
                audit("desk_http_totp", ok=False, detail={"path": p})
                return Access(
                    allow=False,
                    status=403,
                    error="authenticator OTP required for this action",
                    kind="json",
                    session=sess,
                )
    return Access(allow=True, kind="", session=sess)
