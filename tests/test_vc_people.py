"""dashboard/vc_people.py and the Contacts half of followers/x_followers.py.

    python tests/test_vc_people.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dashboard import vc_people  # noqa: E402
from followers import x_followers  # noqa: E402


class NameMatches(unittest.TestCase):
    def test_matches(self):
        self.assertTrue(vc_people.name_matches("Jason", "Calacanis", "@jason"))
        self.assertTrue(vc_people.name_matches("Ann", "Miura-Ko", "Ann Miura-Ko"))
        self.assertTrue(vc_people.name_matches("Turner", "Novak", "Turner Novak 🍌"))
        self.assertTrue(vc_people.name_matches("Paul", "Graham", None))
        self.assertTrue(vc_people.name_matches("Marc", "Andreessen", "Marc Andreessen 🇺🇸"))
        self.assertTrue(vc_people.name_matches("Chamath", "Palihapitiya", "Chamath Palihapitiya"))

    def test_mismatch(self):
        self.assertFalse(vc_people.name_matches("Bill", "Gurley", "Crypto Deals Daily"))
        self.assertFalse(vc_people.name_matches("Paul", "Graham", "Startup Deals Bot"))


class Table(unittest.TestCase):
    snap = {"shows": [{"id": "s1", "name": "All-In", "relationship": "Client", "category": ["Venture Capital"]}],
            "people": [
                {"id": "a", "name": "Jason Calacanis", "first": "Jason", "last": "Calacanis", "type": ["Investor", "Host"], "x": "https://x.com/Jason", "x_followers": 900000, "x_name": "@jason", "hosts": ["s1"]},
                {"id": "b", "name": "Paul Graham", "first": "Paul", "last": "Graham", "type": ["Investor"], "x": "https://x.com/paulg", "x_followers": 2000000, "x_name": "Paul Graham", "hosts": []},
                {"id": "c", "name": "Tie One", "first": "Tie", "last": "One", "type": ["Investor"], "x": "https://x.com/t1", "x_followers": 900000, "x_name": "Tie One", "hosts": []},
                {"id": "d", "name": "New Guy", "first": "New", "last": "Guy", "type": ["Investor"], "x": "https://x.com/newguy", "x_followers": None, "x_name": None, "hosts": []},
                {"id": "e", "name": "A Lead", "first": "A", "last": "Lead", "type": ["Client Contact"], "x": "https://x.com/lead", "x_followers": 5, "hosts": []},
                {"id": "f", "name": "Bill Gurley", "first": "Bill", "last": "Gurley", "type": ["Investor"], "x": "https://x.com/bgurley", "x_followers": 640000, "x_name": "Crypto Deals Daily", "hosts": []},
            ]}

    def test_ranks_investors_only(self):
        t = vc_people.table(self.snap)
        self.assertEqual([r["id"] for r in t["rows"]], ["b", "a", "c", "f", "d"])
        self.assertEqual({r["id"]: r.get("rank") for r in t["rows"]}, {"b": 1, "a": 2, "c": 2, "f": 4, "d": None})
        self.assertEqual((t["ranked"], t["pending"], t["suspect"]), (4, 1, 1))
        a = t["rows"][1]
        self.assertEqual(a["handle"], "Jason")
        self.assertTrue(a["ours"])
        self.assertEqual(a["shows"][0]["name"], "All-In")
        self.assertTrue(a["shows"][0]["vc"])
        self.assertEqual([r["id"] for r in t["rows"] if r["suspect"]], ["f"])


class PlanContacts(unittest.TestCase):
    def test_writes_count_name_and_date(self):
        X = x_followers
        contacts = [
            {"id": "r1", "fields": {X.C_FIRST: "Paul", X.C_LAST: "Graham", X.C_URL: "https://x.com/paulg", X.C_FOLLOWERS: 1, X.C_XNAME: "Paul Graham", X.C_UPDATED: "2026-09-25"}},
            {"id": "r2", "fields": {X.C_FIRST: "Gone", X.C_LAST: "Person", X.C_URL: "https://x.com/gone"}},
            {"id": "r3", "fields": {X.C_FIRST: "No", X.C_LAST: "Url", X.C_URL: "https://linkedin.com/in/x"}},
            {"id": "r4", "fields": {X.C_FIRST: "Same", X.C_LAST: "Day", X.C_URL: "https://twitter.com/same", X.C_FOLLOWERS: 7, X.C_XNAME: "Same Day", X.C_UPDATED: "2026-09-26"}},
            {"id": "r5", "fields": {X.C_FIRST: "First", X.C_LAST: "Time", X.C_URL: "https://x.com/first"}},
        ]
        users = {"paulg": {"followers": 2, "name": "Paul Graham"}, "same": {"followers": 7, "name": "Same Day"},
                 "first": {"followers": 10, "name": "First Time"}}
        updates, skipped = X.plan_contacts(contacts, users, "2026-09-26")
        self.assertEqual(updates, [
            {"id": "r1", "fields": {X.C_FOLLOWERS: 2, X.C_UPDATED: "2026-09-26"}},
            {"id": "r5", "fields": {X.C_FOLLOWERS: 10, X.C_XNAME: "First Time", X.C_UPDATED: "2026-09-26"}},
        ])
        self.assertEqual(skipped, ["Gone Person (@gone)", "No Url (not an X URL)"])

    def test_channels_plan_unchanged(self):
        X = x_followers
        chans = [{"id": "c1", "fields": {X.F_NAME: "Pod", X.F_PROFILE: "https://x.com/pod", X.F_FOLLOWERS: 5}},
                 {"id": "c2", "fields": {X.F_NAME: "Old", X.F_STATUS: {"name": "Inactive"}, X.F_PROFILE: "https://x.com/old"}}]
        self.assertEqual(X.plan(chans, {"pod": 6}), ([{"id": "c1", "fields": {X.F_FOLLOWERS: 6}}], []))


if __name__ == "__main__":
    unittest.main(verbosity=1)
