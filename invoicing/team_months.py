"""
Team, Contracts and Team Months in the Good Future Invoices base.

Pay lives in the Invoices base, not the main one: who someone is comes from
the main base's Team table, how they are paid is a Contract row typed in by
hand, and this job joins the two into one row per person per month so
"what does a video cost us" has a real answer instead of the experimental
formulas on Videos.

Once a day, from the dashboard snapshot gf-api already holds:

  Team         upsert one row per main-base team member (name, email, roles,
               status, start date) matched on Main base record. Never touches
               Notes, Contracts or anything typed in by hand.
  Team Months  for every person and month since MONTHS_FROM: videos finished
               as editor (Date Finished Editing in the month), videos posted
               (first post of a video they edited went live in the month),
               videos directed, and Base Pay = Monthly Base of the contract
               in force on the first of that month. Bonus and Notes are left
               alone. Rows are matched on Person + Month; the Key is display.

Environment
    AIRTABLE_INVOICES_TOKEN     PAT scoped to the Invoices base (same as the
                                Stripe sync)
    TEAM_MONTHS_SECONDS         default 86400; 0 disables the timer
    TEAM_MONTHS_FROM            default 2024-01; earliest month written
"""
import logging
import os
import threading
import time
from collections import defaultdict
from datetime import date, datetime, timezone

import requests

log = logging.getLogger("team_months")

BASE = os.environ.get("AIRTABLE_INVOICES_BASE_ID", "appQTaSN3LkKVBgPe")
T_TEAM, T_CONTRACTS, T_MONTHS = "tblVtqKWu348fHzCk", "tblH057iCn5HvkScX", "tbldc73RXSYfxOE0U"
TEAM = {"name": "fld5dS2uIhUxM2ZMu", "email": "fld05hGmnMuOUX4cr", "roles": "fldq43YEoIfE3aiit",
        "status": "fldJ5PUOd9xSaX6us", "start": "fldUugzi3FyhA6R35", "main_id": "fld3tOjQK9eDPofDz"}
CON = {"person": "fldSfatMOr7fpgZyz", "start": "fldQDbMVZfN6SWdyo", "end": "fld1CzfNQipMVsdTl",
       "base": "fldc3Lr4LlhDleLJE"}
MON = {"key": "fldKruhqJ5bkPmC9U", "person": "fldIIVbheQ9DMWYS1", "month": "fldAOIRA3v33Al7fJ",
       "finished": "fldxMJ5cADPnF3w8z", "posted": "fldIT2Q6HXCFWc0Kr", "directed": "fldebwWaCoIOtI07t",
       "base": "fldmNEujkKH8re6CA"}
ROLES = {"Editor", "Director", "Exec", "Bootcamp Recruit", "Business Development Representative"}


# ── Airtable ──────────────────────────────────────────────────────────────

def _h(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def list_rows(token, table, fields=None):
    out, offset = [], None
    while True:
        params = {"returnFieldsByFieldId": "true", "pageSize": 100}
        if fields:
            params["fields[]"] = fields
        if offset:
            params["offset"] = offset
        r = requests.get(f"https://api.airtable.com/v0/{BASE}/{table}", headers=_h(token), params=params, timeout=30)
        r.raise_for_status()
        body = r.json()
        out.extend(body.get("records", []))
        offset = body.get("offset")
        if not offset:
            return out


def write_rows(token, table, method, records):
    for i in range(0, len(records), 10):
        r = requests.request(method, f"https://api.airtable.com/v0/{BASE}/{table}", headers=_h(token),
                             json={"records": records[i:i + 10], "typecast": True}, timeout=60)
        if r.status_code != 200:
            raise RuntimeError(f"Airtable {method} {table} failed: {r.status_code} {r.text[:300]}")
        time.sleep(0.25)


# ── pure planning ─────────────────────────────────────────────────────────

def month_of(iso):
    """'2026-09-17' or '2026-09-17T…' -> '2026-09'; None for blanks."""
    return iso[:7] if iso and len(iso) >= 7 else None


def plan_team(team_rows, existing):
    """Main-base team -> creates/updates for the Team table. `existing` is the
    Team table's rows. Everyone with a role is kept, active or not, so old
    months still have a person to hang on."""
    by_main = {}
    for r in existing:
        mid = (r.get("fields", {}).get(TEAM["main_id"]) or "").strip()
        if mid:
            by_main.setdefault(mid, r)
    creates, updates = [], []
    for t in team_rows:
        roles = [x for x in (t.get("roles") or []) if x in ROLES]
        if not roles:
            continue  # the "Client" placeholder and the like
        want = {TEAM["name"]: t["name"], TEAM["roles"]: roles,
                TEAM["status"]: "Active" if t.get("active") else "Inactive"}
        if t.get("email"):
            want[TEAM["email"]] = t["email"]
        if t.get("start"):
            want[TEAM["start"]] = t["start"]
        row = by_main.get(t["id"])
        if row is None:
            want[TEAM["main_id"]] = t["id"]
            creates.append({"fields": want})
            continue
        f = row.get("fields", {})
        have = {TEAM["name"]: f.get(TEAM["name"]), TEAM["roles"]: sorted(f.get(TEAM["roles"]) or []),
                TEAM["status"]: f.get(TEAM["status"]), TEAM["email"]: f.get(TEAM["email"]),
                TEAM["start"]: f.get(TEAM["start"])}
        diff = {k: v for k, v in want.items() if (sorted(v) if isinstance(v, list) else v) != have.get(k)}
        if diff:
            updates.append({"id": row["id"], "fields": diff})
    return creates, updates


def contract_base(contracts, person_row, month):
    """Monthly Base of the contract in force on the first of `month` for this
    Team row, or None. Latest start wins if two overlap."""
    first = f"{month}-01"
    best = None
    for c in contracts:
        f = c.get("fields", {})
        if person_row not in (f.get(CON["person"]) or []):
            continue
        start, end = f.get(CON["start"]), f.get(CON["end"])
        if not start or start > first or (end and end < first):
            continue
        if best is None or start > best[0]:
            best = (start, f.get(CON["base"]))
    return best[1] if best else None


def count_months(videos, posts, name_to_main, months_from):
    """{(main_id, 'YYYY-MM'): {finished, posted, directed}} from the snapshot."""
    first_post = {}
    for p in posts:
        v, d = p.get("video"), p.get("date")
        if v and d and (v not in first_post or d < first_post[v]):
            first_post[v] = d
    out = defaultdict(lambda: {"finished": 0, "posted": 0, "directed": 0})
    for v in videos:
        ed, di = name_to_main.get(v.get("editor") or ""), name_to_main.get(v.get("director") or "")
        fm = month_of(v.get("finished"))
        if fm and fm >= months_from:
            if ed:
                out[(ed, fm)]["finished"] += 1
            if di:
                out[(di, fm)]["directed"] += 1
        pm = month_of(first_post.get(v.get("id")))
        if ed and pm and pm >= months_from:
            out[(ed, pm)]["posted"] += 1
    return out


def plan_months(counts, team_by_main, contracts, existing, this_month):
    """creates/updates for Team Months. A row exists for every (person, month)
    with any output, plus every month a contract was in force (so a zero
    month still shows its cost). Bonus and Notes are never written."""
    have = {}
    for r in existing:
        f = r.get("fields", {})
        p, m = (f.get(MON["person"]) or [None])[0], month_of(f.get(MON["month"]))
        if p and m:
            have[(p, m)] = r
    # Months each person had a contract for, up to the current month.
    keys = set(counts)
    for c in contracts:
        f = c.get("fields", {})
        start = month_of(f.get(CON["start"]))
        if not start:
            continue
        end = min(month_of(f.get(CON["end"])) or this_month, this_month)
        for p in f.get(CON["person"]) or []:
            main = next((m for m, row in team_by_main.items() if row["id"] == p), None)
            y, mo = int(start[:4]), int(start[5:7])
            while f"{y:04d}-{mo:02d}" <= end:
                if main:
                    keys.add((main, f"{y:04d}-{mo:02d}"))
                mo += 1
                if mo > 12:
                    y, mo = y + 1, 1
    creates, updates = [], []
    for main, month in sorted(keys):
        row = team_by_main.get(main)
        if row is None:
            continue
        c = counts.get((main, month), {"finished": 0, "posted": 0, "directed": 0})
        want = {MON["key"]: f"{row['fields'].get(TEAM['name'])} · {month}",
                MON["person"]: [row["id"]], MON["month"]: f"{month}-01",
                MON["finished"]: c["finished"], MON["posted"]: c["posted"], MON["directed"]: c["directed"]}
        base = contract_base(contracts, row["id"], month)
        if base is not None:
            want[MON["base"]] = base
        ex = have.get((row["id"], month))
        if ex is None:
            creates.append({"fields": want})
            continue
        f = ex.get("fields", {})
        diff = {k: v for k, v in want.items()
                if k != MON["person"] and (f.get(k) if k != MON["month"] else month_of(f.get(k)) + "-01") != v}
        if diff:
            updates.append({"id": ex["id"], "fields": diff})
    return creates, updates


# ── run ───────────────────────────────────────────────────────────────────

def configured():
    return bool(os.environ.get("AIRTABLE_INVOICES_TOKEN"))


def sync(snapshot, dry_run=False):
    token = os.environ["AIRTABLE_INVOICES_TOKEN"]
    months_from = os.environ.get("TEAM_MONTHS_FROM", "2024-01")
    this_month = date.today().strftime("%Y-%m")
    team_rows = snapshot.get("team") or []

    existing_team = list_rows(token, T_TEAM, list(TEAM.values()))
    tc, tu = plan_team(team_rows, existing_team)
    if not dry_run:
        if tc:
            write_rows(token, T_TEAM, "POST", tc)
        if tu:
            write_rows(token, T_TEAM, "PATCH", tu)
        if tc or tu:
            existing_team = list_rows(token, T_TEAM, list(TEAM.values()))
    team_by_main = {(r["fields"].get(TEAM["main_id"]) or ""): r for r in existing_team}
    team_by_main.pop("", None)

    name_to_main = {}
    for t in team_rows:
        name_to_main.setdefault(t["name"], t["id"])
    counts = count_months(snapshot.get("videos") or [], snapshot.get("posts") or [], name_to_main, months_from)
    contracts = list_rows(token, T_CONTRACTS, list(CON.values()))
    existing_months = list_rows(token, T_MONTHS, list(MON.values()))
    mc, mu = plan_months(counts, team_by_main, contracts, existing_months, this_month)
    if not dry_run:
        if mc:
            write_rows(token, T_MONTHS, "POST", mc)
        if mu:
            write_rows(token, T_MONTHS, "PATCH", mu)
    summary = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "dry_run": dry_run,
               "team_created": len(tc), "team_updated": len(tu), "contracts": len(contracts),
               "months_created": len(mc), "months_updated": len(mu), "months_total": len(existing_months) + len(mc)}
    log.info("team months: %s", summary)
    return summary


last = {"summary": None, "error": None}


def _loop(seconds, get_snapshot):
    time.sleep(180)  # after the dashboard snapshot has warmed
    while True:
        try:
            snap = get_snapshot()
            if snap:
                last["summary"], last["error"] = sync(snap), None
        except Exception as e:
            last["error"] = f"{type(e).__name__}: {e}"
            log.exception("team months failed")
        time.sleep(seconds)


def start_background(get_snapshot):
    seconds = int(os.environ.get("TEAM_MONTHS_SECONDS", "86400"))
    if not configured():
        log.info("team months: AIRTABLE_INVOICES_TOKEN not set; not running")
        return
    if seconds <= 0:
        return
    threading.Thread(target=_loop, args=(seconds, get_snapshot), name="team-months", daemon=True).start()
