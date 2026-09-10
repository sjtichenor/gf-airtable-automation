"""
api.goodfuturemedia.com — the TikTok "log in and allow" flow, under our own app.

Routes
    GET /                    plain landing page
    GET /health              for Render
    GET /tiktok/login?key=…  starts the flow; the key stops strangers authorising
                             their own accounts into our table
    GET /tiktok/callback     TikTok sends the browser here with a code; we swap
                             it for tokens, read the account's profile and latest
                             videos, encrypt the tokens into Airtable, and show
                             the result

Environment
    TIKTOK_CLIENT_KEY, TIKTOK_CLIENT_SECRET   the sandbox pair while testing,
                                              the production pair once approved
    TIKTOK_ENVIRONMENT      "Sandbox" or "Production" — written to Airtable so
                            the sync knows which client pair a row belongs to
    TIKTOK_REDIRECT_URI     must equal the URI registered with TikTok, character
                            for character. Default is the registered one.
    AUTH_LINK_SECRET        any long random string; appears as ?key= in the login
                            link and nowhere else
    TOKEN_ENCRYPTION_KEY    a Fernet key. Generate one with
                              python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
                            Lose it and every stored token has to be re-authorised.
    AIRTABLE_PERSONAL_ACCESS_TOKEN
    AIRTABLE_BASE_ID        defaults to the Good Future Media base
    TIKTOK_AUTH_TABLE_ID    defaults to the TikTok Auth table

Nothing secret is ever logged or rendered. The only place a token exists in
clear text is inside this process, briefly, before it is encrypted.

TikTok facts this code depends on (docs, 2026-09):
  - authorize:  https://www.tiktok.com/v2/auth/authorize/  (client_key, scope,
                redirect_uri, state, response_type=code). No PKCE for web.
  - token:      POST https://open.tiktokapis.com/v2/oauth/token/ as
                application/x-www-form-urlencoded; response fields are top-level.
  - access_token lives 24h; refresh_token lives 365 days from ISSUE, and a
                refresh may hand back a NEW refresh_token which must replace
                the old one. Persisting that rotation is the point of the table.
"""

import html
import json
import logging
import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List
from urllib.parse import urlencode

import requests
from cryptography.fernet import Fernet, InvalidToken
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse

log = logging.getLogger("tiktok_auth")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

TIKTOK_AUTHORIZE = "https://www.tiktok.com/v2/auth/authorize/"
TIKTOK_API = "https://open.tiktokapis.com"
SCOPES = "user.info.basic,user.info.profile,user.info.stats,video.list"

USER_FIELDS = (
    "open_id,union_id,avatar_url,display_name,username,profile_deep_link,"
    "bio_description,is_verified,follower_count,following_count,likes_count,video_count"
)
VIDEO_FIELDS = (
    "id,title,create_time,cover_image_url,share_url,"
    "view_count,like_count,comment_count,share_count"
)

CLIENT_KEY = os.environ.get("TIKTOK_CLIENT_KEY", "")
CLIENT_SECRET = os.environ.get("TIKTOK_CLIENT_SECRET", "")
ENVIRONMENT = os.environ.get("TIKTOK_ENVIRONMENT", "Sandbox")
REDIRECT_URI = os.environ.get(
    "TIKTOK_REDIRECT_URI", "https://api.goodfuturemedia.com/tiktok/callback"
)
AUTH_LINK_SECRET = os.environ.get("AUTH_LINK_SECRET", "")
ENCRYPTION_KEY = os.environ.get("TOKEN_ENCRYPTION_KEY", "")

AIRTABLE_TOKEN = os.environ.get("AIRTABLE_PERSONAL_ACCESS_TOKEN", "")
AIRTABLE_BASE = os.environ.get("AIRTABLE_BASE_ID", "appxCYu0Tfwc6h7X7")
AUTH_TABLE = os.environ.get("TIKTOK_AUTH_TABLE_ID", "tblkAwVZQWrsXH8Ee")

# state -> issued-at. One instance, short-lived, so memory is fine.
_pending: Dict[str, float] = {}
STATE_TTL = 600

app = FastAPI(title="Good Future Media API", docs_url=None, redoc_url=None)

# Analytics dashboard: /dashboard (see dashboard/README in OPERATIONS.md).
from fastapi.middleware.gzip import GZipMiddleware  # noqa: E402
from dashboard.routes import clients as client_router, router as dashboard_router, start_background  # noqa: E402

app.add_middleware(GZipMiddleware, minimum_size=1024)
app.include_router(dashboard_router)
app.include_router(client_router)


@app.on_event("startup")
def _warm_dashboard() -> None:
    start_background()


def missing_config() -> List[str]:
    wanted = {
        "TIKTOK_CLIENT_KEY": CLIENT_KEY,
        "TIKTOK_CLIENT_SECRET": CLIENT_SECRET,
        "AUTH_LINK_SECRET": AUTH_LINK_SECRET,
        "TOKEN_ENCRYPTION_KEY": ENCRYPTION_KEY,
        "AIRTABLE_PERSONAL_ACCESS_TOKEN": AIRTABLE_TOKEN,
    }
    return [name for name, value in wanted.items() if not value]


def fernet() -> Fernet:
    return Fernet(ENCRYPTION_KEY.encode())


# ── pages ─────────────────────────────────────────────────────────────────

PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>{title}</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
 body{{font:16px/1.5 -apple-system,Segoe UI,Helvetica,Arial,sans-serif;max-width:860px;margin:40px auto;padding:0 20px;color:#111}}
 h1{{font-size:22px;margin:0 0 6px}} .muted{{color:#666}}
 .acct{{display:flex;gap:16px;align-items:center;margin:24px 0}}
 .acct img{{width:72px;height:72px;border-radius:50%}}
 table{{border-collapse:collapse;width:100%;margin-top:16px}}
 th,td{{text-align:left;padding:8px 10px;border-bottom:1px solid #eee;vertical-align:top}}
 th{{font-weight:600;color:#444}} td.n{{text-align:right;font-variant-numeric:tabular-nums}}
 .ok{{background:#e8f7ee;border:1px solid #b6e3c6;padding:12px 14px;border-radius:8px}}
 .err{{background:#fdecec;border:1px solid #f3b6b6;padding:12px 14px;border-radius:8px}}
</style></head><body>{body}</body></html>"""


def page(title: str, body: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(PAGE.format(title=html.escape(title), body=body), status_code=status)


def esc(value) -> str:
    return html.escape(str(value if value is not None else ""))


@app.get("/", response_class=HTMLResponse)
def index():
    return page(
        "Good Future Media API",
        "<h1>Good Future Media</h1><p class='muted'>Internal analytics endpoints. "
        "Nothing to see here.</p>",
    )


@app.get("/health", response_class=PlainTextResponse)
def health():
    gaps = missing_config()
    return PlainTextResponse("ok" if not gaps else "missing: " + ", ".join(gaps))


# ── step 1: send the browser to TikTok ────────────────────────────────────

@app.get("/tiktok/login")
def tiktok_login(key: str = ""):
    if not AUTH_LINK_SECRET or not secrets.compare_digest(key, AUTH_LINK_SECRET):
        return page("Not allowed", "<div class='err'>This link is not valid.</div>", 403)
    gaps = missing_config()
    if gaps:
        return page(
            "Not configured",
            "<div class='err'>Service is missing configuration: "
            + esc(", ".join(gaps))
            + "</div>",
            500,
        )

    now = time.time()
    for old in [s for s, t in _pending.items() if now - t > STATE_TTL]:
        _pending.pop(old, None)
    state = secrets.token_urlsafe(24)
    _pending[state] = now

    params = {
        "client_key": CLIENT_KEY,
        "scope": SCOPES,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "state": state,
    }
    log.info("Starting TikTok login (%s)", ENVIRONMENT)
    return RedirectResponse(f"{TIKTOK_AUTHORIZE}?{urlencode(params)}", status_code=302)


# ── step 2: TikTok sends the browser back here ────────────────────────────

@app.get("/tiktok/callback", response_class=HTMLResponse)
def tiktok_callback(request: Request):
    q = request.query_params
    if q.get("error"):
        log.warning("TikTok returned error=%s", q.get("error"))
        return page(
            "TikTok said no",
            "<div class='err'><b>TikTok declined:</b> "
            + esc(q.get("error_description") or q.get("error"))
            + "</div>",
            400,
        )

    code, state = q.get("code", ""), q.get("state", "")
    issued = _pending.pop(state, None)
    if not code or issued is None or time.time() - issued > STATE_TTL:
        return page(
            "Expired",
            "<div class='err'>This login attempt has expired or was not started "
            "from our link. Start again from the login link.</div>",
            400,
        )

    # 2a. code -> tokens
    try:
        tokens = exchange_code(code)
    except RuntimeError as exc:
        log.warning("Token exchange failed: %s", exc)
        return page("Token exchange failed", f"<div class='err'>{esc(exc)}</div>", 502)

    access = tokens["access_token"]

    # 2b. who is this, and what have they posted
    profile = tiktok_get(access, "/v2/user/info/", {"fields": USER_FIELDS}).get("user", {})
    videos = tiktok_post(
        access, "/v2/video/list/", {"fields": VIDEO_FIELDS}, {"max_count": 10}
    ).get("videos", [])

    # 2c. encrypt and store
    try:
        record = store_tokens(tokens, profile)
    except RuntimeError as exc:
        log.warning("Airtable write failed: %s", exc)
        return page("Could not save", f"<div class='err'>{esc(exc)}</div>", 502)

    log.info(
        "Authorised @%s (%s), %d video(s) visible, Airtable %s",
        profile.get("username"),
        ENVIRONMENT,
        len(videos),
        record,
    )
    return page("Connected", render_success(profile, videos, record))


# ── TikTok calls ──────────────────────────────────────────────────────────

def exchange_code(code: str) -> dict:
    resp = requests.post(
        f"{TIKTOK_API}/v2/oauth/token/",
        data={
            "client_key": CLIENT_KEY,
            "client_secret": CLIENT_SECRET,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": REDIRECT_URI,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    try:
        body = resp.json()
    except ValueError:
        raise RuntimeError(f"TikTok token endpoint returned {resp.status_code} with no JSON")
    if resp.status_code != 200 or "access_token" not in body:
        # TikTok puts errors at top level: error, error_description
        raise RuntimeError(
            f"TikTok token endpoint: {body.get('error', resp.status_code)} — "
            f"{body.get('error_description', 'no description')}"
        )
    body["obtained_at"] = datetime.now(timezone.utc).isoformat()
    return body


def tiktok_get(access: str, path: str, params: dict) -> dict:
    resp = requests.get(
        f"{TIKTOK_API}{path}",
        params=params,
        headers={"Authorization": f"Bearer {access}"},
        timeout=30,
    )
    return _unwrap(resp, path)


def tiktok_post(access: str, path: str, params: dict, body: dict) -> dict:
    resp = requests.post(
        f"{TIKTOK_API}{path}",
        params=params,
        json=body,
        headers={"Authorization": f"Bearer {access}", "Content-Type": "application/json"},
        timeout=30,
    )
    return _unwrap(resp, path)


def _unwrap(resp: requests.Response, path: str) -> dict:
    try:
        body = resp.json()
    except ValueError:
        log.warning("%s returned %s with no JSON", path, resp.status_code)
        return {}
    err = body.get("error") or {}
    if err.get("code") not in (None, "ok"):
        log.warning("%s error: %s — %s", path, err.get("code"), err.get("message"))
    return body.get("data") or {}


# ── Airtable ──────────────────────────────────────────────────────────────

def store_tokens(tokens: dict, profile: dict) -> str:
    """Encrypt the token set and upsert the row for this open_id. Returns the record id."""
    to_keep = {
        k: tokens.get(k)
        for k in ("access_token", "refresh_token", "expires_in", "refresh_expires_in",
                  "open_id", "scope", "obtained_at")
    }
    ciphertext = fernet().encrypt(json.dumps(to_keep).encode()).decode()

    obtained = datetime.fromisoformat(tokens["obtained_at"])
    refresh_expires = obtained + timedelta(seconds=int(tokens.get("refresh_expires_in", 0)))

    fields = {
        "Username": profile.get("username") or tokens.get("open_id", ""),
        "Open ID": tokens.get("open_id", ""),
        "Display Name": profile.get("display_name", ""),
        "Environment": ENVIRONMENT,
        "Encrypted Tokens": ciphertext,
        "Scope": tokens.get("scope", ""),
        "Refresh Expires At": refresh_expires.isoformat(),
        "Updated At": obtained.isoformat(),
    }

    headers = {"Authorization": f"Bearer {AIRTABLE_TOKEN}", "Content-Type": "application/json"}
    base = f"https://api.airtable.com/v0/{AIRTABLE_BASE}/{AUTH_TABLE}"

    # Same account and same environment -> update in place. Rows are keyed on
    # TikTok's open_id, which is stable for our app.
    open_id = tokens.get("open_id", "").replace("'", "\\'")
    env = ENVIRONMENT.replace("'", "\\'")
    lookup = requests.get(
        base,
        params={"filterByFormula": f"AND({{Open ID}}='{open_id}',{{Environment}}='{env}')",
                "maxRecords": 1},
        headers=headers,
        timeout=30,
    )
    if lookup.status_code != 200:
        raise RuntimeError(f"Airtable lookup returned {lookup.status_code}: {lookup.text[:200]}")
    existing = lookup.json().get("records", [])

    if existing:
        rid = existing[0]["id"]
        resp = requests.patch(f"{base}/{rid}", json={"fields": fields}, headers=headers, timeout=30)
    else:
        resp = requests.post(base, json={"fields": fields, "typecast": True}, headers=headers, timeout=30)
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Airtable write returned {resp.status_code}: {resp.text[:200]}")
    return resp.json()["id"]


def load_tokens(ciphertext: str) -> dict:
    """The inverse, for the sync: ciphertext from Airtable -> token dict."""
    try:
        return json.loads(fernet().decrypt(ciphertext.encode()))
    except InvalidToken:
        raise RuntimeError("Token ciphertext does not match TOKEN_ENCRYPTION_KEY")


# ── result page ───────────────────────────────────────────────────────────

def render_success(profile: dict, videos: list, record_id: str) -> str:
    def n(v):
        try:
            return f"{int(v):,}"
        except (TypeError, ValueError):
            return "—"

    rows = []
    for v in videos:
        when = ""
        if v.get("create_time"):
            when = datetime.fromtimestamp(int(v["create_time"]), tz=timezone.utc).strftime("%Y-%m-%d")
        title = v.get("title") or "(untitled)"
        link = v.get("share_url") or "#"
        rows.append(
            f"<tr><td><a href='{esc(link)}'>{esc(title[:80])}</a></td>"
            f"<td>{esc(when)}</td><td class='n'>{n(v.get('view_count'))}</td>"
            f"<td class='n'>{n(v.get('like_count'))}</td><td class='n'>{n(v.get('comment_count'))}</td>"
            f"<td class='n'>{n(v.get('share_count'))}</td></tr>"
        )
    table = (
        "<table><tr><th>Video</th><th>Posted</th><th>Views</th><th>Likes</th>"
        "<th>Comments</th><th>Shares</th></tr>" + "".join(rows) + "</table>"
        if rows
        else "<p class='muted'>No public videos returned.</p>"
    )
    avatar = profile.get("avatar_url") or ""
    return (
        "<h1>Connected to TikTok</h1>"
        f"<p class='muted'>{esc(ENVIRONMENT)} · scopes: {esc(SCOPES)}</p>"
        "<div class='acct'>"
        + (f"<img src='{esc(avatar)}' alt=''>" if avatar else "")
        + f"<div><b>{esc(profile.get('display_name'))}</b><br>@{esc(profile.get('username'))}"
        f"<br><span class='muted'>{n(profile.get('follower_count'))} followers · "
        f"{n(profile.get('likes_count'))} likes · {n(profile.get('video_count'))} videos</span></div></div>"
        "<div class='ok'>Access saved to Airtable (TikTok Auth, record "
        f"{esc(record_id)}). Tokens are stored encrypted; nothing sensitive is shown here.</div>"
        "<h2 style='font-size:17px;margin-top:28px'>Latest videos</h2>"
        + table
    )
