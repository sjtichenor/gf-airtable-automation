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
the Videos "Source Show" text field.

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
    "episode": "flda34XvSlQaFXapj",   # Full Episode (link)
    "source": "fldW950uakYMpCGxr",    # Source Show (text, ours)
    "yt_title": "fldK1spMcwCSb8srL",  # YouTube Title
    "tweet": "fldy3EkUij952pIt4",     # Tweet
    "yt_desc": "fldbteQua126m01Zz",   # YouTube Description ("-- @toly ... on @ThePeelPod")
    "tt_desc": "fldtpn48iHbElFTh6",   # TikTok/IG Description
    "hashtags": "fldaUn8aFP5bToKg4",  # TikTok/IG Hashtags
    "notes": "fldQC5mrvisd8rQ3m",     # Description: HOOK / OPEN / CLOSE / SOURCE
    "ai_notes": "fld9EoWkILV5apHVU",  # AI Copywriter output, starts with its research
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


def list_candidates(token):
    """Videos of the covered clients with no Source Show and no Full Episode."""
    ids = client_ids(token)
    if not ids:
        return []
    recs = _get(token, {"filterByFormula": 'AND({Source Show} = "", {Client Account} != "", {Full Episode} = "")',
                        "fields[]": [F["title"], F["client"], F["yt_title"], F["tweet"], F["yt_desc"], F["tt_desc"], F["hashtags"], F["notes"], F["ai_notes"]]})
    return [r for r in recs if any(x in ids for x in (r["fields"].get(F["client"]) or []))]


def known_shows(token):
    recs = _get(token, {"filterByFormula": '{Source Show} != ""', "fields[]": [F["source"]]})
    names = {(r["fields"].get(F["source"]) or "").strip() for r in recs}
    return sorted(n for n in names if n and n != UNKNOWN)


def evidence(rec):
    """The compact, model-facing view of one video. Pure."""
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
    attrib = []
    for ln in copy.splitlines():
        ln = ln.strip()
        if ln and ln not in attrib and re.search(r"\bon (the )?@|\bon the .{2,40}(pod|podcast|show)\b|podcast", ln, re.I):
            attrib.append(ln)
    return {
        "id": rec["id"],
        "title": (f.get(F["title"]) or "").strip(),
        "youtube_title": (f.get(F["yt_title"]) or "").strip() or None,
        "attribution": attrib[:3] or None,
        "tweet_start": tweet[:160] if tweet and not attrib else None,
        "hashtags": (f.get(F["hashtags"]) or "").strip() or None,
        "source_url": src.group(1).rstrip(">") if src else None,
        "research": ai or None,
    }


PROMPT = """You label short clips with the podcast, show, conference talk or interview they were cut from.

For each video below, use its evidence (title, YouTube title, tweet attribution lines, hashtags, source URL, the copywriter's research notes) to name the ORIGINAL show or event the clip came from -- not the client, not the speaker, not the account it was posted to.

Rules:
- Give the show's proper name, not a handle: "The Peel", "PokerNews Podcast", "Lightspeed", "Solana Breakpoint 2026", "Bloomberg Crypto". A handle is only a hint (@ThePeelPod -> The Peel).
- If several videos clearly come from the same show, spell it identically. Prefer a name from the known list when it is the same show.
- Solana's own productions count: "Solana Ecosystem Calls", "Solana Stories", "Solana, New Ideas" when the title says so.
- If the evidence does not name a show or event, answer null. Do not guess from the speaker alone.

Known show names so far: {known}

Videos:
{videos}

Answer with JSON only: {{"<video id>": "<show name>" or null, ...}} covering every id."""


def ask(api_key, batch, known, model=None):
    """{id: show|None} for one batch. Anything the model leaves out is None."""
    body = {
        "model": model or os.environ.get("SOURCE_SHOW_MODEL", "claude-sonnet-5"),
        "max_tokens": 4000,  # 1500 cut a 15-video answer off mid-JSON on the first live run
        "messages": [{"role": "user", "content": PROMPT.format(known=", ".join(known) or "(none yet)",
                                                                videos=json.dumps(batch, ensure_ascii=False, indent=1))}],
    }
    r = requests.post(ANTHROPIC, headers={"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                      json=body, timeout=120)
    if r.status_code != 200:
        raise RuntimeError(f"Anthropic {r.status_code}: {r.text[:200]}")
    body = r.json()
    text = "".join(c.get("text", "") for c in body.get("content", []))
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise RuntimeError(f"no JSON in answer (stop_reason={body.get('stop_reason')}): {text[:200]}")
    answers = json.loads(m.group(0))
    out = {}
    for v in batch:
        a = answers.get(v["id"])
        out[v["id"]] = a.strip() if isinstance(a, str) and a.strip() and a.strip().lower() not in ("null", "unknown", "none") else None
    return out


def configured():
    return bool(os.environ.get("ANTHROPIC_API_KEY") and os.environ.get("AIRTABLE_PERSONAL_ACCESS_TOKEN"))


def sync(dry_run=False, limit=None):
    token, key = os.environ["AIRTABLE_PERSONAL_ACCESS_TOKEN"], os.environ["ANTHROPIC_API_KEY"]
    cands = list_candidates(token)
    if limit:
        cands = cands[:limit]
    known = known_shows(token)
    labelled, unknown, updates, errors = {}, [], [], []
    for i in range(0, len(cands), BATCH):
        batch = [evidence(r) for r in cands[i:i + BATCH]]
        try:
            answers = ask(key, batch, known)
        except Exception as e:
            # One bad answer must not sink the other 28 batches; those videos
            # stay blank and are asked again next hour.
            log.warning("source show: batch at %d failed: %s", i, e)
            errors.append(f"{type(e).__name__}: {str(e)[:120]}")
            continue
        for v in batch:
            show = answers.get(v["id"])
            if show:
                labelled[v["id"]] = show
                if show not in known:
                    known.append(show)
            else:
                unknown.append(v["title"])
            updates.append({"id": v["id"], "fields": {F["source"]: show or UNKNOWN}})
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
               "candidates": len(cands), "labelled": len(labelled), "unknown": len(unknown), "batch_errors": errors,
               "shows": dict(sorted(counts.items(), key=lambda kv: -kv[1])), "unknown_titles": unknown[:20]}
    if dry_run:
        summary["labels"] = {cands_title(c): labelled.get(c["id"]) for c in cands}
    log.info("source show: %s", summary)
    return summary


def cands_title(rec):
    return (rec.get("fields", {}).get(F["title"]) or rec["id"]).strip()[:80]


last = {"summary": None, "error": None}


def _loop(seconds):
    time.sleep(300)
    while True:
        try:
            last["summary"], last["error"] = sync(), None
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
