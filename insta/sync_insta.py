import os
import sys
import time

import requests

from insta_sync import InstagramDynamicSync


def repair_subtracted_views(sync):
    """One-off: the 2026-09-16 run subtracted Facebook views from Instagram
    posts under the wrong assumption. Every post it touched carries the raw
    figure in "Views incl. Facebook"; put that back into Views and clear it."""
    print("IG_REPAIR: restoring Views from 'Views incl. Facebook'")
    offset, fixed = None, 0
    while True:
        params = {'pageSize': 100, 'filterByFormula': "{Views incl. Facebook}",
                  'fields[]': ['Views', 'Views incl. Facebook']}
        if offset:
            params['offset'] = offset
        r = requests.get(sync.posts_table_url, headers=sync.airtable_headers, params=params)
        r.raise_for_status()
        body = r.json()
        batch = [{'id': rec['id'], 'fields': {'Views': rec['fields']['Views incl. Facebook'], 'Views incl. Facebook': None}}
                 for rec in body.get('records', []) if rec['fields'].get('Views incl. Facebook') is not None]
        for i in range(0, len(batch), 10):
            pr = requests.patch(sync.posts_table_url, headers=sync.airtable_headers, json={'records': batch[i:i + 10]})
            if pr.status_code != 200:
                print(f"  write failed: {pr.status_code} {pr.text[:200]}")
            else:
                fixed += len(batch[i:i + 10])
            time.sleep(0.22)
        offset = body.get('offset')
        if not offset:
            break
    print(f"IG_REPAIR: {fixed} posts restored")


# The eleven accounts the Page route cannot see are not a permissions problem:
# the sync only discovers Instagram accounts by walking FACEBOOK_PAGES, and an
# account with no linked Page is invisible to that walk whatever scopes the
# token carries. Asking the business what it owns is a different call, and a
# user token can make it as well as a system-user one. This reports whether
# the token already in place can, so we know before minting a new one.
# Runs whenever META_BUSINESS_ID is set, then the normal sync continues — no
# flag to leave switched on by mistake. Prints counts and usernames only.
PAGE_ROUTE_CANNOT_SEE = ["real.good.politics", "weightsandbiases", "goodbillies",
                         "real.good.crypto", "legit.conspiracies", "innovators_exchange",
                         "10xpod", "tech.totherescue", "altryne_ai", "all_in_stans", "piratewires"]


def probe_business(sync):
    biz = os.getenv("META_BUSINESS_ID")
    if not biz:
        return
    sys_tok = os.getenv("META_SYSTEM_USER_TOKEN")
    token = sys_tok or sync.user_access_token
    which = "META_SYSTEM_USER_TOKEN" if sys_tok else "META_USER_ACCESS_TOKEN"
    if not token:
        print(f"\n\U0001F50E business probe: META_BUSINESS_ID is set but no Meta token is")
        return
    print(f"\n\U0001F50E business probe: business {biz}, using {which}")

    # The business read keeps failing with Meta's catch-all 400, which covers
    # a missing scope, a wrong business id and a system user that is not in
    # that business alike. Ask the token what it actually is before guessing.
    # Scope and account names only ever get printed, never the token itself.
    app_id, app_secret = os.getenv("META_APP_ID"), os.getenv("META_APP_SECRET")
    if app_id and app_secret:
        try:
            r = requests.get("https://graph.facebook.com/v21.0/debug_token",
                             params={"input_token": token, "access_token": f"{app_id}|{app_secret}"}, timeout=30)
            d = (r.json().get("data") or {}) if r.status_code == 200 else {}
            if d:
                scopes = sorted(d.get("scopes") or [])
                print(f"   token: type={d.get('type')} app={d.get('app_id')} valid={d.get('is_valid')} "
                      f"expires={d.get('expires_at') or 'never'}")
                print(f"   scopes ({len(scopes)}): {', '.join(scopes) or 'none'}")
                print(f"   business_management: {'YES' if 'business_management' in scopes else 'MISSING'}")
            else:
                print(f"   debug_token: HTTP {r.status_code} {r.text[:200]}")
        except Exception as exc:
            print(f"   debug_token: {exc}")
    else:
        print("   debug_token: skipped, META_APP_ID/META_APP_SECRET not set")

    # Which businesses can this token see at all? If the id we were given is
    # not in this list, the id is the problem rather than the permissions.
    for path, label in (("me", "identity"), ("me/businesses", "businesses")):
        try:
            r = requests.get(f"https://graph.facebook.com/v21.0/{path}",
                             params={"fields": "id,name", "access_token": token, "limit": 50}, timeout=30)
            if r.status_code != 200:
                print(f"   {label}: HTTP {r.status_code} {r.text[:160]}")
                continue
            body = r.json()
            if "data" in body:
                rows = [f"{b.get('name')} ({b.get('id')})" for b in body["data"]]
                print(f"   {label}: {len(rows)} — {'; '.join(rows) or 'none'}")
                print(f"   {biz} is in that list: {'YES' if any(b.get('id') == biz for b in body['data']) else 'NO'}")
            else:
                print(f"   {label}: {body.get('name')} ({body.get('id')})")
        except Exception as exc:
            print(f"   {label}: {exc}")

    seen = set()
    # owned_pages is the control: if a plain Pages edge on the same business
    # fails too, this is about reaching the business, not about Instagram.
    for edge in ("owned_pages", "owned_instagram_accounts", "client_instagram_accounts", "instagram_accounts"):
        try:
            r = requests.get(f"https://graph.facebook.com/v21.0/{biz}/{edge}",
                             params={"fields": "id,username", "access_token": token, "limit": 100}, timeout=30)
            if r.status_code != 200:
                msg = ""
                try:
                    msg = (r.json().get("error") or {}).get("message", "")
                except ValueError:
                    pass
                print(f"   {edge}: HTTP {r.status_code} {msg or r.text[:160]}")
                continue
            rows = r.json().get("data", [])
            names = [a.get("username") for a in rows if a.get("username")]
            if edge != "owned_pages":
                seen.update(names)
            print(f"   {edge}: {len(rows)} row(s)" + (f", {len(names)} with a username" if names else ""))
        except Exception as exc:
            print(f"   {edge}: {exc}")
    hit = [n for n in PAGE_ROUTE_CANNOT_SEE if n in seen]
    print(f"   reaches {len(seen)} account(s) in total; of the {len(PAGE_ROUTE_CANNOT_SEE)} the Page route "
          f"cannot see it reaches {len(hit)}: {', '.join(hit) or 'none'}")


if __name__ == "__main__":
    sync = InstagramDynamicSync()
    if os.environ.get("IG_REPAIR") == "1":
        repair_subtracted_views(sync)
        sys.exit(0)
    probe_business(sync)
    sync.sync_instagram_posts()
    print("\n" + "="*60 + "\n")
    sync.sync_instagram_followers()
