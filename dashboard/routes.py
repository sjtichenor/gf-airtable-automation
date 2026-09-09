"""HTTP surface of the dashboard: /dashboard, /dashboard/login, /dashboard/api/*."""
import html
import os
import time
from urllib.parse import parse_qs

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from . import auth
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


def start_background() -> None:
    cache.start()
