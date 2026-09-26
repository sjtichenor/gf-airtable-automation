"""HTTP surface of the dashboard: /dashboard, /dashboard/login, /dashboard/api/*."""
import html
import os
import time
from urllib.parse import parse_qs, urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from . import activity, auth, slack
from .data import BASE, EP, FAKE as FAKE_DATA, TABLES, TOKEN, API as AIRTABLE_API, cache, client_view, slugify
from datetime import datetime
import requests

router = APIRouter(prefix="/dashboard")
HERE = os.path.dirname(__file__)


def login_page(error: str = "", title: str = "Good Future Media", heading: str = "Good Future Media",
               blurb: str = "Analytics dashboard. Team password.", action: str = "/dashboard/login",
               google_next: str = "") -> str:
    # str.format would trip on the CSS braces; marker swaps are safer.
    google = ""
    if google_next and auth.google_configured():
        google = (f'<a class="google" href="/dashboard/auth/google?next={html.escape(google_next)}">'
                  '<svg width="18" height="18" viewBox="0 0 48 48" aria-hidden="true"><path fill="#EA4335" d="M24 9.5c3.5 0 6.6 1.2 9.1 3.6l6.8-6.8C35.8 2.4 30.3 0 24 0 14.6 0 6.5 5.4 2.6 13.3l7.9 6.1C12.4 13.6 17.7 9.5 24 9.5z"/><path fill="#4285F4" d="M46.5 24.5c0-1.6-.1-3.1-.4-4.5H24v9h12.7c-.6 3-2.3 5.5-4.8 7.2l7.5 5.8c4.4-4 7.1-10 7.1-17.5z"/><path fill="#FBBC05" d="M10.5 28.6A14.5 14.5 0 0 1 9.5 24c0-1.6.3-3.1.8-4.6l-7.9-6.1A24 24 0 0 0 0 24c0 3.9.9 7.5 2.6 10.7l7.9-6.1z"/><path fill="#34A853" d="M24 48c6.5 0 11.9-2.1 15.9-5.8l-7.5-5.8c-2.1 1.4-4.9 2.3-8.4 2.3-6.3 0-11.6-4.1-13.5-9.8l-7.9 6.1C6.5 42.6 14.6 48 24 48z"/></svg>'
                  'Sign in with Google</a><div class="or">or use the team password</div>')
        blurb = "Analytics dashboard."
    return (LOGIN.replace("<!--ERROR-->", error).replace("<!--TITLE-->", html.escape(title))
            .replace("<!--HEADING-->", html.escape(heading)).replace("<!--BLURB-->", html.escape(blurb))
            .replace("<!--GOOGLE-->", google)
            .replace('action="/dashboard/login"', f'action="{action}"'))


def _read(name: str) -> str:
    with open(os.path.join(HERE, name), encoding="utf-8") as fh:
        return fh.read()


LOGIN = """<!doctype html><html><head><meta charset="utf-8"><title><!--TITLE--> · Sign in</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" href="/favicon.svg" type="image/svg+xml"><link rel="icon" href="/favicon.ico" sizes="32x32"><link rel="apple-touch-icon" href="/apple-touch-icon.png">
<link rel="preconnect" href="https://fonts.googleapis.com"><link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
:root{color-scheme:light dark}
body{margin:0;min-height:100vh;display:grid;place-items:center;font-family:Inter,-apple-system,Segoe UI,sans-serif;background:#0e1116;color:#e6e8ec}
form{width:min(360px,90vw);padding:32px;border-radius:16px;background:#161b22;border:1px solid #262d38;box-shadow:0 20px 60px rgba(0,0,0,.4)}
h1{font-size:18px;margin:0 0 4px} p{margin:0 0 20px;color:#8b95a5;font-size:14px}
input{width:100%;box-sizing:border-box;padding:12px 14px;border-radius:10px;border:1px solid #2c3441;background:#0e1116;color:#fff;font-size:15px}
button{margin-top:12px;width:100%;padding:12px;border:0;border-radius:10px;background:#4f8cff;color:#fff;font-weight:600;font-size:15px;cursor:pointer}
.err{color:#ff7b72;font-size:13px;margin:10px 0 0}
.google{display:flex;align-items:center;justify-content:center;gap:10px;width:100%;padding:11px;border-radius:10px;background:#fff;color:#1f2937;font-weight:600;font-size:15px;text-decoration:none;border:1px solid #2c3441}
.google:hover{background:#f3f4f6}
.or{color:#8b95a5;font-size:12px;text-align:center;margin:14px 0 10px}
</style></head><body><form method="post" action="/dashboard/login">
<h1><!--HEADING--></h1><p><!--BLURB--></p>
<!--GOOGLE-->
<input type="password" name="password" placeholder="Password" autocomplete="current-password">
<button type="submit">Sign in</button><!--ERROR--></form></body></html>"""


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def home(request: Request):
    if not auth.is_authed(request):
        return RedirectResponse("/dashboard/login", status_code=303)
    return HTMLResponse(_page_with_mode({"mode": "team"}))


# Built from the live configuration rather than a hand-kept list, so a client
# added or dropped is reflected here without anyone remembering to edit it.
def _client_rows(session=None) -> str:
    from .data import (client_also_by_client, client_groups, client_matches, client_video_accounts,
                       no_follower_clients, no_show_clients)
    groups, matches = client_groups(), client_matches()
    also = client_also_by_client()
    hidden_followers, hidden_shows = no_follower_clients(), no_show_clients()
    by_account = client_video_accounts()
    exec_only = auth.exec_only_clients()
    snap = cache.snapshot or {}
    channel_names = {c["name"].lower(): c["name"] for c in snap.get("channels", [])}

    rows = []
    for slug in sorted(auth.client_passwords()):
        if slug in exec_only and not auth.is_exec(session, snap.get("team", [])):
            continue  # not listed for colleagues who cannot open it
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
        if slug in also:
            caveats.append("plus anything tagged " + " or ".join(sorted(n.upper() if len(n) <= 4 else n.title() for n in also[slug])) + " in Client Account, wherever it ran")
        if slug in exec_only:
            caveats.append("exec-only: other team members do not see this page")
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
    session = auth.is_authed(request)
    team = (cache.snapshot or {}).get("team", [])
    visible = [s for s in auth.client_passwords() if s not in auth.exec_only_clients() or auth.is_exec(session, team)]
    n = len(visible)
    note = f"{n} report{'' if n == 1 else 's'} \u00b7 one password each \u00b7 attribution stripped server-side"
    page = (_read("links.html")
            .replace("<!--CLIENTS-->", _client_rows(session))
            .replace("<!--CLIENT_NOTE-->", html.escape(note))
            .replace("<!--REFRESH-->", html.escape(_every_minutes())))
    return HTMLResponse(page)


def _page_with_mode(mode: dict) -> str:
    import json
    return _read("index.html").replace("<!--MODE-->", "<script>window.GF_MODE=" + json.dumps(mode) + "</script>")


def _safe_next(value: str) -> str:
    return value if (value or "").startswith("/") and not value.startswith("//") else "/dashboard"


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request, next: str = "/dashboard", denied: str = ""):
    if auth.is_authed(request):
        return RedirectResponse(_safe_next(next), status_code=303)
    err = ""
    if denied:
        err = f"<p class='err'>{html.escape(denied)}</p>"
    elif not auth.configured() and not auth.google_configured():
        err = "<p class='err'>Neither DASHBOARD_PASSWORD nor Google sign-in is set up on the server.</p>"
    return HTMLResponse(login_page(err, google_next=_safe_next(next)))


# ── Google sign-in ──
@router.get("/auth/google")
def google_start(next: str = "/dashboard"):
    if not auth.google_configured():
        return RedirectResponse("/dashboard/login?denied=Google+sign-in+is+not+configured", status_code=303)
    url, state = auth.start_google(_safe_next(next))
    resp = RedirectResponse(url, status_code=303)
    auth.set_state_cookie(resp, state)
    return resp


@router.get("/auth/google/callback")
def google_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    def bounce(msg: str):
        return RedirectResponse("/dashboard/login?" + urlencode({"denied": msg}), status_code=303)
    if error:
        return bounce(f"Google said: {error}")
    st = auth.read_state(request.cookies.get(auth.STATE_COOKIE, ""), state)
    if not st or not code:
        return bounce("That sign-in link expired or did not match. Try again.")
    try:
        info = auth.google_userinfo(code)
    except Exception as exc:
        return bounce(f"Google sign-in failed: {exc}")
    team_rows = (cache.snapshot or {}).get("team", [])
    who = auth.allowed_email(info["email"], team_rows)
    if not who:
        return bounce(f"{info['email']} is not on the team. Ask Spencer to add you to the Team table.")
    name = who.get("name") or info.get("name") or info["email"]
    resp = RedirectResponse(st.get("next") or "/dashboard", status_code=303)
    auth.set_cookie(resp, auth.make_session(info["email"], name, "google"))
    resp.delete_cookie(auth.STATE_COOKIE, path="/dashboard/auth")
    return resp


@router.post("/login", response_class=HTMLResponse)
async def login(request: Request):
    ip = auth.client_ip(request)
    if auth.throttled(ip):
        return HTMLResponse(login_page("<p class='err'>Too many attempts. Try again in ten minutes.</p>"), status_code=429)
    body = (await request.body()).decode("utf-8", "replace")
    candidate = parse_qs(body).get("password", [""])[0]
    if auth.check_password(candidate):
        resp = RedirectResponse("/dashboard", status_code=303)
        auth.set_cookie(resp)  # the shared password: an anonymous team session
        return resp
    auth.record_failure(ip)
    time.sleep(1)
    return HTMLResponse(login_page("<p class='err'>That's not it.</p>", google_next="/dashboard"), status_code=401)


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


@router.get("/vc", response_class=HTMLResponse)
def vc_page(request: Request):
    """VC podcast rankings by following. Any signed-in team member."""
    if not auth.is_authed(request):
        return RedirectResponse("/dashboard/login", status_code=303)
    return HTMLResponse(_read("vc.html"))


@router.get("/api/vc")
def api_vc(request: Request):
    if not auth.is_authed(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    snap = cache.snapshot
    if snap is None:
        return JSONResponse({"error": "warming up", **cache.status()}, status_code=503, headers={"Retry-After": "5"})
    from . import vc_pods
    return JSONResponse(vc_pods.table(snap), headers={"Cache-Control": "private, max-age=120"})


@router.get("/team/{team_id}", response_class=HTMLResponse)
def person_page(request: Request, team_id: str):
    if not auth.is_authed(request):
        return RedirectResponse("/dashboard/login", status_code=303)
    return HTMLResponse(_read("person.html"))


@router.get("/api/health")
def api_health(request: Request):
    """Follower data-health findings, the same audit the snapshot cron runs."""
    if not auth.is_authed(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    snap = cache.snapshot
    if snap is None:
        return JSONResponse({"error": "warming up", **cache.status()}, status_code=503, headers={"Retry-After": "5"})
    from . import health_view
    return JSONResponse(health_view.summary(snap), headers={"Cache-Control": "private, max-age=300"})


@router.get("/api/person/{team_id}")
def api_person(request: Request, team_id: str):
    if not auth.is_authed(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    snap = cache.snapshot
    if snap is None:
        return JSONResponse({"error": "warming up", **cache.status()}, status_code=503, headers={"Retry-After": "5"})
    prof = activity.person_profile(snap, team_id)
    if not prof:
        return JSONResponse({"error": "no such person"}, status_code=404)
    return JSONResponse(prof, headers={"Cache-Control": "private, max-age=60"})


def _me(request: Request, snap: dict) -> dict:
    """Identity for the pages: name, team id if the Team table knows them,
    and whether they may act as someone else."""
    session = auth.is_authed(request) or {}
    row = auth.team_row_for(session, snap.get("team", []))
    return {"name": (row or {}).get("name") or session.get("name") or "Team",
            "email": session.get("email"), "team_id": (row or {}).get("id"),
            "admin": auth.is_admin(session), "exec": auth.is_exec(session, snap.get("team", [])),
            "anonymous": session.get("sub") == "shared"}


@router.get("/api/whoami")
def api_whoami(request: Request):
    if not auth.is_authed(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return JSONResponse(_me(request, cache.snapshot or {}))


@router.post("/api/stripe/sync")
def api_stripe_sync(request: Request, dry: int = 0):
    """Run the Stripe -> Airtable invoice sync now. Admins only; the timer in
    invoicing.stripe_sync does the same thing every 15 minutes unattended."""
    session = auth.is_authed(request)
    if not session:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if not auth.is_admin(session):
        return JSONResponse({"error": "admins only"}, status_code=403)
    from invoicing import stripe_sync
    if not stripe_sync.configured():
        return JSONResponse({"error": "STRIPE_API_KEY / AIRTABLE_INVOICES_TOKEN not set"}, status_code=503)
    try:
        return JSONResponse(stripe_sync.sync(dry_run=bool(dry)))
    except Exception as e:
        return JSONResponse({"error": f"{type(e).__name__}: {str(e)[:300]}"}, status_code=502)


@router.post("/api/team-months/sync")
def api_team_months_sync(request: Request, dry: int = 0):
    """Rebuild Team and Team Months in the Invoices base now. Admins only."""
    session = auth.is_authed(request)
    if not session:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if not auth.is_admin(session):
        return JSONResponse({"error": "admins only"}, status_code=403)
    from invoicing import team_months
    if not team_months.configured():
        return JSONResponse({"error": "AIRTABLE_INVOICES_TOKEN not set"}, status_code=503)
    if not cache.snapshot:
        return JSONResponse({"error": "snapshot not ready"}, status_code=503)
    try:
        return JSONResponse(team_months.sync(cache.snapshot, dry_run=bool(dry)))
    except Exception as e:
        return JSONResponse({"error": f"{type(e).__name__}: {str(e)[:300]}"}, status_code=502)


@router.get("/api/team-months/status")
def api_team_months_status(request: Request):
    if not auth.is_authed(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    from invoicing import team_months
    return JSONResponse({"configured": team_months.configured(), **team_months.last})


@router.post("/api/x-followers/sync")
def api_x_followers_sync(request: Request, dry: int = 0):
    """Refresh X follower counts on Channels now. Admins only."""
    session = auth.is_authed(request)
    if not session:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if not auth.is_admin(session):
        return JSONResponse({"error": "admins only"}, status_code=403)
    from followers import x_followers
    if not x_followers.configured():
        return JSONResponse({"error": "TWITTER_BEARER_TOKEN not set"}, status_code=503)
    try:
        return JSONResponse(x_followers.sync(dry_run=bool(dry)))
    except Exception as e:
        return JSONResponse({"error": f"{type(e).__name__}: {str(e)[:300]}"}, status_code=502)


@router.get("/api/x-followers/status")
def api_x_followers_status(request: Request):
    if not auth.is_authed(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    from followers import x_followers
    return JSONResponse({"configured": x_followers.configured(), **x_followers.last})


@router.post("/api/source-shows/sync")
def api_source_shows_sync(request: Request, dry: int = 0, limit: int = 0):
    """Label videos with the show they were cut from, now. Admins only."""
    session = auth.is_authed(request)
    if not session:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if not auth.is_admin(session):
        return JSONResponse({"error": "admins only"}, status_code=403)
    from videos import source_show
    if not source_show.configured():
        return JSONResponse({"error": "ANTHROPIC_API_KEY not set"}, status_code=503)
    try:
        return JSONResponse(source_show.sync(dry_run=bool(dry), limit=limit or None))
    except Exception as e:
        return JSONResponse({"error": f"{type(e).__name__}: {str(e)[:300]}"}, status_code=502)


@router.get("/api/source-shows/preview")
def api_source_shows_preview(request: Request, limit: int = 30):
    """Dry run as a link: what the next pass would write, nothing written. Admins only."""
    session = auth.is_authed(request)
    if not session:
        return JSONResponse({"error": "unauthorized; sign in to the dashboard first"}, status_code=401)
    if not auth.is_admin(session):
        return JSONResponse({"error": "admins only"}, status_code=403)
    from videos import source_show
    if not source_show.configured():
        return JSONResponse({"error": "ANTHROPIC_API_KEY not set on gf-api yet"}, status_code=503)
    try:
        return JSONResponse(source_show.sync(dry_run=True, limit=max(1, min(limit, 100))))
    except Exception as e:
        return JSONResponse({"error": f"{type(e).__name__}: {str(e)[:300]}"}, status_code=502)


@router.post("/api/link-episodes/sync")
def api_link_episodes_sync(request: Request, dry: int = 0, limit: int = 0):
    """Link clips to Full Episodes now. Admins only; ?dry=1 plans without writing."""
    session = auth.is_authed(request)
    if not session:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if not auth.is_admin(session):
        return JSONResponse({"error": "admins only"}, status_code=403)
    from videos import link_episodes
    try:
        return JSONResponse(link_episodes.sync(dry_run=bool(dry), limit=limit or None))
    except Exception as e:
        return JSONResponse({"error": f"{type(e).__name__}: {str(e)[:300]}"}, status_code=502)


@router.get("/api/link-episodes/preview")
def api_link_episodes_preview(request: Request, limit: int = 100):
    """The same plan as a link to open signed in: what would be linked and why the rest was not."""
    session = auth.is_authed(request)
    if not session:
        return JSONResponse({"error": "unauthorized; sign in to the dashboard first"}, status_code=401)
    if not auth.is_admin(session):
        return JSONResponse({"error": "admins only"}, status_code=403)
    from videos import link_episodes
    try:
        return JSONResponse(link_episodes.sync(dry_run=True, limit=max(1, min(limit, 500))))
    except Exception as e:
        return JSONResponse({"error": f"{type(e).__name__}: {str(e)[:300]}"}, status_code=502)


@router.get("/api/link-episodes/status")
def api_link_episodes_status(request: Request):
    if not auth.is_authed(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    from videos import link_episodes
    return JSONResponse({"configured": link_episodes.configured(), **link_episodes.last})


@router.get("/api/source-shows/status")
def api_source_shows_status(request: Request):
    if not auth.is_authed(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    from videos import source_show
    return JSONResponse({"configured": source_show.configured(), **source_show.last})


@router.get("/api/stripe/status")
def api_stripe_status(request: Request):
    if not auth.is_authed(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    from invoicing import stripe_sync
    return JSONResponse({"configured": stripe_sync.configured(), **stripe_sync.last})


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
        "me": _me(request, snap),
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
    # Execs hand an episode to someone; same write as a claim, in their name.
    "assign":  lambda who, now: {EP["miner"]: [who], EP["status"]: "Claimed", EP["claimed"]: now},
    # Execs park an episode that is not worth mining; it leaves the board.
    "hide":    lambda who, now: {EP["hidden"]: True},
    "unhide":  lambda who, now: {EP["hidden"]: False},
}
EXEC_ACTIONS = ("assign", "hide", "unhide")


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
    ep_id, action = body.get("episode"), body.get("action")
    if action not in MINE_ACTIONS:
        return JSONResponse({"error": "unknown action"}, status_code=400)
    episode = next((e for e in snap.get("episodes", []) if e["id"] == ep_id), None)
    if not episode:
        return JSONResponse({"error": "unknown episode"}, status_code=404)
    # A signed-in person is themselves. An admin may name someone else. The
    # shared-password session has no identity, so it may still pick a name.
    me = _me(request, snap)
    if action in EXEC_ACTIONS and not (me["admin"] or me["exec"]):
        return JSONResponse({"error": "only execs can do that"}, status_code=403)
    may_name = me["admin"] or me["anonymous"] or action == "assign"
    who = body.get("who") if may_name else me["team_id"]
    who = who or me["team_id"]
    person = next((t for t in snap.get("team", []) if t["id"] == who), None)
    if action in ("claim", "start", "assign") and not person:
        msg = ("pick who you are first" if me["anonymous"]
               else f"{me['email'] or me['name']} is not in the Team table yet -- add a row with that email")
        return JSONResponse({"error": msg}, status_code=400)

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
    if EP["hidden"] in fields:
        episode["hidden"] = fields[EP["hidden"]]
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
    session, team = auth.is_authed(request), (cache.snapshot or {}).get("team", [])
    if session and not auth.team_may_open(session, slug, team) and not auth.is_client_authed(request, slug):
        # A signed-in colleague who is not an exec: this page does not exist
        # for them. Clients themselves never carry a team session.
        return HTMLResponse("Not found", status_code=404)
    if not (auth.is_client_authed(request, slug) or auth.team_may_open(session, slug, team)):
        return RedirectResponse(f"/clients/{slug}/login", status_code=303)
    show = _client_show(slug) or {"name": slug}
    return HTMLResponse(_page_with_mode({"mode": "client", "slug": slug, "name": show.get("name"), "logo": show.get("logo")}))


@clients.get("/{slug}/login", response_class=HTMLResponse)
def client_login_form(slug: str, request: Request):
    slug = slug.lower()
    session, team = auth.is_authed(request), (cache.snapshot or {}).get("team", [])
    if session and not auth.team_may_open(session, slug, team):
        return HTMLResponse("Not found", status_code=404)
    if auth.is_client_authed(request, slug) or auth.team_may_open(session, slug, team):
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
    if not (auth.is_client_authed(request, slug)
            or auth.team_may_open(auth.is_authed(request), slug, (cache.snapshot or {}).get("team", []))):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if cache.snapshot is None:
        return JSONResponse({"error": "warming up"}, status_code=503, headers={"Retry-After": "5"})
    view = client_view(cache.snapshot, slug)
    if not view:
        return JSONResponse({"error": "unknown show"}, status_code=404)
    return JSONResponse(view, headers={"Cache-Control": "private, max-age=60"})
