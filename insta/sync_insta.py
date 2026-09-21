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
    seen = set()
    for edge in ("owned_instagram_accounts", "client_instagram_accounts"):
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
            names = [a.get("username") for a in r.json().get("data", []) if a.get("username")]
            seen.update(names)
            print(f"   {edge}: {len(names)} account(s)")
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
