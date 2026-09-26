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
from datetime import date, datetime, timedelta, timezone

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
CHANNEL_STATUS = "Status"
# Platform -> Channels profile-URL field. A profile with no count is itself a
# finding, so the audit needs to know which platforms an account is on.
PROFILE_FIELDS = {
    "Instagram": "IG Profile",
    "TikTok": "TikTok Profile",
    "X": "Twitter Profile",
    "YouTube": "YouTube Profile",
    "Facebook": "Facebook Profile",
    "Threads": "Threads Profile",
}

# Follower Logs fields (by name; the table was built by hand in the UI).
F_PLATFORM = "Platform"
F_DATE = "Date"
F_COUNT = "Follower Count"
F_PREVIOUS = "Previous Count"
F_CHANNEL = "Social Media Account"
F_NOTES = "Notes"

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
    logs = list_all(LOGS, **{"fields[]": [F_PLATFORM, F_DATE, F_COUNT, F_CHANNEL, F_NOTES]})
    latest_before, today_rows = {}, {}
    for rec in logs:
        f = rec.get("fields", {})
        links = f.get(F_CHANNEL) or []
        if not links or not f.get(F_DATE):
            continue
        key = (links[0], f.get(F_PLATFORM))
        if f[F_DATE] == TODAY:
            # Carry whether the row came from somewhere else (Notes set), so a
            # stale Channels figure cannot overwrite a better source.
            today_rows[key] = (rec["id"], bool((f.get(F_NOTES) or "").strip()))
        elif f[F_DATE] < TODAY:
            prev = latest_before.get(key)
            if prev is None or f[F_DATE] > prev[0]:
                latest_before[key] = (f[F_DATE], f.get(F_COUNT))

    creates, updates, left_alone = [], [], 0
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
                rid, from_elsewhere = today_rows[key]
                if from_elsewhere:
                    # Social Blade (or another source) already logged today for
                    # this account. Its figure is better than the Channels
                    # field, which goes stale whenever a platform sync cannot
                    # reach an account — 27 Instagram accounts today. Leave it.
                    left_alone += 1
                    continue
                updates.append({"id": rid, "fields": fields})
            else:
                creates.append({"fields": fields})

    if creates:
        write(LOGS, "POST", creates)
    if updates:
        write(LOGS, "PATCH", updates)
    print(f"Follower Logs: {len(creates)} row(s) created, {len(updates)} updated, "
          f"{left_alone} left to a better source, for {TODAY}")


# Runs after the snapshot has finished writing, never before, so a health
# complaint can never cost a day of data. Prints the report every day; when
# something needs acting on and HEALTH_ALERT is not "0", exits 3 so Render
# sends its cron-failed email -- the only push channel we have until Slack is
# wired. HEALTH_IGNORE mutes known cases: "Good Politics/Instagram;Steelman/*".
def health_check():
    import health
    fields = [CHANNEL_NAME, CHANNEL_STATUS, *PLATFORM_FIELDS, *PROFILE_FIELDS.values()]
    channels = []
    for rec in list_all(CHANNELS, **{"fields[]": fields}):
        f = rec.get("fields", {})
        status = f.get(CHANNEL_STATUS)
        status = status.get("name") if isinstance(status, dict) else status
        channels.append({
            "id": rec["id"], "name": f.get(CHANNEL_NAME) or rec["id"],
            "active": status == "Active",
            "profiles": {p: f.get(fld) for p, fld in PROFILE_FIELDS.items()},
            "counts": {p: f.get(fld) for fld, p in PLATFORM_FIELDS.items()},
        })
    since = (date.fromisoformat(TODAY) - timedelta(days=7)).isoformat()
    logs = []
    for rec in list_all(LOGS, filterByFormula=f"IS_AFTER({{{F_DATE}}}, '{since}')",
                        **{"fields[]": [F_PLATFORM, F_DATE, F_COUNT, F_CHANNEL, F_NOTES]}):
        f = rec.get("fields", {})
        links = f.get(F_CHANNEL) or []
        if links:
            logs.append({"channel": links[0], "platform": f.get(F_PLATFORM), "date": f.get(F_DATE),
                         "count": f.get(F_COUNT),
                         "independent": (f.get(F_NOTES) or "").strip().startswith("Social Blade")})
    ignore = [x for x in os.environ.get("HEALTH_IGNORE", "").replace(",", ";").split(";") if x.strip()]
    findings = health.audit(channels, logs, today=TODAY, ignore=ignore)
    fill_from_socialblade(findings)
    print("\n" + health.render(findings))
    if any(f["severity"] == 2 for f in findings) and os.environ.get("HEALTH_ALERT", "1") != "0":
        sys.exit(3)


# The Airtable interfaces read the Channels follower fields, and those are
# written only by the platform syncs -- which cannot reach every account
# (Good Politics' Instagram has no Facebook Page, so the Meta API never
# sees it). Social Blade can, and already logs the figure daily. When the
# two disagree and Social Blade's row is fresh, put its figure into the
# field so the interface stops showing a frozen number. A reachable account
# gets overwritten again by the official sync later the same day, which is
# fine: that figure is better and the gap between them is small. An empty
# field with a Social Blade row behind it is filled the same way -- ThursdAI
# and Solana Clipped had months of daily rows that nothing ever copied
# across. Muted pairs (HEALTH_IGNORE) are left alone entirely -- mute means
# do not touch.
# A filled finding drops to "look" severity: the number people see is now
# right, and the log still records that the official sync is lagging.
def fill_from_socialblade(findings):
    if os.environ.get("SB_FILL_CHANNELS", "1") == "0":
        return
    field_for = {platform: field for field, platform in PLATFORM_FIELDS.items()}
    today = date.fromisoformat(TODAY)
    updates, filled = [], []
    for f in findings:
        if f.get("check") not in ("DIVERGES", "NO FIELD") or not f.get("sb_date"):
            continue
        # Social Blade skips the odd day for smaller accounts, so accept a row
        # up to three days old: that is still far better than a field that is
        # frozen or empty, which is the only kind that reaches this point.
        if (today - date.fromisoformat(f["sb_date"])).days > 3:
            continue
        field = field_for.get(f["platform"])
        if not field:
            continue
        updates.append({"id": f["channel_id"], "fields": {field: f["sb_count"]}})
        filled.append(f)
    if not updates:
        return
    # One PATCH record per channel: Airtable rejects a batch that names the
    # same record twice, which a channel with two findings (Instagram and
    # TikTok, say) produced on 2026-09-26 and the whole fill was lost.
    merged = {}
    for u in updates:
        merged.setdefault(u["id"], {"id": u["id"], "fields": {}})["fields"].update(u["fields"])
    updates = list(merged.values())
    try:
        write(CHANNELS, "PATCH", updates)
    except SystemExit as exc:
        print(f"Social Blade fill failed, fields left as they were: {exc}")
        return
    for f in filled:
        f["severity"] = 1
        f["detail"] += f" -> Channels filled with {f['sb_count']:,} from Social Blade"
    print(f"Channels: {len(filled)} follower field(s) filled from Social Blade")


if __name__ == "__main__":
    # Social Blade first when SB_MODE is set, because for the platforms it
    # covers it beats the Channels follower fields, which freeze whenever a
    # platform sync cannot reach an account. The snapshot then fills only
    # what Social Blade did not write — X, Threads, and any account it cannot
    # see. Both always run: the backfill is wrapped so that a Social Blade
    # failure can never stop the snapshot, which is the mistake that cost
    # eleven days of YouTube and X history from 2026-09-10.
    # Three ways to run the Social Blade step, all read here so a flag left
    # on cannot keep spending credits: SB_MODE (every run, the original
    # switch); SB_ONCE=YYYY-MM-DD (only on that date -- for a one-off pull,
    # no cleanup needed); SB_MONTHLY_ONLY=<name filter> (on day SB_MONTHLY_DAY,
    # default 1, for the channels whose name contains the filter -- the VC
    # benchmark accounts' Instagram/TikTok refresh, 2026-09-26).
    today_ = date.fromisoformat(TODAY)
    scheduled = None
    if os.environ.get("SB_ONCE") == TODAY:
        scheduled = "SB_ONCE"
    elif os.environ.get("SB_MONTHLY_ONLY") and today_.day == int(os.environ.get("SB_MONTHLY_DAY", "1")):
        scheduled = "SB_MONTHLY_ONLY"
        os.environ["SB_ONLY"] = os.environ["SB_MONTHLY_ONLY"]
        os.environ.setdefault("SB_PLATFORMS", "instagram,tiktok")
        # A refresh only needs the last few weeks; the archive pull costs
        # up to three credits a profile, the default one.
        os.environ["SB_HISTORY"] = "default"
    if scheduled:
        os.environ["SB_MODE"] = "run"
        print(f"Social Blade step scheduled by {scheduled}")
    if os.environ.get("SB_MODE"):
        try:
            import socialblade_backfill
            socialblade_backfill.main()
        except Exception as exc:
            print(f"Social Blade step failed, continuing to the snapshot: {exc}")

    main()
    try:
        health_check()
    except SystemExit:
        raise
    except Exception as exc:
        print(f"Health check failed to run, snapshot itself is fine: {exc}")
