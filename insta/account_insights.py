"""
Daily Instagram account insights → Airtable "Account Insights".

One row per account per day: reach and views at the account level — the
numbers Meta Business Suite shows under Insights → Reach and Views. Days
before this module existed were imported from Business Suite exports; this
keeps the table current from the Graph API with the same page tokens the
Facebook sync uses, and matches accounts to Channels by Instagram handle.

Runs from fb/main.py on every run. It re-pulls the last
INSIGHTS_LOOKBACK_DAYS (default 7) so late-arriving counts settle, and
upserts by Key ("<Channel> · Instagram · <date>"), so reruns add nothing
twice. INSIGHTS_DISABLE=1 skips it; INSIGHTS_LOOKBACK_DAYS=90 backfills.
"""
import os
import time
from datetime import date, datetime, timedelta, timezone

import requests

from insta.demographics import API, BASE, GRAPH, _headers, channels_by_handle, ig_accounts

TABLE = os.getenv("ACCOUNT_INSIGHTS_TABLE_ID", "tbl5tBiqrbwN3WgzT")
LOOKBACK = int(os.getenv("INSIGHTS_LOOKBACK_DAYS", "7"))
SOURCE = "Instagram Graph API"


def _get(url, params, token):
    r = requests.get(url, params=dict(params, access_token=token), timeout=30)
    try:
        body = r.json()
    except ValueError:
        body = {"error": {"message": r.text[:200]}}
    if r.status_code != 200:
        raise RuntimeError((body.get("error") or {}).get("message") or f"HTTP {r.status_code}")
    return body


def _ts(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp())


def daily_reach(ig, start: date, end: date) -> dict:
    """{iso date: reach}. `reach` is a time series; the API caps one request
    at 30 days, so walk the range in 30-day windows. Each value's end_time is
    the end of the reporting day in the account's own timezone, expressed in
    UTC; subtracting 12 hours before taking the date lands on the right day
    for any timezone."""
    out, cur = {}, start
    while cur <= end:
        stop = min(cur + timedelta(days=29), end)
        body = _get(f"{GRAPH}/{ig['id']}/insights",
                    {"metric": "reach", "period": "day", "since": _ts(cur), "until": _ts(stop + timedelta(days=1))},
                    ig["token"])
        for m in body.get("data", []):
            for v in m.get("values", []):
                et = v.get("end_time")
                if not et:
                    continue
                day = (datetime.strptime(et[:19], "%Y-%m-%dT%H:%M:%S") - timedelta(hours=12)).date()
                if start <= day <= end:
                    out[day.isoformat()] = int(v.get("value") or 0)
        cur = stop + timedelta(days=1)
        time.sleep(0.2)
    return out


def daily_views(ig, start: date, end: date) -> dict:
    """{iso date: views}. Account-level `views` only comes as a total for a
    window (metric_type=total_value), so ask for each day on its own."""
    out, d = {}, start
    while d <= end:
        body = _get(f"{GRAPH}/{ig['id']}/insights",
                    {"metric": "views", "period": "day", "metric_type": "total_value",
                     "since": _ts(d), "until": _ts(d + timedelta(days=1))},
                    ig["token"])
        for m in body.get("data", []):
            tv = m.get("total_value") or {}
            if "value" in tv:
                out[d.isoformat()] = int(tv["value"] or 0)
        d += timedelta(days=1)
        time.sleep(0.15)
    return out


def existing_rows(prefix: str, start: date) -> dict:
    """{Key: record id} for one account from `start` on."""
    rows, offset = {}, None
    safe = prefix.replace("'", "\\'")
    formula = f"AND(FIND('{safe}', {{Key}}) = 1, IS_AFTER({{Date}}, '{(start - timedelta(days=1)).isoformat()}'))"
    while True:
        params = {"fields[]": ["Key"], "filterByFormula": formula, "pageSize": 100}
        if offset:
            params["offset"] = offset
        r = requests.get(f"{API}/{BASE}/{TABLE}", headers=_headers(), params=params, timeout=30)
        r.raise_for_status()
        body = r.json()
        for rec in body.get("records", []):
            rows[rec["fields"].get("Key")] = rec["id"]
        offset = body.get("offset")
        if not offset:
            return rows


def _write(method, records):
    for i in range(0, len(records), 10):
        r = requests.request(method, f"{API}/{BASE}/{TABLE}", headers=_headers(), json={"records": records[i:i + 10]}, timeout=30)
        if r.status_code != 200:
            print(f"   Airtable {method} failed: {r.status_code} {r.text[:200]}")
            return False
        time.sleep(0.22)
    return True


def run(lookback: int = LOOKBACK):
    end = date.today() - timedelta(days=1)  # yesterday is the last complete day
    start = end - timedelta(days=max(lookback, 1) - 1)
    print(f"\n📡 Instagram account insights {start} → {end}")
    by_handle = channels_by_handle()
    accounts = ig_accounts()
    print(f"   {len(accounts)} Instagram accounts reachable, {len(by_handle)} channels with an IG handle")
    created = updated = 0
    for ig in accounts:
        ch = by_handle.get((ig.get("username") or "").lower())
        if not ch:
            print(f"   {ig.get('username')}: no channel with that IG handle, skipped")
            continue
        name = ch["fields"].get("Social Media Account")
        try:
            reach = daily_reach(ig, start, end)
            views = daily_views(ig, start, end)
        except Exception as exc:
            print(f"   {name}: {exc}")
            continue
        prefix = f"{name} · Instagram · "
        have = existing_rows(prefix, start)
        new, upd = [], []
        for i in range((end - start).days + 1):
            d = (start + timedelta(days=i)).isoformat()
            if d not in reach and d not in views:
                continue
            fields = {"Reach": reach.get(d), "Views": views.get(d), "Source": SOURCE}
            key = prefix + d
            if key in have:
                upd.append({"id": have[key], "fields": fields})
            else:
                new.append({"fields": dict(fields, Key=key, Platform="Instagram", Date=d,
                                           **{"Social Media Account": [ch["id"]]})})
        ok = _write("POST", new) and _write("PATCH", upd)
        if ok:
            created += len(new)
            updated += len(upd)
        print(f"   {name}: {len(reach)} days reach, {len(views)} days views → {len(new)} new, {len(upd)} refreshed")
    print(f"   done: {created} rows added, {updated} refreshed")


def run_if_enabled():
    if os.getenv("INSIGHTS_DISABLE") == "1":
        print("\n📡 account insights: disabled (INSIGHTS_DISABLE=1)")
        return
    run()


if __name__ == "__main__":
    run()
