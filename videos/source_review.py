"""
The review queue behind /dashboard/sources: every covered client's clip
that still lacks a Source Show or Source Episode, with the evidence a
person needs to decide (title, screenshot, the copy's sign-off, notes,
links to watch the clip), and a writer for what they decide.

Reads Airtable live (the snapshot does not carry notes or attachments),
cached for a minute. Writes go straight to the Videos row; a value set
here is exactly what the hourly labeller would have written, so it leaves
it alone from then on.
"""
import os
import re
import threading
import time

import requests

from videos import source_show as ss

F = dict(ss.F, dropbox="fldj42yMUdyiaJEeX", frameio="fldKc35kfIqHxmptK", created="fldOqa4OOrnkgTMxx")
UNKNOWN = ss.UNKNOWN
_cache = {"at": 0.0, "rows": None}
_lock = threading.Lock()


def _token():
    return os.environ.get("AIRTABLE_PERSONAL_ACCESS_TOKEN", "")


def fetch(force=False):
    """Every candidate clip, shaped for the page. Cached 60 s."""
    with _lock:
        if not force and _cache["rows"] is not None and time.time() - _cache["at"] < 60:
            return _cache["rows"]
        ids = ss.client_ids(_token())
        formula = ('AND({Client Account} != "", {Full Episode} = "", '
                   'OR({Source Show} = "", {Source Show} = "Unknown", {Source Episode} = "", {Source Episode} = "Unknown"))')
        fields = [F[k] for k in ("title", "client", "source", "episode", "url", "yt_title", "tweet", "yt_desc", "tt_desc",
                                 "hashtags", "notes", "ai_notes", "attachment", "thumbnail", "dropbox", "frameio", "created")]
        recs = ss._get(_token(), {"filterByFormula": formula, "fields[]": fields, "sort[0][field]": "Date Created", "sort[0][direction]": "desc"})
        rows = [shape(r, ids) for r in recs if any(x in ids for x in (r["fields"].get(F["client"]) or []))]
        _cache["rows"], _cache["at"] = rows, time.time()
        return rows


def shape(rec, ids):
    f = rec.get("fields", {})
    ev = ss.evidence(rec)
    notes = (f.get(F["notes"]) or "").strip()
    src = (f.get(F["source"]) or "").strip()
    ep = (f.get(F["episode"]) or "").strip()
    return {
        "id": rec["id"],
        "title": ev["title"],
        "client": next((ids[x] for x in (f.get(F["client"]) or []) if x in ids), None),
        "created": (f.get(F["created"]) or "")[:10],
        "show": src if src and src != UNKNOWN else "",
        "show_unknown": src == UNKNOWN,
        "episode": ep if ep and ep != UNKNOWN else "",
        "episode_unknown": ep == UNKNOWN,
        "url": f.get(F["url"]) or "",
        "youtube_title": ev.get("youtube_title"),
        "attribution": ev.get("attribution") or [],
        "hashtags": ev.get("hashtags"),
        "source_url": ev.get("source_url"),
        "source_video": ev.get("source_video"),
        "research": ev.get("research"),
        "notes": notes[:1200],
        "image": ss.image_url(rec),
        "dropbox": f.get(F["dropbox"]),
        "frameio": f.get(F["frameio"]),
        "airtable": f"https://airtable.com/{ss.BASE}/{ss.VIDEOS}/{rec['id']}",
    }


def known_shows():
    """Names to offer in the picker: Source Show values already in use."""
    return ss.known_shows(_token())


def write(video_id, show=None, episode=None, url=None):
    """Set what the reviewer decided. Empty string clears a field; "Unknown"
    marks it as looked-at-and-undeterminable so the labeller stops asking."""
    if not re.fullmatch(r"rec[A-Za-z0-9]{14}", video_id or ""):
        raise ValueError("bad record id")
    fields = {}
    if show is not None:
        fields[F["source"]] = show.strip() or None
    if episode is not None:
        fields[F["episode"]] = episode.strip() or None
    if url is not None:
        u = url.strip()
        if u and not re.match(r"https?://", u):
            raise ValueError("Source URL must start with http")
        fields[F["url"]] = u or None
    if not fields:
        return {}
    r = requests.patch(f"https://api.airtable.com/v0/{ss.BASE}/{ss.VIDEOS}/{video_id}",
                       headers={"Authorization": f"Bearer {_token()}", "Content-Type": "application/json"},
                       json={"fields": fields}, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"Airtable {r.status_code}: {r.text[:200]}")
    with _lock:
        _cache["rows"] = None  # next fetch sees the change
    return r.json().get("fields", {})


def fake_rows():
    """Fixtures for DASHBOARD_FAKE_DATA."""
    out = []
    for n in range(1, 13):
        out.append({"id": f"recFAKE{n:010d}", "title": f"Clip {n}: something someone said about Solana", "client": "Solana",
                    "created": f"2026-09-{(n % 28) + 1:02d}", "show": "" if n % 3 else "Lightspeed", "show_unknown": n % 3 == 1,
                    "episode": "", "episode_unknown": n % 4 == 0, "url": "", "youtube_title": None,
                    "attribution": ["— Toly on @ThePeelPod"] if n % 5 == 0 else [], "hashtags": "#solana",
                    "source_url": "https://www.youtube.com/watch?v=xQ6KQWpPKTA" if n % 4 == 0 else None,
                    "source_video": {"title": "Inside Solana's Plan", "channel": "The Peel with Turner Novak"} if n % 4 == 0 else None,
                    "research": None, "notes": "HOOK:\nsomething\n\nSOURCE:\n", "image": None,
                    "dropbox": "https://www.dropbox.com/x", "frameio": None, "airtable": "https://airtable.com/x"})
    return out
