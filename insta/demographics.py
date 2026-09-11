"""
Weekly Instagram audience demographics → Airtable "Audience Demographics".

Instagram reports follower age, gender, country and city for professional
accounts with at least 100 followers (metric follower_demographics). This
finds every Instagram account we can reach — through the Pages in
FACEBOOK_PAGES, plus the Business's owned/client accounts when
META_SYSTEM_USER_TOKEN and META_BUSINESS_ID are set — matches each to a
Channel by handle, and writes one row per segment for the current week.
Rows are keyed so a rerun in the same week adds nothing twice.

Runs from fb/main.py on the first Facebook run of each Monday (UTC), or
whenever DEMOGRAPHICS_FORCE=1.
"""
import json
import os
import sys
import time
from datetime import date, datetime, timedelta
from urllib.parse import urlparse

import requests

GRAPH = "https://graph.facebook.com/v21.0"
API = "https://api.airtable.com/v0"
BASE = os.getenv("AIRTABLE_BASE_ID", "appxCYu0Tfwc6h7X7")
CHANNELS = os.getenv("AIRTABLE_CHANNELS_TABLE_ID", "tblP8bh3crTHLVoZA")
TABLE = os.getenv("DEMOGRAPHICS_TABLE_ID", "tblG4ElwziblQZM9E")
BREAKDOWNS = [("age", "Age"), ("gender", "Gender"), ("country", "Country"), ("city", "City")]
GENDER = {"F": "Women", "M": "Men", "U": "Unknown"}


def _headers():
    return {"Authorization": f"Bearer {os.environ['AIRTABLE_PERSONAL_ACCESS_TOKEN']}", "Content-Type": "application/json"}


def due() -> bool:
    if os.getenv("DEMOGRAPHICS_FORCE") == "1":
        return True
    now = datetime.utcnow()
    return now.weekday() == 0 and now.hour < 6


def pages():
    raw = os.getenv("FACEBOOK_PAGES", "[]")
    try:
        return json.JSONDecoder().raw_decode(raw.strip())[0]
    except Exception:
        return []


def ig_accounts():
    """[{id, username, token, via}] for every reachable Instagram account."""
    seen, out = set(), []
    for p in pages():
        tok, pid = p.get("page_access_token"), p.get("page_id")
        if not tok or not pid:
            continue
        r = requests.get(f"{GRAPH}/{pid}", params={"fields": "instagram_business_account{id,username}", "access_token": tok}, timeout=30)
        ig = (r.json().get("instagram_business_account") or {}) if r.status_code == 200 else {}
        if ig.get("id") and ig["id"] not in seen:
            seen.add(ig["id"])
            out.append({"id": ig["id"], "username": ig.get("username"), "token": tok, "via": p.get("page_name")})
    sys_tok, biz = os.getenv("META_SYSTEM_USER_TOKEN"), os.getenv("META_BUSINESS_ID")
    if sys_tok and biz:
        for edge in ("owned_instagram_accounts", "client_instagram_accounts"):
            r = requests.get(f"{GRAPH}/{biz}/{edge}", params={"fields": "id,username", "access_token": sys_tok, "limit": 100}, timeout=30)
            if r.status_code != 200:
                print(f"   {edge}: HTTP {r.status_code} {r.text[:200]}")
                continue
            for ig in r.json().get("data", []):
                if ig["id"] not in seen:
                    seen.add(ig["id"])
                    out.append({"id": ig["id"], "username": ig.get("username"), "token": sys_tok, "via": edge})
    return out


def handle(url):
    if not url:
        return None
    path = [x for x in urlparse(url.strip()).path.split("/") if x]
    return path[0].lower().lstrip("@") if path else None


def channels_by_handle():
    out, offset = {}, None
    while True:
        params = {"fields[]": ["Social Media Account", "IG Profile", "Status"], "pageSize": 100}
        if offset:
            params["offset"] = offset
        r = requests.get(f"{API}/{BASE}/{CHANNELS}", headers=_headers(), params=params, timeout=30)
        r.raise_for_status()
        body = r.json()
        for rec in body.get("records", []):
            h = handle(rec["fields"].get("IG Profile"))
            if h:
                out[h] = rec
        offset = body.get("offset")
        if not offset:
            return out


def fetch(ig):
    """{dimension_label: [(segment, followers)]} for one account."""
    result = {}
    for breakdown, label in BREAKDOWNS:
        r = requests.get(f"{GRAPH}/{ig['id']}/insights",
                         params={"metric": "follower_demographics", "period": "lifetime", "metric_type": "total_value",
                                 "breakdown": breakdown, "access_token": ig["token"]}, timeout=30)
        if r.status_code != 200:
            err = (r.json().get("error") or {}).get("message", r.text[:200]) if r.headers.get("content-type", "").startswith("application/json") else r.text[:200]
            print(f"   {ig['username']} {breakdown}: HTTP {r.status_code} {err}")
            continue
        rows = []
        for m in r.json().get("data", []):
            for bd in (m.get("total_value") or {}).get("breakdowns", []):
                for res in bd.get("results", []):
                    seg = (res.get("dimension_values") or ["?"])[0]
                    if breakdown == "gender":
                        seg = GENDER.get(seg, seg)
                    rows.append((seg, int(res.get("value") or 0)))
        rows.sort(key=lambda x: -x[1])
        result[label] = rows
        time.sleep(0.2)
    return result


def existing_keys(week):
    keys, offset = set(), None
    while True:
        params = {"fields[]": ["Key"], "filterByFormula": f"IS_SAME({{Week}}, '{week}', 'day')", "pageSize": 100}
        if offset:
            params["offset"] = offset
        r = requests.get(f"{API}/{BASE}/{TABLE}", headers=_headers(), params=params, timeout=30)
        r.raise_for_status()
        body = r.json()
        keys.update(rec["fields"].get("Key") for rec in body.get("records", []))
        offset = body.get("offset")
        if not offset:
            return keys


def run():
    today = date.today()
    week = (today - timedelta(days=today.weekday())).isoformat()
    print(f"\n👥 Instagram audience demographics for week of {week}")
    by_handle = channels_by_handle()
    have = existing_keys(week)
    accounts = ig_accounts()
    print(f"   {len(accounts)} Instagram accounts reachable, {len(by_handle)} channels with an IG handle")
    created = 0
    for ig in accounts:
        ch = by_handle.get((ig.get("username") or "").lower())
        if not ch:
            print(f"   {ig.get('username')}: no channel with that IG handle, skipped")
            continue
        name = ch["fields"].get("Social Media Account")
        data = fetch(ig)
        batch = []
        for dim, rows in data.items():
            for seg, n in rows:
                key = f"{name} · Instagram · {dim} · {seg} · {week}"
                if key in have:
                    continue
                batch.append({"fields": {"Key": key, "Social Media Account": [ch["id"]], "Platform": "Instagram",
                                         "Dimension": dim, "Segment": seg, "Followers": n, "Week": week,
                                         "Source": "Instagram follower_demographics"}})
        for i in range(0, len(batch), 10):
            r = requests.post(f"{API}/{BASE}/{TABLE}", headers=_headers(), json={"records": batch[i:i + 10]}, timeout=30)
            if r.status_code != 200:
                print(f"   Airtable write failed: {r.status_code} {r.text[:200]}")
                break
            time.sleep(0.22)
        created += len(batch)
        print(f"   {name}: " + ", ".join(f"{d} {len(v)}" for d, v in data.items()) + f" → {len(batch)} rows")
    print(f"   done: {created} rows for week {week}")


def run_if_due():
    if due():
        run()
    else:
        print("\n👥 demographics: not due (runs on the first Monday run, or DEMOGRAPHICS_FORCE=1)")


if __name__ == "__main__":
    run()
