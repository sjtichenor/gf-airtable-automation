"""
TikTok posts + followers sync on our own app.

Reads the accounts that signed in through api.goodfuturemedia.com/tiktok/login
(the "TikTok Auth" table: one row per account per environment, tokens
Fernet-encrypted), refreshes each access token, pulls view/like/comment
counts for every TikTok post in Airtable in batches of 20 per account, and
writes follower counts to Channels. Never writes a number the API did not
return. Refreshed tokens are written back encrypted, since TikTok rotates
refresh tokens.

Environment
    TIKTOK_CLIENT_KEY, TIKTOK_CLIENT_SECRET   production app keys
    TIKTOK_ENVIRONMENT                        Production (default) | Sandbox
    TOKEN_ENCRYPTION_KEY                      same Fernet key as gf-api
    AIRTABLE_PERSONAL_ACCESS_TOKEN, AIRTABLE_BASE_ID
    TIKTOK_AUTH_TABLE_ID, AIRTABLE_TABLE_ID (Posts), AIRTABLE_CHANNELS_TABLE_ID
    TIKTOK_LOOKBACK_DAYS   re-sync posts dated within N days (default 120);
                           posts with no Views yet are always synced; -1 = all
"""
import json
import os
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import requests
from cryptography.fernet import Fernet, InvalidToken

CLIENT_KEY = os.environ.get("TIKTOK_CLIENT_KEY", "")
CLIENT_SECRET = os.environ.get("TIKTOK_CLIENT_SECRET", "")
ENVIRONMENT = os.environ.get("TIKTOK_ENVIRONMENT", "Production")
ENC_KEY = os.environ.get("TOKEN_ENCRYPTION_KEY", "")
AT_TOKEN = os.environ.get("AIRTABLE_PERSONAL_ACCESS_TOKEN", "")
BASE = os.environ.get("AIRTABLE_BASE_ID", "appxCYu0Tfwc6h7X7")
AUTH_TABLE = os.environ.get("TIKTOK_AUTH_TABLE_ID", "tblkAwVZQWrsXH8Ee")
POSTS = os.environ.get("AIRTABLE_TABLE_ID", "tblMpYJQjbb5yuKfC")
CHANNELS = os.environ.get("AIRTABLE_CHANNELS_TABLE_ID", "tblP8bh3crTHLVoZA")
LOOKBACK = int(os.environ.get("TIKTOK_LOOKBACK_DAYS", "120"))
TT = "https://open.tiktokapis.com"
AT = f"https://api.airtable.com/v0/{BASE}"
HEADERS = {"Authorization": f"Bearer {AT_TOKEN}", "Content-Type": "application/json"}
VIDEO_FIELDS = "id,view_count,like_count,comment_count,share_count,create_time"


def missing():
    return [k for k, v in {"TIKTOK_CLIENT_KEY": CLIENT_KEY, "TIKTOK_CLIENT_SECRET": CLIENT_SECRET,
                           "TOKEN_ENCRYPTION_KEY": ENC_KEY, "AIRTABLE_PERSONAL_ACCESS_TOKEN": AT_TOKEN}.items() if not v]


# ── Airtable helpers ─────────────────────────────────────────────────────

def at_list(table, **params):
    out, offset = [], None
    while True:
        q = dict(params)
        if offset:
            q["offset"] = offset
        r = requests.get(f"{AT}/{table}", headers=HEADERS, params=q, timeout=30)
        r.raise_for_status()
        body = r.json()
        out.extend(body.get("records", []))
        offset = body.get("offset")
        if not offset:
            return out
        time.sleep(0.21)


def at_patch(table, record_id, fields):
    r = requests.patch(f"{AT}/{table}/{record_id}", headers=HEADERS, json={"fields": fields}, timeout=30)
    if r.status_code != 200:
        print(f"      Airtable update failed: {r.status_code} {r.text[:200]}")
        return False
    return True


# ── tokens ───────────────────────────────────────────────────────────────

def fernet():
    return Fernet(ENC_KEY.encode())


def load_accounts():
    """Signed-in accounts for this environment, tokens decrypted."""
    rows = at_list(AUTH_TABLE, filterByFormula=f"{{Environment}}='{ENVIRONMENT}'")
    accounts = []
    for r in rows:
        f = r["fields"]
        try:
            tokens = json.loads(fernet().decrypt((f.get("Encrypted Tokens") or "").encode()).decode())
        except (InvalidToken, ValueError) as exc:
            print(f"   {f.get('Username')}: cannot decrypt tokens ({type(exc).__name__}); re-authorise this account")
            continue
        accounts.append({"record_id": r["id"], "username": (f.get("Username") or "").lower(),
                         "open_id": f.get("Open ID"), "channel": (f.get("Channel") or [None])[0], "tokens": tokens})
    return accounts


def refresh(account):
    """Get a fresh access token (they last 24h) and persist the rotated refresh token."""
    t = account["tokens"]
    r = requests.post(f"{TT}/v2/oauth/token/", headers={"Content-Type": "application/x-www-form-urlencoded"},
                      data={"client_key": CLIENT_KEY, "client_secret": CLIENT_SECRET,
                            "grant_type": "refresh_token", "refresh_token": t.get("refresh_token", "")}, timeout=30)
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code != 200 or not body.get("access_token"):
        print(f"   @{account['username']}: token refresh failed {r.status_code} {body.get('error', '')} {body.get('error_description', '')}")
        return False
    now = datetime.now(timezone.utc)
    t.update({"access_token": body["access_token"], "refresh_token": body.get("refresh_token", t.get("refresh_token")),
              "expires_in": body.get("expires_in"), "refresh_expires_in": body.get("refresh_expires_in"),
              "scope": body.get("scope", t.get("scope")), "obtained_at": now.isoformat()})
    ciphertext = fernet().encrypt(json.dumps(t).encode()).decode()
    refresh_expires = now + timedelta(seconds=int(t.get("refresh_expires_in") or 0))
    at_patch(AUTH_TABLE, account["record_id"], {"Encrypted Tokens": ciphertext, "Updated At": now.isoformat(),
                                                "Refresh Expires At": refresh_expires.isoformat()})
    return True


def tt_post(account, path, params, body):
    r = requests.post(f"{TT}{path}", params=params, json=body,
                      headers={"Authorization": f"Bearer {account['tokens']['access_token']}", "Content-Type": "application/json"},
                      timeout=30)
    try:
        j = r.json()
    except ValueError:
        j = {}
    err = (j.get("error") or {})
    if r.status_code != 200 or err.get("code") not in (None, "ok"):
        print(f"      {path} {r.status_code}: {err.get('code')} {err.get('message', '')[:160]}")
        return None
    return j.get("data") or {}


# ── posts ────────────────────────────────────────────────────────────────

def resolve_short(url):
    try:
        r = requests.head(url, allow_redirects=True, timeout=10,
                          headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"})
        return r.url if "/video/" in r.url else None
    except requests.RequestException:
        return None


def video_ref(url):
    """(username, video_id) from a TikTok post URL, resolving vm.tiktok.com / tiktok.com/t/ links."""
    if not url:
        return None, None
    if "vm.tiktok.com" in url or "tiktok.com/t/" in url:
        url = resolve_short(url) or ""
    m = re.search(r"tiktok\.com/@([^/?#]+)/video/(\d+)", url)
    return (m.group(1).lower(), m.group(2)) if m else (None, None)


def tiktok_posts():
    if LOOKBACK < 0:
        formula = "{Social Network}='TikTok'"
    else:
        formula = ("AND({Social Network}='TikTok', OR(NOT({Views}), "
                   f"IS_AFTER({{Date Posted}}, DATEADD(TODAY(), -{LOOKBACK}, 'days'))))")
    return at_list(POSTS, filterByFormula=formula, **{"fields[]": ["Link to Post", "Views", "Date Posted", "Social Media Account"]})


def sync_posts(accounts):
    by_user = {a["username"]: a for a in accounts}
    posts = tiktok_posts()
    print(f"\nTikTok posts to sync: {len(posts)} (lookback {LOOKBACK} days)")
    grouped, unknown, unparsed = defaultdict(list), defaultdict(int), 0
    for p in posts:
        user, vid = video_ref(p["fields"].get("Link to Post", ""))
        if not vid:
            unparsed += 1
            continue
        if user not in by_user:
            unknown[user] += 1
            continue
        grouped[user].append((vid, p))
    if unparsed:
        print(f"   {unparsed} posts had no readable video id")
    for u, n in sorted(unknown.items(), key=lambda x: -x[1]):
        print(f"   @{u}: {n} posts, account not signed in — skipped")

    updated = failed = 0
    for user, items in grouped.items():
        account = by_user[user]
        print(f"\n@{user}: {len(items)} posts")
        for i in range(0, len(items), 20):
            chunk = items[i:i + 20]
            data = tt_post(account, "/v2/video/query/", {"fields": VIDEO_FIELDS},
                           {"filters": {"video_ids": [vid for vid, _ in chunk]}})
            found = {str(v.get("id")): v for v in (data or {}).get("videos", [])}
            for vid, p in chunk:
                v = found.get(vid)
                if not v or v.get("view_count") is None:
                    failed += 1
                    continue
                fields = {"Views": v.get("view_count"), "Likes": v.get("like_count"), "Comments": v.get("comment_count")}
                if not p["fields"].get("Date Posted") and v.get("create_time"):
                    fields["Date Posted"] = datetime.fromtimestamp(v["create_time"], timezone.utc).strftime("%Y-%m-%d")
                if at_patch(POSTS, p["id"], {k: val for k, val in fields.items() if val is not None}):
                    updated += 1
                else:
                    failed += 1
            time.sleep(0.3)
        print(f"   done")
    print(f"\nposts: {updated} updated, {failed} not returned or not written")


# ── followers ────────────────────────────────────────────────────────────

def sync_followers(accounts):
    channels = at_list(CHANNELS, **{"fields[]": ["Social Media Account", "TikTok Profile", "TikTok Followers"]})
    by_handle = {}
    for c in channels:
        url = c["fields"].get("TikTok Profile") or ""
        path = [x for x in urlparse(url).path.split("/") if x]
        if path:
            by_handle[path[0].lower().lstrip("@")] = c
    print("\nFollowers:")
    for a in accounts:
        data = tt_post(a, "/v2/user/info/", {"fields": "open_id,username,follower_count,likes_count,video_count"}, {})
        info = (data or {}).get("user") or {}
        if info.get("follower_count") is None:
            print(f"   @{a['username']}: no follower count returned")
            continue
        ch = None
        if a.get("channel"):
            ch = next((c for c in channels if c["id"] == a["channel"]), None)
        ch = ch or by_handle.get(a["username"])
        if not ch:
            print(f"   @{a['username']}: {info['follower_count']:,} followers, no matching channel")
            continue
        prev = ch["fields"].get("TikTok Followers")
        if at_patch(CHANNELS, ch["id"], {"TikTok Followers": info["follower_count"]}):
            delta = f" ({info['follower_count'] - prev:+,})" if isinstance(prev, (int, float)) else ""
            print(f"   {ch['fields'].get('Social Media Account')}: {info['follower_count']:,}{delta}")


def main():
    gaps = missing()
    if gaps:
        sys.exit("missing: " + ", ".join(gaps))
    print(f"TikTok sync ({ENVIRONMENT})")
    accounts = load_accounts()
    print(f"{len(accounts)} signed-in account(s): " + ", ".join("@" + a["username"] for a in accounts))
    live = [a for a in accounts if refresh(a)]
    if not live:
        sys.exit("no account with a working token; re-authorise via /tiktok/login")
    sync_posts(live)
    sync_followers(live)


if __name__ == "__main__":
    main()
