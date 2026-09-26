"""Airtable → in-memory snapshot for the analytics dashboard.

Everything the page needs is pulled by a background thread every
DASHBOARD_REFRESH_SECONDS (default 15 min), shaped into plain lists and
served from memory, so a page load never waits on Airtable and the token
never leaves the server. Fields are addressed by id, so renames in the base
do not break anything.
"""
import logging
import os
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

import requests

log = logging.getLogger("dashboard")

BASE = os.environ.get("AIRTABLE_BASE_ID", "appxCYu0Tfwc6h7X7")
TOKEN = os.environ.get("AIRTABLE_PERSONAL_ACCESS_TOKEN", "")
REFRESH_SECONDS = int(os.environ.get("DASHBOARD_REFRESH_SECONDS", "900"))
FAKE = os.environ.get("DASHBOARD_FAKE_DATA") == "1"
# Channels with Status "Inactive" are ideas and past experiments; keep them
# off the dashboard unless explicitly asked for.
INCLUDE_INACTIVE = os.environ.get("DASHBOARD_INCLUDE_INACTIVE") == "1"
API = "https://api.airtable.com/v0"

TABLES = {
    "channels": "tblP8bh3crTHLVoZA",
    "shows": "tblKMC5hDmmc7YsVW",
    "posts": "tblMpYJQjbb5yuKfC",
    "videos": "tblu3ur57YdUMWw1B",
    "followers": "tblsSv5OlermzmpZh",
    "team": "tblSZz4LUOn5tB4ZT",
    "clients": "tblYF8v9O280SU2oB",
    "status_logs": "tblnPcYMXNYwLkpYD",
    "demographics": "tblG4ElwziblQZM9E",
    "episodes": "tblHBczQjSraq5hWe",
}
ACTIVITY_DAYS = int(os.environ.get("DASHBOARD_ACTIVITY_DAYS", "120"))

# Field ids per table. Names in comments are what they were called on 2026-09-09.
CH = {
    "name": "fldAjaBOa54I8jKpM",      # Social Media Account
    "owned": "fldW9m4TaXzmdKByV",     # GF Owned Media
    "shows": "fld1XOCMyGXS6Zs03",     # Shows
    "status": "fldxFw8ogXiwLvFOa",    # Status
    "photo": "fldKPVewyNpA0v9KV",     # Profile Photo
    "profiles": {
        "Instagram": "fldgJcSCvj5oBxYfC",
        "TikTok": "flduqszFjmEUgObTU",
        "X": "fldE82ljcHodseVOv",
        "YouTube": "flddjvnl0wmq6s5kq",
        "Facebook": "fldSXoSMWJjU4v88i",
        "Threads": "fldNB2Syl7xOzFKpQ",
        "LinkedIn": "fldOfir5CjJwYi0NW",
    },
    "followers": {
        "Instagram": "fldpWdSqp25p7zu7F",
        "TikTok": "fldeEwn5LW38z8Txj",
        "X": "fldmoDtZyGC2bKY6N",
        "YouTube": "fldIX35NrSzWGzzrh",
        "Facebook": "fldwjZPScuJNn5r7E",
        "Threads": "fldjHXFP7sEgYWHKe",
    },
}
SH = {
    "name": "fldk6cIUZZTRMvY1V",          # Show Name
    "relationship": "fldrtfrwG8XFqtfCr",  # Relationship
    "client": "fldaMsJNvRpddhUIr",        # Client Account
    "logo": "fldZZsHZ4bliqgLmh",          # Podcast Logo
    "default_miner": "fldE0FjzekRf7Kh2u",  # Default Miner (link to Team) -- who usually mines it
}
PO = {
    "url": "fldJBwCXb4Hu8DOGp",       # Link to Post
    "hook": "fldO0NTyCbZ6515Mp",      # Hook
    "views": "fld1oP7XcjS1NQonH",     # Views
    "likes": "fldhm6tWcllj1dKbE",     # Likes
    "comments": "fld4LdxcL0Jy990Lk",  # Comments
    "replays": "fldOGmgENcHmxfYxc",   # Replays
    "reach": "fldZwpI0hednwh4Nm",     # Reach
    "date": "fldgHe4gUeVJw2jFg",      # Date Posted
    "video": "fldSlfO9SCRhOFi4k",     # Video
    "poster": "fldbNWm270HiSzZ9p",    # Posted By (collaborator)
    "channel": "fldPyqtkStu3H4lGQ",   # Social Media Account
    "network": "fldSozXACESabN5H5",   # Social Network (formula)
    "created": "fldlRS5b7W2H8OZsy",   # Date Created
}
VI = {
    "title": "fldgMXwbozJdOUjiu",     # Video Title
    "show": "fldKDD0we1AXdTWHn",      # Show (formula)
    "editor": "fldlayy9dEaBTPhFh",    # Editor (link to Team)
    "client": "fldSqPARmtwxe9m15",    # Client Account
    "type": "fldj5fXyUHW6XfSfh",      # Video Type
    "speaker": "fldsuqjVoDQPqXtlD",   # Speaker
    "director": "fldcoMQPBbLPHE2ag",  # Director (link to Team)
    "miner": "fld8kyGVFfsFpJHIi",     # Miner (link to Team)
    "status": "fldNytSQ6ccYRUt5O",    # Video Status
    "status_since": "fld4n1PD2zzV3V6p9",  # Status Last Modified
    "created": "fldOqa4OOrnkgTMxx",   # Date Created
    "created_by": "fldO1RhayZxcsWvRH",  # Created by
    "tier": "fldSdpynhbuP9xGQB",      # Editing Tier Level
    "started": "fldgCet8UIFFyReqz",   # Date Started Editing
    "finished": "fldlyyfQG6JQ6Mp7U",  # Date Finished Editing
    "episode": "flda34XvSlQaFXapj",   # Full Episode (link)
    "views": "fldBTytz2Zd8oUOKl",     # Views rollup (SUM across its posts)
    "post_count": "fld3JS6ACIpCHGlYf",  # how many posts that video became
}
SL = {
    "status": "fld0AbS8C4hW6idfb",    # Status
    "start": "fld6ucsroLEgZFPwX",     # Start Time (created time)
    "end": "fldUJ9jBmQGiMpG2o",       # End Time
    "video": "fldB2x96IZjfufPfQ",     # Linked Video
}
FL = {
    "channel": "fldlUAVQLVmNL7X5m",   # Social Media Account
    "platform": "fldpIeuj3nur9A393",  # Platform
    "date": "fld1JmpcbEVXEL3f5",      # Date
    "count": "flddzOdphxZnvDKDZ",     # Follower Count
    "prev": "fldT0GzajOXGarc4T",      # Previous Count
    "notes": "fldzZQzMg6XvKlltN",     # Notes ("Social Blade backfill" marks an independent row)
}
DM = {
    "channel": "flddkaLHNlpAowbny",   # Social Media Account
    "platform": "fldmZNYRKF2s6hK1R",  # Platform
    "dimension": "fldeWhYpXmKBtxrpn", # Dimension
    "segment": "fldwi2ZU45kYJMgjG",   # Segment
    "followers": "fld4AtOLwZ6YZTrye", # Followers
    "week": "fldk2CxcRNWbFqMnX",      # Week
}
TE = {
    "name": "fldbUkybFQu3SyAFI",      # Name (formula)
    "user": "fldoDCZTsGZotYLqH",      # User (collaborator)
    "roles": "fldhq8V4usi9Tk50N",     # Role
    "team": "fldTsIm5kvpl4dnci",      # Team
    "status": "fldw41MwIUcikCimz",    # Employment Status
    "slack_id": "fldinJYPc2nTSa7KC",  # Slack ID
    "photo": "fldEw903S1b9Bi6aC",     # Photo
    "start": "fldlq4ZRfCkdkKsF6",     # Start Date
    "bootcamp": "flddA3zwYMTfOhU9z",  # Bootcamp Class
}
# Full Episodes, for the mining board. Only what a miner needs to pick an
# episode and play it; nothing here reaches a client page (client_view builds
# its own dict and never includes episodes).
EP = {
    "title": "fldsKkmKBls8DjWBz",       # Episode Title
    "air": "fldASlNq1KPv6DlQp",         # Air Date
    "number": "fldaMFu54hu4MUGrj",      # Episode Number
    "show": "fldTdRD3q55c2BvNG",        # Show (link)
    "page": "fldpUJ5JxUCv0MtXW",        # Episode Page
    "yt": "fldrzQAp2FQlD5wNe",          # YouTube Link
    "length": "fld4Pl9XjwPnV2l2Q",      # Episode Length (seconds)
    "clips": "fldD9QN5gARZfGKVS",       # # of Clips (count)
    "clip_views": "fldpQZrvpsFakqUyU",  # Total Clip Views (rollup)
    "miner": "fld4DPB0678mdBLiD",       # Miner (link to Team)
    "status": "fldzKFuWnEoZ5yLgw",      # Mining Status
    "claimed": "fldLFYgneqraALI1V",     # Claimed At
    "priority": "fld6rh4X1MyDhfyv0",    # Mining Priority (formula, "1 - Highest" .. "5 - Don't Mine")
    "mineable": "fldojTMh0P9G89Ox1",    # Mineable? (formula, "Yes"/"No")
    "hidden": "fld1SWYVBIN9ouRFr",      # Hide from Mining Board (checkbox; execs' "not worth it")
    "description": "fldAcIGEnEZ8z5DxQ", # Episode Description
    "guest": "fldb0D8nFJJZYZ7yG",       # Guest (detected) (aiText)
    "art": "fldSGEgzNTZAME4IE",         # Episode Art
    "art_sq": "fldVVebI3fb19x4fq",      # Episode Art (Square)
    "notes": "fldlP68wXyCcN0FSJ",       # Mining Notes
    "target": "fld1YFQOGxTnwDjms",      # Target Clips
    "created": "fldIti64r0qOuIYqp",     # Date Created
}
MINING_STATUS = ("Available", "Claimed", "Mining", "Mined", "Skipped")

CL = {"name": "fldGWgYaByXkGtc3Y",    # Client Account Name
      "logo": "fldnTGv016lmdGlmg"}  # Logo attachment (9 of 34 clients had one, 2026-09-21)

PLATFORM_ALIASES = {"Twitter": "X", "twitter": "X", "x": "X"}


def norm_platform(value: Optional[str]) -> str:
    value = (value or "").strip()
    return PLATFORM_ALIASES.get(value, value) or "Other"


# ── Airtable ─────────────────────────────────────────────────────────────

class _Pacer:
    """Keeps every thread together under Airtable's 5 requests/second per
    base. Tables are fetched in parallel; pages within a table are
    sequential (offsets), so this is what bounds the build."""

    def __init__(self, per_second: float = 4.5):
        self.interval = 1.0 / per_second
        self.next_at = 0.0
        self.lock = threading.Lock()

    def wait(self) -> None:
        with self.lock:
            now = time.monotonic()
            at = max(now, self.next_at)
            self.next_at = at + self.interval
        if at > now:
            time.sleep(at - now)


_pacer = _Pacer()


def fetch_table(table: str, field_ids: List[str], formula: Optional[str] = None) -> List[dict]:
    """All records of a table, fields keyed by id. Paces itself under the
    5 req/s base limit and waits out a 429. `formula` is an Airtable
    filterByFormula (which has to use field *names*)."""
    url = f"{API}/{BASE}/{table}"
    params = [("returnFieldsByFieldId", "true"), ("pageSize", "100")]
    params += [("fields[]", f) for f in field_ids]
    if formula:
        params.append(("filterByFormula", formula))
    headers = {"Authorization": f"Bearer {TOKEN}"}
    out: List[dict] = []
    offset = None
    while True:
        q = list(params) + ([("offset", offset)] if offset else [])
        _pacer.wait()
        resp = requests.get(url, params=q, headers=headers, timeout=60)
        if resp.status_code == 429:
            log.warning("Airtable 429 on %s; sleeping 30s", table)
            time.sleep(30)
            continue
        resp.raise_for_status()
        body = resp.json()
        out.extend(body.get("records", []))
        offset = body.get("offset")
        if not offset:
            return out


def _flatten(field_ids) -> List[str]:
    ids: List[str] = []
    for v in field_ids:
        ids.extend(v.values() if isinstance(v, dict) else [v])
    return ids


def _first(v):
    return v[0] if isinstance(v, list) and v else (None if isinstance(v, list) else v)


def _thumb(att):
    if isinstance(att, list) and att:
        t = att[0].get("thumbnails", {}).get("small") or att[0].get("thumbnails", {}).get("large")
        return (t or att[0]).get("url")
    return None


def _thumb_large(att):
    """Card-sized art: Airtable's 'large' thumbnail (~512px), never the full file."""
    if isinstance(att, list) and att:
        t = att[0].get("thumbnails", {}).get("large") or att[0].get("thumbnails", {}).get("small")
        return (t or att[0]).get("url")
    return None


_YT = re.compile(r"(?:[?&]v=|youtu\.be/|/shorts/|/live/|/embed/)([A-Za-z0-9_-]{11})")


def youtube_id(url):
    m = _YT.search(url or "")
    return m.group(1) if m else None


# ── shaping ──────────────────────────────────────────────────────────────

def shape_episode(f: dict, rid: str, shows: dict, team: dict) -> dict:
    st = f.get(EP["status"])
    guest = f.get(EP["guest"])
    if isinstance(guest, dict):  # aiText: {"state": ..., "value": ...}
        guest = guest.get("value")
    show_id = _first(f.get(EP["show"]))
    return {
        "id": rid,
        "title": f.get(EP["title"]) or "",
        "air": f.get(EP["air"]),
        "number": f.get(EP["number"]),
        "show": (shows.get(show_id) or {}).get("name") if show_id else None,
        "show_id": show_id,
        "client": (shows.get(show_id) or {}).get("relationship") == "Client",
        "suggested_id": (shows.get(show_id) or {}).get("default_miner_id"),
        "suggested": (shows.get(show_id) or {}).get("default_miner"),
        "page": f.get(EP["page"]),
        "yt": f.get(EP["yt"]),
        "yt_id": youtube_id(f.get(EP["yt"])),
        "length": f.get(EP["length"]),
        "clips": f.get(EP["clips"]) or 0,
        "clip_views": f.get(EP["clip_views"]) or 0,
        "miner": team.get(_first(f.get(EP["miner"])) or "", None),
        "miner_id": _first(f.get(EP["miner"])),
        "status": st.get("name") if isinstance(st, dict) else st,
        "claimed": f.get(EP["claimed"]),
        "priority": f.get(EP["priority"]) or "",
        "mineable": f.get(EP["mineable"]) == "Yes",
        "hidden": bool(f.get(EP["hidden"])),
        "description": (f.get(EP["description"]) or "").strip()[:600],
        "guest": (guest or "").strip(),
        "art": _thumb_large(f.get(EP["art_sq"]) or f.get(EP["art"])),
        "notes": f.get(EP["notes"]) or "",
        "target": f.get(EP["target"]),
        "created": f.get(EP["created"]),
    }


def build_snapshot() -> dict:
    if FAKE:
        return fake_snapshot()
    if not TOKEN:
        raise RuntimeError("AIRTABLE_PERSONAL_ACCESS_TOKEN is not set")

    # Every table pull is independent; run them together under the pacer.
    formula = ("OR(IS_AFTER({Start Time}, DATEADD(NOW(), -%d, 'days')), {End Time} = BLANK())" % ACTIVITY_DAYS)
    jobs = {
        "team": (TABLES["team"], _flatten(TE.values()), None),
        "clients": (TABLES["clients"], _flatten(CL.values()), None),
        "shows": (TABLES["shows"], _flatten(SH.values()), None),
        "channels": (TABLES["channels"], _flatten([CH["name"], CH["owned"], CH["shows"], CH["status"], CH["photo"], CH["profiles"], CH["followers"]]), None),
        "videos": (TABLES["videos"], _flatten(VI.values()), None),
        "posts": (TABLES["posts"], _flatten(PO.values()), None),
        "followers": (TABLES["followers"], _flatten(FL.values()), None),
        "status_logs": (TABLES["status_logs"], _flatten(SL.values()), formula),
        "demographics": (TABLES["demographics"], _flatten(DM.values()), None),
        "episodes": (TABLES["episodes"], _flatten(EP.values()), None),
    }
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        futures = {name: pool.submit(fetch_table, *args) for name, args in jobs.items()}
        raw = {name: f.result() for name, f in futures.items()}

    team_rows = []
    for r in raw["team"]:
        f = r["fields"]
        user = f.get(TE["user"]) or {}
        st = f.get(TE["status"])
        tm = f.get(TE["team"])
        bc = f.get(TE["bootcamp"])
        team_rows.append({
            "bootcamp_class": bc.get("name") if isinstance(bc, dict) else bc,
            "id": r["id"],
            "name": f.get(TE["name"]) or user.get("name") or "?",
            "email": user.get("email"),
            "user_id": user.get("id"),
            "roles": [x.get("name") if isinstance(x, dict) else x for x in (f.get(TE["roles"]) or [])],
            "team": tm.get("name") if isinstance(tm, dict) else tm,
            "active": (st.get("name") if isinstance(st, dict) else st) != "Inactive",
            "slack_id": f.get(TE["slack_id"]),
            "photo": _thumb(f.get(TE["photo"])),
            "start": f.get(TE["start"]),
        })
    team = {t["id"]: t["name"] for t in team_rows}
    by_user = {t["user_id"]: t["name"] for t in team_rows if t["user_id"]}
    by_email = {t["email"]: t["name"] for t in team_rows if t["email"]}

    def collab_name(c):
        if not isinstance(c, dict):
            return None
        return by_user.get(c.get("id")) or by_email.get(c.get("email")) or c.get("name")
    clients = {r["id"]: (r["fields"].get(CL["name"]) or "?") for r in raw["clients"]}
    # Client logos keyed by name, so any kind of client dashboard can wear its
    # own mark. Airtable attachment URLs expire, but the snapshot is rebuilt
    # every 15 minutes, well inside that window.
    client_logos = {}
    for r in raw["clients"]:
        nm, att = r["fields"].get(CL["name"]), r["fields"].get(CL["logo"])
        if nm and att:
            client_logos[nm.strip().lower()] = _thumb(att)

    shows = {}
    for r in raw["shows"]:
        f = r["fields"]
        rel = f.get(SH["relationship"])
        shows[r["id"]] = {
            "id": r["id"],
            "name": f.get(SH["name"]) or "?",
            "relationship": rel.get("name") if isinstance(rel, dict) else rel,
            "client": clients.get(_first(f.get(SH["client"])) or "", None),
            "logo": _thumb(f.get(SH["logo"])),
            # Client shows are assigned, not grabbed: the board keeps them in
            # their own section and suggests the show's usual miner.
            "default_miner_id": _first(f.get(SH["default_miner"])),
            "default_miner": team.get(_first(f.get(SH["default_miner"])) or "", None),
        }

    channels = {}
    for r in raw["channels"]:
        f = r["fields"]
        status = f.get(CH["status"])
        status_name = status.get("name") if isinstance(status, dict) else status
        if status_name == "Inactive" and not INCLUDE_INACTIVE:
            continue
        channels[r["id"]] = {
            "id": r["id"],
            "name": f.get(CH["name"]) or "?",
            "owned": bool(f.get(CH["owned"])),
            "shows": [s for s in (f.get(CH["shows"]) or []) if s in shows],
            "status": status.get("name") if isinstance(status, dict) else status,
            "photo": _thumb(f.get(CH["photo"])),
            "profiles": {p: f.get(fid) for p, fid in CH["profiles"].items() if f.get(fid)},
            "followers": {p: f.get(fid) for p, fid in CH["followers"].items() if f.get(fid) is not None},
        }

    # Episode titles, so a video can say which episode it was cut from. Many
    # clips come from a YouTube segment or a client's source file rather than
    # a logged episode, and those simply have none.
    episode_title = {r["id"]: (r["fields"].get(EP["title"]) or "") for r in raw["episodes"]}

    videos = {}
    for r in raw["videos"]:
        f = r["fields"]
        vtype = f.get(VI["type"]); vstat = f.get(VI["status"]); tier = f.get(VI["tier"])
        videos[r["id"]] = {
            "id": r["id"],
            "title": f.get(VI["title"]) or "",
            "show": f.get(VI["show"]) or "",
            "episode": episode_title.get(_first(f.get(VI["episode"])) or "", "") or None,
            "editor": team.get(_first(f.get(VI["editor"])) or "", None),
            "director": team.get(_first(f.get(VI["director"])) or "", None),
            "miner": team.get(_first(f.get(VI["miner"])) or "", None),
            "client": clients.get(_first(f.get(VI["client"])) or "", None),
            "type": vtype.get("name") if isinstance(vtype, dict) else vtype,
            "tier": tier.get("name") if isinstance(tier, dict) else tier,
            "status": vstat.get("name") if isinstance(vstat, dict) else vstat,
            "status_since": f.get(VI["status_since"]),
            "created": f.get(VI["created"]),
            "created_by": collab_name(f.get(VI["created_by"])),
            "started": f.get(VI["started"]),
            "finished": f.get(VI["finished"]),
            "speaker": f.get(VI["speaker"]) or "",
            "views": f.get(VI["views"]),
            "posts": f.get(VI["post_count"]),
        }

    posts = []
    for r in raw["posts"]:
        f = r["fields"]
        vid = videos.get(_first(f.get(PO["video"])) or "", {})
        ch = _first(f.get(PO["channel"]))
        if ch and ch not in channels:
            continue  # posted to an inactive channel
        posts.append({
            "id": r["id"],
            "url": f.get(PO["url"]),
            "platform": norm_platform(f.get(PO["network"])),
            "channel": ch if ch in channels else None,
            # Which video this post came from. A video has no account of its
            # own, so this is the only honest way to say which accounts it
            # went out on.
            "video": _first(f.get(PO["video"])) or None,
            "show": vid.get("show") or "",
            "editor": vid.get("editor"),
            "client": vid.get("client"),
            "title": vid.get("title") or "",
            "hook": f.get(PO["hook"]) or "",
            "type": vid.get("type"),
            # Date Posted is filled by the platform syncs; LinkedIn has no sync,
            # so fall back to the day the post was logged (posts are logged
            # when they go out) and say so.
            "date": f.get(PO["date"]) or (f.get(PO["created"]) or "")[:10] or None,
            "date_estimated": not f.get(PO["date"]),
            "created": (f.get(PO["created"]) or "")[:10] or None,
            "created_at": f.get(PO["created"]),
            "poster": collab_name(f.get(PO["poster"])),
            "views": f.get(PO["views"]),
            "likes": f.get(PO["likes"]),
            "comments": f.get(PO["comments"]),
            "replays": f.get(PO["replays"]),
            "reach": f.get(PO["reach"]),
        })

    followers = []
    for r in raw["followers"]:
        f = r["fields"]
        ch = _first(f.get(FL["channel"]))
        if not ch or ch not in channels or f.get(FL["count"]) is None or not f.get(FL["date"]):
            continue
        followers.append({
            "channel": ch,
            "platform": norm_platform(f.get(FL["platform"])),
            "date": f.get(FL["date"]),
            "count": f.get(FL["count"]),
            "prev": f.get(FL["prev"]),
            # Rows Social Blade wrote can contradict the Channels field; rows
            # the snapshot copied out of that field cannot. The health audit
            # needs to know which is which.
            "independent": (f.get(FL["notes"]) or "").startswith("Social Blade"),
        })

    # Status history: only the recent window plus anything still open. The
    # table is 25k+ rows and growing; filterByFormula needs field names.
    status_logs = []
    for r in raw["status_logs"]:
        f = r["fields"]
        vid = _first(f.get(SL["video"]))
        st = f.get(SL["status"])
        if not vid or not st:
            continue
        status_logs.append({
            "video": vid,
            "status": st.get("name") if isinstance(st, dict) else st,
            "start": f.get(SL["start"]),
            "end": f.get(SL["end"]),
        })

    # Shows worth listing: linked to an active channel, or named on a kept post.
    used_show_ids = {sid for c in channels.values() for sid in c["shows"]}
    used_show_names = {p["show"] for p in posts if p["show"]}
    shows = {sid: sh for sid, sh in shows.items() if sid in used_show_ids or sh["name"] in used_show_names}
    for c in channels.values():
        c["shows"] = [sid for sid in c["shows"] if sid in shows]

    # Audience demographics: keep each account/platform's latest week only.
    latest: Dict[tuple, str] = {}
    demo_rows = []
    for r in raw["demographics"]:
        f = r["fields"]
        ch = _first(f.get(DM["channel"]))
        wk = f.get(DM["week"])
        if not ch or ch not in channels or not wk:
            continue
        plat = f.get(DM["platform"]); dim = f.get(DM["dimension"])
        plat = plat.get("name") if isinstance(plat, dict) else plat
        dim = dim.get("name") if isinstance(dim, dict) else dim
        demo_rows.append({"channel": ch, "platform": plat, "dimension": dim, "segment": f.get(DM["segment"]),
                          "followers": f.get(DM["followers"]) or 0, "week": wk})
        key = (ch, plat)
        if wk > latest.get(key, ""):
            latest[key] = wk
    demographics = [d for d in demo_rows if latest.get((d["channel"], d["platform"])) == d["week"]]

    episodes = [shape_episode(r["fields"], r["id"], shows, team) for r in raw["episodes"]]

    return {
        "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "episodes": episodes,
        "shows": list(shows.values()),
        "channels": list(channels.values()),
        "demographics": demographics,
        "team": team_rows,
        "videos": list(videos.values()),
        "status_logs": status_logs,
        "posts": posts,
        "followers": followers,
        "client_logos": client_logos,
    }


# ── fake data for local layout work (DASHBOARD_FAKE_DATA=1) ──────────────

def fake_episodes(rnd, shows, team_rows):
    """Enough shape to lay the mining board out: a spread of priorities,
    a few taken, a few already clipped, half with a YouTube id."""
    from datetime import date, timedelta
    prios = ["1 - Highest", "2 - High", "3 - Medium", "4 - Low", "5 - Don't Mine"]
    guests = ["Sam Altman", "Chamath Palihapitiya", "Bill Gurley", "Anatoly Yakovenko", "", "Kara Swisher", "Dylan Patel", ""]
    people = [t for t in team_rows if t.get("active")]
    out = []
    for n in range(140):
        sh = shows[n % len(shows)]
        aired = date(2026, 9, 23) - timedelta(days=int(rnd.expovariate(1 / 25)))
        taken = rnd.random() < 0.08
        who = rnd.choice(people) if taken and people else None
        clips = rnd.choice([0, 0, 0, 0, 3, 7, 12])
        out.append({
            "id": f"ep{n}", "title": f"Episode {n}: {rnd.choice(['The bond market is warning us', 'Why nobody trusts the news', 'AI bears are asking the wrong question', 'Tokenized funds could replace ETFs', 'What a Chicago winter does to everybody'])}",
            "air": aired.isoformat(), "number": 300 - n, "show": sh["name"], "show_id": sh["id"],
            "client": sh.get("relationship") == "Client",
            "suggested_id": (people[n % len(people)]["id"] if people and sh.get("relationship") == "Client" and n % 3 else None),
            "suggested": (people[n % len(people)]["name"] if people and sh.get("relationship") == "Client" and n % 3 else None),
            "page": "https://example.com/episode", "yt": "https://www.youtube.com/watch?v=dQw4w9WgXcQ" if n % 2 else None,
            "yt_id": "dQw4w9WgXcQ" if n % 2 else None, "length": rnd.randint(1500, 7200),
            "clips": clips, "clip_views": clips * rnd.randint(2000, 90000),
            "miner": who["name"] if who else None, "miner_id": who["id"] if who else None,
            "status": rnd.choice(["Claimed", "Mining"]) if who else None,
            "claimed": (aired + timedelta(days=1)).isoformat() + "T15:00:00Z" if who else None,
            "priority": prios[min(4, int(rnd.expovariate(1 / 1.6)))], "mineable": True, "hidden": n % 23 == 0,
            "description": "A fake description long enough to wrap onto a couple of lines so the card layout can be judged honestly. " * 2,
            "guest": rnd.choice(guests), "art": None, "notes": "", "target": rnd.choice([None, 5, 8]), "created": aired.isoformat(),
        })
    return out


def fake_snapshot() -> dict:
    rnd = random.Random(7)
    show_names = ["All-In", "The Techno Optimist", "Good Politics", "20VC", "Solana", "ThursdAI"]
    shows = [{"id": f"show{i}", "name": n, "relationship": rnd.choice(["Client", "Owned"]), "client": None, "logo": None, "default_miner_id": None, "default_miner": None}
             for i, n in enumerate(show_names)]
    platforms = ["Instagram", "TikTok", "YouTube", "X", "Facebook", "Threads"]
    channels = []
    for i, s in enumerate(shows):
        for j in range(2):
            channels.append({
                "id": f"ch{i}{j}", "name": f"{s['name']}{' Clips' if j else ''}", "owned": s["relationship"] == "Owned",
                "shows": [s["id"]], "status": "Active", "photo": None, "profiles": {},
                "followers": {p: rnd.randint(500, 90000) for p in platforms[:4]},
            })
    editors = ["Ana", "Ben", "Chloe", "Dev", "Eli"]
    directors = ["Maya", "Theo"]
    posters = ["Jay", "Kim", "Lee"]
    recruits = ["Pam", "Vitor"]
    editors_all = editors + recruits
    team_rows = ([{"id": f"t{n}", "name": n, "email": None, "user_id": None, "roles": ["Editor"], "team": "Video Production", "active": True, "slack_id": None, "photo": None, "start": None, "bootcamp_class": "Class #3" if n == "Eli" else None} for n in editors]
                 + [{"id": f"t{n}", "name": n, "email": None, "user_id": None, "roles": ["Bootcamp Recruit"], "team": "Video Production", "active": True, "slack_id": None, "photo": None, "start": None, "bootcamp_class": "Class #5"} for n in recruits]
                 + [{"id": f"t{n}", "name": n, "email": None, "user_id": None, "roles": ["Director"], "team": "Video Production", "active": True, "slack_id": None, "photo": None, "start": None} for n in directors]
                 + [{"id": f"t{n}", "name": n, "email": None, "user_id": None, "roles": ["Social Media Manager"], "team": "Video Production", "active": True, "slack_id": None, "photo": None, "start": None} for n in posters])
    today = date.today()
    followers = []
    for ch in channels:
        for p, base in ch["followers"].items():
            v = base * 0.6
            for d in range(365, -1, -1):
                v += rnd.uniform(-0.002, 0.006) * v
                day = today - timedelta(days=d)
                followers.append({"channel": ch["id"], "platform": p, "date": day.isoformat(),
                                  "count": int(v), "prev": int(v)})
    # videos walk through the pipeline; each transition is a status log
    videos, status_logs = [], []
    chain = ["Up For Grabs", "Assigned", "Editing", "Internal Review", "Ready to Post", "Video Shipped"]
    now = datetime.utcnow()
    for n in range(900):
        ed = rnd.choice(editors_all); di = rnd.choice(directors)
        t0 = now - timedelta(days=rnd.uniform(0, 130), hours=rnd.uniform(8, 20))
        if ed == "Dev" and (now - t0).days < 6:  # Dev is on holiday this week
            t0 -= timedelta(days=7)
        steps = min(len(chain), 1 + int(rnd.expovariate(1 / 4)))
        t = t0; hist = []
        for i in range(steps):
            st = chain[i]
            if st == "Video Shipped":
                hist.append((st, t, None)); break
            dur = {"Up For Grabs": rnd.uniform(2, 60), "Assigned": rnd.uniform(1, 30), "Editing": rnd.uniform(3, 50),
                   "Internal Review": rnd.uniform(1, 30), "Ready to Post": rnd.uniform(2, 40)}[st]
            end = t + timedelta(hours=dur)
            if i == steps - 1 or end > now:
                hist.append((st, t, None)); break
            hist.append((st, t, end))
            if st == "Internal Review" and rnd.random() < 0.25:
                r_end = end + timedelta(hours=rnd.uniform(2, 30))
                if r_end > now: hist.append(("Needs More Edits", end, None)); break
                hist.append(("Needs More Edits", end, r_end)); end = r_end
                e2 = end + timedelta(hours=rnd.uniform(1, 20))
                if e2 > now: hist.append(("Editing", end, None)); break
                hist.append(("Editing", end, e2)); end = e2
            t = end
        cur = hist[-1][0]
        videos.append({"id": f"v{n}", "title": f"Clip {n}: something someone said", "show": show_names[n % 6], "editor": ed, "director": di,
                       "episode": (f"Episode {300 - n // 3}: " + rnd.choice(["The bond market is warning us", "Why nobody trusts the news", "AI bears are asking the wrong question"])) if n % 5 else None,
                       "miner": rnd.choice(editors), "client": None, "type": "Clip", "tier": "1 - Basic", "status": cur,
                       "status_since": hist[-1][1].isoformat(timespec="seconds") + "Z", "created": t0.isoformat(timespec="seconds") + "Z",
                       "created_by": rnd.choice(editors), "started": None,
                       "finished": (t.date() - timedelta(days=rnd.randint(0, 400))).isoformat() if cur in ("Video Shipped", "Ready to Post") or n % 3 == 0 else None, "speaker": "",
                       "views": int(rnd.lognormvariate(9, 1.4)), "posts": rnd.randint(1, 5)})
        for st, a, b in hist:
            status_logs.append({"video": f"v{n}", "status": st, "start": a.isoformat(timespec="seconds") + "Z", "end": b.isoformat(timespec="seconds") + "Z" if b else None})
    posts = []
    for n in range(3000):
        ch = rnd.choice(channels)
        p = rnd.choice(list(ch["followers"].keys()))
        day = today - timedelta(days=int(rnd.expovariate(1 / 120)) % 400)
        views = int(rnd.lognormvariate(8, 1.3))
        # Point the post at a video of the same show, so the video-to-account
        # mapping the page builds from posts has something real to chew on.
        si = int(ch["id"][2])
        posts.append({
            "id": f"post{n}", "url": "https://example.com/p", "platform": p, "channel": ch["id"],
            "video": f"v{(n % 150) * 6 + si}",
            "show": show_names[si], "editor": rnd.choice(editors), "client": None,
            "title": f"Clip {n}: something someone said", "hook": "", "type": "Clip",
            "date": day.isoformat(), "created": day.isoformat(),
            "created_at": f"{day.isoformat()}T{rnd.randint(13, 23):02d}:{rnd.randint(0, 59):02d}:00.000Z", "poster": rnd.choice(posters),
            "views": views, "likes": int(views * rnd.uniform(0.01, 0.08)),
            "comments": int(views * rnd.uniform(0, 0.004)), "replays": None, "reach": None,
        })
    demographics = []
    for ch in channels:
        if "Instagram" not in ch["followers"]:
            continue
        total = ch["followers"]["Instagram"]
        for seg, share in [("18-24", .22), ("25-34", .38), ("35-44", .22), ("45-54", .11), ("55-64", .05), ("65+", .02)]:
            demographics.append({"channel": ch["id"], "platform": "Instagram", "dimension": "Age", "segment": seg, "followers": int(total * share * rnd.uniform(.8, 1.2)), "week": today.isoformat()})
        for seg, share in [("Men", .64), ("Women", .34), ("Unknown", .02)]:
            demographics.append({"channel": ch["id"], "platform": "Instagram", "dimension": "Gender", "segment": seg, "followers": int(total * share), "week": today.isoformat()})
        for seg, share in [("US", .48), ("GB", .09), ("CA", .07), ("IN", .06), ("AU", .04), ("DE", .03), ("BR", .03)]:
            demographics.append({"channel": ch["id"], "platform": "Instagram", "dimension": "Country", "segment": seg, "followers": int(total * share * rnd.uniform(.7, 1.3)), "week": today.isoformat()})
        for seg, share in [("New York, New York", .08), ("Los Angeles, California", .06), ("London, England", .05), ("San Francisco, California", .04), ("Toronto, Ontario", .03), ("Chicago, Illinois", .02)]:
            demographics.append({"channel": ch["id"], "platform": "Instagram", "dimension": "City", "segment": seg, "followers": int(total * share * rnd.uniform(.7, 1.3)), "week": today.isoformat()})
    return {"generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "shows": shows, "channels": channels, "demographics": demographics, "team": team_rows, "videos": videos, "status_logs": status_logs,
            "episodes": fake_episodes(rnd, shows, team_rows),
            "posts": posts, "followers": followers,
            "client_logos": {"flock": "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 738 213'%3E%3Crect width='738' height='213' fill='%23111'/%3E%3Ctext x='369' y='140' font-size='120' font-family='sans-serif' font-weight='bold' text-anchor='middle' fill='%234ade80'%3EFLOCK%3C/text%3E%3C/svg%3E"}}


# ── cache + background refresh ───────────────────────────────────────────

class Cache:
    def __init__(self):
        self.snapshot: Optional[dict] = None
        self.last_error: Optional[str] = None
        self.refreshing = False
        self.started_at = time.time()
        self.attempts = 0
        self.lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None

    def refresh(self) -> None:
        with self.lock:
            if self.refreshing:
                return
            self.refreshing = True
        started = time.time()
        try:
            snap = build_snapshot()
            snap["build_seconds"] = round(time.time() - started, 1)
            self.snapshot = snap
            self.last_error = None
            log.info("dashboard snapshot: %d posts, %d follower rows in %ss",
                     len(snap["posts"]), len(snap["followers"]), snap["build_seconds"])
        except Exception as exc:  # keep the last good snapshot
            self.last_error = f"{type(exc).__name__}: {exc}"
            log.exception("dashboard refresh failed")
        finally:
            self.refreshing = False
            self.attempts += 1

    def ready(self) -> bool:
        """Render's health check: keep the previous instance serving until
        this one has data. Gives up gating after the first failed attempt
        or five minutes, so a broken Airtable token cannot wedge deploys."""
        return self.snapshot is not None or self.attempts > 0 or time.time() - self.started_at > 300

    def _loop(self) -> None:
        while True:
            self.refresh()
            time.sleep(REFRESH_SECONDS)

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="dashboard-refresh", daemon=True)
            self._thread.start()

    def status(self) -> dict:
        return {
            "ready": self.snapshot is not None,
            "refreshing": self.refreshing,
            "generated_at": self.snapshot.get("generated_at") if self.snapshot else None,
            "last_error": self.last_error,
            "refresh_seconds": REFRESH_SECONDS,
            "uptime_seconds": round(time.time() - self.started_at),
        }


cache = Cache()


# ── client dashboards ────────────────────────────────────────────────────

def slugify(name: str) -> str:
    return "-".join(w for w in "".join(c.lower() if c.isalnum() else " " for c in (name or "")).split())


def client_groups() -> Dict[str, dict]:
    """Clients defined as a set of accounts rather than a show.
    CLIENT_GROUPS = "ffp=FFP:Steelman|US In Common;other=Name:Account A|Account B"
    (slug = display name : channel names separated by |)."""
    out: Dict[str, dict] = {}
    for part in os.environ.get("CLIENT_GROUPS", "").split(";"):
        if "=" not in part:
            continue
        slug, rest = part.split("=", 1)
        name, _, chans = rest.partition(":")
        names = [c.strip().lower() for c in chans.split("|") if c.strip()]
        if slug.strip() and names:
            out[slug.strip().lower()] = {"name": name.strip() or slug.strip(), "channels": names}
    return out


def client_matches() -> Dict[str, dict]:
    """Clients picked out by a word in the clip title, for work where the
    Client field on Videos was never filled in.
    CLIENT_MATCHES = "flock=Flock:flock;other=Name:word"
    (slug = display name : the text to look for, case-insensitive).

    These get post performance only. The clips run on our own channels
    alongside unrelated work, so that account's followers and audience are
    not this client's and are deliberately left out rather than shown as
    if they were."""
    out: Dict[str, dict] = {}
    for part in os.environ.get("CLIENT_MATCHES", "").split(";"):
        if "=" not in part:
            continue
        slug, rest = part.split("=", 1)
        name, _, needle = rest.partition(":")
        needle = needle.strip().lower()
        if slug.strip() and needle:
            out[slug.strip().lower()] = {"name": name.strip() or slug.strip(), "match": needle}
    return out


def client_video_accounts() -> Dict[str, set]:
    """Clients whose videos are identified by the Client Account link on
    Videos rather than by show name.
    CLIENT_VIDEO_ACCOUNTS = "solana=Solana;other=Name A|Name B"
    (slug = Client Account name(s) in the Clients table, separated by |).

    Show name is a poor selector wherever the Show formula falls through to
    the channel: a video on two of the client's accounts comes back as
    "Show, Show" and one on no account at all comes back empty, so both drop
    out. Solana lost 94 of its 419 videos that way. The Client Account link is
    the field that actually means "this is theirs"."""
    out: Dict[str, set] = {}
    for part in os.environ.get("CLIENT_VIDEO_ACCOUNTS", "").split(";"):
        if "=" not in part:
            continue
        slug, rest = part.split("=", 1)
        names = {n.strip().lower() for n in rest.split("|") if n.strip()}
        if slug.strip() and names:
            out[slug.strip().lower()] = names
    return out


def client_also_by_client() -> Dict[str, set]:
    """Clients whose page should also carry anything tagged to them in the
    Client Account field, wherever it ran.
    CLIENT_ALSO_BY_CLIENT = "ffp=FFP;other=Name A|Name B"
    (slug = Client Account name(s), | separated).

    FFP is defined by its two accounts, but work made for FFP sometimes goes
    out on our own accounts instead. Those videos carry Client Account =
    FFP, and their posts inherit it, so both are pulled in on top of the
    account-based selection. A post from one of our accounts has its
    channel blanked, the same as on a show-based page: the client sees the
    clip and its numbers, not which of our accounts it ran on."""
    out: Dict[str, set] = {}
    for part in os.environ.get("CLIENT_ALSO_BY_CLIENT", "").split(";"):
        if "=" not in part:
            continue
        slug, rest = part.split("=", 1)
        names = {n.strip().lower() for n in rest.split("|") if n.strip()}
        if slug.strip() and names:
            out[slug.strip().lower()] = names
    return out


def no_follower_clients() -> set:
    """Client slugs whose account followers are not theirs to claim.
    CLIENT_NO_FOLLOWERS = "solana;other" (slugs, separated by ; or ,).

    Some accounts are posted to by many people, not only us, so the follower
    count on them is not something the client paid for and not a number they
    should read as a measure of our work. Those pages get post performance
    only, the same shape a title-matched client gets."""
    raw = os.environ.get("CLIENT_NO_FOLLOWERS", "")
    return {s.strip().lower() for s in raw.replace(",", ";").split(";") if s.strip()}


def no_show_clients() -> set:
    """Client slugs whose show attribution is not worth showing.
    CLIENT_NO_SHOWS = "solana;other" (slugs, separated by ; or ,).

    A video's Show is a formula: the episode's show when it has an episode,
    otherwise the show its channel is linked to. For an account whose clips
    are cut from conference talks, interviews and other people's podcasts
    rather than from our own episodes, every video falls through to that
    second branch and gets labelled with the one show hanging off the
    account -- which is not where any of it came from. Better to say nothing
    than to tell a client their clips came from a show they did not make."""
    raw = os.environ.get("CLIENT_NO_SHOWS", "")
    return {s.strip().lower() for s in raw.replace(",", ";").split(";") if s.strip()}


def client_view(snap: dict, slug: str) -> Optional[dict]:
    """The slice of the snapshot one client may see. A client is either a
    show (slug = slugified show name: that show, the channels linked to it,
    posts on those channels or cut from its episodes), a CLIENT_GROUPS entry
    (a named set of accounts: exactly those channels and their posts), or a
    CLIENT_MATCHES entry (every clip whose title carries a given word,
    wherever it ran). In all three, internal attribution — editor, director,
    poster, client account — is removed before anything leaves the server."""
    keep = ("id", "url", "platform", "channel", "video", "show", "title", "hook", "type", "date", "date_estimated",
            "created", "views", "likes", "comments", "replays", "reach")
    match = client_matches().get(slug)
    group = client_groups().get(slug)
    if match:
        needle = match["match"]
        hit = lambda text: needle in (text or "").lower()
        # The clip title only, as asked — not the post's own hook text, which
        # would drag in anything that happens to use the word in passing.
        posts = [{k: p.get(k) for k in keep} for p in snap.get("posts", []) if hit(p.get("title"))]
        if not posts:
            return None
        # The account these ran on is ours and carries unrelated work, so no
        # account name, follower history or audience goes out with them.
        for p in posts:
            p["channel"] = None
            p["show"] = None
        channels, shows, primary = [], [], []
        channel_ids = set()
        name, logo = match["name"], None
    elif group:
        channels = [dict(c) for c in snap.get("channels", []) if c["name"].lower() in group["channels"]]
        if not channels:
            return None
        channel_ids = {c["id"] for c in channels}
        show_ids = {sid for c in channels for sid in c.get("shows", [])}
        shows = [sh for sh in snap.get("shows", []) if sh["id"] in show_ids]
        posts = [{k: p.get(k) for k in keep} for p in snap.get("posts", []) if p.get("channel") in channel_ids]
        name, logo = group["name"], next((c.get("photo") for c in channels if c.get("photo")), None)
        primary = shows
    else:
        show = next((sh for sh in snap.get("shows", []) if slugify(sh["name"]) == slug), None)
        if not show:
            return None
        channels = [dict(c, shows=[show["id"]]) for c in snap.get("channels", []) if show["id"] in c.get("shows", [])]
        channel_ids = {c["id"] for c in channels}
        posts = [{k: p.get(k) for k in keep}
                 for p in snap.get("posts", [])
                 if (p.get("channel") in channel_ids) or (p.get("show") == show["name"])]
        for p in posts:
            if p["channel"] not in channel_ids:
                p["channel"] = None
            p["show"] = show["name"]
        name, logo, primary = show["name"], show.get("logo"), [show]
    # Any client may carry a logo on its Clients record; that wins for a
    # title-matched client, which has no show or channel to borrow one from.
    logo = snap.get("client_logos", {}).get((name or "").strip().lower()) or logo
    followers = ([] if (slug or "").strip().lower() in no_follower_clients()
                 else [f for f in snap.get("followers", []) if f["channel"] in channel_ids])
    demographics = [d for d in snap.get("demographics", []) if d["channel"] in channel_ids]
    show_names = {sh["name"] for sh in primary}
    vkeep = ("id", "title", "show", "episode", "type", "created", "views", "posts")  # no pipeline state leaves the server
    accounts = client_video_accounts().get((slug or "").strip().lower())
    if match:
        pick = lambda v: hit(v.get("title"))
    elif accounts:
        pick = lambda v: (v.get("client") or "").strip().lower() in accounts
    else:
        # The Show lookup joins with ", " when a video hangs off more than one
        # channel, so "Show, Show" has to match Show. Plain equality dropped
        # every such video.
        pick = lambda v: bool(show_names & {x.strip() for x in (v.get("show") or "").split(",") if x.strip()})
    videos = [{k: v.get(k) for k in vkeep} for v in snap.get("videos", []) if pick(v)]
    also = client_also_by_client().get((slug or "").strip().lower())
    if also and not match:
        tagged = lambda x: (x.get("client") or "").strip().lower() in also
        have_v = {v["id"] for v in videos}
        videos += [{k: v.get(k) for k in vkeep} for v in snap.get("videos", []) if tagged(v) and v["id"] not in have_v]
        have_p = {p["id"] for p in posts}
        extra = [{k: p.get(k) for k in keep} for p in snap.get("posts", []) if tagged(p) and p["id"] not in have_p]
        for p in extra:
            if p.get("channel") not in channel_ids:
                p["channel"] = None  # ran on one of ours; not the client's to see
        posts += extra

    # Videos are selected by show name, so the show can only be dropped once
    # that selection has happened.
    hide_shows = (slug or "").strip().lower() in no_show_clients()
    if match or hide_shows:
        for v in videos:
            v["show"] = None
    if hide_shows:
        for p in posts:
            p["show"] = None
        primary = []
    return {
        "generated_at": snap.get("generated_at"),
        "client": {"slug": slug, "name": name, "logo": logo},
        "shows": primary,
        "channels": channels,
        "demographics": demographics,
        "posts": posts,
        "followers": followers,
        "videos": videos,
    }
