"""
VC podcast rankings: every show tagged "Venture Capital" in Shows, ranked by
the following of its official accounts on each platform.

Which channel counts for a show: the ones linked to it that are not GF
Owned Media (our clip accounts would rank our own work, not the show). A
show with only owned channels falls back to them, so Trading Places' own
accounts still count. Per platform the largest count across those channels
is used, so a show with two accounts on X is not double-counted.
"""
PLATFORMS = ["YouTube", "X", "Instagram", "TikTok", "Facebook", "Threads"]
TAG = "Venture Capital"


def table(snap: dict) -> dict:
    by_show = {}
    for c in snap.get("channels", []):
        if c.get("status") == "Inactive":
            continue
        for sid in c.get("shows") or []:
            by_show.setdefault(sid, []).append(c)
    rows = []
    for sh in snap.get("shows", []):
        if TAG not in (sh.get("category") or []):
            continue
        chans = by_show.get(sh["id"], [])
        official = [c for c in chans if not c.get("owned")] or chans
        counts = {}
        for p in PLATFORMS:
            vals = [c["followers"][p] for c in official if isinstance((c.get("followers") or {}).get(p), (int, float))]
            if vals:
                counts[p] = int(max(vals))
        if not counts:
            continue  # nothing to rank yet (no accounts, or the syncs have not run)
        rows.append({
            "id": sh["id"], "name": sh["name"], "relationship": sh.get("relationship"),
            "ours": sh.get("relationship") in ("Client", "Owned"),
            "logo": sh.get("logo"),
            "accounts": [{"name": c["name"], "profiles": c.get("profiles") or {}} for c in official],
            "counts": counts, "total": sum(counts.values()),
        })
    # Ranks per platform and overall; ties share a rank.
    ranks = {}
    for p in PLATFORMS + ["total"]:
        have = [r for r in rows if (p == "total" or p in r["counts"])]
        have.sort(key=lambda r: -(r["total"] if p == "total" else r["counts"][p]))
        pos, prev = 0, None
        for i, r in enumerate(have):
            v = r["total"] if p == "total" else r["counts"][p]
            if v != prev:
                pos, prev = i + 1, v
            r.setdefault("rank", {})[p] = pos
        ranks[p] = len(have)
    rows.sort(key=lambda r: -r["total"])
    return {"rows": rows, "platforms": [p for p in PLATFORMS if ranks.get(p)], "ranked": ranks, "generated_at": snap.get("generated_at")}
