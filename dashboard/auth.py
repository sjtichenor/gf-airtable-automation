"""Shared-password gate for the dashboard.

One password (DASHBOARD_PASSWORD) for the team. A correct login sets a
signed, HttpOnly cookie for 30 days; the signature key is DASHBOARD_SECRET
or, failing that, a hash of the password, so changing either logs everyone
out. No user accounts yet — Google sign-in restricted to the domain is the
next step if the audience grows beyond the team.
"""
import hashlib
import hmac
import os
import threading
import time
from typing import Dict

from fastapi import Request

PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "")
COOKIE = "gf_dash"
MAX_AGE = 30 * 24 * 3600
ON_RENDER = bool(os.environ.get("RENDER"))
FAKE_DATA = os.environ.get("DASHBOARD_FAKE_DATA") == "1"

_failures: Dict[str, list] = {}
_lock = threading.Lock()


def configured() -> bool:
    return bool(PASSWORD)


def _secret() -> str:
    return os.environ.get("DASHBOARD_SECRET") or hashlib.sha256(("gf-dash:" + PASSWORD).encode()).hexdigest()


def session_token() -> str:
    return hmac.new(_secret().encode(), b"gf-dashboard-session-v1", hashlib.sha256).hexdigest()


def is_authed(request: Request) -> bool:
    if FAKE_DATA:
        return True  # local layout work on synthetic numbers; nothing to protect
    if not PASSWORD:
        return False
    return hmac.compare_digest(request.cookies.get(COOKIE, ""), session_token())


def check_password(candidate: str) -> bool:
    return bool(PASSWORD) and hmac.compare_digest(candidate.encode(), PASSWORD.encode())


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return (fwd.split(",")[0].strip() if fwd else None) or (request.client.host if request.client else "?")


def throttled(ip: str) -> bool:
    """More than 10 wrong passwords in 10 minutes from one address → wait."""
    now = time.time()
    with _lock:
        recent = [t for t in _failures.get(ip, []) if now - t < 600]
        _failures[ip] = recent
        return len(recent) >= 10


def record_failure(ip: str) -> None:
    with _lock:
        _failures.setdefault(ip, []).append(time.time())


def set_cookie(response) -> None:
    response.set_cookie(COOKIE, session_token(), max_age=MAX_AGE, httponly=True,
                        secure=ON_RENDER, samesite="lax", path="/dashboard")


def clear_cookie(response) -> None:
    response.delete_cookie(COOKIE, path="/dashboard")
