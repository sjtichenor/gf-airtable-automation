"""
Instant metrics for a post the moment it is added to Airtable.

The Posts table's "Fetch metrics for new post" automation POSTs
{record_id, post_url} here; the crons would otherwise get to it in up to
six hours. This replaces the contractor's `webhook` web service (Farhan's
repo, branch `whook`, its own copy of every platform's fetch code and his
Meta/TikTok credentials). Here each platform reuses the sync that already
runs under our own repo, for one post:

    youtube    Data API v3 with YOUTUBE_API_KEY
    twitter    X API v2 with TWITTER_BEARER_TOKEN
    tiktok     tiktok/sync_tiktok.py (tokens from the TikTok Auth table)
    facebook   fb/main.py FacebookSync (FACEBOOK_PAGES)
    instagram  insta/insta_sync.py InstagramDynamicSync (META_* tokens)

A platform whose keys are not on this service is reported as "not
configured" and left to its cron; nothing is written. Date Posted is only
filled when the record has none, like the crons.

    POST /webhook/new-post   header X-Webhook-Secret: $WEBHOOK_SECRET
                             body   {"record_id": "rec...", "post_url": "https://..."}
    GET  /webhook/health     which platforms this deployment can handle
"""
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone

import requests
from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import JSONResponse

log = logging.getLogger("webhook")
router = APIRouter(prefix="/webhook")

# The inherited sync classes read these with no default; gf-api has always
# used the base id by default, so give them the same.
os.environ.setdefault("AIRTABLE_BASE_ID", "appxCYu0Tfwc6h7X7")
os.environ.setdefault("AIRTABLE_TABLE_ID", "tblMpYJQjbb5yuKfC")
BASE = os.environ["AIRTABLE_BASE_ID"]
POSTS = os.environ["AIRTABLE_TABLE_ID"]


def _token():
    return os.environ.get("AIRTABLE_PERSONAL_ACCESS_TOKEN", "")


def _headers():
    return {"Authorization": f"Bearer {_token()}", "Content-Type": "application/json"}


def detect_platform(url):
    u = (url or "").strip().lower()
    if "instagram.com" in u:
        return "instagram"
    if "tiktok.com" in u:
        return "tiktok"
    if "youtube.com" in u or "youtu.be" in u:
        return "youtube"
    if "twitter.com" in u or "x.com" in u:
        return "twitter"
    if "facebook.com" in u or "fb.watch" in u:
        return "facebook"
    return None


def get_record(record_id):
    """{"id", "fields": {Name, Link to Post, Date Posted, ...}} or None."""
    r = requests.get(f"https://api.airtable.com/v0/{BASE}/{POSTS}/{record_id}", headers=_headers(), timeout=30)
    if r.status_code != 200:
        log.warning("webhook: record %s: %s %s", record_id, r.status_code, r.text[:120])
        return None
    return r.json()


def write(record_id, fields):
    r = requests.patch(f"https://api.airtable.com/v0/{BASE}/{POSTS}/{record_id}", headers=_headers(), json={"fields": fields}, timeout=30)
    if r.status_code != 200:
        log.warning("webhook: write %s failed: %s %s", record_id, r.status_code, r.text[:160])
        return False
    log.info("webhook: wrote %s: %s", record_id, fields)
    return True


# ── youtube ───────────────────────────────────────────────────────────────

def yt_configured():
    return bool(os.environ.get("YOUTUBE_API_KEY"))


def handle_youtube(rec, url):
    m = re.search(r"(?:v=|youtu\.be/|/shorts/|/embed/|/live/)([A-Za-z0-9_-]{11})", url)
    if not m:
        return "no video id in url"
    r = requests.get("https://www.googleapis.com/youtube/v3/videos",
                     params={"key": os.environ["YOUTUBE_API_KEY"], "id": m.group(1), "part": "statistics,snippet"}, timeout=30)
    items = (r.json() if r.status_code == 200 else {}).get("items") or []
    if not items:
        return f"youtube {r.status_code}: video not found"
    stats, snippet = items[0].get("statistics", {}), items[0].get("snippet", {})
    fields = {"Views": int(stats.get("viewCount", 0))}
    if stats.get("likeCount") is not None:
        fields["Likes"] = int(stats["likeCount"])
    if not rec["fields"].get("Date Posted") and snippet.get("publishedAt"):
        fields["Date Posted"] = snippet["publishedAt"][:10]
    return "written" if write(rec["id"], fields) else "write failed"


# ── twitter / x ───────────────────────────────────────────────────────────

def tw_configured():
    return bool(os.environ.get("TWITTER_BEARER_TOKEN"))


def handle_twitter(rec, url):
    m = re.search(r"(?:twitter\.com|x\.com)/[^/]+/status/(\d+)", url)
    if not m:
        return "no tweet id in url"
    r = requests.get("https://api.x.com/2/tweets", headers={"Authorization": f"Bearer {os.environ['TWITTER_BEARER_TOKEN']}", "User-Agent": "gf-webhook/1.0"},
                     params={"ids": m.group(1), "tweet.fields": "public_metrics,created_at"}, timeout=30)
    data = (r.json() if r.status_code == 200 else {}).get("data") or []
    if not data:
        return f"x {r.status_code}: tweet not found"
    pm = data[0].get("public_metrics") or {}
    fields = {"Views": pm.get("impression_count", 0), "Likes": pm.get("like_count", 0)}
    if not rec["fields"].get("Date Posted") and data[0].get("created_at"):
        fields["Date Posted"] = data[0]["created_at"][:10]
    return "written" if write(rec["id"], fields) else "write failed"


# ── tiktok ────────────────────────────────────────────────────────────────

def tt_configured():
    from tiktok import sync_tiktok
    return not sync_tiktok.missing()


def handle_tiktok(rec, url):
    from tiktok import sync_tiktok as tt
    user, vid = tt.video_ref(url)
    if not vid:
        return "no video id in url"
    account = next((a for a in tt.load_accounts() if a["username"] == user), None)
    if not account:
        return f"@{user} is not signed in"
    # Access tokens last 24 h; refresh only when the stored one is near the end
    # so a cron running at the same moment does not race on the rotated
    # refresh token.
    t = account["tokens"]
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(t.get("obtained_at"))).total_seconds()
    except (TypeError, ValueError):
        age = 10 ** 9
    if age > 0.8 * int(t.get("expires_in") or 86400) and not tt.refresh(account):
        return "token refresh failed"
    data = tt.tt_post(account, "/v2/video/query/", {"fields": tt.VIDEO_FIELDS}, {"filters": {"video_ids": [vid]}})
    v = next((x for x in (data or {}).get("videos", []) if str(x.get("id")) == vid), None)
    if not v or v.get("view_count") is None:
        return "video not returned"
    fields = {"Views": v.get("view_count"), "Likes": v.get("like_count"), "Comments": v.get("comment_count")}
    if not rec["fields"].get("Date Posted") and v.get("create_time"):
        fields["Date Posted"] = datetime.fromtimestamp(v["create_time"], timezone.utc).strftime("%Y-%m-%d")
    return "written" if write(rec["id"], {k: x for k, x in fields.items() if x is not None}) else "write failed"


# ── facebook ──────────────────────────────────────────────────────────────

def fb_configured():
    return bool(os.environ.get("FACEBOOK_PAGES"))


def handle_facebook(rec, url):
    from fb.main import FacebookSync
    ok = FacebookSync().process_single_facebook_post(rec)
    return "written" if ok else "not written (see log)"


# ── instagram ─────────────────────────────────────────────────────────────

def ig_configured():
    return bool(os.environ.get("META_USER_ACCESS_TOKEN") or os.environ.get("META_SYSTEM_USER_TOKEN"))


_ig = {"sync": None, "at": 0.0}
_ig_lock = threading.Lock()


def _instagram():
    """One InstagramDynamicSync with its page/account mapping built, kept 30
    minutes: building it is a dozen Graph calls, too many per post."""
    with _ig_lock:
        if _ig["sync"] is None or time.time() - _ig["at"] > 1800:
            from insta.insta_sync import InstagramDynamicSync
            s = InstagramDynamicSync()
            s.build_instagram_mapping()
            if os.environ.get("META_SYSTEM_USER_TOKEN") and os.environ.get("META_BUSINESS_ID"):
                s.add_business_instagram_accounts()
            _ig["sync"], _ig["at"] = s, time.time()
        return _ig["sync"]


def handle_instagram(rec, url):
    ok = _instagram().process_instagram_post(rec)
    return "written" if ok else "not written (see log)"


HANDLERS = {
    "youtube": (yt_configured, handle_youtube),
    "twitter": (tw_configured, handle_twitter),
    "tiktok": (tt_configured, handle_tiktok),
    "facebook": (fb_configured, handle_facebook),
    "instagram": (ig_configured, handle_instagram),
}

last = {}  # platform -> {"at", "record", "result"}; for /webhook/health


def run(platform, record_id, url):
    configured, handler = HANDLERS[platform]
    try:
        if not configured():
            result = "not configured on this service; the cron will pick it up"
        else:
            rec = get_record(record_id)
            result = handler(rec, url) if rec else "record not found"
    except Exception as e:  # a bad post must never take the API down
        log.exception("webhook: %s %s failed", platform, record_id)
        result = f"{type(e).__name__}: {str(e)[:160]}"
    last[platform] = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "record": record_id, "result": result}
    log.info("webhook: %s %s -> %s", platform, record_id, result)


@router.post("/new-post")
async def new_post(request: Request, background: BackgroundTasks):
    secret = os.environ.get("WEBHOOK_SECRET", "")
    if not secret:
        return JSONResponse({"error": "WEBHOOK_SECRET not set on this service"}, status_code=503)
    if request.headers.get("X-Webhook-Secret", "") != secret:
        return JSONResponse({"error": "invalid secret"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    record_id = (body.get("record_id") or "").strip()
    url = (body.get("post_url") or "").strip()
    if not re.fullmatch(r"rec[A-Za-z0-9]{14}", record_id) or not url:
        return JSONResponse({"error": "record_id and post_url required"}, status_code=400)
    platform = detect_platform(url)
    if not platform:
        return JSONResponse({"status": "ignored", "reason": "unknown platform"}, status_code=200)
    background.add_task(run, platform, record_id, url)
    return JSONResponse({"status": "queued", "platform": platform, "record_id": record_id}, status_code=202)


@router.get("/health")
def health():
    return {"status": "ok", "secret_set": bool(os.environ.get("WEBHOOK_SECRET")),
            "platforms": {p: c() for p, (c, _) in HANDLERS.items()}, "last": last}
