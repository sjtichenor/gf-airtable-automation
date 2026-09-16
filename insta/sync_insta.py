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


if __name__ == "__main__":
    sync = InstagramDynamicSync()
    if os.environ.get("IG_REPAIR") == "1":
        repair_subtracted_views(sync)
        sys.exit(0)
    sync.sync_instagram_posts()
    print("\n" + "="*60 + "\n")
    sync.sync_instagram_followers()
