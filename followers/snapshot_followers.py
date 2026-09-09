"""
Daily follower snapshot: copies today's follower counts from Channels into the
Follower Logs table, one row per channel per platform, so growth can be charted.

Makes no social-platform API calls. The existing syncs already keep the six
per-platform follower fields on Channels current; this job just records them
once a day. Idempotent: rerunning on the same day updates that day's rows
rather than adding duplicates.

Environment
    AIRTABLE_PERSONAL_ACCESS_TOKEN
    AIRTABLE_BASE_ID            defaults to the Good Future Media base
    AIRTABLE_CHANNELS_TABLE_ID  defaults to Channels
    FOLLOWER_LOGS_TABLE_ID      defaults to Follower Logs
    SNAPSHOT_DATE               optional YYYY-MM-DD override, for backfills
"""

import os
import sys
from datetime import date, datetime, timezone

import requests

TOKEN = os.environ["AIRTABLE_PERSONAL_ACCESS_TOKEN"]
BASE = os.environ.get("AIRTABLE_BASE_ID", "appxCYu0Tfwc6h7X7")
CHANNELS = os.environ.get("AIRTABLE_CHANNELS_TABLE_ID", "tblP8bh3crTHLVoZA")
LOGS = os.environ.get("FOLLOWER_LOGS_TABLE_ID", "tblsSv5OlermzmpZh")
TODAY = os.environ.get("SNAPSHOT_DATE") or date.today().isoformat()

# Channels field -> Platform label written to Follower Logs.
PLATFORM_FIELDS = {
    "IG Followers": "Instagram",
    "TikTok Followers": "TikTok",
    "Twitter Followers": "X",
    "YouTube Followers": "YouTube",
    "Facebook Followers": "Facebook",
    "Threads Followers": "Threads",
}
CHANNEL_NAME = "Social Media Account"  # the Channels primary field

# Follower Logs fields (by name; the table was built by hand in the UI).
F_PLATFORM = "Platform"
F_DATE = "Date"
F_COUNT = "Follower Count"
F_PREVIOUS = "Previous Count"
F_CHANNEL = "Social Media Account"

HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
API = f"https://api.airtable.com/v0/{BASE}"


def list_all(table, **params):
    out, offset = [], None
    while True:
        q = dict(params)
        if offset:
            q["offset"] = offset
        r = requests.get(f"{API}/{table}", headers=HEADERS, params=q, timeout=30)
        r.raise_for_status()
        body = r.json()
        out.extend(body.get("records", []))
        offset = body.get("offset")
        if not offset:
            return out


def write(table, method, records):
    """Create or update in batches of 10, the API's limit."""
    for i in range(0, len(records), 10):
        r = requests.request(
            method, f"{API}/{table}", headers=HEADERS,
            json={"records": records[i:i + 10]}, timeout=30,
        )
        if r.status_code != 200:
            sys.exit(f"Airtable {method} failed: {r.status_code} {r.text[:300]}")


def main():
    channels = list_all(CHANNELS, **{"fields[]": [CHANNEL_NAME, *PLATFORM_FIELDS]})
    print(f"{len(channels)} channel(s); snapshot date {TODAY}")

    # Latest prior row per (channel, platform) gives Previous Count; today's
    # rows, if any, get updated instead of duplicated.
    logs = list_all(LOGS, **{"fields[]": [F_PLATFORM, F_DATE, F_COUNT, F_CHANNEL]})
    latest_before, today_rows = {}, {}
    for rec in logs:
        f = rec.get("fields", {})
        links = f.get(F_CHANNEL) or []
        if not links or not f.get(F_DATE):
            continue
        key = (links[0], f.get(F_PLATFORM))
        if f[F_DATE] == TODAY:
            today_rows[key] = rec["id"]
        elif f[F_DATE] < TODAY:
            prev = latest_before.get(key)
            if prev is None or f[F_DATE] > prev[0]:
                latest_before[key] = (f[F_DATE], f.get(F_COUNT))

    creates, updates = [], []
    for ch in channels:
        f = ch.get("fields", {})
        for field, platform in PLATFORM_FIELDS.items():
            count = f.get(field)
            if count is None:
                continue  # platform not tracked for this channel
            key = (ch["id"], platform)
            prev = latest_before.get(key, (None, None))[1]
            fields = {
                F_PLATFORM: platform,
                F_DATE: TODAY,
                F_COUNT: count,
                F_CHANNEL: [ch["id"]],
            }
            if prev is not None:
                fields[F_PREVIOUS] = prev
            if key in today_rows:
                updates.append({"id": today_rows[key], "fields": fields})
            else:
                creates.append({"fields": fields})

    if creates:
        write(LOGS, "POST", creates)
    if updates:
        write(LOGS, "PATCH", updates)
    print(f"Follower Logs: {len(creates)} row(s) created, {len(updates)} updated for {TODAY}")


if __name__ == "__main__":
    main()
