"""
Link a clip to the Full Episode it was cut from, once its Source Show is a
real Show of ours.

`videos/source_show.py` names the podcast a client's clip came from (Source
Show), the episode (Source Episode) and, when the editor left one, the
source link (Source URL). Once such a show has a Shows row with an RSS feed
and Auto-Add Episodes on, the hourly episode sync fills the Full Episodes
table for it -- and this module closes the loop: for every video that has a
Source Show matching a Shows row and no Full Episode yet, it links the
episode when it is sure, and only then.

Two rules link, nothing weaker does:

  (a) the Source URL is a YouTube link whose video id equals the id in the
      episode's YouTube Link, or
  (b) the episode title contains the clip's Source Episode guest/title words
      (see episode_words / title_score) and the episode's Air Date is within
      DATE_WINDOW_DAYS of the clip's Date Created.

A Source Show that matches no Shows row, or more than one, is skipped. A
rule-(b) tie between two episodes is skipped. Everything skipped is listed
in the run summary under "unmatched" with the reason, so a human can look.

Runs inside gf-api on a timer (start_background) and as a script:

    python -m videos.link_episodes [--dry-run] [--limit N]

Environment
    AIRTABLE_PERSONAL_ACCESS_TOKEN
    AIRTABLE_BASE_ID          defaults to the Good Future Media base
    SOURCE_LINK_SECONDS       timer inside gf-api, default 3600; 0 disables
"""
import json
import logging
import os
import re
import sys
import threading
import time
from datetime import date, datetime, timedelta, timezone

import requests

log = logging.getLogger("link_episodes")

BASE = os.environ.get("AIRTABLE_BASE_ID", "appxCYu0Tfwc6h7X7")
VIDEOS = "tblu3ur57YdUMWw1B"
SHOWS = "tblKMC5hDmmc7YsVW"
EPISODES = "tblHBczQjSraq5hWe"

V = {
    "title": "fldgMXwbozJdOUjiu",           # Video Title
    "client": "fldSqPARmtwxe9m15",          # Client Account (link)
    "episode": "flda34XvSlQaFXapj",         # Full Episode (link)
    "source_show": "fldW950uakYMpCGxr",     # Source Show (text)
    "source_episode": "fld9LeMsRUyqJ9bzL",  # Source Episode (text)
    "source_url": "fldkAXEuh8TcBFcs0",      # Source URL
    "created": "fldOqa4OOrnkgTMxx",         # Date Created
}
S = {"name": "fldk6cIUZZTRMvY1V"}           # Show Name
E = {
    "title": "fldsKkmKBls8DjWBz",           # Episode Title
    "air": "fldASlNq1KPv6DlQp",             # Air Date
    "yt": "fldrzQAp2FQlD5wNe",              # YouTube Link
    "show": "fldTdRD3q55c2BvNG",            # Show (link)
}

UNKNOWN = "Unknown"
DATE_WINDOW_DAYS = 14
# Below this share of the clip's episode words found in the episode title,
# rule (b) does not fire. 0.5 lets "Anatoly Yakovenko on X with Tom Bilyeu"
# match a title that names the guest but not the host.
MIN_TITLE_SCORE = 0.5
MIN_MATCHED_WORDS = 2
FIRST_DELAY_SECONDS = 400
PATCH_BATCH = 10

# Words that carry no identity: articles, joiners, and the labels editors put
# around a show or episode name ("on the X podcast", "episode with Y").
STOPWORDS = {
    "a", "an", "the", "and", "or", "of", "on", "in", "at", "to", "for", "by",
    "with", "w", "vs", "from", "is", "are", "as", "his", "her", "their", "its",
    "podcast", "pod", "show", "episode", "ep", "interview", "full", "clip",
    "ft", "feat", "featuring", "live", "official", "channel", "network",
}

_YT_ID = re.compile(
    r"(?:youtube\.com/(?:watch\?[^#\s]*?v=|shorts/|live/|embed/|v/)|youtu\.be/)"
    r"([A-Za-z0-9_-]{11})"
)


def youtube_id(url):
    """The 11-character video id in a YouTube URL, or None."""
    if not url or not isinstance(url, str):
        return None
    m = _YT_ID.search(url.strip())
    return m.group(1) if m else None


def tokens(text):
    """Lower-case word tokens of `text` with punctuation and STOPWORDS gone.
    Keeps order and duplicates so callers can look for runs."""
    if not text:
        return []
    words = re.findall(r"[a-z0-9]+", text.lower().replace("'", ""))
    return [w for w in words if w not in STOPWORDS]


def _is_run(needle, hay):
    """True when list `needle` occurs as a contiguous run inside list `hay`."""
    n = len(needle)
    if n == 0 or n > len(hay):
        return False
    return any(hay[i:i + n] == needle for i in range(len(hay) - n + 1))


def show_matches(label, name):
    """Does the Source Show text `label` name the Shows row `name`?

    Loose on purpose: editors write "The Peel" for "The Peel with Turner
    Novak", "The Index" for "The Index Show", "Talking Tokens Podcast" for
    "Talking Tokens". Match when, after dropping stopwords, every word of
    the label occurs as one contiguous run in the show name. Only that way
    round: a label with words the name lacks ("Solana Breakpoint 2025"
    against the "Solana" row) is an event or a different thing, not a
    loose spelling.
    """
    a, b = tokens(label), tokens(name)
    if not a or not b:
        return False
    return _is_run(a, b)


def find_show(label, shows):
    """The single Shows row `label` names, or None when none or several do.

    `shows` is a list of {"id", "name"}. An exact (normalised) name wins
    over loose matches, so "Solana" does not become ambiguous just because
    "Solana Podcast" also exists.
    """
    if not label or label.strip().lower() == UNKNOWN.lower():
        return None
    raw = label.strip().lower()
    same = [s for s in shows if (s.get("name") or "").strip().lower() == raw]
    if len(same) == 1:
        return same[0]
    want = tokens(label)
    exact = [s for s in shows if tokens(s.get("name")) == want]
    if len(exact) == 1:
        return exact[0]
    loose = [s for s in shows if show_matches(label, s.get("name"))]
    return loose[0] if len(loose) == 1 else None


def episode_words(source_episode, show_name):
    """The clip's guest/title words: Source Episode tokens minus the show's
    own words (an editor writes "Armani Ferrante on Lightspeed")."""
    if not source_episode or source_episode.strip().lower() == UNKNOWN.lower():
        return []
    show = set(tokens(show_name))
    return [w for w in tokens(source_episode) if w not in show]


def title_score(words, episode_title):
    """How well `words` (from episode_words) fit an episode title.

    Returns (share, matched) where share is the fraction of `words` found in
    the title and matched the count. Rule (b) also needs a *run* of two
    consecutive words (a name, "Lucas Bruder", or a phrase) in the title, so
    a title that merely mentions the same company does not pass; that is
    checked by episode_matches.
    """
    if not words:
        return 0.0, 0
    title = tokens(episode_title)
    present = set(title)
    matched = sum(1 for w in words if w in present)
    return matched / len(words), matched


def episode_matches(words, episode_title):
    """Rule (b)'s title half: enough of the clip's words are in the title and
    two of them appear together, in order."""
    share, matched = title_score(words, episode_title)
    if share < MIN_TITLE_SCORE or matched < MIN_MATCHED_WORDS:
        return False
    title = tokens(episode_title)
    return any(_is_run(words[i:i + 2], title) for i in range(len(words) - 1))


def parse_date(value):
    """A date from an Airtable date or datetime string; None when absent."""
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None


def within_window(air, created, days=DATE_WINDOW_DAYS):
    if air is None or created is None:
        return False
    return abs((air - created).days) <= days


def plan(videos, shows, episodes):
    """Decide which videos to link. Pure: no network, no clock.

    videos    [{"id", "title", "source_show", "source_episode", "source_url",
                "created"}]  -- videos with a Source Show and no Full Episode
    shows     [{"id", "name"}]
    episodes  [{"id", "show" (Shows record id), "title", "air", "yt"}]

    Returns {"links": [{"video", "episode", "rule", "title", "episode_title"}],
             "unmatched": [{"video", "title", "reason"}]}.
    """
    by_show = {}
    for e in episodes:
        by_show.setdefault(e.get("show"), []).append(e)
    links, unmatched = [], []

    for v in videos:
        label = (v.get("source_show") or "").strip()
        show = find_show(label, shows)
        if show is None:
            unmatched.append({"video": v["id"], "title": v.get("title"), "reason": f"no single show for {label!r}"})
            continue
        cands = by_show.get(show["id"], [])
        if not cands:
            unmatched.append({"video": v["id"], "title": v.get("title"), "reason": f"{show['name']!r} has no episodes yet"})
            continue

        # (a) the same YouTube video.
        vid = youtube_id(v.get("source_url"))
        if vid:
            same = [e for e in cands if youtube_id(e.get("yt")) == vid]
            if len(same) == 1:
                links.append({"video": v["id"], "episode": same[0]["id"], "rule": "youtube_id",
                              "title": v.get("title"), "episode_title": same[0].get("title")})
                continue
            if len(same) > 1:
                unmatched.append({"video": v["id"], "title": v.get("title"), "reason": f"{len(same)} episodes share YouTube id {vid}"})
                continue

        # (b) title words + air date near the clip's creation.
        words = episode_words(v.get("source_episode"), show["name"])
        created = parse_date(v.get("created"))
        if not words or created is None:
            why = "no Source Episode words" if not words else "no Date Created"
            unmatched.append({"video": v["id"], "title": v.get("title"), "reason": f"{why}; YouTube id did not match"})
            continue
        scored = []
        for e in cands:
            if not within_window(parse_date(e.get("air")), created):
                continue
            if episode_matches(words, e.get("title")):
                scored.append((title_score(words, e.get("title"))[0], e))
        if not scored:
            unmatched.append({"video": v["id"], "title": v.get("title"),
                              "reason": f"no episode of {show['name']!r} within {DATE_WINDOW_DAYS}d titled like {v.get('source_episode')!r}"})
            continue
        scored.sort(key=lambda se: -se[0])
        if len(scored) > 1 and scored[0][0] == scored[1][0]:
            unmatched.append({"video": v["id"], "title": v.get("title"),
                              "reason": f"tie between {scored[0][1].get('title')!r} and {scored[1][1].get('title')!r}"})
            continue
        best = scored[0][1]
        links.append({"video": v["id"], "episode": best["id"], "rule": "title_and_date",
                      "title": v.get("title"), "episode_title": best.get("title")})
    return {"links": links, "unmatched": unmatched}


# ---------------------------------------------------------------- Airtable

def _headers(token):
    return {"Authorization": f"Bearer {token}"}


def _get_all(token, table, params):
    out, offset = [], None
    while True:
        p = dict(params, returnFieldsByFieldId="true", pageSize=100)
        if offset:
            p["offset"] = offset
        r = requests.get(f"https://api.airtable.com/v0/{BASE}/{table}", headers=_headers(token), params=p, timeout=60)
        r.raise_for_status()
        body = r.json()
        out.extend(body.get("records", []))
        offset = body.get("offset")
        if not offset:
            return out


def fetch_videos(token):
    formula = f'AND({{Source Show}} != "", {{Source Show}} != "{UNKNOWN}", {{Full Episode}} = "")'
    recs = _get_all(token, VIDEOS, {"filterByFormula": formula,
                                    "fields[]": [V["title"], V["source_show"], V["source_episode"], V["source_url"], V["created"]]})
    return [{"id": r["id"], "title": r["fields"].get(V["title"]), "source_show": r["fields"].get(V["source_show"]),
             "source_episode": r["fields"].get(V["source_episode"]), "source_url": r["fields"].get(V["source_url"]),
             "created": r["fields"].get(V["created"]) or r.get("createdTime")} for r in recs]


def fetch_shows(token):
    recs = _get_all(token, SHOWS, {"fields[]": [S["name"]]})
    return [{"id": r["id"], "name": r["fields"].get(S["name"]) or ""} for r in recs]


def _quote(name):
    return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'


def fetch_episodes(token, show_names):
    """Episodes of the named shows only, 20 names per request."""
    names = sorted({n for n in show_names if n})
    out = []
    for i in range(0, len(names), 20):
        clauses = ", ".join(f"FIND({_quote(n)}, ARRAYJOIN({{Show}}))" for n in names[i:i + 20])
        recs = _get_all(token, EPISODES, {"filterByFormula": f"OR({clauses})",
                                          "fields[]": [E["title"], E["air"], E["yt"], E["show"]]})
        for r in recs:
            f = r["fields"]
            links = f.get(E["show"]) or []
            out.append({"id": r["id"], "show": links[0] if links else None, "title": f.get(E["title"]),
                        "air": f.get(E["air"]), "yt": f.get(E["yt"])})
    return out


def configured():
    return bool(os.environ.get("AIRTABLE_PERSONAL_ACCESS_TOKEN"))


def sync(dry_run=False, limit=None):
    token = os.environ["AIRTABLE_PERSONAL_ACCESS_TOKEN"]
    videos = fetch_videos(token)
    if limit:
        videos = videos[:limit]
    shows = fetch_shows(token)
    wanted = {}
    for v in videos:
        s = find_show(v.get("source_show"), shows)
        if s:
            wanted[s["id"]] = s["name"]
    episodes = fetch_episodes(token, wanted.values()) if wanted else []
    result = plan(videos, shows, episodes)
    if result["links"] and not dry_run:
        updates = [{"id": l["video"], "fields": {V["episode"]: [l["episode"]]}} for l in result["links"]]
        for i in range(0, len(updates), PATCH_BATCH):
            r = requests.patch(f"https://api.airtable.com/v0/{BASE}/{VIDEOS}",
                               headers={**_headers(token), "Content-Type": "application/json"},
                               json={"records": updates[i:i + PATCH_BATCH]}, timeout=60)
            if r.status_code != 200:
                raise RuntimeError(f"Airtable PATCH failed: {r.status_code} {r.text[:200]}")
            time.sleep(0.25)
    rules = {}
    for l in result["links"]:
        rules[l["rule"]] = rules.get(l["rule"], 0) + 1
    summary = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "dry_run": dry_run,
               "candidates": len(videos), "shows_matched": len(wanted), "episodes_seen": len(episodes),
               "linked": len(result["links"]), "by_rule": rules, "unmatched": len(result["unmatched"]),
               "links": result["links"][:50], "unmatched_detail": result["unmatched"][:50]}
    for u in result["unmatched"]:
        log.info("link episodes: not linked %s (%s): %s", u["video"], (u.get("title") or "")[:60], u["reason"])
    log.info("link episodes: %s", {k: v for k, v in summary.items() if k not in ("links", "unmatched_detail")})
    return summary


last = {"summary": None, "error": None}


def _loop(seconds):
    time.sleep(FIRST_DELAY_SECONDS)
    while True:
        try:
            last["summary"], last["error"] = sync(), None
        except Exception as e:
            last["error"] = f"{type(e).__name__}: {e}"
            log.exception("link episodes failed")
        time.sleep(seconds)


def start_background():
    seconds = int(os.environ.get("SOURCE_LINK_SECONDS", "3600"))
    if not configured():
        log.info("link episodes: AIRTABLE_PERSONAL_ACCESS_TOKEN not set; not running")
        return
    if seconds <= 0:
        return
    threading.Thread(target=_loop, args=(seconds,), name="link-episodes", daemon=True).start()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not configured():
        sys.exit("set AIRTABLE_PERSONAL_ACCESS_TOKEN")
    lim = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None
    print(json.dumps(sync(dry_run="--dry-run" in sys.argv, limit=lim), indent=1, ensure_ascii=False))
