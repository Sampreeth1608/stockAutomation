#!/usr/bin/env python3
"""Local page: add rules → combinations + decision tree.

Research only. Nothing papers. Nothing goes live.

  python3 decision_tree_app.py --host 127.0.0.1 --port 8791
  open http://127.0.0.1:8791/
"""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from decision_tree import LAB_NAME, build, catalog_families, text_report

ROOT = Path(__file__).resolve().parent
HTML_PATH = ROOT / "decision_tree.html"
SAVE_DIR = ROOT / "data" / "decision_tree"
SAVE_PATH = SAVE_DIR / "last.json"
MAX_BODY = 1_000_000


def _json_bytes(payload: Any, status: int = 200) -> tuple[int, bytes, str]:
    body = json.dumps(payload, indent=2).encode("utf-8")
    return status, body, "application/json; charset=utf-8"


def handle_request(method: str, path: str, raw_body: bytes) -> tuple[int, bytes, str]:
    parsed = urlparse(path)
    route = parsed.path.rstrip("/") or "/"
    if method == "GET" and route in ("/", "/index.html"):
        html = HTML_PATH.read_text(encoding="utf-8")
        return 200, html.encode("utf-8"), "text/html; charset=utf-8"
    if method == "GET" and route == "/api/catalog":
        return _json_bytes({"ok": True, "lab": LAB_NAME, "families": catalog_families()})
    if method == "GET" and route == "/api/load":
        if not SAVE_PATH.is_file():
            return _json_bytes({"ok": True, "saved": False, "payload": None})
        try:
            payload = json.loads(SAVE_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return _json_bytes({"ok": False, "error": "saved file is not valid JSON"}, 400)
        return _json_bytes({"ok": True, "saved": True, "payload": payload})
    if method == "POST" and route == "/api/build":
        try:
            data = json.loads(raw_body.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            return _json_bytes({"ok": False, "errors": ["body must be JSON"]}, 400)
        if not isinstance(data, dict):
            return _json_bytes({"ok": False, "errors": ["body must be an object"]}, 400)
        payload = build(
            data.get("rules"),
            max_and=int(data.get("max_and") or 3),
            allow_mixed=bool(data.get("allow_mixed")),
            include_tree=data.get("include_tree", True) is not False,
        )
        payload["report"] = text_report(payload)
        status = 200 if payload.get("ok") else 400
        return _json_bytes(payload, status)
    if method == "POST" and route == "/api/save":
        try:
            data = json.loads(raw_body.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            return _json_bytes({"ok": False, "error": "body must be JSON"}, 400)
        if not isinstance(data, dict):
            return _json_bytes({"ok": False, "error": "body must be an object"}, 400)
        SAVE_DIR.mkdir(parents=True, exist_ok=True)
        SAVE_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return _json_bytes({"ok": True, "path": str(SAVE_PATH)})
    return _json_bytes({"ok": False, "error": "not found"}, 404)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"{self.address_string()} {fmt % args}")

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        if length < 0 or length > MAX_BODY:
            return b""
        return self.rfile.read(length)

    def do_GET(self) -> None:  # noqa: N802
        status, body, ctype = handle_request("GET", self.path, b"")
        self._send(status, body, ctype)

    def do_POST(self) -> None:  # noqa: N802
        status, body, ctype = handle_request("POST", self.path, self._read_body())
        self._send(status, body, ctype)


def make_server(host: str, port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8791)
    args = ap.parse_args()
    if not HTML_PATH.is_file():
        raise SystemExit(f"missing {HTML_PATH}")
    server = make_server(args.host, int(args.port))
    bound = server.server_address
    print(LAB_NAME, f"http://{bound[0]}:{bound[1]}/")
    print("Research only. Add rules, generate combinations, inspect the tree.")
    print("Does not paper. Does not live. Does not ENABLE anything.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
