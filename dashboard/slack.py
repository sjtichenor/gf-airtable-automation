"""Send the daily digest to a Slack group DM.

Needs SLACK_BOT_TOKEN (a bot token with chat:write, im:write, mpim:write).
Recipients come from DIGEST_SLACK_USER_IDS (comma-separated) or, failing
that, from DIGEST_RECIPIENTS (comma-separated Team names) resolved through
the Team table's Slack ID field. The scheduler in routes.py calls send()
once a day at DIGEST_HOUR local time on DIGEST_DAYS (1=Mon … 7=Sun).
"""
import logging
import os
from typing import List, Optional

import requests

log = logging.getLogger("dashboard.slack")
TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
USER_IDS = [x.strip() for x in os.environ.get("DIGEST_SLACK_USER_IDS", "").split(",") if x.strip()]
RECIPIENTS = [x.strip() for x in os.environ.get("DIGEST_RECIPIENTS", "Spencer Tichenor,Chris P Madden").split(",") if x.strip()]
HOUR = int(os.environ.get("DIGEST_HOUR", "9"))
DAYS = {int(c) for c in os.environ.get("DIGEST_DAYS", "12345") if c.isdigit()}


def configured() -> bool:
    return bool(TOKEN)


def recipient_ids(snap: dict) -> List[str]:
    if USER_IDS:
        return USER_IDS
    ids = []
    for t in snap.get("team", []):
        if t.get("name") in RECIPIENTS and t.get("slack_id"):
            ids.append(t["slack_id"])
    return ids


def _call(method: str, **payload) -> dict:
    r = requests.post(f"https://slack.com/api/{method}", json=payload,
                      headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json; charset=utf-8"}, timeout=30)
    body = r.json()
    if not body.get("ok"):
        raise RuntimeError(f"Slack {method}: {body.get('error')}")
    return body


def send(snap: dict, digest: dict, channel: Optional[str] = None) -> str:
    if not TOKEN:
        raise RuntimeError("SLACK_BOT_TOKEN is not set")
    if not channel:
        ids = recipient_ids(snap)
        if not ids:
            raise RuntimeError("no recipients: set DIGEST_SLACK_USER_IDS or fill Slack ID on the Team rows named in DIGEST_RECIPIENTS")
        channel = _call("conversations.open", users=",".join(ids))["channel"]["id"]
    _call("chat.postMessage", channel=channel, text=digest["text"], blocks=digest["blocks"], unfurl_links=False)
    log.info("digest %s → %s sent to %s", digest["from"], digest["to"], channel)
    return channel
