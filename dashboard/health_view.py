"""
The follower data-health audit, run against the dashboard snapshot so its
findings show on the Team page and in the daily Slack digest, not only in a
cron log nobody reads.

The 09:00 UTC gf-follower-snapshot run does the same audit and fails the
cron (Render emails) on an "act" finding; this is the second channel for the
same facts. HEALTH_IGNORE mutes the same "Channel/Platform" pairs here, so
set it on gf-api to match the cron.
"""
import os
from datetime import date

from followers import health


def _ignore():
    return [x for x in os.environ.get("HEALTH_IGNORE", "").split(";") if x.strip()]


def findings(snap: dict):
    channels = [{"id": c["id"], "name": c["name"], "active": c.get("status") != "Inactive",
                 "profiles": c.get("profiles") or {}, "counts": c.get("followers") or {}}
                for c in (snap.get("channels") or [])]
    logs = [{"channel": r["channel"], "platform": r["platform"], "date": r["date"], "count": r["count"],
             "independent": r.get("independent", False)} for r in snap.get("followers", [])]
    return health.audit(channels, logs, today=date.today(), ignore=_ignore())


def summary(snap: dict) -> dict:
    f = findings(snap)
    act = [x for x in f if x["severity"] == 2]
    look = [x for x in f if x["severity"] == 1]
    return {"act": act, "look": look, "text": health.render(f), "checked": len(snap.get("channels") or [])}
