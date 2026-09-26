"""
X (Twitter) follower counts into Channels, for every channel that has a
Twitter Profile.

The inherited twitter_followers_sync.py only knows handles that appear in a
hand-maintained TWITTER_ACCOUNTS list, so ThursdAI, Steelman, US In Common
and Solana Clipped never got a count. This one asks the X API for the handle
directly: GET /2/users/by?usernames=... returns public_metrics for up to 100
handles per call, no id mapping needed. Social Blade cannot stand in for it
-- their Business API has no X endpoint.

Runs inside gf-api once a day (start_background), and as a script:

    python -m followers.x_followers [--dry-run]

Writes only the Twitter Followers field, only when the API returned a count,
and only when it differs from what is there. Channels marked Inactive are
skipped. A handle the API cannot find (renamed, suspended) is logged and
left alone rather than zeroed.

Environment
    TWITTER_BEARER_TOKEN              Spencer's X developer app
    AIRTABLE_PERSONAL_ACCESS_TOKEN
    AIRTABLE_BASE_ID                  defaults to the Good Future Media base
    X_FOLLOWERS_SECONDS               timer inside gf-api, default 86400; 0 disables
"""
import logging
import os
import re
import sys
import threading
import time
from datetime import datetime, timezone

import requests

log = logging.getLogger("x_followers")

BASE = os.environ.get("AIRTABLE_BASE_ID", "appxCYu0Tfwc6h7X7")
CHANNELS = os.environ.get("AIRTABLE_CHANNELS_TABLE_ID", "tblP8bh3crTHLVoZA")
F_NAME, F_STATUS = "fldAjaBOa54I8jKpM", "fldxFw8ogXiwLvFOa"
F_PROFILE, F_FOLLOWERS = "fldE82ljcHodseVOv", "fldmoDtZyGC2bKY6N"  # Twitter Profile, Twitter Followers
X_API = "https://api.x.com/2/users/by"


def handle_of(url):
    """'https://x.com/thursdai_pod?s=21' -> 'thursdai_pod'; None if not an X URL."""
    m = re.search(r"(?:twitter\.com|x\.com)/@?([A-Za-z0-9_]{1,15})", url or "")
    return m.group(1) if m else None


def list_channels(token):
    out, offset = [], None
    while True:
        params = {"returnFieldsByFieldId": "true", "pageSize": 100, "fields[]": [F_NAME, F_STATUS, F_PROFILE, F_FOLLOWERS]}
        if offset:
            params["offset"] = offset
        r = requests.get(f"https://api.airtable.com/v0/{BASE}/{CHANNELS}", headers={"Authorization": f"Bearer {token}"}, params=params, timeout=30)
        r.raise_for_status()
        body = r.json()
        out.extend(body.get("records", []))
        offset = body.get("offset")
        if not offset:
            return out


def fetch_counts(bearer, handles):
    """{handle_lower: followers} for the handles X knows; missing ones are absent."""
    found = {}
    for i in range(0, len(handles), 100):
        batch = handles[i:i + 100]
        r = requests.get(X_API, headers={"Authorization": f"Bearer {bearer}", "User-Agent": "gf-x-followers/1.0"},
                         params={"usernames": ",".join(batch), "user.fields": "public_metrics"}, timeout=30)
        if r.status_code != 200:
            raise RuntimeError(f"X API {r.status_code}: {r.text[:200]}")
        body = r.json()
        for u in body.get("data", []):
            found[u["username"].lower()] = (u.get("public_metrics") or {}).get("followers_count")
        for e in body.get("errors", []):
            log.warning("x followers: %s: %s", e.get("value"), e.get("detail"))
        if i + 100 < len(handles):
            time.sleep(2)
    return found


def plan(channels, counts):
    """Airtable PATCH records for channels whose count changed. Pure."""
    updates, skipped = [], []
    for c in channels:
        f = c.get("fields", {})
        st = f.get(F_STATUS)
        if (st.get("name") if isinstance(st, dict) else st) == "Inactive":
            continue
        h = handle_of(f.get(F_PROFILE))
        if not h:
            continue
        n = counts.get(h.lower())
        if n is None:
            skipped.append(f"{f.get(F_NAME)} (@{h})")
            continue
        if f.get(F_FOLLOWERS) != n:
            updates.append({"id": c["id"], "fields": {F_FOLLOWERS: n}})
    return updates, skipped


def configured():
    return bool(os.environ.get("TWITTER_BEARER_TOKEN") and os.environ.get("AIRTABLE_PERSONAL_ACCESS_TOKEN"))


def sync(dry_run=False):
    token, bearer = os.environ["AIRTABLE_PERSONAL_ACCESS_TOKEN"], os.environ["TWITTER_BEARER_TOKEN"]
    channels = list_channels(token)
    handles = sorted({handle_of(c["fields"].get(F_PROFILE)) for c in channels
                      if handle_of(c["fields"].get(F_PROFILE))
                      and ((c["fields"].get(F_STATUS) or {}).get("name") if isinstance(c["fields"].get(F_STATUS), dict) else c["fields"].get(F_STATUS)) != "Inactive"},
                     key=str.lower)
    counts = fetch_counts(bearer, handles) if handles else {}
    updates, skipped = plan(channels, counts)
    if updates and not dry_run:
        for i in range(0, len(updates), 10):
            r = requests.patch(f"https://api.airtable.com/v0/{BASE}/{CHANNELS}",
                               headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                               json={"records": updates[i:i + 10]}, timeout=60)
            if r.status_code != 200:
                raise RuntimeError(f"Airtable PATCH failed: {r.status_code} {r.text[:200]}")
            time.sleep(0.25)
    summary = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "dry_run": dry_run,
               "handles": len(handles), "found": len(counts), "updated": len(updates), "not_found": skipped}
    log.info("x followers: %s", summary)
    return summary


last = {"summary": None, "error": None}


def _loop(seconds):
    time.sleep(240)
    while True:
        try:
            last["summary"], last["error"] = sync(), None
        except Exception as e:
            last["error"] = f"{type(e).__name__}: {e}"
            log.exception("x followers failed")
        time.sleep(seconds)


def start_background():
    seconds = int(os.environ.get("X_FOLLOWERS_SECONDS", "86400"))
    if not configured():
        log.info("x followers: TWITTER_BEARER_TOKEN not set; not running")
        return
    if seconds <= 0:
        return
    threading.Thread(target=_loop, args=(seconds,), name="x-followers", daemon=True).start()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not configured():
        sys.exit("set TWITTER_BEARER_TOKEN and AIRTABLE_PERSONAL_ACCESS_TOKEN")
    s = sync(dry_run="--dry-run" in sys.argv)
    print(f"{s['handles']} handle(s), {s['found']} found, {s['updated']} updated; not found: {', '.join(s['not_found']) or '-'}")
