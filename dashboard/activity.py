"""Who did what, when — derived from video status history and posts.

A status log row says a video sat in `status` from `start` to `end`. The
moment it *left* a status is someone's action: leaving Editing is the editor
finishing a draft, leaving Internal Review is the director deciding, and so
on. Posts carry who posted them. Everything is attributed by name so it
lines up with the Team table.

All "days" are in DIGEST_TZ (default America/New_York): the team's day, not
the server's.
"""
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

TZ_NAME = os.environ.get("DIGEST_TZ", "America/New_York")
try:
    from zoneinfo import ZoneInfo  # py3.9+
    TZ = ZoneInfo(TZ_NAME)
except Exception:  # local py3.8: fixed Eastern-ish offset is fine for layout work
    TZ = timezone(timedelta(hours=-4))

PRE = {"Up For Grabs", "Assigned"}
REVISION = {"Needs More Edits", "Awaiting Revision"}
AFTER_EDIT = {"Internal Review", "Client Review", "Ready to Post", "Video Shipped"}
OPEN_STATUSES = ["Up For Grabs", "Assigned", "Editing", "Needs More Edits", "Internal Review", "Client Review", "Ready to Post"]
STUCK_AFTER_DAYS = {"Up For Grabs": 5, "Assigned": 3, "Editing": 3, "Needs More Edits": 2, "Internal Review": 2, "Client Review": 5, "Ready to Post": 2}
WORK_ROLES = {"Editor", "Director", "Social Media Manager", "Bootcamp Recruit"}

KIND_LABEL = {
    "picked_up": "Picked up", "started": "Started editing", "finished": "Finished edit", "dropped": "Dropped",
    "moved": "Moved", "revision_started": "Started revision", "approved": "Approved", "sent_back": "Sent back",
    "client_review_closed": "Client review closed", "posted": "Posted", "created": "Created video",
}


def parse(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def fmt_dur(minutes: Optional[float]) -> str:
    if minutes is None:
        return ""
    m = int(minutes)
    if m < 60:
        return f"{m}m"
    h, m = divmod(m, 60)
    if h < 24:
        return f"{h}h {m:02d}m" if m else f"{h}h"
    d, h = divmod(h, 24)
    return f"{d}d {h}h" if h else f"{d}d"


def _event(ts: datetime, person, role, kind, video=None, title="", detail="", minutes=None, **extra) -> dict:
    loc = ts.astimezone(TZ)
    e = {"ts": ts.isoformat(timespec="seconds"), "date": loc.date().isoformat(), "time": loc.strftime("%H:%M"),
         "person": person, "role": role, "kind": kind, "label": KIND_LABEL.get(kind, kind), "video": video,
         "title": title, "detail": detail, "minutes": round(minutes) if minutes is not None else None}
    e.update(extra)
    return e


def build_events(snap: dict) -> List[dict]:
    videos = {v["id"]: v for v in snap.get("videos", [])}
    by_video: Dict[str, list] = defaultdict(list)
    for l in snap.get("status_logs", []):
        if l.get("start"):
            by_video[l["video"]].append(l)
    events: List[dict] = []
    for vid, logs in by_video.items():
        logs.sort(key=lambda l: l["start"])
        v = videos.get(vid, {})
        title = v.get("title") or vid
        editor, director = v.get("editor"), v.get("director")
        for i, l in enumerate(logs):
            end = parse(l.get("end"))
            if not end:
                continue
            start = parse(l["start"])
            minutes = (end - start).total_seconds() / 60 if start else None
            nxt = logs[i + 1]["status"] if i + 1 < len(logs) else None
            left = l["status"]
            if left in PRE:
                events.append(_event(end, editor, "editor", "picked_up" if left == "Up For Grabs" else "started", vid, title))
            elif left == "Editing":
                kind = "finished" if nxt in AFTER_EDIT else ("dropped" if nxt in PRE else "moved")
                events.append(_event(end, editor, "editor", kind, vid, title, f"{fmt_dur(minutes)} in Editing" + (f" → {nxt}" if nxt else ""), minutes))
            elif left in REVISION:
                events.append(_event(end, editor, "editor", "revision_started", vid, title, f"waited {fmt_dur(minutes)}", minutes))
            elif left == "Internal Review":
                kind = "sent_back" if nxt in REVISION or nxt == "Editing" else "approved"
                events.append(_event(end, director, "director", kind, vid, title, f"{fmt_dur(minutes)} in review" + (f" → {nxt}" if nxt else ""), minutes))
            elif left == "Client Review":
                events.append(_event(end, director, "director", "client_review_closed", vid, title, f"{fmt_dur(minutes)} with client" + (f" → {nxt}" if nxt else ""), minutes))
    for p in snap.get("posts", []):
        ts = parse(p.get("created_at"))
        if not ts:
            continue
        events.append(_event(ts, p.get("poster"), "social", "posted", p.get("video"), p.get("title") or p.get("hook") or "(untitled)",
                             " · ".join(x for x in [p.get("platform"), _channel_name(snap, p.get("channel"))] if x),
                             None, url=p.get("url"), platform=p.get("platform")))
    for v in snap.get("videos", []):
        ts = parse(v.get("created"))
        if ts and v.get("created_by"):
            events.append(_event(ts, v["created_by"], "miner", "created", v["id"], v.get("title") or v["id"], v.get("show") or ""))
    events.sort(key=lambda e: e["ts"])
    return events


_channel_cache: Dict[int, Dict[str, str]] = {}


def _channel_name(snap, cid):
    key = id(snap)
    if key not in _channel_cache:
        _channel_cache.clear()
        _channel_cache[key] = {c["id"]: c["name"] for c in snap.get("channels", [])}
    return _channel_cache[key].get(cid)


GROUP_ORDER = ["Editors", "Directors", "Social", "Bootcamp", "Other"]


def group_of(roles) -> str:
    """Which section of the Team page someone belongs in. Bootcamp recruits
    are grouped together even when they also carry Director, so a class can
    be read as a class."""
    r = set(roles or [])
    if "Bootcamp Recruit" in r:
        return "Bootcamp"
    if "Editor" in r:
        return "Editors"
    if "Director" in r:
        return "Directors"
    if "Social Media Manager" in r:
        return "Social"
    return "Other"


def today_local() -> datetime:
    return datetime.now(timezone.utc).astimezone(TZ)


def people_index(snap: dict) -> Dict[str, dict]:
    return {t["name"]: t for t in snap.get("team", [])}


def summary(snap: dict, days: int = 90) -> dict:
    events = build_events(snap)
    today = today_local().date()
    start = today - timedelta(days=days - 1)
    s_iso, t_iso = start.isoformat(), today.isoformat()
    in_range = [e for e in events if s_iso <= e["date"] <= t_iso]

    team = people_index(snap)
    people: Dict[str, dict] = {}

    def hidden(name) -> bool:
        """Former team members (Employment Status = Inactive) stay off the page."""
        t = team.get(name)
        return bool(t) and not t.get("active", True)

    events = [e for e in events if not (e["person"] and hidden(e["person"]))]
    in_range = [e for e in in_range if not (e["person"] and hidden(e["person"]))]

    def person(name):
        if name not in people:
            t = team.get(name, {})
            roles = t.get("roles") or []
            people[name] = {"name": name, "roles": roles, "team": t.get("team"), "active": t.get("active", True),
                            "bootcamp_class": t.get("bootcamp_class"), "group": group_of(roles),
                            "photo": t.get("photo"), "in_team": name in team, "by_day": {}, "totals": {}, "windows": {},
                            "last_active": None, "quiet_days": None, "in_progress": []}
        return people[name]

    for t in team.values():
        if t.get("active") and set(t.get("roles") or []) & WORK_ROLES:
            person(t["name"])
    for e in events:
        if not e["person"]:
            continue
        p = person(e["person"])
        if e["ts"] > (p["last_active"] or ""):
            p["last_active"] = e["ts"]
    for e in in_range:
        if not e["person"]:
            continue
        p = person(e["person"])
        p["by_day"][e["date"]] = p["by_day"].get(e["date"], 0) + 1
        p["totals"][e["kind"]] = p["totals"].get(e["kind"], 0) + 1
        w = p["windows"].setdefault(e["date"], [e["time"], e["time"]])
        w[0], w[1] = min(w[0], e["time"]), max(w[1], e["time"])
    for p in people.values():
        p["active_days"] = len(p["by_day"])
        if p["last_active"]:
            last = datetime.fromisoformat(p["last_active"]).astimezone(TZ).date()
            p["quiet_days"] = (today - last).days
    now = datetime.now(timezone.utc)
    for v in snap.get("videos", []):
        st = v.get("status")
        if st not in OPEN_STATUSES or st in ("Up For Grabs", "Ready to Post"):
            continue  # nobody owns it yet / it is the social team's now
        since = parse(v.get("status_since"))
        age = (now - since).total_seconds() / 86400 if since else None
        owner = v.get("director") if st in ("Internal Review", "Client Review") else v.get("editor")
        if owner and owner in people:
            people[owner]["in_progress"].append({"video": v["id"], "title": v.get("title"), "status": st, "days": round(age, 1) if age is not None else None})
    for p in people.values():
        p["in_progress"].sort(key=lambda x: -(x["days"] or 0))

    pipeline = {st: {"count": 0, "stuck": []} for st in OPEN_STATUSES}
    for v in snap.get("videos", []):
        st = v.get("status")
        if st not in pipeline:
            continue
        pipeline[st]["count"] += 1
        since = parse(v.get("status_since"))
        age = (now - since).total_seconds() / 86400 if since else None
        if age is not None and age >= STUCK_AFTER_DAYS.get(st, 99):
            pipeline[st]["stuck"].append({"video": v["id"], "title": v.get("title"), "days": round(age, 1),
                                          "owner": v.get("director") if st in ("Internal Review", "Client Review") else v.get("editor"), "show": v.get("show")})
    for st in pipeline:
        pipeline[st]["stuck"].sort(key=lambda x: -x["days"])

    plist = sorted(people.values(), key=lambda p: (GROUP_ORDER.index(p["group"]), p["bootcamp_class"] or "", -(sum(p["by_day"].values())), p["name"]))
    return {"tz": TZ_NAME, "today": t_iso, "start": s_iso, "days": days, "people": plist, "events": in_range, "pipeline": pipeline,
            "kinds": KIND_LABEL}


# ── daily digest ─────────────────────────────────────────────────────────

def _fmt_day(d) -> str:
    return d.strftime("%a %b %-d") if os.name != "nt" else d.strftime("%a %b %d")


def digest(snap: dict, day_from, day_to=None) -> dict:
    """Plain text + Slack blocks for the activity between two local dates
    (inclusive). Monday's report normally covers Friday to Sunday."""
    day_to = day_to or day_from
    f_iso, t_iso = day_from.isoformat(), day_to.isoformat()
    events = [e for e in build_events(snap) if f_iso <= e["date"] <= t_iso]
    team = people_index(snap)
    today = today_local().date()
    by_person: Dict[str, list] = defaultdict(list)
    for e in events:
        if e["person"]:
            by_person[e["person"]].append(e)

    def roles_of(name):
        return set(team.get(name, {}).get("roles") or [])

    def window(evs):
        times = sorted(e["time"] for e in evs)
        return f"{times[0]}–{times[-1]}" if len(times) > 1 else times[0]

    editors, directors, social, quiet = [], [], [], []
    names = sorted((set(by_person) | {n for n, t in team.items() if t.get("active") and roles_of(n) & WORK_ROLES})
                   - {n for n, t in team.items() if not t.get("active", True)})
    for name in names:
        evs = by_person.get(name, [])
        r = roles_of(name)
        kinds = defaultdict(int)
        for e in evs:
            kinds[e["kind"]] += 1
        if not evs:
            if r & WORK_ROLES:
                last = team.get(name, {}).get("_last")  # filled below
                quiet.append(name)
            continue
        edit_bits = []
        fin = [e for e in evs if e["kind"] == "finished"]
        if fin:
            avg = sum(e["minutes"] or 0 for e in fin) / len(fin)
            edit_bits.append(f"finished {len(fin)} (avg {fmt_dur(avg)} in Editing)")
        for k, lab in [("started", "started"), ("picked_up", "picked up"), ("revision_started", "revisions"), ("dropped", "dropped"), ("created", "created")]:
            if kinds[k]:
                edit_bits.append(f"{lab} {kinds[k]}")
        dir_bits = []
        if kinds["approved"]:
            dir_bits.append(f"approved {kinds['approved']}")
        if kinds["sent_back"]:
            dir_bits.append(f"sent back {kinds['sent_back']}")
        if kinds["client_review_closed"]:
            dir_bits.append(f"client reviews closed {kinds['client_review_closed']}")
        posts = [e for e in evs if e["kind"] == "posted"]
        if posts:
            plat = defaultdict(int)
            accts = []
            for e in posts:
                plat[e.get("platform") or "?"] += 1
                a = (e.get("detail") or "").split(" · ")[-1]
                if a and a not in accts:
                    accts.append(a)
            social.append(f"*{name}* — {len(posts)} posts (" + ", ".join(f"{p} {n}" for p, n in sorted(plat.items(), key=lambda x: -x[1])) + ")"
                          + (" · " + ", ".join(accts[:6]) + ("…" if len(accts) > 6 else "") if accts else "") + f" · {window(posts)}")
        if edit_bits:
            bc = team.get(name, {}).get("bootcamp_class")
            tag = f" ({bc})" if "Bootcamp Recruit" in r and bc else ""
            editors.append(f"*{name}*{tag} — " + ", ".join(edit_bits) + f" · {window([e for e in evs if e['role'] in ('editor', 'miner')])}")
        if dir_bits:
            directors.append(f"*{name}* — " + ", ".join(dir_bits) + f" · {window([e for e in evs if e['role'] == 'director'])}")

    # quiet people: when were they last seen?
    last_seen: Dict[str, str] = {}
    for e in build_events(snap):
        if e["person"]:
            last_seen[e["person"]] = e["date"]
    quiet_lines = []
    for name in quiet:
        ls = last_seen.get(name)
        if ls and ls > t_iso:
            continue  # nothing in the window, but active since — not quiet
        if ls:
            gap = (day_to - datetime.strptime(ls, "%Y-%m-%d").date()).days
            quiet_lines.append(f"{name} (last seen {_fmt_day(datetime.strptime(ls, '%Y-%m-%d'))}, {gap}d before)")
        else:
            quiet_lines.append(f"{name} (no activity in the loaded window)")

    # pipeline now
    now = datetime.now(timezone.utc)
    counts = defaultdict(int)
    stuck = []
    for v in snap.get("videos", []):
        st = v.get("status")
        if st in OPEN_STATUSES:
            counts[st] += 1
            since = parse(v.get("status_since"))
            age = (now - since).total_seconds() / 86400 if since else None
            if age is not None and age >= STUCK_AFTER_DAYS.get(st, 99):
                owner = v.get("director") if st in ("Internal Review", "Client Review") else v.get("editor")
                stuck.append((age, f"“{(v.get('title') or '')[:60]}” in {st} {age:.0f}d" + (f" ({owner})" if owner else "")))
    stuck.sort(key=lambda x: -x[0])
    pipe = " · ".join(f"{st} {counts[st]}" for st in OPEN_STATUSES if counts[st])

    title = f"Daily report · {_fmt_day(day_from)}" + (f" – {_fmt_day(day_to)}" if day_to != day_from else "")
    sections = []
    sections.append(("Editors", editors or ["No editing activity."]))
    sections.append(("Directors", directors or ["No reviews."]))
    sections.append(("Social", social or ["No posts."]))
    if quiet_lines:
        sections.append(("No activity", quiet_lines))
    sections.append(("Pipeline now", [pipe or "empty"] + ([f"Stuck: " + "; ".join(s for _, s in stuck[:6])] if stuck else [])))

    text = title + "\n\n" + "\n\n".join(f"{h}\n" + "\n".join(lines) for h, lines in sections)
    blocks = [{"type": "header", "text": {"type": "plain_text", "text": title}}]
    for h, lines in sections:
        body = f"*{h}*\n" + "\n".join(lines)
        for chunk in _chunks(body, 2900):
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": chunk}})
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": f"Times in {TZ_NAME}. Full detail: api.goodfuturemedia.com/dashboard/team"}]})
    return {"title": title, "text": text, "blocks": blocks, "from": f_iso, "to": t_iso, "events": len(events)}


def _chunks(s: str, n: int) -> List[str]:
    out, cur = [], ""
    for line in s.split("\n"):
        if len(cur) + len(line) + 1 > n and cur:
            out.append(cur)
            cur = ""
        cur = (cur + "\n" + line) if cur else line
    if cur:
        out.append(cur)
    return out


def digest_window(for_day=None):
    """Which days a report sent on `for_day` (default today) covers: the
    previous day, or Friday–Sunday on a Monday."""
    d = for_day or today_local().date()
    if d.weekday() == 0:
        return d - timedelta(days=3), d - timedelta(days=1)
    return d - timedelta(days=1), d - timedelta(days=1)
