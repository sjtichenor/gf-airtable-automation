"""
VC investor rankings: every Contacts row tagged Person Type = Investor,
ranked by X (Twitter) following. Individuals only; firms and podcasts have
their own page (vc_pods).

Counts come from Contacts "X Followers", written daily by
followers/x_followers.py, which also records "X Name" -- the display name X
reports for the handle. A handle that resolves to someone whose name shares
nothing with the contact's is flagged (`suspect`), so a squatter or a wrong
guess shows on the page instead of quietly ranking.
"""
import re
import unicodedata

TAG = "Investor"


def handle_of(url):
    m = re.search(r"(?:twitter\.com|x\.com)/@?([A-Za-z0-9_]{1,15})", url or "")
    return m.group(1) if m else None


def _fold(s):
    """'Miura-Ko' -> 'miurako'; strips accents, emoji and punctuation."""
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if c.isalnum() or c.isspace()).lower()


def name_matches(first, last, x_name):
    """Does X's display name look like this person? True when unknown yet.

    Either the first or the last name (folded) must appear in the folded
    display name, or the display name's words must all appear in the
    contact's. 'Jason' vs '@jason' passes, 'Bill Gurley' vs 'Crypto Deals
    Daily' does not."""
    if not x_name:
        return True
    xn = _fold(x_name)
    # An emoji-only display name (Hunter Walk's is a laptop and a coffee)
    # says nothing either way; only a *different* name is a warning.
    if not xn.strip():
        return True
    xn_words = set(xn.split())
    for part in (first, last):
        p = _fold(part).replace(" ", "")
        if p and (p in xn.replace(" ", "")):
            return True
    mine = set(_fold(f"{first or ''} {last or ''}").split())
    return bool(xn_words) and xn_words <= mine


def table(snap: dict) -> dict:
    shows = {s["id"]: s for s in snap.get("shows", [])}
    rows = []
    for p in snap.get("people", []):
        if TAG not in (p.get("type") or []):
            continue
        n = p.get("x_followers")
        n = int(n) if isinstance(n, (int, float)) else None
        hosts = [shows[s] for s in (p.get("hosts") or []) if s in shows]
        rows.append({
            "id": p["id"], "name": p.get("name") or "?", "first": p.get("first"), "last": p.get("last"),
            "firm": p.get("firm"), "title": p.get("title"),
            "x": p.get("x"), "handle": handle_of(p.get("x")),
            "followers": n, "pending": n is None,
            "x_name": p.get("x_name"), "x_updated": p.get("x_updated"),
            "suspect": not name_matches(p.get("first"), p.get("last"), p.get("x_name")),
            "photo": p.get("photo"),
            "shows": [{"id": s["id"], "name": s["name"], "relationship": s.get("relationship"),
                       "vc": "Venture Capital" in (s.get("category") or [])} for s in hosts],
            # Hosts of a show we clip for get the highlight, like the pods page.
            "ours": any(s.get("relationship") in ("Client", "Owned") for s in hosts),
        })
    ranked = [r for r in rows if not r["pending"]]
    ranked.sort(key=lambda r: -r["followers"])
    pos, prev = 0, None
    for i, r in enumerate(ranked):
        if r["followers"] != prev:
            pos, prev = i + 1, r["followers"]
        r["rank"] = pos
    rows.sort(key=lambda r: (r["pending"], -(r["followers"] or 0), r["name"].lower()))
    return {"rows": rows, "ranked": len(ranked), "pending": len(rows) - len(ranked),
            "suspect": sum(1 for r in rows if r["suspect"]),
            "updated": max((r["x_updated"] for r in rows if r.get("x_updated")), default=None),
            "generated_at": snap.get("generated_at")}
