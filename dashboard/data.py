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
import threading
import time
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

import requests

log = logging.getLogger("dashboard")

BASE = os.environ.get("AIRTABLE_BASE_ID", "appxCYu0Tfwc6h7X7")
TOKEN = os.environ.get("AIRTABLE_PERSONAL_ACCESS_TOKEN", "")
REFRESH_SECONDS = int(os.environ.get("DASHBOARD_REFRESH_SECONDS", "900"))
FAKE = os.environ.get("DASHBOARD_FAKE_DATA") == "1"
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
}
CL = {"name": "fldGWgYaByXkGtc3Y"}    # Client Account Name

PLATFORM_ALIASES = {"Twitter": "X", "twitter": "X", "x": "X"}


def norm_platform(value: Optional[str]) -> str:
    value = (value or "").strip()
    return PLATFORM_ALIASES.get(value, value) or "Other"


# ── Airtable ─────────────────────────────────────────────────────────────

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
        time.sleep(0.22)


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


# ── shaping ──────────────────────────────────────────────────────────────

def build_snapshot() -> dict:
    if FAKE:
        return fake_snapshot()
    if not TOKEN:
        raise RuntimeError("AIRTABLE_PERSONAL_ACCESS_TOKEN is not set")

    team_rows = []
    for r in fetch_table(TABLES["team"], _flatten(TE.values())):
        f = r["fields"]
        user = f.get(TE["user"]) or {}
        st = f.get(TE["status"])
        tm = f.get(TE["team"])
        team_rows.append({
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
    clients = {r["id"]: (r["fields"].get(CL["name"]) or "?") for r in fetch_table(TABLES["clients"], [CL["name"]])}

    shows = {}
    for r in fetch_table(TABLES["shows"], _flatten(SH.values())):
        f = r["fields"]
        rel = f.get(SH["relationship"])
        shows[r["id"]] = {
            "id": r["id"],
            "name": f.get(SH["name"]) or "?",
            "relationship": rel.get("name") if isinstance(rel, dict) else rel,
            "client": clients.get(_first(f.get(SH["client"])) or "", None),
            "logo": _thumb(f.get(SH["logo"])),
        }

    channels = {}
    for r in fetch_table(TABLES["channels"], _flatten([CH["name"], CH["owned"], CH["shows"], CH["status"], CH["photo"], CH["profiles"], CH["followers"]])):
        f = r["fields"]
        status = f.get(CH["status"])
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

    videos = {}
    for r in fetch_table(TABLES["videos"], _flatten(VI.values())):
        f = r["fields"]
        vtype = f.get(VI["type"]); vstat = f.get(VI["status"]); tier = f.get(VI["tier"])
        videos[r["id"]] = {
            "id": r["id"],
            "title": f.get(VI["title"]) or "",
            "show": f.get(VI["show"]) or "",
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
        }

    posts = []
    for r in fetch_table(TABLES["posts"], _flatten(PO.values())):
        f = r["fields"]
        vid = videos.get(_first(f.get(PO["video"])) or "", {})
        ch = _first(f.get(PO["channel"]))
        posts.append({
            "id": r["id"],
            "url": f.get(PO["url"]),
            "platform": norm_platform(f.get(PO["network"])),
            "channel": ch if ch in channels else None,
            "show": vid.get("show") or "",
            "editor": vid.get("editor"),
            "client": vid.get("client"),
            "title": vid.get("title") or "",
            "hook": f.get(PO["hook"]) or "",
            "type": vid.get("type"),
            "date": f.get(PO["date"]),
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
    for r in fetch_table(TABLES["followers"], _flatten(FL.values())):
        f = r["fields"]
        ch = _first(f.get(FL["channel"]))
        if not ch or f.get(FL["count"]) is None or not f.get(FL["date"]):
            continue
        followers.append({
            "channel": ch,
            "platform": norm_platform(f.get(FL["platform"])),
            "date": f.get(FL["date"]),
            "count": f.get(FL["count"]),
            "prev": f.get(FL["prev"]),
        })

    # Status history: only the recent window plus anything still open. The
    # table is 25k+ rows and growing; filterByFormula needs field names.
    formula = ("OR(IS_AFTER({Start Time}, DATEADD(NOW(), -%d, 'days')), {End Time} = BLANK())" % ACTIVITY_DAYS)
    status_logs = []
    for r in fetch_table(TABLES["status_logs"], _flatten(SL.values()), formula=formula):
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

    return {
        "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "shows": list(shows.values()),
        "channels": list(channels.values()),
        "team": team_rows,
        "videos": list(videos.values()),
        "status_logs": status_logs,
        "posts": posts,
        "followers": followers,
    }


# ── fake data for local layout work (DASHBOARD_FAKE_DATA=1) ──────────────

def fake_snapshot() -> dict:
    rnd = random.Random(7)
    show_names = ["All-In", "The Techno Optimist", "Good Politics", "20VC", "Solana", "ThursdAI"]
    shows = [{"id": f"show{i}", "name": n, "relationship": rnd.choice(["Client", "Owned"]), "client": None, "logo": None}
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
    team_rows = ([{"id": f"t{n}", "name": n, "email": None, "user_id": None, "roles": ["Editor"], "team": "Video Production", "active": True, "slack_id": None, "photo": None, "start": None} for n in editors]
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
        ed = rnd.choice(editors); di = rnd.choice(directors)
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
                       "miner": rnd.choice(editors), "client": None, "type": "Clip", "tier": "1 - Basic", "status": cur,
                       "status_since": hist[-1][1].isoformat(timespec="seconds") + "Z", "created": t0.isoformat(timespec="seconds") + "Z",
                       "created_by": rnd.choice(editors), "started": None, "finished": None, "speaker": ""})
        for st, a, b in hist:
            status_logs.append({"video": f"v{n}", "status": st, "start": a.isoformat(timespec="seconds") + "Z", "end": b.isoformat(timespec="seconds") + "Z" if b else None})
    posts = []
    for n in range(3000):
        ch = rnd.choice(channels)
        p = rnd.choice(list(ch["followers"].keys()))
        day = today - timedelta(days=int(rnd.expovariate(1 / 120)) % 400)
        views = int(rnd.lognormvariate(8, 1.3))
        posts.append({
            "id": f"post{n}", "url": "https://example.com/p", "platform": p, "channel": ch["id"],
            "show": show_names[int(ch["id"][2])], "editor": rnd.choice(editors), "client": None,
            "title": f"Clip {n}: something someone said", "hook": "", "type": "Clip",
            "date": day.isoformat(), "created": day.isoformat(),
            "created_at": f"{day.isoformat()}T{rnd.randint(13, 23):02d}:{rnd.randint(0, 59):02d}:00.000Z", "poster": rnd.choice(posters),
            "views": views, "likes": int(views * rnd.uniform(0.01, 0.08)),
            "comments": int(views * rnd.uniform(0, 0.004)), "replays": None, "reach": None,
        })
    return {"generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "shows": shows, "channels": channels, "team": team_rows, "videos": videos, "status_logs": status_logs,
            "posts": posts, "followers": followers}


# ── cache + background refresh ───────────────────────────────────────────

class Cache:
    def __init__(self):
        self.snapshot: Optional[dict] = None
        self.last_error: Optional[str] = None
        self.refreshing = False
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
        }


cache = Cache()
