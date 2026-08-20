"""HTML desk login, CSRF, TOTP, and bind lock."""

from __future__ import annotations

import json
import os
import threading
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen

import analytics.desk_auth as desk_auth
import desk_http_auth as http_auth
from desk_http_auth import (
    check_access,
    create_session,
    desk_http_start_error,
    path_requires_totp,
    public_bind_blocked,
    reset_sessions_for_tests,
    try_login,
)


def _isolate(tmp: Path, body: str) -> None:
    env = tmp / ".env"
    env.write_text(body, encoding="utf-8")
    desk_auth._RESOLVED_ENV_PATH = None
    desk_auth._ENV_LOADED = False
    os.environ["DESK_ENV_PATH"] = str(env)
    os.environ.pop("GP_ENV_PATH", None)
    os.environ.pop("DESK_PASSWORD", None)
    os.environ.pop("DESK_PASSWORD_HASH", None)
    os.environ.pop("DESK_AUTH", None)
    os.environ.pop("DESK_TOTP_SECRET", None)
    os.environ.pop("DESK_TOTP_REQUIRED", None)
    os.environ.pop("DESK_BIND_PUBLIC", None)
    reset_sessions_for_tests()


def _clear_isolate() -> None:
    os.environ.pop("DESK_ENV_PATH", None)
    os.environ.pop("DESK_PASSWORD", None)
    os.environ.pop("DESK_PASSWORD_HASH", None)
    os.environ.pop("DESK_AUTH", None)
    os.environ.pop("DESK_TOTP_SECRET", None)
    os.environ.pop("DESK_TOTP_REQUIRED", None)
    os.environ.pop("DESK_BIND_PUBLIC", None)
    desk_auth._RESOLVED_ENV_PATH = None
    desk_auth._ENV_LOADED = False
    reset_sessions_for_tests()


def test_public_bind_refused() -> None:
    os.environ.pop("DESK_BIND_PUBLIC", None)
    assert public_bind_blocked("127.0.0.1") is None
    assert public_bind_blocked("0.0.0.0")
    assert public_bind_blocked("::")
    os.environ["DESK_BIND_PUBLIC"] = "true"
    assert public_bind_blocked("0.0.0.0") is None
    os.environ.pop("DESK_BIND_PUBLIC", None)


def test_start_error_when_auth_forced_without_password(tmp_path: Path) -> None:
    _isolate(tmp_path, "DESK_AUTH=true\n")
    try:
        err = desk_http_start_error()
        assert err
        assert "DESK_PASSWORD" in err
    finally:
        _clear_isolate()


def test_auth_off_allows_api(tmp_path: Path) -> None:
    _isolate(tmp_path, "DESK_AUTH=false\n")
    try:
        acc = check_access(method="GET", path="/api/status", headers={})
        assert acc.allow
        acc = check_access(method="POST", path="/api/emergency", headers={}, data={"off": True})
        assert acc.allow
    finally:
        _clear_isolate()


def test_auth_on_blocks_then_login(tmp_path: Path) -> None:
    _isolate(tmp_path, "DESK_AUTH=true\nDESK_PASSWORD=gate-secret\n")
    try:
        acc = check_access(method="GET", path="/api/status", headers={})
        assert not acc.allow
        assert acc.status == 401
        html = check_access(method="GET", path="/", headers={})
        assert not html.allow
        assert html.kind == "redirect"
        login_page = check_access(method="GET", path="/login", headers={})
        assert login_page.kind == "login_page"
        bad = try_login(password="nope", ip="127.0.0.1")
        assert not bad.ok
        good = try_login(password="gate-secret", ip="127.0.0.1")
        assert good.ok and good.session
        headers = {
            "Cookie": f"{http_auth.COOKIE_NAME}={good.session.sid}",
            http_auth.CSRF_HEADER: good.session.csrf,
        }
        acc = check_access(method="GET", path="/api/status", headers=headers)
        assert acc.allow
        no_csrf = check_access(
            method="POST",
            path="/api/emergency",
            headers={"Cookie": f"{http_auth.COOKIE_NAME}={good.session.sid}"},
            data={"off": True},
        )
        assert not no_csrf.allow
        assert no_csrf.status == 403
        with_csrf = check_access(
            method="POST",
            path="/api/emergency",
            headers=headers,
            data={"off": True},
        )
        assert with_csrf.allow
    finally:
        _clear_isolate()


def test_totp_required_on_arm_live(tmp_path: Path) -> None:
    _isolate(
        tmp_path,
        "DESK_AUTH=true\nDESK_PASSWORD=gate-secret\nDESK_TOTP_SECRET=MFRGGZDFMY\nDESK_TOTP_REQUIRED=true\n",
    )
    try:
        assert path_requires_totp("/api/desk/arm", {"mode": "live", "confirm": "LIVE"})
        assert path_requires_totp("/api/bot/restart", {})
        assert not path_requires_totp("/api/desk/arm", {"mode": "paper"})
        assert not path_requires_totp("/api/emergency", {"off": True})
        sess = create_session()
        headers = {
            "Cookie": f"{http_auth.COOKIE_NAME}={sess.sid}",
            http_auth.CSRF_HEADER: sess.csrf,
        }
        blocked = check_access(
            method="POST",
            path="/api/desk/arm",
            headers=headers,
            data={"mode": "live", "confirm": "LIVE"},
        )
        assert not blocked.allow
        assert blocked.status == 403
        orig = http_auth.verify_totp
        http_auth.verify_totp = lambda code: str(code) == "123456"  # type: ignore[assignment]
        try:
            ok = check_access(
                method="POST",
                path="/api/desk/arm",
                headers=headers,
                data={"mode": "live", "confirm": "LIVE", "totp": "123456"},
            )
            assert ok.allow
        finally:
            http_auth.verify_totp = orig
    finally:
        _clear_isolate()


def test_open_redirect_rejected() -> None:
    assert http_auth.safe_next("//evil.example") == "/"
    assert http_auth.safe_next("/lite") == "/lite"
    assert http_auth.safe_next("/\\evil") == "/"


def test_http_login_cookie_roundtrip(tmp_path: Path) -> None:
    _isolate(tmp_path, "DESK_AUTH=true\nDESK_PASSWORD=gate-secret\n")
    from control_panel import ControlHandler
    from http.server import ThreadingHTTPServer

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), ControlHandler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    try:
        try:
            urlopen(Request(base + "/api/status"), timeout=5)
            raise AssertionError("status should 401")
        except HTTPError as err:
            assert err.code == 401
            body = json.loads(err.read().decode("utf-8"))
            assert "trace" not in body
            assert body.get("login") == "/login"

        redir = urlopen(Request(base + "/", method="GET"), timeout=5)
        # urllib follows 302 to /login
        page = redir.read().decode("utf-8", "replace")
        assert "Unlock desk" in page or "gp-desk-login" in page

        jar = CookieJar()
        opener = build_opener(HTTPCookieProcessor(jar))
        req = Request(
            base + "/api/desk/login",
            data=json.dumps({"password": "gate-secret"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with opener.open(req, timeout=5) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        assert payload["ok"] is True
        csrf = payload["csrf"]
        assert csrf
        with opener.open(Request(base + "/api/desk/session"), timeout=5) as resp:
            sess = json.loads(resp.read().decode("utf-8"))
        assert sess["ok"] is True
        assert sess["csrf"] == csrf

        try:
            opener.open(
                Request(
                    base + "/api/desk/logout",
                    data=b"{}",
                    headers={"Content-Type": "application/json"},
                    method="POST",
                ),
                timeout=5,
            )
            raise AssertionError("logout without CSRF should 403")
        except HTTPError as err:
            assert err.code == 403

        with opener.open(
            Request(
                base + "/api/desk/logout",
                data=b"{}",
                headers={
                    "Content-Type": "application/json",
                    http_auth.CSRF_HEADER: csrf,
                },
                method="POST",
            ),
            timeout=5,
        ) as resp:
            assert json.loads(resp.read().decode("utf-8")).get("ok") is True
    finally:
        httpd.shutdown()
        httpd.server_close()
        _clear_isolate()


if __name__ == "__main__":
    from tempfile import TemporaryDirectory

    test_public_bind_refused()
    print("ok bind")
    with TemporaryDirectory() as td:
        test_start_error_when_auth_forced_without_password(Path(td))
        print("ok start error")
    with TemporaryDirectory() as td:
        test_auth_off_allows_api(Path(td))
        print("ok auth off")
    with TemporaryDirectory() as td:
        test_auth_on_blocks_then_login(Path(td))
        print("ok login csrf")
    with TemporaryDirectory() as td:
        test_totp_required_on_arm_live(Path(td))
        print("ok totp")
    test_open_redirect_rejected()
    print("ok next")
    with TemporaryDirectory() as td:
        test_http_login_cookie_roundtrip(Path(td))
        print("ok http cookie")
    print("ALL test_desk_http_auth OK")
