"""Angel One SmartAPI login helpers."""

from __future__ import annotations

import os
from dataclasses import dataclass

import pyotp
from dotenv import load_dotenv
from SmartApi import SmartConnect


@dataclass
class AngelSession:
    api: SmartConnect
    client_id: str
    api_key: str
    auth_token: str
    feed_token: str
    refresh_token: str


def load_env() -> None:
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))


def require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing {name} in .env")
    return value


def login() -> AngelSession:
    load_env()
    client_id = require_env("ANGEL_CLIENT_ID")
    password = require_env("ANGEL_PASSWORD")
    api_key = require_env("ANGEL_API_KEY")
    totp_secret = require_env("ANGEL_TOTP_SECRET")

    api = SmartConnect(api_key=api_key)
    totp = pyotp.TOTP(totp_secret).now()
    data = api.generateSession(client_id, password, totp)

    if not data or data.get("status") is False:
        raise RuntimeError(f"Angel login failed: {data}")

    payload = data["data"]
    auth_token = payload["jwtToken"]
    refresh_token = payload["refreshToken"]
    feed_token = api.getfeedToken()

    if not feed_token:
        raise RuntimeError("Feed token missing after login")

    return AngelSession(
        api=api,
        client_id=client_id,
        api_key=api_key,
        auth_token=auth_token,
        feed_token=feed_token,
        refresh_token=refresh_token,
    )


if __name__ == "__main__":
    session = login()
    profile = session.api.getProfile(session.refresh_token)
    print("LOGIN OK")
    print("Client:", session.client_id)
    print("Exchanges:", profile.get("data", {}).get("exchanges"))
