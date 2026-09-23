"""HTTP surface of the dashboard: /dashboard, /dashboard/login, /dashboard/api/*."""
import html
import os
import time
from urllib.parse import parse_qs

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from . import activity, auth, slack
from .data import BASE, EP, FAKE as FAKE_DATA, TABLES, TOKEN, API as AIRTABLE_API, cache, client_view, slugify
from datetime import datetime
import requests

router = APIRouter(prefix="/dashboard")
HERE = os.path.dirname(__file__)


def login_page(error: str = "", title: str = "Good Future Media", heading: str = "Good Future Media",
               blurb: str = "Analytics dashboard. Team password.", action: str = "/dashboard/login") -> str:
    # str.format would trip on the CSS braces; marker swaps are safer.
    return (LOGIN.replace("<!--ERROR-->", error).replace("<!--TITLE-->", html.escape(title))
            .replace("<!--HEADING-->", html.escape(heading)).replace("<!--BLURB-->", html.escape(blurb))
            .replace('action="/dashboard/login"', f'action="{action}"'))


def _read(name: str) -> str:
    with open(os.path.join(HERE, name), encoding="utf-8") as fh:
        return fh.read()


LOGIN = """<!doctype html><html><head><meta charset="utf-8"><title><!--TITLE--> · Sign in</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="preconnect" href="https://fonts.googleapis.com"><link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
:root{color-scheme:light dark}
body{margin:0;min-height:100vh;display:grid;place-items:center;font-family:Inter,-apple-system,Segoe UI,sans-serif;background:#0e1116;color:#e6e8ec}
form{width:min(360px,90vw);padding:32px;border-radius:16px;background:#161b22;border:1px solid #262d38;box-shadow:0 20px 60px rgba(0,0,0,.4)}
h1{font-size:18px;margin:0 0 4px} p{margin:0 0 20px;color:#8b95a5;font-size:14px}
input{width:100%;box-sizing:border-box;padding:12px 14px;border-radius:10px;border:1px solid #2c3441;background:#0e1116;color:#fff;font-size:15px}
button{margin-top:12px;width:100%;padding:12px;border:0;border-radius:10px;background:#4f8cff;color:#fff;font-weight:600;font-size:15px;cursor:pointer}
.err{color:#ff7b72;font-size:13px;margin:10px 0 0}
</style></head><body><form method="post" action="/dashboard/login">
<h1><!--HEADING--></h1><p><!--BLURB--></p>
<input type="password" name="password" placeholder="Password" autofocus autocomplete="current-password">
<button type="submit">Sign in</button><!--ERROR--></form></body></html>"""


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def home(request: Request):
    if not auth.is_authed(request):
        return RedirectResponse("/dashboard/login", status_code=303)
    return HTMLResponse(_page_with_mode({"mode": "team"}))


# Built from the live configuration rather than a hand-kept list, so a client
# added or dropped is reflected here without anyone remembering to edit it.
def _client_rows() -> str:
    from .data import (client_groups, client_matches, client_video_accounts,
                       no_follower_clients, no_show_clients)
    groups, matches = client_groups(), client_matches()
    hidden_followers, hidden_shows = no_follower_clients(), no_show_clients()
    by_account = client_video_accounts()
    snap = cache.snapshot or {}
    channel_names = {c["name"].lower(): c["name"] for c in snap.get("channels", [])}

    rows = []
    for slug in sorted(auth.client_passwords()):
        match, group = matches.get(slug), groups.get(slug)
        if match:
            name, kind = match["name"], "by title word"
            desc = (f'Every clip whose title carries \u201c{match["match"]}\u201d, wherever it ran. Post '
                    "performance only \u2014 the clips sit on our own accounts alongside unrelated "
                    "work, so no account names, followers or audience go out.")
        elif group:
            name, kind = group["name"], "by accounts"
            listed = [channel_names.get(c, c.title()) for c in group["channels"]]
            desc = "Accounts: " + ", ".join(listed) + "."
        else:
            show = next((sh for sh in snap.get("shows", []) if slugify(sh["name"]) == slug), None)
            name = show["name"] if show else slug.replace("-", " ").title()
            kind = "by show"
            desc = ("The show, the accounts linked to it, and posts either on those accounts "
                    "or cut from its episodes.")

        caveats = []
        if slug in hidden_followers:
            caveats.append("Followers are hidden, because plenty of people post to those accounts besides us")
        if slug in by_account:
            caveats.append("videos are matched by Client Account rather than show name")
        if slug in hidden_shows:
            caveats.append("show attribution is dropped")
        if caveats:
            desc += " " + caveats[0][0].upper() + caveats[0][1:] + (
                ("; " + "; ".join(caveats[1:])) if len(caveats) > 1 else "") + "."

        rows.append(
            '<li><a class="row" href="/clients/{slug}">'
            '<span class="name">{name} <span class="arrow">&rarr;</span></span>'
            '<span class="tag client">{kind}</span>'
            '<span class="path">/clients/{slug}</span>'
            '<span class="desc">{desc}</span></a></li>'.format(
                slug=html.escape(slug), name=html.escape(name),
                kind=html.escape(kind), desc=html.escape(desc)))

    if not rows:
        rows.append('<li><a class="row" href="/dashboard"><span class="name">No client reports yet</span>'
                    '<span class="desc">A client appears here once its slug is in CLIENT_PASSWORDS.</span></a></li>')
    return "".join(rows)


def _every_minutes() -> str:
    seconds = int(os.environ.get("DASHBOARD_REFRESH_SECONDS", "900"))
    if seconds % 60:
        return f"{seconds} seconds"
    minutes = seconds // 60
    return "minute" if minutes == 1 else f"{minutes} minutes"


@router.get("/links", response_class=HTMLResponse)
def links(request: Request):
    if not auth.is_authed(request):
        return RedirectResponse("/dashboard/login", status_code=303)
    n = len(auth.client_passwords())
    note = f"{n} report{'' if n == 1 else 's'} \u00b7 one password each \u00b7 attribution stripped server-side"
    page = (_read("links.html")
            .replace("<!--CLIENTS-->", _client_rows())
            .replace("<!--CLIENT_NOTE-->", html.escape(note))
            .replace("<!--REFRESH-->", html.escape(_every_minutes())))
    return HTMLResponse(page)


def _page_with_mode(mode: dict) -> str:
    import json
    return _read("index.html").replace("<!--MODE-->", "<script>window.GF_MODE=" + json.dumps(mode) + "</script>")


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    if auth.is_authed(request):
        return RedirectResponse("/dashboard", status_code=303)
    err = "" if auth.configured() else "<p class='err'>DASHBOARD_PASSWORD is not set on the server.</p>"
    return HTMLResponse(login_page(err))


@router.post("/login", response_class=HTMLResponse)
async def login(request: Request):
    ip = auth.client_ip(request)
    if auth.throttled(ip):
        return HTMLResponse(login_page("<p class='err'>Too many attempts. Try again in ten minutes.</p>"), status_code=429)
    body = (await request.body()).decode("utf-8", "replace")
    candidate = parse_qs(body).get("password", [""])[0]
    if auth.check_password(candidate):
        resp = RedirectResponse("/dashboard", status_code=303)
        auth.set_cookie(resp)
        return resp
    auth.record_failure(ip)
    time.sleep(1)
    return HTMLResponse(login_page("<p class='err'>That's not it.</p>"), status_code=401)


@router.get("/logout")
def logout():
    resp = RedirectResponse("/dashboard/login", status_code=303)
    auth.clear_cookie(resp)
    return resp


@router.get("/ready")
def ready():
    """Health check target for Render (Settings → Health Check Path =
    /dashboard/ready). 503 until the first snapshot is in memory, so a new
    deploy only takes traffic once it can answer without the loading screen."""
    if cache.ready():
        return JSONResponse({"ready": True, "generated_at": cache.snapshot.get("generated_at") if cache.snapshot else None})
    return JSONResponse({"ready": False, "warming": True}, status_code=503)


@router.get("/api/status")
def status(request: Request):
    if not auth.is_authed(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return JSONResponse(cache.status())


@router.get("/api/data")
def data(request: Request):
    if not auth.is_authed(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if cache.snapshot is None:
        return JSONResponse({"error": "warming up", **cache.status()}, status_code=503,
                            headers={"Retry-After": "5"})
    return JSONResponse(cache.snapshot, headers={"Cache-Control": "private, max-age=60"})


@router.post("/api/refresh")
def refresh(request: Request):
    if not auth.is_authed(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    import threading
    threading.Thread(target=cache.refresh, daemon=True).start()
    return JSONResponse({"started": True})


@router.get("/team", response_class=HTMLResponse)
def team_page(request: Request):
    if not auth.is_authed(request):
        return RedirectResponse("/dashboard/login", status_code=303)
    return HTMLResponse(_read("team.html"))


@router.get("/mine", response_class=HTMLResponse)
def mine_page(request: Request):
    if not auth.is_authed(request):
        return RedirectResponse("/dashboard/login", status_code=303)
    return HTMLResponse(_read("mine.html"))


@router.get("/api/mine")
def api_mine(request: Request):
    if not auth.is_authed(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    snap = cache.snapshot
    if snap is None:
        return JSONResponse({"error": "warming up", **cache.status()}, status_code=503, headers={"Retry-After": "5"})
    people = [{"id": t["id"], "name": t["name"], "photo": t.get("photo"), "roles": t.get("roles") or []}
              for t in snap.get("team", []) if t.get("active")]
    people.sort(key=lambda t: t["name"])
    return JSONResponse({
        "generated_at": snap.get("generated_at"),
        "episodes": snap.get("episodes", []),
        "team": people,
    }, headers={"Cache-Control": "private, max-age=30"})


# What each board action writes. Miner is a link, Claimed At a datetime,
# Mining Status one of MINING_STATUS. Field ids, so a rename in Airtable
# cannot break it.
MINE_ACTIONS = {
    "claim":   lambda who, now: {EP["miner"]: [who], EP["status"]: "Claimed", EP["claimed"]: now},
    "start":   lambda who, now: {EP["miner"]: [who], EP["status"]: "Mining"},
    "release": lambda who, now: {EP["miner"]: [], EP["status"]: "Available", EP["claimed"]: None},
    "mined":   lambda who, now: {EP["status"]: "Mined"},
    "skip":    lambda who, now: {EP["status"]: "Skipped"},
}


@router.post("/api/mine/act")
async def api_mine_act(request: Request):
    """One episode, one action, written straight to Airtable and mirrored
    into the snapshot so the board reflects it without waiting for the next
    refresh. Airtable has no record locking: two people claiming the same
    episode in the same second both succeed and the last write wins, exactly
    as in the interface this replaces -- the board re-reads every 30 s."""
    if not auth.is_authed(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    snap = cache.snapshot
    if snap is None:
        return JSONResponse({"error": "warming up"}, status_code=503)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "bad json"}, status_code=400)
    ep_id, action, who = body.get("episode"), body.get("action"), body.get("who")
    if action not in MINE_ACTIONS:
        return JSONResponse({"error": "unknown action"}, status_code=400)
    episode = next((e for e in snap.get("episodes", []) if e["id"] == ep_id), None)
    if not episode:
        return JSONResponse({"error": "unknown episode"}, status_code=404)
    person = next((t for t in snap.get("team", []) if t["id"] == who), None)
    if action in ("claim", "start") and not person:
        return JSONResponse({"error": "pick who you are first"}, status_code=400)

    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    fields = MINE_ACTIONS[action](who, now)
    if not FAKE_DATA:
        r = requests.patch(f"{AIRTABLE_API}/{BASE}/{TABLES['episodes']}/{ep_id}",
                           headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
                           json={"fields": fields}, timeout=30)
        if r.status_code != 200:
            detail = ""
            try:
                detail = (r.json().get("error") or {}).get("message") or ""
            except ValueError:
                pass
            return JSONResponse({"error": f"Airtable said {r.status_code}: {detail or r.text[:200]}"}, status_code=502)

    # Mirror the write so the next paint is right.
    if EP["miner"] in fields:
        episode["miner_id"] = who if fields[EP["miner"]] else None
        episode["miner"] = person["name"] if fields[EP["miner"]] and person else None
    if EP["status"] in fields:
        episode["status"] = fields[EP["status"]]
    if EP["claimed"] in fields:
        episode["claimed"] = fields[EP["claimed"]]
    return JSONResponse({"ok": True, "episode": episode})


@router.get("/api/activity")
def api_activity(request: Request, days: int = 90):
    if not auth.is_authed(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if cache.snapshot is None:
        return JSONResponse({"error": "warming up", **cache.status()}, status_code=503, headers={"Retry-After": "5"})
    return JSONResponse(activity.summary(cache.snapshot, max(7, min(days, 180))), headers={"Cache-Control": "private, max-age=60"})


def _window(date: str = "", to: str = ""):
    from datetime import date as _d
    if date:
        a = _d.fromisoformat(date)
        b = _d.fromisoformat(to) if to else a
        return a, b
    return activity.digest_window()


@router.get("/api/digest")
def api_digest(request: Request, date: str = "", to: str = ""):
    """Preview the Slack digest as text + blocks. No date → what this
    morning's report would cover."""
    if not auth.is_authed(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if cache.snapshot is None:
        return JSONResponse({"error": "warming up", **cache.status()}, status_code=503)
    a, b = _window(date, to)
    d = activity.digest(cache.snapshot, a, b)
    d["slack_configured"] = slack.configured()
    d["recipients"] = slack.recipient_ids(cache.snapshot)
    return JSONResponse(d)


@router.post("/api/digest/send")
def api_digest_send(request: Request, date: str = "", to: str = ""):
    if not auth.is_authed(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if cache.snapshot is None:
        return JSONResponse({"error": "warming up"}, status_code=503)
    a, b = _window(date, to)
    try:
        channel = slack.send(cache.snapshot, activity.digest(cache.snapshot, a, b))
    except Exception as exc:
        return JSONResponse({"sent": False, "error": str(exc)}, status_code=500)
    return JSONResponse({"sent": True, "channel": channel, "from": a.isoformat(), "to": b.isoformat()})


_digest_state = {"last_sent": None}


def _digest_loop() -> None:
    import logging
    import time as _t
    log = logging.getLogger("dashboard.digest")
    while True:
        try:
            now = activity.today_local()
            due = (slack.configured() and cache.snapshot is not None and now.hour == slack.HOUR and now.minute < 10
                   and (now.weekday() + 1) in slack.DAYS and _digest_state["last_sent"] != now.date().isoformat())
            if due:
                _digest_state["last_sent"] = now.date().isoformat()
                a, b = activity.digest_window(now.date())
                slack.send(cache.snapshot, activity.digest(cache.snapshot, a, b))
        except Exception:
            log.exception("daily digest failed")
        _t.sleep(60)


def start_background() -> None:
    cache.start()
    import threading
    threading.Thread(target=_digest_loop, name="dashboard-digest", daemon=True).start()


# ── client dashboards: /clients/<show-slug> ──────────────────────────────
clients = APIRouter(prefix="/clients")


def _client_show(slug: str):
    if slug not in auth.client_passwords():
        return None
    if cache.snapshot is None:
        from .data import client_groups
        g = client_groups().get(slug)
        return {"name": g["name"] if g else slug.replace("-", " ").title(), "warming": True}
    view = client_view(cache.snapshot, slug)
    return view["client"] if view else None


def _client_login(slug: str, error: str = "", status: int = 200):
    show = _client_show(slug)
    if not show:
        return HTMLResponse("Not found", status_code=404)
    return HTMLResponse(login_page(error, title=show["name"], heading=show["name"],
                                   blurb="Performance report by Good Future Media.", action=f"/clients/{slug}/login"),
                        status_code=status)


@clients.get("/{slug}", response_class=HTMLResponse)
def client_home(slug: str, request: Request):
    slug = slug.lower()
    if slug not in auth.client_passwords():
        return HTMLResponse("Not found", status_code=404)
    if not auth.is_client_authed(request, slug):
        return RedirectResponse(f"/clients/{slug}/login", status_code=303)
    show = _client_show(slug) or {"name": slug}
    return HTMLResponse(_page_with_mode({"mode": "client", "slug": slug, "name": show.get("name"), "logo": show.get("logo")}))


@clients.get("/{slug}/login", response_class=HTMLResponse)
def client_login_form(slug: str, request: Request):
    slug = slug.lower()
    if auth.is_client_authed(request, slug):
        return RedirectResponse(f"/clients/{slug}", status_code=303)
    return _client_login(slug)


@clients.post("/{slug}/login", response_class=HTMLResponse)
async def client_login(slug: str, request: Request):
    slug = slug.lower()
    ip = auth.client_ip(request)
    if auth.throttled(ip):
        return _client_login(slug, "<p class='err'>Too many attempts. Try again in ten minutes.</p>", 429)
    body = (await request.body()).decode("utf-8", "replace")
    candidate = parse_qs(body).get("password", [""])[0]
    if auth.check_client_password(slug, candidate):
        resp = RedirectResponse(f"/clients/{slug}", status_code=303)
        auth.set_client_cookie(resp, slug)
        return resp
    auth.record_failure(ip)
    time.sleep(1)
    return _client_login(slug, "<p class='err'>That's not it.</p>", 401)


@clients.get("/{slug}/logout")
def client_logout(slug: str):
    slug = slug.lower()
    resp = RedirectResponse(f"/clients/{slug}/login", status_code=303)
    auth.clear_client_cookie(resp, slug)
    return resp


@clients.get("/{slug}/api/data")
def client_data(slug: str, request: Request):
    slug = slug.lower()
    if not auth.is_client_authed(request, slug):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if cache.snapshot is None:
        return JSONResponse({"error": "warming up"}, status_code=503, headers={"Retry-After": "5"})
    view = client_view(cache.snapshot, slug)
    if not view:
        return JSONResponse({"error": "unknown show"}, status_code=404)
    return JSONResponse(view, headers={"Cache-Control": "private, max-age=60"})
