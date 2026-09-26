"""
Source Show for clips that have no Full Episode of ours.

Solana's clips are cut from dozens of other people's podcasts, conference
talks and interviews, so the Videos "Show" formula falls through to the
channel and calls every one of them "Solana". The editor's own material
usually says where a clip came from -- the title ("The Peel - Turner Novak,
Anatoly Yakovenko - ..."), the tweet copy ("-- @toly on @ThePeelPod"), the
hashtags (#peelpod), the copywriter's working notes ("The podcast is the
PokerNews Podcast"), the SOURCE line in the notes. This module hands that
evidence to Claude, one small batch at a time, and writes the answer into
the Videos "Source Show", "Source Episode" and "Source URL" fields.

It only ever fills blanks: a value typed by hand stays. When nothing in the
evidence names a show the field is set to "Unknown" so the video is not
asked about again; the dashboards show that as blank. Clear it to retry.

Runs inside gf-api on a timer (start_background) and as a script:

    python -m videos.source_show [--dry-run] [--limit N]

Environment
    ANTHROPIC_API_KEY
    AIRTABLE_PERSONAL_ACCESS_TOKEN
    AIRTABLE_BASE_ID          defaults to the Good Future Media base
    SOURCE_SHOW_CLIENTS       Client Account names to cover, "|"-separated; default "Solana"
    SOURCE_SHOW_MODEL         default claude-sonnet-5
    SOURCE_SHOW_SECONDS       timer inside gf-api, default 3600; 0 disables
    SOURCE_SHOW_IMAGES        "1" (default) also shows Claude the video's screenshot
                              (Attachment, else Thumbnail) when the text evidence is
                              thin; "0" sends text only

Screenshots: most of Solana's clips carry nothing but a title, and the
editor's Attachment is a screenshot of the source video, which usually shows
the channel name, the show's logo or a TV chyron. When a video has no
attribution line, no source video and no research, its first image
attachment goes to Claude as an image block next to the JSON evidence (one
image per video, Airtable's ~512px "large" thumbnail). Attachment URLs are
signed and expire about two hours after Airtable hands them out, so they are
read fresh in every pass and never stored.
"""
import json
import logging
import os
import re
import sys
import threading
import time
from datetime import datetime, timezone

import requests

log = logging.getLogger("source_show")

BASE = os.environ.get("AIRTABLE_BASE_ID", "appxCYu0Tfwc6h7X7")
VIDEOS = "tblu3ur57YdUMWw1B"
F = {
    "title": "fldgMXwbozJdOUjiu",     # Video Title
    "client": "fldSqPARmtwxe9m15",    # Client Account (link)
    "full_episode": "flda34XvSlQaFXapj",   # Full Episode (link)
    "source": "fldW950uakYMpCGxr",    # Source Show (text, ours)
    "episode": "fld9LeMsRUyqJ9bzL",   # Source Episode (text, ours)
    "url": "fldkAXEuh8TcBFcs0",       # Source URL (ours)
    "yt_title": "fldK1spMcwCSb8srL",  # YouTube Title
    "tweet": "fldy3EkUij952pIt4",     # Tweet
    "yt_desc": "fldbteQua126m01Zz",   # YouTube Description ("-- @toly ... on @ThePeelPod")
    "tt_desc": "fldtpn48iHbElFTh6",   # TikTok/IG Description
    "hashtags": "fldaUn8aFP5bToKg4",  # TikTok/IG Hashtags
    "notes": "fldQC5mrvisd8rQ3m",     # Description: HOOK / OPEN / CLOSE / SOURCE
    "ai_notes": "fld9EoWkILV5apHVU",  # AI Copywriter output, starts with its research
    "attachment": "fldFVXrzbHJSQ35dx",  # Attachment: usually a screenshot of the source video
    "thumbnail": "fldDPEL5aBYj3KgFZ",   # Thumbnail
}
UNKNOWN = "Unknown"
ANTHROPIC = "https://api.anthropic.com/v1/messages"
BATCH = 15


def clients():
    raw = os.environ.get("SOURCE_SHOW_CLIENTS", "Solana")
    return {c.strip().lower() for c in raw.split("|") if c.strip()}


def _get(token, params):
    out, offset = [], None
    while True:
        p = dict(params, returnFieldsByFieldId="true", pageSize=100)
        if offset:
            p["offset"] = offset
        r = requests.get(f"https://api.airtable.com/v0/{BASE}/{VIDEOS}", headers={"Authorization": f"Bearer {token}"}, params=p, timeout=60)
        r.raise_for_status()
        body = r.json()
        out.extend(body.get("records", []))
        offset = body.get("offset")
        if not offset:
            return out


CLIENTS = "tblYF8v9O280SU2oB"
F_CLIENT_NAME = "fldGWgYaByXkGtc3Y"


def client_ids(token):
    """{record id: name} for the Client Accounts we cover. A link comes back
    over REST as record ids, so the name has to be looked up here."""
    want = clients()
    r = requests.get(f"https://api.airtable.com/v0/{BASE}/{CLIENTS}", headers={"Authorization": f"Bearer {token}"},
                     params={"returnFieldsByFieldId": "true", "fields[]": [F_CLIENT_NAME], "pageSize": 100}, timeout=30)
    r.raise_for_status()
    return {c["id"]: c["fields"].get(F_CLIENT_NAME) for c in r.json().get("records", [])
            if (c["fields"].get(F_CLIENT_NAME) or "").strip().lower() in want}


def list_candidates(token, retry_unknown=False):
    """Videos of the covered clients with no Source Show and no Full Episode.
    retry_unknown also takes the ones marked Unknown (after the evidence got better)."""
    ids = client_ids(token)
    if not ids:
        return []
    # New rows have no Source Show; rows labelled before the episode fields
    # existed have a show but no Source Episode yet. Either way they are asked.
    blank = ('OR({Source Show} = "", {Source Show} = "Unknown", AND({Source Show} != "Unknown", {Source Episode} = ""))'
             if retry_unknown else 'OR({Source Show} = "", AND({Source Show} != "Unknown", {Source Episode} = ""))')
    recs = _get(token, {"filterByFormula": f'AND({blank}, {{Client Account}} != "", {{Full Episode}} = "")',
                        "fields[]": [F["title"], F["client"], F["source"], F["yt_title"], F["tweet"], F["yt_desc"], F["tt_desc"], F["hashtags"], F["notes"], F["ai_notes"],
                                     F["attachment"], F["thumbnail"]]})
    return [r for r in recs if any(x in ids for x in (r["fields"].get(F["client"]) or []))]


def known_shows(token):
    recs = _get(token, {"filterByFormula": '{Source Show} != ""', "fields[]": [F["source"]]})
    names = {(r["fields"].get(F["source"]) or "").strip() for r in recs}
    return sorted(n for n in names if n and n != UNKNOWN)


def image_url(rec):
    """URL of the first image attachment: Attachment first, else Thumbnail;
    only image/* files. Airtable's "large" thumbnail (~512px) when it has
    one, else the file itself. None when there is no image. Pure.

    The URL is a signed airtableusercontent link that expires ~2 h after the
    record was fetched: use it in the pass that fetched it, do not keep it."""
    f = rec.get("fields", {})
    for key in ("attachment", "thumbnail"):
        for a in f.get(F[key]) or []:
            if not isinstance(a, dict) or not (a.get("type") or "").startswith("image/"):
                continue
            large = ((a.get("thumbnails") or {}).get("large") or {}).get("url")
            return large or a.get("url") or None
    return None


def thin(v):
    """True when the text evidence of one video (an evidence() dict) is only a
    title: nothing attributes it, no source video, no research. That is when
    a screenshot is worth its tokens."""
    return not (v.get("attribution") or v.get("source_video") or v.get("research"))


def images_enabled():
    return os.environ.get("SOURCE_SHOW_IMAGES", "1").strip() not in ("0", "false", "no", "")


def evidence(rec):
    """The compact, model-facing view of one video. Pure. The "image" key is
    for content_blocks(), not for the JSON the model reads."""
    f = rec.get("fields", {})
    notes = f.get(F["notes"]) or ""
    src = re.search(r"SOURCE:\s*<?(\S+)", notes)
    ai = (f.get(F["ai_notes"]) or "").strip()
    # The copywriter's research sits above its first divider; the tweets
    # below repeat the attribution anyway.
    ai = re.split(r"\n[─═]{4,}|\n── ", ai)[0][:500]
    # The attribution line ("-- @toly, Co-Founder of Solana, on @ThePeelPod")
    # is repeated across the tweet and both descriptions; take it wherever it is.
    copy = "\n".join((f.get(k) or "") for k in (F["tweet"], F["yt_desc"], F["tt_desc"]))
    tweet = (f.get(F["tweet"]) or f.get(F["yt_desc"]) or "").strip()
    # The sign-off ("-- Lily Liu, President of the Solana Foundation on Squawk
    # Box Europe") is the line that names the show. Take dash-led lines and
    # anything with an "on @handle" / "on the X podcast" shape.
    attrib = []
    for ln in copy.splitlines():
        ln = ln.strip()
        if ln and ln not in attrib and (ln[0] in "—–-" or re.search(r"\bon (the )?@|\bon (the )?[A-Z][\w.&' ]{2,40}(pod|podcast|show|tv)\b|podcast", ln, re.I)):
            attrib.append(ln)
    src_url = src.group(1).rstrip(">") if src else None
    video = youtube_info(src_url) if src_url else None
    known_show = (f.get(F["source"]) or "").strip()
    return {
        "id": rec["id"],
        "known_show": known_show if known_show and known_show != UNKNOWN else None,
        "title": (f.get(F["title"]) or "").strip(),
        "youtube_title": (f.get(F["yt_title"]) or "").strip() or None,
        "attribution": attrib[:3] or None,
        "tweet_start": tweet[:160] if tweet and not attrib else None,
        "hashtags": (f.get(F["hashtags"]) or "").strip() or None,
        "source_url": src_url,
        "source_video": video,  # {"title", "channel"} from YouTube, when the SOURCE is a YouTube link
        "research": ai or None,
        "image": image_url(rec),  # screenshot URL, sent as an image block when the rest is thin
    }


_yt_cache = {}


def youtube_info(url):
    """Title and channel of a YouTube link via the public oEmbed endpoint (no
    key). The channel name is the single best clue to the show."""
    if not re.search(r"(youtube\.com|youtu\.be)/", url or ""):
        return None
    if url in _yt_cache:
        return _yt_cache[url]
    info = None
    try:
        r = requests.get("https://www.youtube.com/oembed", params={"url": url, "format": "json"}, timeout=15)
        if r.status_code == 200:
            j = r.json()
            info = {"title": j.get("title"), "channel": j.get("author_name")}
    except requests.RequestException:
        pass
    _yt_cache[url] = info
    return info


PROMPT = """You label short clips with the podcast, show, conference talk or interview they were cut from.

For each video below, use its evidence (title, YouTube title, the sign-off/attribution lines from its copy, hashtags, the source URL and -- when present -- the source video's YouTube title and channel, the copywriter's research notes) to name the ORIGINAL show or event the clip came from -- not the client, not the speaker, not the account it was posted to. A source_video channel is the strongest clue: "The Peel with Turner Novak" is the show "The Peel"; a TV segment names the programme ("Squawk Box", "Bloomberg Crypto").

Some videos come with a screenshot of the source video after the JSON ("Video <id> screenshot:"). Read it: it usually shows the YouTube channel name or handle under the player, the show's logo or title card, a podcast's artwork, a conference stage banner, or a TV chyron/watermark (CNBC, Bloomberg, Fox Business). Name the show from what is written or branded in the image; a face or a name alone is not a show. A screenshot belongs only to the video id it is labelled with.

Rules:
- Give the show's proper name, not a handle: "The Peel", "PokerNews Podcast", "Lightspeed", "Solana Breakpoint 2026", "Bloomberg Crypto". A handle is only a hint (@ThePeelPod -> The Peel).
- If several videos clearly come from the same show, spell it identically. Prefer a name from the known list when it is the same show.
- Solana's own productions count: "Solana Ecosystem Calls", "Solana Stories", "Solana, New Ideas" when the title says so.
- If the evidence does not name a show or event, answer null. Do not guess from the speaker alone.
- "episode": the specific episode or segment the clip came from, as a short label a person would recognise: the guest and/or episode title, plus the date if the evidence gives one ("Turner Novak with Anatoly Yakovenko", "Squawk Box Europe, 9 Sep 2026", "Breakpoint 2025 keynote"). null if the evidence does not say.
- "url": the link to the original episode/video when the evidence carries one (a source_url, or a YouTube/Spotify/Apple/X link in the research). Copy it exactly; null otherwise. Never invent a URL.
- A video whose Source Show is already given (field "known_show") keeps that show; only fill the episode and url for it.

Known show names so far: {known}

Videos:
{videos}

Answer with JSON only: {{"<video id>": {{"show": "<show name>" or null, "episode": "<label>" or null, "url": "<link>" or null}}, ...}} covering every id."""


def content_blocks(batch, known, images=True):
    """The user message for one batch as a list of content blocks: the prompt
    with the JSON evidence, then "Video <id> screenshot:" + an image block
    for every video that has an image and thin text evidence (at most one
    image per video). Returns (blocks, number of images). Pure."""
    text_view = [{k: v for k, v in video.items() if k != "image"} for video in batch]
    blocks = [{"type": "text", "text": PROMPT.format(known=", ".join(known) or "(none yet)",
                                                     videos=json.dumps(text_view, ensure_ascii=False, indent=1))}]
    n = 0
    for video in batch:
        if images and video.get("image") and thin(video):
            blocks.append({"type": "text", "text": f"Video {video['id']} screenshot:"})
            blocks.append({"type": "image", "source": {"type": "url", "url": video["image"]}})
            n += 1
    return blocks, n


def ask(api_key, batch, known, model=None, images=None):
    """({id: {"show", "episode", "url"}}, images sent) for one batch. Anything
    the model leaves out is None. images=None follows SOURCE_SHOW_IMAGES."""
    if images is None:
        images = images_enabled()
    blocks, n_images = content_blocks(batch, known, images)
    body = {
        "model": model or os.environ.get("SOURCE_SHOW_MODEL", "claude-sonnet-5"),
        "max_tokens": 4000,  # 1500 cut a 15-video answer off mid-JSON on the first live run
        "messages": [{"role": "user", "content": blocks}],
    }
    headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
    r = requests.post(ANTHROPIC, headers=headers, json=body, timeout=120)
    if r.status_code == 400 and n_images and "image" in r.text.lower():
        # One image the API could not fetch (an expired Airtable URL, say)
        # must not cost the whole batch its text answer: ask again without.
        log.warning("source show: image rejected (%s); retrying batch without images", r.text[:120])
        blocks, n_images = content_blocks(batch, known, images=False)
        body = dict(body, messages=[{"role": "user", "content": blocks}])
        r = requests.post(ANTHROPIC, headers=headers, json=body, timeout=120)
    if r.status_code != 200:
        raise RuntimeError(f"Anthropic {r.status_code}: {r.text[:200]}")
    body = r.json()
    text = "".join(c.get("text", "") for c in body.get("content", []))
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise RuntimeError(f"no JSON in answer (stop_reason={body.get('stop_reason')}): {text[:200]}")
    answers = json.loads(m.group(0))

    def clean(x):
        return x.strip() if isinstance(x, str) and x.strip() and x.strip().lower() not in ("null", "unknown", "none") else None

    out = {}
    for v in batch:
        a = answers.get(v["id"])
        if isinstance(a, str) or a is None:  # an older-style plain answer
            a = {"show": a}
        a = a or {}
        url = clean(a.get("url"))
        if url and not re.match(r"https?://", url):
            url = None
        out[v["id"]] = {"show": v.get("known_show") or clean(a.get("show")), "episode": clean(a.get("episode")), "url": url}
    return out, n_images


def configured():
    return bool(os.environ.get("ANTHROPIC_API_KEY") and os.environ.get("AIRTABLE_PERSONAL_ACCESS_TOKEN"))


def sync(dry_run=False, limit=None, retry_unknown=False):
    token, key = os.environ["AIRTABLE_PERSONAL_ACCESS_TOKEN"], os.environ["ANTHROPIC_API_KEY"]
    cands = list_candidates(token, retry_unknown)
    if limit:
        cands = cands[:limit]
    known = known_shows(token)
    labelled, unknown, updates, errors, images = {}, [], [], [], 0
    for i in range(0, len(cands), BATCH):
        # evidence() is built per batch from the records this pass fetched, so
        # every screenshot URL is well inside its ~2 h life.
        batch = [evidence(r) for r in cands[i:i + BATCH]]
        try:
            answers, sent = ask(key, batch, known)
            images += sent
        except Exception as e:
            # One bad answer must not sink the other 28 batches; those videos
            # stay blank and are asked again next hour.
            log.warning("source show: batch at %d failed: %s", i, e)
            errors.append(f"{type(e).__name__}: {str(e)[:120]}")
            continue
        for v in batch:
            a = answers.get(v["id"]) or {}
            show = a.get("show")
            if show:
                labelled[v["id"]] = show
                if show not in known:
                    known.append(show)
            else:
                unknown.append(v["title"])
            fields = {F["source"]: show or UNKNOWN, F["episode"]: a.get("episode") or UNKNOWN}
            if a.get("url"):
                fields[F["url"]] = a["url"]
            updates.append({"id": v["id"], "fields": fields})
    if updates and not dry_run:
        for i in range(0, len(updates), 10):
            r = requests.patch(f"https://api.airtable.com/v0/{BASE}/{VIDEOS}",
                               headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                               json={"records": updates[i:i + 10]}, timeout=60)
            if r.status_code != 200:
                raise RuntimeError(f"Airtable PATCH failed: {r.status_code} {r.text[:200]}")
            time.sleep(0.25)
    counts = {}
    for s in labelled.values():
        counts[s] = counts.get(s, 0) + 1
    summary = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "dry_run": dry_run,
               "candidates": len(cands), "labelled": len(labelled), "unknown": len(unknown), "images": images, "batch_errors": errors,
               "shows": dict(sorted(counts.items(), key=lambda kv: -kv[1])), "unknown_titles": unknown[:20]}
    summary["episodes"] = sum(1 for u in updates if u["fields"].get(F["episode"]) != UNKNOWN)
    summary["urls"] = sum(1 for u in updates if u["fields"].get(F["url"]))
    if dry_run:
        summary["labels"] = {cands_title(c): {k: u["fields"].get(F[k]) for k in ("source", "episode", "url")}
                             for c, u in zip(cands, updates)}
    log.info("source show: %s", summary)
    return summary


def cands_title(rec):
    return (rec.get("fields", {}).get(F["title"]) or rec["id"]).strip()[:80]


last = {"summary": None, "error": None}


def _loop(seconds):
    time.sleep(300)
    # SOURCE_SHOW_RETRY_UNKNOWN=1: the first pass after boot re-asks the
    # Unknowns (set it after the evidence improves, then unset it).
    retry = os.environ.get("SOURCE_SHOW_RETRY_UNKNOWN") == "1"
    while True:
        try:
            last["summary"], last["error"] = sync(retry_unknown=retry), None
            retry = False
        except Exception as e:
            last["error"] = f"{type(e).__name__}: {e}"
            log.exception("source show failed")
        time.sleep(seconds)


def start_background():
    seconds = int(os.environ.get("SOURCE_SHOW_SECONDS", "3600"))
    if not configured():
        log.info("source show: ANTHROPIC_API_KEY not set; not running")
        return
    if seconds <= 0:
        return
    threading.Thread(target=_loop, args=(seconds,), name="source-show", daemon=True).start()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not configured():
        sys.exit("set ANTHROPIC_API_KEY and AIRTABLE_PERSONAL_ACCESS_TOKEN")
    lim = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None
    print(json.dumps(sync(dry_run="--dry-run" in sys.argv, limit=lim), indent=1, ensure_ascii=False))
