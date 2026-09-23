"""Data-health audit for follower counts.

The US In Common Facebook count sat frozen at 1,455 for days while the real
figure passed 9,000, and nobody noticed until a client did, because the
Instagram and TikTok numbers on the same row kept moving. This looks for
that shape of failure every day so the first person to hear about it is us.

For every active channel, on every platform it has a profile for, four
checks against Follower Logs and the Channels follower fields:

  NO ROW    nothing logged in the last `stale_days` days -- the snapshot or
            Social Blade stopped writing this account.
  FLAT      the last `flat_days` logged counts are identical -- a live
            account does not sit perfectly still; the source is repeating
            itself. Skipped under `flat_min` followers, where still is normal.
  DIVERGES  the Channels field disagrees with the latest log by more than
            `diverge_pct` -- the platform sync that writes Channels is
            broken while Social Blade is fine (the US In Common case). This
            is the one the dashboards cannot see, because they read the logs.
  NO FIELD  a profile URL exists but the Channels follower field is empty --
            nothing has ever written it.

Pure functions only; the callers own the Airtable reads.
"""
from collections import defaultdict
from datetime import date, timedelta

PLATFORMS = ("Instagram", "TikTok", "YouTube", "Facebook", "X", "Threads")

# Only the platforms the daily Social Blade backfill covers get a NO ROW
# check on their logs. X and Threads reach the logs only through the
# Channels fields, so an X count with no field is reported as NO FIELD, not
# as a missing row.
LOGGED_DAILY = {"Instagram", "TikTok", "YouTube", "Facebook"}


def audit(channels, logs, today=None, stale_days=2, flat_days=3, flat_min=100, diverge_pct=5.0, ignore=()):
    """channels: [{id, name, active, profiles: {platform: url}, counts: {platform: int|None}}]
    logs:     [{channel, platform, date (ISO), count, independent}] -- `independent`
              is True when the row came from Social Blade rather than being
              copied out of the Channels field; only those can contradict it.
    ignore:   ("Channel/Platform", "Channel/*", ...) -- known and accepted.
    Returns a list of findings, worst first, each
      {channel, platform, check, detail, severity} with severity 2 (act) or 1 (look)."""
    today = today or date.today()
    if isinstance(today, str):
        today = date.fromisoformat(today)
    muted = {m.strip().lower() for m in ignore if m and m.strip()}

    by_key = defaultdict(list)
    for row in logs:
        if row.get("channel") and row.get("platform") and row.get("date") and row.get("count") is not None:
            by_key[(row["channel"], row["platform"])].append(
                (row["date"], int(row["count"]), bool(row.get("independent"))))
    for rows in by_key.values():
        rows.sort()

    findings = []
    for ch in channels:
        if not ch.get("active", True):
            continue
        for platform in PLATFORMS:
            if not (ch.get("profiles") or {}).get(platform):
                continue
            if f"{ch['name']}/{platform}".lower() in muted or f"{ch['name']}/*".lower() in muted:
                continue
            rows = by_key.get((ch["id"], platform), [])
            field = (ch.get("counts") or {}).get(platform)
            latest = rows[-1] if rows else None
            independent = [r for r in rows if r[2]]

            if field is None:
                finding = _f(ch, platform, "NO FIELD", "profile listed, follower field never written", 1)
                if independent:
                    # Social Blade has the number even though nothing ever
                    # copied it across; a caller can fill the field from it.
                    d, n, _ = independent[-1]
                    finding.update(channel_id=ch["id"], sb_count=n, sb_date=d)
                findings.append(finding)

            if platform in LOGGED_DAILY:
                if not latest:
                    findings.append(_f(ch, platform, "NO ROW", "never logged", 2))
                    continue
                age = (today - date.fromisoformat(latest[0])).days
                if age > stale_days:
                    findings.append(_f(ch, platform, "NO ROW", f"last logged {latest[0]} ({age} days ago)", 2))
                    continue

            if latest and len(rows) >= flat_days:
                tail = [c for _, c, _ in rows[-flat_days:]]
                if len(set(tail)) == 1 and tail[0] >= flat_min:
                    findings.append(_f(ch, platform, "FLAT",
                                       f"{tail[0]:,} for the last {flat_days} logged days", 1))

            # A row copied out of the Channels field cannot disagree with it
            # in any meaningful way, so compare against the newest row that
            # Social Blade wrote itself.
            if independent and field is not None:
                d, n, _ = independent[-1]
                gap = abs(field - n) / max(n, 1) * 100
                if gap > diverge_pct and max(field, n) >= flat_min:
                    finding = _f(ch, platform, "DIVERGES",
                                 f"Channels says {field:,}, Social Blade says {n:,} on {d} "
                                 f"({gap:.0f}% apart)", 2)
                    # Enough for a caller to put Social Blade's figure into
                    # the Channels field itself.
                    finding.update(channel_id=ch["id"], sb_count=n, sb_date=d)
                    findings.append(finding)

    findings.sort(key=lambda x: (-x["severity"], x["channel"], x["platform"]))
    return findings


def _f(ch, platform, check, detail, severity):
    return {"channel": ch["name"], "platform": platform, "check": check, "detail": detail, "severity": severity}


def render(findings):
    """Plain-text report for a cron log or a Slack message."""
    if not findings:
        return "DATA HEALTH: every active account is updating."
    act = [f for f in findings if f["severity"] == 2]
    look = [f for f in findings if f["severity"] == 1]
    lines = [f"DATA HEALTH: {len(act)} to act on, {len(look)} to look at"]
    for f in act:
        lines.append(f"  !! {f['channel']} / {f['platform']}: {f['check']} - {f['detail']}")
    # A filled NO FIELD is worth its own line; the rest are grouped.
    for f in look:
        if f["check"] != "NO FIELD" or "filled" in f["detail"]:
            lines.append(f"   . {f['channel']} / {f['platform']}: {f['check']} - {f['detail']}")
    nofield = [f for f in look if f["check"] == "NO FIELD" and "filled" not in f["detail"]]
    if nofield:
        lines.append("   . NO FIELD (profile listed, follower field never written): "
                     + ", ".join(f"{f['channel']}/{f['platform']}" for f in nofield))
    return "\n".join(lines)
