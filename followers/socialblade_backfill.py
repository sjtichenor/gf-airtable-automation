"""
One-off follower-history backfill from the Social Blade Business API.

Social Blade has tracked most accounts for years; one credit buys one
profile on one platform, daily history included. This pulls each active
channel's Instagram, TikTok, X and Facebook history once and writes it into
Follower Logs, thinned so the table does not balloon: every day for the last
90 days, weekly for the year before that, monthly beyond. Rows are tagged
"Social Blade backfill" in Notes and never overwrite existing rows.

Modes (SB_MODE):
    probe   spend ONE credit on one profile, print the response shape, stop.
    run     back-fill everything (respects SB_MAX_PROFILES / SB_ONLY).

Environment
    SOCIALBLADE_CLIENT_ID, SOCIALBLADE_TOKEN   from the developer dashboard
    SOCIALBLADE_BASE      default https://matrix.sbapis.com/b
    SB_HISTORY            default|extended|archive  (default: archive)
    SB_PLATFORMS          default instagram,tiktok,twitter,facebook
    SB_MAX_PROFILES       cap on profiles pulled this run (default 200)
    SB_ONLY               only channels whose name contains this text
"""
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta
from urllib.parse import urlparse, parse_qs

import requests

from snapshot_followers import (API, HEADERS, CHANNELS, LOGS, CHANNEL_NAME, F_PLATFORM, F_DATE, F_COUNT,
                                F_PREVIOUS, F_CHANNEL, list_all, write)

CLIENT_ID = os.environ.get("SOCIALBLADE_CLIENT_ID", "")
SB_TOKEN = os.environ.get("SOCIALBLADE_TOKEN", "")
SB_BASE = os.environ.get("SOCIALBLADE_BASE", "https://matrix.sbapis.com/b").rstrip("/")
HISTORY = os.environ.get("SB_HISTORY", "archive")
# X is gone from Social Blade (every query 404s), so it is not in the default set.
PLATFORMS = [p.strip() for p in os.environ.get("SB_PLATFORMS", "instagram,tiktok,facebook").split(",") if p.strip()]
MAX_PROFILES = int(os.environ.get("SB_MAX_PROFILES", "200"))
ONLY = [x.strip().lower() for x in os.environ.get("SB_ONLY", "").split(",") if x.strip()]  # channel names, comma-separated
FULL = os.environ.get("SB_FULL") == "1"  # keep every day instead of thinning
MIN_CREDITS = int(os.environ.get("SB_MIN_CREDITS", "30"))  # stop before the balance drops below this
F_NOTES = "Notes"
NOTE = "Social Blade backfill"

# Social Blade platform key -> (Channels URL field, Follower Logs platform label)
PLATFORM_MAP = {
    "instagram": ("IG Profile", "Instagram"),
    "tiktok": ("TikTok Profile", "TikTok"),
    "twitter": ("Twitter Profile", "X"),
    "facebook": ("Facebook Profile", "Facebook"),
    "youtube": ("YouTube Profile", "YouTube"),
}


def handle_from_url(platform, url):
    """The username Social Blade expects, pulled out of a profile URL."""
    if not url:
        return None
    u = urlparse(url.strip())
    path = [p for p in u.path.split("/") if p]
    if platform == "facebook":
        qs = parse_qs(u.query)
        if "id" in qs or (path and path[0].isdigit()):
            return None  # Social Blade wants the page's vanity name, not its numeric id
        return path[0] if path else None
    if platform == "youtube":
        if len(path) >= 2 and path[0] == "channel":
            return path[1]
        return path[0].lstrip("@") if path else None
    if not path:
        return None
    return path[0].lstrip("@")


def sb_get(platform, query, history):
    url = f"{SB_BASE}/{platform}/statistics"
    r = requests.get(url, params={"query": query, "history": history},
                     headers={"clientid": CLIENT_ID, "token": SB_TOKEN}, timeout=60)
    try:
        body = r.json()
    except ValueError:
        body = {"raw": r.text[:500]}
    return r.status_code, body


def daily_rows(body):
    """Normalise whatever Social Blade returns into [(date, followers)]."""
    data = body.get("data") or {}
    daily = data.get("daily") or []
    out = []
    for row in daily:
        d = row.get("date")
        n = row.get("followers", row.get("subs", row.get("subscribers", row.get("likes"))))
        if d and isinstance(n, (int, float)):
            out.append((d[:10], int(n)))
    out.sort()
    return out


def thin(rows, today):
    """Daily for 90 days, weekly for a year before that, monthly beyond.
    SB_FULL=1 keeps every day (fine when only a handful of accounts have
    real history)."""
    if FULL:
        return list(rows)
    keep, last_kept = [], None
    d90 = today - timedelta(days=90)
    d365 = today - timedelta(days=365)
    for d, n in rows:
        day = date.fromisoformat(d)
        if day >= d90:
            ok = True
        elif day >= d365:
            ok = last_kept is None or (day - last_kept).days >= 7
        else:
            ok = last_kept is None or (day - last_kept).days >= 28
        if ok:
            keep.append((d, n))
            last_kept = day
    return keep


_existing = None


def existing_dates(channel_id, label):
    """Dates already logged for a channel/platform. The table is read once;
    rows this run creates are added to the cache as it goes."""
    global _existing
    if _existing is None:
        _existing = {}
        for r in list_all(LOGS, **{"fields[]": [F_DATE, F_PLATFORM, F_CHANNEL]}):
            f = r["fields"]
            for cid in f.get(F_CHANNEL) or []:
                _existing.setdefault((cid, f.get(F_PLATFORM)), set()).add(f.get(F_DATE))
    return _existing.setdefault((channel_id, label), set())


def channels():
    fields = [CHANNEL_NAME, "Status"] + [v[0] for v in PLATFORM_MAP.values()]
    out = []
    for r in list_all(CHANNELS, **{"fields[]": fields}):
        f = r["fields"]
        if f.get("Status") == "Inactive":
            continue
        if ONLY and not any(o in (f.get(CHANNEL_NAME) or "").lower() for o in ONLY):
            continue
        out.append(r)
    return out


def probe():
    for ch in channels():
        for platform in PLATFORMS:
            handle = handle_from_url(platform, ch["fields"].get(PLATFORM_MAP[platform][0]))
            if not handle:
                continue
            print(f"PROBE {platform} {handle!r} for {ch['fields'].get(CHANNEL_NAME)!r}, history={HISTORY}")
            code, body = sb_get(platform, handle, HISTORY)
            print(f"HTTP {code}")
            print("top-level keys:", list(body.keys()) if isinstance(body, dict) else type(body))
            print("status:", json.dumps(body.get("status"), indent=None)[:600] if isinstance(body, dict) else "")
            print("info:", json.dumps(body.get("info"), indent=None)[:600] if isinstance(body, dict) else "")
            data = body.get("data") if isinstance(body, dict) else None
            if isinstance(data, dict):
                print("data keys:", list(data.keys()))
                print("data.general:", json.dumps(data.get("general"))[:400])
                print("data.statistics:", json.dumps(data.get("statistics"))[:600])
                daily = data.get("daily") or []
                print(f"daily rows: {len(daily)}; first: {json.dumps(daily[:2])[:400]}; last: {json.dumps(daily[-2:])[:400]}")
            else:
                print("body:", json.dumps(body)[:800])
            rows = daily_rows(body)
            print(f"parsed {len(rows)} daily points; thinned to {len(thin(rows, date.today()))}")
            return
    print("PROBE: no channel with a usable profile URL")


def run():
    today = date.today()
    pulled = created = skipped = 0
    tracked, untracked, stop = [], [], False
    for ch in channels():
        cid, name = ch["id"], ch["fields"].get(CHANNEL_NAME)
        for platform in PLATFORMS:
            if pulled >= MAX_PROFILES or stop:
                print(f"stopping: {'credit floor' if stop else f'cap of {MAX_PROFILES} profiles'} reached")
                summary(pulled, created, skipped, tracked, untracked)
                return
            url_field, label = PLATFORM_MAP[platform]
            handle = handle_from_url(platform, ch["fields"].get(url_field))
            if not handle:
                continue
            code, body = sb_get(platform, handle, HISTORY)
            pulled += 1
            ok = isinstance(body, dict) and (body.get("status") or {}).get("success", code == 200)
            rows = daily_rows(body) if ok else []
            credits = ((body.get("info") or {}).get("credits") or {}) if isinstance(body, dict) else {}
            left = credits.get("available")
            if not rows:
                print(f"  {name} / {label} ({handle}): HTTP {code}, no daily rows; status={json.dumps((body or {}).get('status'))[:200]}; credits left: {left}")
                skipped += 1
                continue
            if len(rows) <= 2:
                # Social Blade only started tracking this account now: nothing to back-fill.
                untracked.append(f"{name} / {label}")
            else:
                tracked.append(f"{name} / {label} ({rows[0][0]} → {rows[-1][0]}, {len(rows)} days)")
            if isinstance(left, int) and left < MIN_CREDITS:
                print(f"  credits left {left} < SB_MIN_CREDITS {MIN_CREDITS}; stopping after this profile")
                stop = True
            have = existing_dates(cid, label)
            kept = [(d, n) for d, n in thin(rows, today) if d not in have]
            prev = None
            batch = []
            for d, n in thin(rows, today):
                if d in have:
                    prev = n
                    continue
                batch.append({"fields": {F_PLATFORM: label, F_DATE: d, F_COUNT: n, F_PREVIOUS: prev,
                                         F_CHANNEL: [cid], F_NOTES: NOTE}})
                prev = n
            if batch:
                write(LOGS, "POST", batch)  # batches of 10 internally
                have.update(r["fields"][F_DATE] for r in batch)
            created += len(batch)
            print(f"  {name} / {label} ({handle}): {len(rows)} days from Social Blade, {len(batch)} rows written"
                  f" ({rows[0][0]} → {rows[-1][0]}); credits left: {credits.get('available', '?')}")
    summary(pulled, created, skipped, tracked, untracked)


def summary(pulled, created, skipped, tracked, untracked):
    print(f"done: {pulled} profiles pulled, {created} rows created, {skipped} profiles without data")
    print(f"had history on Social Blade ({len(tracked)}):")
    for t in tracked:
        print("   " + t)
    print(f"only tracked from today ({len(untracked)}):")
    for u in untracked:
        print("   " + u)


def main():
    if not CLIENT_ID or not SB_TOKEN:
        sys.exit("SOCIALBLADE_CLIENT_ID / SOCIALBLADE_TOKEN not set")
    mode = os.environ.get("SB_MODE", "probe")
    print(f"Social Blade backfill, mode={mode}, base={SB_BASE}, history={HISTORY}, platforms={PLATFORMS}, full={FULL}, only={ONLY or 'all'}")
    (run if mode == "run" else probe)()


if __name__ == "__main__":
    main()
