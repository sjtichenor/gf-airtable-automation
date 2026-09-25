"""
Stripe -> Airtable invoice sync.

Almost every invoice Good Future sends goes out through Stripe, so the Invoices
table in the Good Future Invoices base should never need typing into. This
copies every finalized Stripe invoice into that table and keeps it current:
the row appears when the invoice is finalized, the paid date lands when Stripe
sees the money, and Void / Uncollectible show in Status rather than vanishing.

Runs inside gf-api on a timer (see start_background) because that service is
where the two keys were pasted; it also runs as a script for backfills:

    python -m invoicing.stripe_sync            # sync
    python -m invoicing.stripe_sync --dry-run  # say what would change

Matching. A Stripe invoice is matched to a row by the Stripe Invoice URL first
(rows this sync has already touched), then by invoice number against Name
(rows typed in by hand before the sync existed). Stripe numbers invoices per
customer, SOL-0010, PVC-0013 and so on, and those are exactly the numbers the
hand-entered rows carry, so the first run reconciles history rather than
duplicating it. Rows with no Stripe counterpart are left alone.

What is written. On a new row everything; on an existing row only what is
blank or what Stripe owns outright (Status, the dashboard link, Amount). Dates
and the client name are never overwritten once set, so a hand correction
sticks. Drafts are ignored until finalized; a voided invoice that was never in
Airtable is not created.

Client Account. Stripe's customer name is "Solana Foundation" or a person's
name; the base uses "Solana", "Trading Places", "FFP". The sync learns the
mapping from the rows already there (invoice-number prefix -> the Client
Account most rows with that prefix use), STRIPE_CLIENT_MAP can pin or add
entries ("SOL=Solana;PVC=Trading Places"), and an unknown prefix falls back to
the Stripe customer name and is logged so someone can add it.

Environment
    STRIPE_API_KEY               restricted key: Invoices read, Customers read
    AIRTABLE_INVOICES_TOKEN      PAT scoped to the Good Future Invoices base
    AIRTABLE_INVOICES_BASE_ID    default appQTaSN3LkKVBgPe
    AIRTABLE_INVOICES_TABLE_ID   default tblx9X2kSBfNYIDvy
    STRIPE_CLIENT_MAP            optional "PREFIX=Client;PREFIX=Client"
    STRIPE_IGNORE                optional "SPEN-0001;TEST-*" - invoice numbers,
                                 or whole prefixes with -*, never written
    STRIPE_SYNC_SECONDS          timer inside gf-api, default 900; 0 disables
"""
import logging
import os
import sys
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone

import requests

log = logging.getLogger("stripe_sync")

STRIPE_API = "https://api.stripe.com/v1"
BASE = os.environ.get("AIRTABLE_INVOICES_BASE_ID", "appQTaSN3LkKVBgPe")
TABLE = os.environ.get("AIRTABLE_INVOICES_TABLE_ID", "tblx9X2kSBfNYIDvy")

# Invoices table, by field id so a rename in Airtable cannot break this.
F = {
    "name": "fldYdDOS3S2Wd3RTI",        # invoice number
    "client": "fldoVoItqWCLP03xv",
    "amount": "fldxYmmXmFYOWh6V0",
    "date": "fldQgmt8KbCQ3oXXf",
    "paid": "fld8wswPr4hmrpUSd",
    "pdf": "fldtmjfzPHCg60W5v",
    "status": "fldjnMdhGDYAOwdAA",
    "stripe": "fldDpgq8PCrDuzBxc",      # dashboard.stripe.com/invoices/in_...
}
STATUS = {"open": "Open", "paid": "Paid", "void": "Void", "uncollectible": "Uncollectible"}
CREATE_FOR = {"open", "paid", "uncollectible"}
DASHBOARD = "https://dashboard.stripe.com/invoices/"


# ── Stripe ────────────────────────────────────────────────────────────────

def fetch_stripe_invoices(key):
    """Every invoice on the account, newest first. ~100 today, so one or two
    pages; there is no updated-since filter on this endpoint and a full read
    is what makes the sync self-healing."""
    out, after = [], None
    while True:
        params = {"limit": 100}
        if after:
            params["starting_after"] = after
        r = requests.get(f"{STRIPE_API}/invoices", params=params,
                         headers={"Authorization": f"Bearer {key}"}, timeout=30)
        r.raise_for_status()
        body = r.json()
        out.extend(body.get("data", []))
        if not body.get("has_more") or not out:
            return out
        after = out[-1]["id"]


def _day(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat() if ts else None


def ignored(number, spec):
    """STRIPE_IGNORE: exact numbers, or PREFIX-* for a whole customer. The
    one so far is SPEN-0001, a test invoice Spencer sent himself in 2022."""
    n = (number or "").strip().upper()
    for item in (spec or "").replace(",", ";").split(";"):
        item = item.strip().upper()
        if not item:
            continue
        if item.endswith("-*") and n.startswith(item[:-1]):
            return True
        if item == n:
            return True
    return False


def shape(inv, ignore=""):
    """The handful of fields the table needs, or None for a draft."""
    if inv.get("status") == "draft" or not inv.get("number"):
        return None
    if ignored(inv["number"], ignore):
        return None
    t = inv.get("status_transitions") or {}
    return {
        "id": inv["id"],
        "number": inv["number"].strip(),
        "status": inv["status"],
        "amount": (inv.get("total") or 0) / 100.0,
        "currency": (inv.get("currency") or "usd").lower(),
        "customer": inv.get("customer_name") or inv.get("customer_email") or "",
        "date": _day(t.get("finalized_at") or inv.get("created")),
        "paid": _day(t.get("paid_at")) if inv["status"] == "paid" else None,
        "pdf": inv.get("invoice_pdf"),
    }


# ── Airtable ──────────────────────────────────────────────────────────────

def airtable(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def list_rows(token):
    url = f"https://api.airtable.com/v0/{BASE}/{TABLE}"
    out, offset = [], None
    while True:
        params = {"returnFieldsByFieldId": "true", "pageSize": 100}
        if offset:
            params["offset"] = offset
        r = requests.get(url, headers=airtable(token), params=params, timeout=30)
        r.raise_for_status()
        body = r.json()
        out.extend(body.get("records", []))
        offset = body.get("offset")
        if not offset:
            return out


def write_rows(token, method, records):
    url = f"https://api.airtable.com/v0/{BASE}/{TABLE}"
    for i in range(0, len(records), 10):
        r = requests.request(method, url, headers=airtable(token),
                             json={"records": records[i:i + 10]}, timeout=60)
        if r.status_code != 200:
            raise RuntimeError(f"Airtable {method} failed: {r.status_code} {r.text[:300]}")
        time.sleep(0.25)  # 5 req/s


# ── the plan: pure, so it can be tested and dry-run ───────────────────────

def prefix_of(number):
    return number.split("-")[0].strip().upper() if number and "-" in number else None


def learn_client_map(rows, override=""):
    """Prefix -> Client Account, from the rows already in the table, with the
    env override winning."""
    votes = defaultdict(Counter)
    for r in rows:
        f = r.get("fields", {})
        p, c = prefix_of(f.get(F["name"]) or ""), (f.get(F["client"]) or "").strip()
        if p and c:
            votes[p][c] += 1
    out = {p: c.most_common(1)[0][0] for p, c in votes.items()}
    for pair in (override or "").split(";"):
        if "=" in pair:
            p, c = pair.split("=", 1)
            if p.strip() and c.strip():
                out[p.strip().upper()] = c.strip()
    return out


def plan(invoices, rows, client_map):
    """Returns (creates, updates, notes). creates/updates are Airtable record
    payloads; notes are strings worth a log line."""
    by_stripe, by_number = {}, {}
    for r in rows:
        f = r.get("fields", {})
        link = (f.get(F["stripe"]) or "").rstrip("/")
        if link.startswith(DASHBOARD):
            by_stripe[link[len(DASHBOARD):]] = r
        n = (f.get(F["name"]) or "").strip().upper()
        if n:
            by_number.setdefault(n, r)

    creates, updates, notes = [], [], []
    unknown = set()
    for inv in invoices:
        row = by_stripe.get(inv["id"]) or by_number.get(inv["number"].upper())
        if inv["currency"] != "usd":
            notes.append(f"{inv['number']} is in {inv['currency'].upper()}; Amount written as-is")
        if row is None and inv["status"] not in CREATE_FOR:
            continue  # voided before we ever saw it; nothing to record
        p = prefix_of(inv["number"])
        client = client_map.get(p) if p else None
        if not client:
            client = inv["customer"]
            if p:
                unknown.add(f"{p} ({inv['customer']})")

        if row is None:
            fields = {
                F["name"]: inv["number"],
                F["client"]: client,
                F["amount"]: inv["amount"],
                F["status"]: STATUS[inv["status"]],
                F["stripe"]: DASHBOARD + inv["id"],
            }
            if inv["date"]:
                fields[F["date"]] = inv["date"]
            if inv["paid"]:
                fields[F["paid"]] = inv["paid"]
            if inv["pdf"]:
                fields[F["pdf"]] = [{"url": inv["pdf"], "filename": f"Invoice-{inv['number']}.pdf"}]
            creates.append({"fields": fields})
            continue

        f = row.get("fields", {})
        fields = {}
        # Stripe owns these outright.
        if (f.get(F["stripe"]) or "").rstrip("/") != DASHBOARD + inv["id"]:
            fields[F["stripe"]] = DASHBOARD + inv["id"]
        if f.get(F["status"]) != STATUS[inv["status"]]:
            fields[F["status"]] = STATUS[inv["status"]]
        if abs((f.get(F["amount"]) or 0) - inv["amount"]) >= 0.005:
            fields[F["amount"]] = inv["amount"]
        # Fill-if-blank: a hand correction sticks.
        if not f.get(F["client"]) and client:
            fields[F["client"]] = client
        if not f.get(F["date"]) and inv["date"]:
            fields[F["date"]] = inv["date"]
        if not f.get(F["paid"]) and inv["paid"]:
            fields[F["paid"]] = inv["paid"]
        if not f.get(F["pdf"]) and inv["pdf"]:
            fields[F["pdf"]] = [{"url": inv["pdf"], "filename": f"Invoice-{inv['number']}.pdf"}]
        if fields:
            updates.append({"id": row["id"], "fields": fields})

    if unknown:
        notes.append("no Client Account mapping for prefix " + ", ".join(sorted(unknown))
                     + " - used the Stripe customer name; set STRIPE_CLIENT_MAP to pin it")
    return creates, updates, notes


# ── run ───────────────────────────────────────────────────────────────────

def configured():
    return bool(os.environ.get("STRIPE_API_KEY") and os.environ.get("AIRTABLE_INVOICES_TOKEN"))


def sync(dry_run=False):
    """One pass. Returns a summary dict; raises on a failed read or write."""
    stripe_key = os.environ["STRIPE_API_KEY"]
    token = os.environ["AIRTABLE_INVOICES_TOKEN"]
    raw = fetch_stripe_invoices(stripe_key)
    ignore = os.environ.get("STRIPE_IGNORE", "")
    invoices = [s for s in (shape(i, ignore) for i in raw) if s]
    rows = list_rows(token)
    cmap = learn_client_map(rows, os.environ.get("STRIPE_CLIENT_MAP", ""))
    creates, updates, notes = plan(invoices, rows, cmap)
    for n in notes:
        log.warning("stripe sync: %s", n)
    if not dry_run:
        if creates:
            write_rows(token, "POST", creates)
        if updates:
            write_rows(token, "PATCH", updates)
    summary = {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "stripe_invoices": len(invoices),
        "skipped": len(raw) - len(invoices),  # drafts and STRIPE_IGNORE
        "airtable_rows": len(rows),
        "created": [c["fields"][F["name"]] for c in creates],
        "updated": len(updates),
        "notes": notes,
        "dry_run": dry_run,
    }
    log.info("stripe sync: %d Stripe invoice(s), %d row(s); created %d, updated %d%s",
             len(invoices), len(rows), len(creates), len(updates), " (dry run)" if dry_run else "")
    return summary


last = {"summary": None, "error": None}


def _loop(seconds):
    time.sleep(90)  # let the dashboard snapshot warm first after a deploy
    while True:
        try:
            last["summary"], last["error"] = sync(), None
        except Exception as e:  # keep the timer alive; the next pass self-heals
            last["error"] = f"{type(e).__name__}: {e}"
            log.exception("stripe sync failed")
        time.sleep(seconds)


def start_background():
    seconds = int(os.environ.get("STRIPE_SYNC_SECONDS", "900"))
    if not configured():
        log.info("stripe sync: STRIPE_API_KEY / AIRTABLE_INVOICES_TOKEN not set; not running")
        return
    if seconds <= 0:
        return
    threading.Thread(target=_loop, args=(seconds,), name="stripe-sync", daemon=True).start()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not configured():
        sys.exit("set STRIPE_API_KEY and AIRTABLE_INVOICES_TOKEN")
    s = sync(dry_run="--dry-run" in sys.argv)
    print(f"created {len(s['created'])}: {', '.join(s['created']) or '-'}")
    print(f"updated {s['updated']}")
    for n in s["notes"]:
        print("note:", n)
