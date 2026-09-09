"""Minimal signed-cookie helpers. This is a trusted-LAN kiosk tool, not a
public-facing app, so we keep this to "tamper-evident", not full auth."""

import hashlib
import hmac
import os
import time

SECRET_KEY = os.getenv("SECRET_KEY", "espressolab-dev-secret-change-me").encode()


def sign(value: str) -> str:
    mac = hmac.new(SECRET_KEY, value.encode(), hashlib.sha256).hexdigest()
    return f"{value}.{mac}"


def unsign(signed_value: str | None) -> str | None:
    if not signed_value or "." not in signed_value:
        return None
    value, _, mac = signed_value.rpartition(".")
    expected = hmac.new(SECRET_KEY, value.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(mac, expected):
        return None
    return value


def make_user_cookie(user_id: str) -> str:
    return sign(f"{user_id}:{int(time.time())}")


def read_user_cookie(cookie_value: str | None, idle_minutes: int) -> str | None:
    raw = unsign(cookie_value)
    if not raw or ":" not in raw:
        return None
    user_id, _, selected_at = raw.rpartition(":")
    try:
        selected_at = int(selected_at)
    except ValueError:
        return None
    if time.time() - selected_at > idle_minutes * 60:
        return None
    return user_id
