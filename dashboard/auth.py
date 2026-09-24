"""Who may see the dashboard, and who they are.

Two kinds of session:

* **Team** -- Google sign-in (GOOGLE_OAUTH_CLIENT_ID / _SECRET), allowed for
  any address on DASHBOARD_ALLOWED_DOMAIN or any email that matches an
  Active row in the Team table. The cookie carries the email and name,
  signed, for 30 days, at path "/" so a team member is also let into every
  client page. The shared DASHBOARD_PASSWORD still works as a fallback and
  yields an anonymous team session.
* **Client** -- one password per slug, no username, own cookie per client,
  unchanged.

The signing key is DASHBOARD_SECRET or, failing that, a hash of the
passwords, so changing either logs everyone out.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from typing import Dict, Optional
from urllib.parse import urlencode

import requests
from fastapi import Request

PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "")
COOKIE = "gf_dash"
STATE_COOKIE = "gf_oauth"
MAX_AGE = 30 * 24 * 3600
ON_RENDER = bool(os.environ.get("RENDER"))
FAKE_DATA = os.environ.get("DASHBOARD_FAKE_DATA") == "1"

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "")
ALLOWED_DOMAIN = os.environ.get("DASHBOARD_ALLOWED_DOMAIN", "goodfuturemedia.com").lower()
BASE_URL = os.environ.get("DASHBOARD_BASE_URL", "https://api.goodfuturemedia.com").rstrip("/")
# Who may act as someone else on the mining board. Everyone else is themselves.
ADMINS = {e.strip().lower() for e in os.environ.get("DASHBOARD_ADMINS", "spencer@goodfuturemedia.com").split(",") if e.strip()}
# Who may see the client pages listed in CLIENT_EXEC_ONLY. Other team
# members do not see those pages exist; the client's own password still
# opens them. The role comes from the Team table's Role field ("Exec"), so
# it is managed in Airtable; DASHBOARD_EXECS (emails) is a fallback for an
# address with no Team row. Separate from ADMINS: one is about visibility,
# the other about acting for someone else on the mining board.
EXECS = {e.strip().lower() for e in os.environ.get("DASHBOARD_EXECS", "").split(",") if e.strip()}
EXEC_ROLE = os.environ.get("DASHBOARD_EXEC_ROLE", "Exec")

_failures: Dict[str, list] = {}
_lock = threading.Lock()


def configured() -> bool:
    return bool(PASSWORD)


def _secret() -> str:
    return os.environ.get("DASHBOARD_SECRET") or hashlib.sha256(("gf-dash:" + PASSWORD + ":" + os.environ.get("CLIENT_PASSWORDS", "")).encode()).hexdigest()


def session_token() -> str:
    return hmac.new(_secret().encode(), b"gf-dashboard-session-v1", hashlib.sha256).hexdigest()


# ── signed sessions ──────────────────────────────────────────────────────
# value = base64url(json) "." hmac. The json carries who this is; nothing in
# it is secret, the signature is what makes it trustworthy.

def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _sign(body: str) -> str:
    return hmac.new(_secret().encode(), body.encode(), hashlib.sha256).hexdigest()


def make_session(email: Optional[str], name: str, sub: str, max_age: int = MAX_AGE) -> str:
    body = _b64(json.dumps({"v": 2, "sub": sub, "email": (email or "").lower() or None, "name": name,
                            "exp": int(time.time()) + max_age}, separators=(",", ":")).encode())
    return body + "." + _sign(body)


def read_session(value: str) -> Optional[dict]:
    if not value:
        return None
    if "." not in value:
        # The pre-identity cookie: a bare token proving the shared password.
        if PASSWORD and hmac.compare_digest(value, session_token()):
            return {"v": 1, "sub": "shared", "email": None, "name": "Team"}
        return None
    body, _, sig = value.partition(".")
    if not hmac.compare_digest(sig, _sign(body)):
        return None
    try:
        data = json.loads(_unb64(body))
    except (ValueError, TypeError):
        return None
    if data.get("exp", 0) < time.time():
        return None
    return data


FAKE_SESSION = {"v": 2, "sub": "fake", "email": "fake@goodfuturemedia.com", "name": "Fake Person"}


def is_authed(request: Request) -> Optional[dict]:
    """The team session, or None. Truthy when signed in, as callers expect."""
    if FAKE_DATA:
        return dict(FAKE_SESSION)  # local layout work on synthetic numbers; nothing to protect
    return read_session(request.cookies.get(COOKIE, ""))


def is_admin(session: Optional[dict]) -> bool:
    if FAKE_DATA:
        return True
    return bool(session and (session.get("email") or "").lower() in ADMINS)


def is_exec(session: Optional[dict], team_rows=None) -> bool:
    if FAKE_DATA:
        return os.environ.get("FAKE_EXEC", "1") == "1"
    if not session:
        return False
    if (session.get("email") or "").lower() in EXECS:
        return True
    row = team_row_for(session, team_rows) if team_rows else None
    return bool(row and EXEC_ROLE in (row.get("roles") or []))


def exec_only_clients() -> set:
    """Client slugs only execs may open with a team session.
    CLIENT_EXEC_ONLY = "flock;other" (slugs, ; or , separated)."""
    raw = os.environ.get("CLIENT_EXEC_ONLY", "")
    return {x.strip().lower() for x in raw.replace(",", ";").split(";") if x.strip()}


def team_may_open(session: Optional[dict], slug: str, team_rows=None) -> bool:
    """A team session opens every client page except the exec-only ones,
    which need the exec role. No session at all -> False; the caller then
    falls back to the client's own password."""
    if not session:
        return False
    return slug not in exec_only_clients() or is_exec(session, team_rows)


# ── Google sign-in ───────────────────────────────────────────────────────

def google_configured() -> bool:
    return bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)


def redirect_uri() -> str:
    return BASE_URL + "/dashboard/auth/google/callback"


def start_google(next_path: str):
    """(url to send the browser to, state cookie value). The state is signed
    and carries where to land afterwards, so a forged callback cannot log
    someone in or bounce them somewhere odd."""
    if not (next_path or "").startswith("/") or next_path.startswith("//"):
        next_path = "/dashboard"
    state_body = _b64(json.dumps({"n": secrets.token_urlsafe(16), "next": next_path,
                                  "exp": int(time.time()) + 600}, separators=(",", ":")).encode())
    state = state_body + "." + _sign(state_body)
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
        "client_id": GOOGLE_CLIENT_ID, "redirect_uri": redirect_uri(), "response_type": "code",
        "scope": "openid email profile", "state": state, "prompt": "select_account",
        "hd": ALLOWED_DOMAIN,  # a hint to Google's account picker; the real check is allowed_email
    })
    return url, state


def read_state(cookie_value: str, returned_state: str) -> Optional[dict]:
    if not cookie_value or not returned_state or not hmac.compare_digest(cookie_value, returned_state):
        return None
    body, _, sig = cookie_value.partition(".")
    if not hmac.compare_digest(sig, _sign(body)):
        return None
    try:
        data = json.loads(_unb64(body))
    except (ValueError, TypeError):
        return None
    return data if data.get("exp", 0) >= time.time() else None


def google_userinfo(code: str) -> dict:
    """Exchange the code, then ask Google who it was. Raises on any failure;
    the caller turns that into a login-page message."""
    r = requests.post("https://oauth2.googleapis.com/token", data={
        "code": code, "client_id": GOOGLE_CLIENT_ID, "client_secret": GOOGLE_CLIENT_SECRET,
        "redirect_uri": redirect_uri(), "grant_type": "authorization_code"}, timeout=20)
    r.raise_for_status()
    token = r.json().get("access_token")
    if not token:
        raise RuntimeError("no access token in Google's reply")
    u = requests.get("https://openidconnect.googleapis.com/v1/userinfo",
                     headers={"Authorization": f"Bearer {token}"}, timeout=20)
    u.raise_for_status()
    info = u.json()
    if not info.get("email") or not info.get("email_verified", False):
        raise RuntimeError("Google did not vouch for that email address")
    return info


def allowed_email(email: str, team_rows) -> Optional[dict]:
    """The Team row for this email (a dict with at least id and name), or a
    stand-in for an address on the allowed domain that has no row yet, or
    None when the address may not come in at all."""
    email = (email or "").lower()
    for t in team_rows or []:
        if t.get("active") and (t.get("email") or "").lower() == email:
            return t
    if ALLOWED_DOMAIN and email.endswith("@" + ALLOWED_DOMAIN):
        return {"id": None, "name": email.split("@")[0].replace(".", " ").title(), "email": email}
    return None


def team_row_for(session: Optional[dict], team_rows) -> Optional[dict]:
    """Resolved at request time, not at login, so a Team row added after
    someone first signed in is picked up without a new login."""
    if not session:
        return None
    if FAKE_DATA:
        active = [t for t in team_rows or [] if t.get("active")]
        return active[0] if active else None
    email = (session.get("email") or "").lower()
    if not email:
        return None
    return next((t for t in team_rows or [] if t.get("active") and (t.get("email") or "").lower() == email), None)


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


def set_cookie(response, session_value: Optional[str] = None) -> None:
    """Path "/" so the same cookie also opens the client pages. No value
    given = the shared-password fallback, an anonymous team session."""
    value = session_value or make_session(None, "Team", "shared")
    response.set_cookie(COOKIE, value, max_age=MAX_AGE, httponly=True,
                        secure=ON_RENDER, samesite="lax", path="/")


def set_state_cookie(response, state: str) -> None:
    response.set_cookie(STATE_COOKIE, state, max_age=600, httponly=True,
                        secure=ON_RENDER, samesite="lax", path="/dashboard/auth")


def clear_cookie(response) -> None:
    response.delete_cookie(COOKIE, path="/")
    response.delete_cookie(COOKIE, path="/dashboard")  # the pre-identity cookie lived here
    response.delete_cookie(STATE_COOKIE, path="/dashboard/auth")


# ── client dashboards ────────────────────────────────────────────────────
# CLIENT_PASSWORDS = "trading-places=hunter2;another-show=pw" — one entry per
# show slug (lower-case show name, non-alphanumerics → '-'). Each client gets
# its own cookie, signed with the shared secret plus their password, so
# changing a client's password logs only that client out.

def client_passwords() -> Dict[str, str]:
    out: Dict[str, str] = {}
    for part in os.environ.get("CLIENT_PASSWORDS", "").split(";"):
        if "=" in part:
            slug, pw = part.split("=", 1)
            if slug.strip() and pw.strip():
                out[slug.strip().lower()] = pw.strip()
    return out


def client_cookie(slug: str) -> str:
    return "gf_client_" + "".join(c if c.isalnum() else "_" for c in slug)


def client_token(slug: str) -> str:
    pw = client_passwords().get(slug, "")
    return hmac.new(_secret().encode(), f"gf-client-v1:{slug}:{pw}".encode(), hashlib.sha256).hexdigest()


def is_client_authed(request: Request, slug: str) -> bool:
    if FAKE_DATA:
        return True
    if slug not in client_passwords():
        return False
    return hmac.compare_digest(request.cookies.get(client_cookie(slug), ""), client_token(slug))


def check_client_password(slug: str, candidate: str) -> bool:
    pw = client_passwords().get(slug)
    return bool(pw) and hmac.compare_digest(candidate.encode(), pw.encode())


def set_client_cookie(response, slug: str) -> None:
    response.set_cookie(client_cookie(slug), client_token(slug), max_age=MAX_AGE, httponly=True,
                        secure=ON_RENDER, samesite="lax", path=f"/clients/{slug}")


def clear_client_cookie(response, slug: str) -> None:
    response.delete_cookie(client_cookie(slug), path=f"/clients/{slug}")
