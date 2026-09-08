"""
Social Media Metrics Webhook Server
====================================
Fetches post metrics (views, likes, date posted) instantly when a new post
is added to Airtable, instead of waiting for the next cron cycle.

Deploy on Render as a Web Service.

  Start command : uvicorn webhook_server:app --host 0.0.0.0 --port $PORT
  Requirements  : pip install fastapi uvicorn requests python-dotenv

Environment variables (same ones your existing cron jobs already use):
  AIRTABLE_PERSONAL_ACCESS_TOKEN
  AIRTABLE_BASE_ID
  AIRTABLE_TABLE_ID
  YOUTUBE_API_KEY
  TWITTER_BEARER_TOKEN
  META_USER_ACCESS_TOKEN
  FACEBOOK_PAGES          (JSON array)
  TIKTOK_ACCOUNTS         (JSON array)
  TIKTOK_CLIENT_KEY
  TIKTOK_CLIENT_SECRET
  WEBHOOK_SECRET           (optional — shared secret for request auth)
"""

import os
import re
import json
import time
import logging
import requests
from datetime import datetime, timedelta
from contextlib import asynccontextmanager

from fastapi import FastAPI, BackgroundTasks, Request, HTTPException
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("webhook")

# ─────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────
AIRTABLE_TOKEN   = os.getenv("AIRTABLE_PERSONAL_ACCESS_TOKEN")
AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID")
POSTS_TABLE_ID   = os.getenv("AIRTABLE_TABLE_ID", "tblMpYJQjbb5yuKfC")

YOUTUBE_API_KEY       = os.getenv("YOUTUBE_API_KEY")
YOUTUBE_BASE_URL      = os.getenv("YOUTUBE_API_BASE_URL", "https://www.googleapis.com/youtube/v3")
TWITTER_BEARER_TOKEN  = os.getenv("TWITTER_BEARER_TOKEN")
META_USER_ACCESS_TOKEN = os.getenv("META_USER_ACCESS_TOKEN")

TIKTOK_CLIENT_KEY    = os.getenv("TIKTOK_CLIENT_KEY", "")
TIKTOK_CLIENT_SECRET = os.getenv("TIKTOK_CLIENT_SECRET", "")

WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")

AIRTABLE_HEADERS = {
    "Authorization": f"Bearer {AIRTABLE_TOKEN}",
    "Content-Type": "application/json",
}

# Parse JSON env vars safely
def _load_json_env(name, default="[]"):
    try:
        return json.loads(os.getenv(name, default))
    except (json.JSONDecodeError, TypeError):
        log.warning(f"Could not parse {name} env var")
        return []

FACEBOOK_PAGES  = _load_json_env("FACEBOOK_PAGES")
TIKTOK_ACCOUNTS = _load_json_env("TIKTOK_ACCOUNTS")

# ─────────────────────────────────────────────
# Simple in-memory cache with TTL
# ─────────────────────────────────────────────
_cache = {}
CACHE_TTL = 1800  # 30 minutes

def _cache_get(key):
    entry = _cache.get(key)
    if entry and datetime.now() < entry["expires"]:
        return entry["data"]
    return None

def _cache_set(key, data):
    _cache[key] = {"data": data, "expires": datetime.now() + timedelta(seconds=CACHE_TTL)}

# Persistent token store for TikTok (survives cache expiry)
_tiktok_live_accounts = None

# ─────────────────────────────────────────────
# Airtable helpers
# ─────────────────────────────────────────────
def update_airtable_record(record_id, fields):
    """PATCH a single record in the Posts table."""
    url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{POSTS_TABLE_ID}/{record_id}"
    resp = requests.patch(url, headers=AIRTABLE_HEADERS, json={"fields": fields})
    if resp.status_code == 200:
        log.info(f"✅ Updated {record_id}: {fields}")
        return True
    log.error(f"❌ Airtable update failed for {record_id}: {resp.text}")
    return False

# ─────────────────────────────────────────────
# Platform detection from URL
# ─────────────────────────────────────────────
def detect_platform(url):
    if not url:
        return None
    u = url.strip().lower()
    if "instagram.com" in u:
        return "instagram"
    if "tiktok.com" in u:
        return "tiktok"
    if "youtube.com" in u or "youtu.be" in u:
        return "youtube"
    if "twitter.com" in u or "x.com" in u:
        return "twitter"
    if "facebook.com" in u:
        return "facebook"
    return None


# ═════════════════════════════════════════════
#  YOUTUBE
# ═════════════════════════════════════════════
def _yt_extract_video_id(url):
    for pattern in [
        r"youtube\.com/watch\?v=([^&?]+)",
        r"youtube\.com/shorts/([^?&/]+)",
        r"youtu\.be/([^?&/]+)",
    ]:
        m = re.search(pattern, url)
        if m:
            return m.group(1)
    return None


def handle_youtube(record_id, post_url):
    video_id = _yt_extract_video_id(post_url)
    if not video_id:
        log.warning(f"YouTube: cannot extract video ID from {post_url}")
        return

    try:
        resp = requests.get(f"{YOUTUBE_BASE_URL}/videos", params={
            "key": YOUTUBE_API_KEY,
            "id": video_id,
            "part": "statistics,snippet",
        })
        data = resp.json()

        if "items" not in data or not data["items"]:
            log.warning(f"YouTube: video not found {video_id}")
            return

        stats   = data["items"][0]["statistics"]
        snippet = data["items"][0]["snippet"]

        fields = {
            "Views": int(stats.get("viewCount", 0)),
            "Likes": int(stats.get("likeCount", 0)),
        }

        published = snippet.get("publishedAt")
        if published:
            dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
            fields["Date Posted"] = dt.strftime("%Y-%m-%d")

        update_airtable_record(record_id, fields)

    except Exception as e:
        log.error(f"YouTube error: {e}")


# ═════════════════════════════════════════════
#  TWITTER / X
# ═════════════════════════════════════════════
def _tw_extract_tweet_id(url):
    for pattern in [
        r"(?:twitter\.com|x\.com)/[^/]+/status/(\d+)",
        r"(?:mobile\.twitter\.com|m\.twitter\.com)/[^/]+/status/(\d+)",
    ]:
        m = re.search(pattern, url)
        if m:
            return m.group(1)
    return None


def handle_twitter(record_id, post_url):
    tweet_id = _tw_extract_tweet_id(post_url)
    if not tweet_id:
        log.warning(f"Twitter: cannot extract tweet ID from {post_url}")
        return

    try:
        resp = requests.get("https://api.x.com/2/tweets", headers={
            "Authorization": f"Bearer {TWITTER_BEARER_TOKEN}",
            "User-Agent": "MetricsWebhook/1.0",
        }, params={
            "ids": tweet_id,
            "tweet.fields": "public_metrics,created_at",
            "expansions": "attachments.media_keys",
            "media.fields": "public_metrics",
        })
        data = resp.json()

        if "data" not in data or not data["data"]:
            log.warning(f"Twitter: tweet not found {tweet_id}")
            return

        tweet   = data["data"][0]
        metrics = tweet.get("public_metrics", {})

        fields = {
            "Views": metrics.get("impression_count", 0),
            "Likes": metrics.get("like_count", 0),
        }

        created = tweet.get("created_at")
        if created:
            dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
            fields["Date Posted"] = dt.strftime("%Y-%m-%d")

        update_airtable_record(record_id, fields)

    except Exception as e:
        log.error(f"Twitter error: {e}")


# ═════════════════════════════════════════════
#  TIKTOK
# ═════════════════════════════════════════════
TIKTOK_BASE = "https://open.tiktokapis.com"


def _tt_refresh_token(account):
    """Refresh a TikTok access token. Mutates account dict in-place."""
    try:
        resp = requests.post(f"{TIKTOK_BASE}/v2/oauth/token/", headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Cache-Control": "no-cache",
        }, data={
            "client_key": TIKTOK_CLIENT_KEY,
            "client_secret": TIKTOK_CLIENT_SECRET,
            "grant_type": "refresh_token",
            "refresh_token": account["refresh_token"],
        })
        if resp.status_code == 200:
            td = resp.json()
            if "access_token" in td:
                account["access_token"]  = td["access_token"]
                account["refresh_token"] = td["refresh_token"]
                account["expires_in"]    = td.get("expires_in", 86400)
                account["token_refreshed_at"] = datetime.now().isoformat()
                log.info(f"TikTok: refreshed token for {account.get('channel_name', '?')}")
                return True
        log.warning(f"TikTok: token refresh failed ({resp.status_code})")
    except Exception as e:
        log.error(f"TikTok: token refresh error: {e}")
    return False


def _tt_is_expired(account):
    try:
        if "token_refreshed_at" in account and "expires_in" in account:
            refreshed = datetime.fromisoformat(account["token_refreshed_at"])
            expires   = refreshed + timedelta(seconds=account["expires_in"])
            return datetime.now() > (expires - timedelta(hours=2))
    except Exception:
        pass
    return True


def _build_tiktok_mapping():
    global _tiktok_live_accounts
    cached = _cache_get("tiktok")
    if cached:
        return cached

    # Use previously-refreshed tokens if available, else start from env
    accounts = _tiktok_live_accounts or [a.copy() for a in TIKTOK_ACCOUNTS]
    mapping = {}

    for acct in accounts:
        if _tt_is_expired(acct):
            if not _tt_refresh_token(acct):
                continue

        try:
            resp = requests.get(f"{TIKTOK_BASE}/v2/user/info/", headers={
                "Authorization": f"Bearer {acct['access_token']}",
            }, params={
                "fields": "open_id,display_name,username,profile_deep_link",
            })
            if resp.status_code == 200:
                user = resp.json().get("data", {}).get("user", {})
                username = user.get("username", "")
                if not username:
                    link = user.get("profile_deep_link", "")
                    m = re.search(r"tiktok\.com/@([^/?&]+)", link)
                    if m:
                        username = m.group(1)
                if username:
                    mapping[username] = acct.copy()
                    log.info(f"TikTok: mapped @{username}")
        except Exception as e:
            log.error(f"TikTok mapping error: {e}")
        time.sleep(0.5)

    _tiktok_live_accounts = accounts
    _cache_set("tiktok", mapping)
    return mapping


def _tt_extract_info(url):
    """Returns (username, video_id) from a TikTok URL."""
    url = url.strip()
    # Resolve short URLs
    if "vm.tiktok.com" in url or "tiktok.com/t/" in url:
        try:
            r = requests.head(url, allow_redirects=True, timeout=10,
                              headers={"User-Agent": "Mozilla/5.0"})
            url = r.url
        except Exception:
            return None, None
    m = re.search(r"tiktok\.com/@([^/]+)/video/(\d+)", url)
    if m:
        return m.group(1), m.group(2)
    return None, None


def handle_tiktok(record_id, post_url):
    username, video_id = _tt_extract_info(post_url)
    if not username or not video_id:
        log.warning(f"TikTok: cannot extract info from {post_url}")
        return

    mapping = _build_tiktok_mapping()
    account = mapping.get(username)
    if not account:
        log.warning(f"TikTok: no account for @{username}")
        return

    try:
        resp = requests.post(f"{TIKTOK_BASE}/v2/video/query/", headers={
            "Authorization": f"Bearer {account['access_token']}",
            "Content-Type": "application/json",
        }, params={
            "fields": "id,create_time,view_count,like_count,share_count,comment_count",
        }, json={
            "filters": {"video_ids": [video_id]},
        })
        data = resp.json()

        videos = data.get("data", {}).get("videos", [])
        if not videos:
            log.warning(f"TikTok: video not found {video_id}")
            return

        v = videos[0]
        fields = {
            "Views": v.get("view_count", 0),
            "Likes": v.get("like_count", 0),
        }
        ct = v.get("create_time")
        if ct:
            try:
                fields["Date Posted"] = datetime.fromtimestamp(ct).strftime("%Y-%m-%d")
            except Exception:
                pass

        update_airtable_record(record_id, fields)

    except Exception as e:
        log.error(f"TikTok error: {e}")


# ═════════════════════════════════════════════
#  INSTAGRAM
# ═════════════════════════════════════════════
META_BASE = "https://graph.facebook.com/v23.0"


def _build_instagram_mapping():
    cached = _cache_get("instagram")
    if cached:
        return cached

    mapping = {}
    try:
        # 1) Discover all Facebook pages
        all_pages = []
        url = f"{META_BASE}/me/accounts"
        params = {"access_token": META_USER_ACCESS_TOKEN,
                  "fields": "id,name,access_token", "limit": 100}
        while url:
            r = requests.get(url, params=params)
            if r.status_code != 200:
                break
            d = r.json()
            if "data" in d:
                all_pages.extend(d["data"])
                url = d.get("paging", {}).get("next")
                params = None
            else:
                break
            time.sleep(0.10)

        # 2) For each page, find connected IG account
        for page in all_pages:
            pid, ptok = page["id"], page["access_token"]
            try:
                r = requests.get(f"{META_BASE}/{pid}",
                                 params={"fields": "instagram_business_account",
                                         "access_token": ptok})
                if r.status_code != 200:
                    continue
                ig = r.json().get("instagram_business_account")
                if not ig:
                    continue
                ig_id = ig["id"]

                r2 = requests.get(f"{META_BASE}/{ig_id}",
                                  params={"fields": "username,followers_count",
                                          "access_token": ptok})
                if r2.status_code == 200:
                    udata = r2.json()
                    uname = udata.get("username")
                    if uname:
                        mapping[uname] = {
                            "page_access_token": ptok,
                            "ig_account_id": ig_id,
                            "page_name": page.get("name", ""),
                        }
                        log.info(f"Instagram: mapped @{uname}")
            except Exception as e:
                log.error(f"Instagram page mapping error: {e}")
            time.sleep(0.10)

    except Exception as e:
        log.error(f"Instagram mapping error: {e}")

    _cache_set("instagram", mapping)
    return mapping


def _ig_extract_info(url):
    """Returns (username | None, shortcode)."""
    for pat in [r"instagram\.com/([^/]+)/(?:p|reel)/([^/?&]+)",
                r"instagram\.com/(?:p|reel)/([^/?&]+)"]:
        m = re.search(pat, url)
        if m:
            if len(m.groups()) == 2:
                return m.group(1), m.group(2)
            return None, m.group(1)
    return None, None


def _ig_find_media(ig_account_id, shortcode, token):
    """Search an IG account's media for a matching shortcode. Returns (media_id, timestamp)."""
    url = f"{META_BASE}/{ig_account_id}/media"
    params = {"access_token": token, "fields": "id,shortcode,timestamp", "limit": 50}
    pages = 0
    while url and pages < 10:
        pages += 1
        r = requests.get(url, params=params)
        if r.status_code != 200:
            break
        d = r.json()
        for media in d.get("data", []):
            if media.get("shortcode") == shortcode:
                return media["id"], media.get("timestamp")
        url = d.get("paging", {}).get("next")
        params = None
        time.sleep(0.10)
    return None, None


def _ig_format_date(iso_ts):
    try:
        if "+0000" in iso_ts:
            iso_ts = iso_ts.replace("+0000", "+00:00")
        elif iso_ts.endswith("Z"):
            iso_ts = iso_ts.replace("Z", "+00:00")
        return datetime.fromisoformat(iso_ts).strftime("%Y-%m-%d")
    except Exception:
        return None


def handle_instagram(record_id, post_url):
    username, shortcode = _ig_extract_info(post_url)
    if not shortcode:
        log.warning(f"Instagram: cannot extract shortcode from {post_url}")
        return

    mapping   = _build_instagram_mapping()
    page_data = None
    media_id  = None
    timestamp = None

    # Try direct username match first
    if username and username in mapping:
        page_data = mapping[username]
    else:
        # Search across all accounts
        for uname, pdata in mapping.items():
            mid, ts = _ig_find_media(pdata["ig_account_id"], shortcode, pdata["page_access_token"])
            if mid:
                page_data = pdata
                media_id  = mid
                timestamp = ts
                break

    if not page_data:
        log.warning(f"Instagram: no account found for {post_url}")
        return

    if not media_id:
        media_id, timestamp = _ig_find_media(
            page_data["ig_account_id"], shortcode, page_data["page_access_token"]
        )

    if not media_id:
        log.warning(f"Instagram: media not found for shortcode {shortcode}")
        return

    try:
        r = requests.get(f"{META_BASE}/{media_id}/insights", params={
            "metric": "likes,comments,reach,shares,saved,total_interactions,views",
            "access_token": page_data["page_access_token"],
        })
        if r.status_code != 200:
            log.error(f"Instagram insights error: {r.status_code} {r.text}")
            return

        d = r.json()
        if "data" not in d:
            log.warning(f"Instagram: no insights for media {media_id}")
            return

        raw = {}
        for insight in d["data"]:
            val = insight["values"][0]["value"] if insight.get("values") else 0
            raw[insight["name"]] = val

        fields = {
            "Views": raw.get("views", raw.get("impressions", 0)),
            "Reach": raw.get("reach", 0),
            "Likes": raw.get("likes", 0),
        }

        if timestamp:
            fmt = _ig_format_date(timestamp)
            if fmt:
                fields["Date Posted"] = fmt

        update_airtable_record(record_id, fields)

    except Exception as e:
        log.error(f"Instagram error: {e}")


# ═════════════════════════════════════════════
#  FACEBOOK
# ═════════════════════════════════════════════
FB_BASE = "https://graph.facebook.com/v21.0"


def _build_facebook_mapping():
    cached = _cache_get("facebook")
    if cached:
        return cached
    mapping = {}
    for page in FACEBOOK_PAGES:
        pid = page.get("page_id")
        tok = page.get("page_access_token")
        if pid and tok:
            mapping[pid] = tok
    _cache_set("facebook", mapping)
    return mapping


def _fb_extract_post_id(url):
    for pat in [
        r"facebook\.com/share/v/([^/?&]+)",
        r"facebook\.com/reel/([^/?&]+)",
        r"facebook\.com/share/r/([^/?&]+)",
        r"facebook\.com/[^/]+/posts/([^/?&]+)",
        r"facebook\.com/[^/]+/videos/([^/?&]+)",
    ]:
        m = re.search(pat, url)
        if m:
            return m.group(1)
    return None


def handle_facebook(record_id, post_url):
    post_id = _fb_extract_post_id(post_url)
    if not post_id:
        log.warning(f"Facebook: cannot extract post ID from {post_url}")
        return

    mapping = _build_facebook_mapping()

    # Figure out which page owns this post
    working_token = None
    for pid, tok in mapping.items():
        try:
            r = requests.get(f"{FB_BASE}/{post_id}",
                             params={"access_token": tok, "fields": "id"})
            if r.status_code == 200:
                working_token = tok
                break
        except Exception:
            continue

    if not working_token:
        log.warning(f"Facebook: no page token works for post {post_id}")
        return

    try:
        # Video insights
        r = requests.get(f"{FB_BASE}/{post_id}/video_insights", params={
            "access_token": working_token,
            "metric": "post_video_likes_by_reaction_type,fb_reels_total_plays",
        })

        views = 0
        likes = 0
        if r.status_code == 200:
            for metric in r.json().get("data", []):
                name   = metric.get("name")
                values = metric.get("values", [])
                if name == "fb_reels_total_plays" and values:
                    views = values[0].get("value", 0)
                elif name == "post_video_likes_by_reaction_type" and values:
                    reactions = values[0].get("value", {})
                    likes = sum(reactions.values()) if reactions else 0

        fields = {"Views": views, "Likes": likes}

        # Try to get date posted
        try:
            r2 = requests.get(f"{FB_BASE}/{post_id}",
                              params={"access_token": working_token,
                                      "fields": "created_time"})
            if r2.status_code == 200:
                ct = r2.json().get("created_time")
                if ct:
                    ct = ct.replace("+0000", "+00:00")
                    fields["Date Posted"] = datetime.fromisoformat(ct).strftime("%Y-%m-%d")
        except Exception:
            pass

        update_airtable_record(record_id, fields)

    except Exception as e:
        log.error(f"Facebook error: {e}")


# ═════════════════════════════════════════════
#  FASTAPI APP
# ═════════════════════════════════════════════
HANDLERS = {
    "youtube":   handle_youtube,
    "twitter":   handle_twitter,
    "tiktok":    handle_tiktok,
    "instagram": handle_instagram,
    "facebook":  handle_facebook,
}

app = FastAPI(title="Social Media Metrics Webhook")


@app.post("/webhook/new-post")
async def webhook_new_post(request: Request, background_tasks: BackgroundTasks):
    # Optional auth check
    if WEBHOOK_SECRET:
        secret = request.headers.get("X-Webhook-Secret", "")
        if secret != WEBHOOK_SECRET:
            raise HTTPException(status_code=401, detail="Invalid secret")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    record_id = payload.get("record_id", "").strip()
    post_url  = payload.get("post_url", "").strip()

    if not record_id or not post_url:
        raise HTTPException(status_code=400, detail="Missing record_id or post_url")

    platform = detect_platform(post_url)
    if not platform:
        raise HTTPException(status_code=400, detail=f"Unknown platform: {post_url}")

    handler = HANDLERS.get(platform)
    if handler:
        background_tasks.add_task(handler, record_id, post_url)

    return {"status": "queued", "platform": platform, "record_id": record_id}


@app.get("/health")
async def health():
    return {"status": "ok", "platforms": list(HANDLERS.keys())}


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)