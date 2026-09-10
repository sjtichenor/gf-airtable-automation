"""HTTP surface of the dashboard: /dashboard, /dashboard/login, /dashboard/api/*."""
import html
import os
import time
from urllib.parse import parse_qs

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from . import activity, auth, slack
from .data import cache

router = APIRouter(prefix="/dashboard")
HERE = os.path.dirname(__file__)


def login_page(error: str = "") -> str:
    # str.format would trip on the CSS braces; a marker swap is safer.
    return LOGIN.replace("<!--ERROR-->", error)


def _read(name: str) -> str:
    with open(os.path.join(HERE, name), encoding="utf-8") as fh:
        return fh.read()


LOGIN = """<!doctype html><html><head><meta charset="utf-8"><title>Good Future Media · Sign in</title>
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
<h1>Good Future Media</h1><p>Analytics dashboard. Team password.</p>
<input type="password" name="password" placeholder="Password" autofocus autocomplete="current-password">
<button type="submit">Sign in</button><!--ERROR--></form></body></html>"""


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def home(request: Request):
    if not auth.is_authed(request):
        return RedirectResponse("/dashboard/login", status_code=303)
    return HTMLResponse(_read("index.html"))


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
