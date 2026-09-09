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
}

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
}
FL = {
    "channel": "fldlUAVQLVmNL7X5m",   # Social Media Account
    "platform": "fldpIeuj3nur9A393",  # Platform
    "date": "fld1JmpcbEVXEL3f5",      # Date
    "count": "flddzOdphxZnvDKDZ",     # Follower Count
    "prev": "fldT0GzajOXGarc4T",      # Previous Count
}
TE = {"name": "fldbUkybFQu3SyAFI"}    # Name
CL = {"name": "fldGWgYaByXkGtc3Y"}    # Client Account Name

PLATFORM_ALIASES = {"Twitter": "X", "twitter": "X", "x": "X"}


def norm_platform(value: Optional[str]) -> str:
    value = (value or "").strip()
    return PLATFORM_ALIASES.get(value, value) or "Other"


# ── Airtable ─────────────────────────────────────────────────────────────

def fetch_table(table: str, field_ids: List[str]) -> List[dict]:
    """All records of a table, fields keyed by id. Paces itself under the
    5 req/s base limit and waits out a 429."""
    url = f"{API}/{BASE}/{table}"
    params = [("returnFieldsByFieldId", "true"), ("pageSize", "100")]
    params += [("fields[]", f) for f in field_ids]
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

    team = {r["id"]: (r["fields"].get(TE["name"]) or "?") for r in fetch_table(TABLES["team"], [TE["name"]])}
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
        vtype = f.get(VI["type"])
        videos[r["id"]] = {
            "title": f.get(VI["title"]) or "",
            "show": f.get(VI["show"]) or "",
            "editor": team.get(_first(f.get(VI["editor"])) or "", None),
            "client": clients.get(_first(f.get(VI["client"])) or "", None),
            "type": vtype.get("name") if isinstance(vtype, dict) else vtype,
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

    return {
        "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "shows": list(shows.values()),
        "channels": list(channels.values()),
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
            "views": views, "likes": int(views * rnd.uniform(0.01, 0.08)),
            "comments": int(views * rnd.uniform(0, 0.004)), "replays": None, "reach": None,
        })
    return {"generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "shows": shows, "channels": channels, "posts": posts, "followers": followers}


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
