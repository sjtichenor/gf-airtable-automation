"""Pure-function tests for videos/link_episodes.py. No network, no env.

Run either way:
    python -m pytest tests/test_link_episodes.py
    python tests/test_link_episodes.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from videos import link_episodes as le  # noqa: E402


SHOWS = [
    {"id": "sh_peel", "name": "The Peel with Turner Novak"},
    {"id": "sh_index", "name": "The Index Show"},
    {"id": "sh_tokens", "name": "Talking Tokens"},
    {"id": "sh_light", "name": "Lightspeed"},
    {"id": "sh_solana", "name": "Solana"},
    {"id": "sh_compound", "name": "The Compound and Friends"},
    {"id": "sh_ark", "name": "ARK Invest Podcast (FYI - For Your Innovation)"},
    {"id": "sh_raoul", "name": "Raoul Pal: The Journey Man"},
    {"id": "sh_impact", "name": "Impact Theory"},
]


class YouTubeId(unittest.TestCase):
    def test_forms(self):
        for url in ("https://www.youtube.com/watch?v=Xx8KUk-X340",
                    "https://youtu.be/Xx8KUk-X340?is=pRn3Kwv_qSvQnxpQ",
                    "https://www.youtube.com/watch?app=desktop&v=Xx8KUk-X340&ra=m",
                    "https://www.youtube.com/shorts/Xx8KUk-X340",
                    "https://www.youtube.com/live/Xx8KUk-X340?feature=share"):
            self.assertEqual(le.youtube_id(url), "Xx8KUk-X340", url)

    def test_not_youtube(self):
        self.assertIsNone(le.youtube_id("https://www.dropbox.com/scl/fi/x/y.mp4?dl=0"))
        self.assertIsNone(le.youtube_id("https://vimeo.com/1110198379/453012c0ba"))
        self.assertIsNone(le.youtube_id(None))
        self.assertIsNone(le.youtube_id(""))


class ShowMatching(unittest.TestCase):
    def test_loose_names(self):
        self.assertEqual(le.find_show("The Peel", SHOWS)["id"], "sh_peel")
        self.assertEqual(le.find_show("The Index", SHOWS)["id"], "sh_index")
        self.assertEqual(le.find_show("Talking Tokens Podcast", SHOWS)["id"], "sh_tokens")
        self.assertEqual(le.find_show("The Compound Podcast", SHOWS)["id"], "sh_compound")
        self.assertEqual(le.find_show("Ark Invest Podcast", SHOWS)["id"], "sh_ark")
        self.assertEqual(le.find_show("The Raoul Pal Show", SHOWS)["id"], "sh_raoul")
        self.assertEqual(le.find_show("Lightspeed", SHOWS)["id"], "sh_light")

    def test_exact_beats_loose(self):
        shows = SHOWS + [{"id": "sh_solpod", "name": "Solana Podcast"}]
        self.assertEqual(le.find_show("Solana", shows)["id"], "sh_solana")

    def test_no_match_or_ambiguous(self):
        self.assertIsNone(le.find_show("CNBC", SHOWS))
        self.assertIsNone(le.find_show("Unknown", SHOWS))
        self.assertIsNone(le.find_show("", SHOWS))
        self.assertIsNone(le.find_show(None, SHOWS))
        # An event named after a show is not the show.
        self.assertIsNone(le.find_show("Solana Breakpoint 2025", SHOWS))
        # Two loose candidates: refuse rather than guess.
        shows = SHOWS + [{"id": "sh_index2", "name": "The Index Podcast"}]
        self.assertIsNone(le.find_show("The Index", shows))

    def test_show_matches_is_order_sensitive_and_one_way(self):
        self.assertFalse(le.show_matches("Pal Raoul", "Raoul Pal: The Journey Man"))
        self.assertTrue(le.show_matches("Raoul Pal", "Raoul Pal: The Journey Man"))
        # The label may drop words from the name, never add them.
        self.assertTrue(le.show_matches("Talking Tokens Podcast", "Talking Tokens"))
        self.assertFalse(le.show_matches("Lightspeed by Blockworks", "Lightspeed"))


class EpisodeWords(unittest.TestCase):
    def test_strips_show_and_stopwords(self):
        self.assertEqual(le.episode_words("Armani Ferrante on Lightspeed", "Lightspeed"), ["armani", "ferrante"])
        self.assertEqual(le.episode_words("Anatoly Yakovenko on Impact Theory with Tom Bilyeu", "Impact Theory"),
                         ["anatoly", "yakovenko", "tom", "bilyeu"])
        self.assertEqual(le.episode_words("Unknown", "Lightspeed"), [])
        self.assertEqual(le.episode_words(None, "Lightspeed"), [])

    def test_title_match_needs_a_name_run(self):
        words = le.episode_words("Anatoly Yakovenko on Impact Theory with Tom Bilyeu", "Impact Theory")
        self.assertTrue(le.episode_matches(words, "How Crypto Is Reshaping Finance with Solana Founder Anatoly Yakovenko"))
        # Same words, but never two together: not a match.
        self.assertFalse(le.episode_matches(words, "Anatoly on Bilyeu's Tom Yakovenko Theory"))
        # Only one word shared: not a match.
        self.assertFalse(le.episode_matches(words, "Why Tom Sold Quest Nutrition"))

    def test_title_match_share(self):
        words = le.episode_words("Jito CEO Lucas Bruder on JTX", "Lightspeed")
        self.assertTrue(le.episode_matches(words, "Inside JTX: Jito's New Trading Platform | Lucas Bruder"))
        # Two of five words present is below the 0.5 share.
        self.assertFalse(le.episode_matches(words, "Do Buybacks Make Sense? | Lucas Bruder"))


class Dates(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(str(le.parse_date("2026-07-06T01:01:23.000Z")), "2026-07-06")
        self.assertEqual(str(le.parse_date("2026-07-06")), "2026-07-06")
        self.assertIsNone(le.parse_date(None))
        self.assertIsNone(le.parse_date("not a date"))

    def test_window(self):
        d = le.parse_date
        self.assertTrue(le.within_window(d("2026-07-01"), d("2026-07-15")))
        self.assertTrue(le.within_window(d("2026-07-15"), d("2026-07-01")))
        self.assertFalse(le.within_window(d("2026-06-30"), d("2026-07-15")))
        self.assertFalse(le.within_window(None, d("2026-07-15")))


class Plan(unittest.TestCase):
    EPISODES = [
        {"id": "ep_ferrante", "show": "sh_light", "title": "The Future of Finance Starts Onchain | Armani Ferrante",
         "air": "2026-07-02", "yt": "https://www.youtube.com/watch?v=Xx8KUk-X340"},
        {"id": "ep_bruder_jtx", "show": "sh_light", "title": "Inside JTX: Jito's New Trading Platform | Lucas Bruder",
         "air": "2026-05-14", "yt": "https://www.youtube.com/watch?v=aaaaaaaaaaa"},
        {"id": "ep_bruder_old", "show": "sh_light", "title": "Do Buybacks Make Sense? | Lucas Bruder",
         "air": "2026-02-01", "yt": None},
        {"id": "ep_gu", "show": "sh_light", "title": "How Institutions Are Moving Onchain | Catherine Gu",
         "air": "2026-04-16", "yt": "https://www.youtube.com/watch?v=6RCgvwdjrZE"},
        {"id": "ep_bilyeu", "show": "sh_impact", "title": "How Crypto Is Reshaping Finance with Solana Founder Anatoly Yakovenko",
         "air": "2026-01-20", "yt": "https://www.youtube.com/watch?v=IrXLg12dYeU"},
        {"id": "ep_bilyeu2", "show": "sh_impact", "title": "Anatoly Yakovenko Pt 2 | How Solana's Founder Sees Crypto",
         "air": "2026-01-22", "yt": "https://www.youtube.com/watch?v=bbbbbbbbbbb"},
    ]

    def run_plan(self, *videos):
        return le.plan(list(videos), SHOWS, self.EPISODES)

    def test_rule_a_youtube_id_wins_regardless_of_date(self):
        r = self.run_plan({"id": "v1", "title": "clip", "source_show": "Lightspeed",
                           "source_episode": "Unknown", "source_url": "https://youtu.be/Xx8KUk-X340?is=x",
                           "created": "2026-12-25T00:00:00.000Z"})
        self.assertEqual([(l["episode"], l["rule"]) for l in r["links"]], [("ep_ferrante", "youtube_id")])
        self.assertEqual(r["unmatched"], [])

    def test_rule_b_title_and_date(self):
        r = self.run_plan({"id": "v2", "title": "clip", "source_show": "Lightspeed",
                           "source_episode": "Jito CEO Lucas Bruder on JTX", "source_url": None,
                           "created": "2026-05-22T16:08:57.000Z"})
        self.assertEqual([(l["episode"], l["rule"]) for l in r["links"]], [("ep_bruder_jtx", "title_and_date")])

    def test_rule_b_refuses_outside_window(self):
        r = self.run_plan({"id": "v3", "title": "clip", "source_show": "Lightspeed",
                           "source_episode": "Jito CEO Lucas Bruder on JTX", "source_url": None,
                           "created": "2026-07-17T00:00:00.000Z"})
        self.assertEqual(r["links"], [])
        self.assertEqual(len(r["unmatched"]), 1)
        self.assertIn("within 14d", r["unmatched"][0]["reason"])

    def test_rule_b_refuses_a_tie(self):
        r = self.run_plan({"id": "v4", "title": "clip", "source_show": "Impact Theory",
                           "source_episode": "Anatoly Yakovenko on Impact Theory", "source_url": None,
                           "created": "2026-01-23T00:00:00.000Z"})
        self.assertEqual(r["links"], [])
        self.assertIn("tie", r["unmatched"][0]["reason"])

    def test_rule_a_breaks_the_tie(self):
        r = self.run_plan({"id": "v5", "title": "clip", "source_show": "Impact Theory",
                           "source_episode": "Anatoly Yakovenko on Impact Theory", "source_url": "https://www.youtube.com/watch?v=IrXLg12dYeU",
                           "created": "2026-01-23T00:00:00.000Z"})
        self.assertEqual([l["episode"] for l in r["links"]], ["ep_bilyeu"])

    def test_unknown_show_and_no_episodes(self):
        r = self.run_plan(
            {"id": "v6", "title": "clip", "source_show": "CNBC", "source_episode": "Lily Liu", "source_url": None, "created": "2026-01-01"},
            {"id": "v7", "title": "clip", "source_show": "The Peel", "source_episode": "Toly", "source_url": None, "created": "2026-01-01"},
        )
        self.assertEqual(r["links"], [])
        reasons = {u["video"]: u["reason"] for u in r["unmatched"]}
        self.assertIn("no single show", reasons["v6"])
        self.assertIn("no episodes yet", reasons["v7"])

    def test_no_words_and_no_url_is_not_linked(self):
        r = self.run_plan({"id": "v8", "title": "clip", "source_show": "Lightspeed",
                           "source_episode": "Unknown", "source_url": "https://www.dropbox.com/x.mp4",
                           "created": "2026-07-02"})
        self.assertEqual(r["links"], [])
        self.assertIn("no Source Episode words", r["unmatched"][0]["reason"])

    def test_wrong_youtube_id_falls_through_to_rule_b(self):
        r = self.run_plan({"id": "v9", "title": "clip", "source_show": "Lightspeed",
                           "source_episode": "Catherine Gu - How Institutions Are Moving Onchain",
                           "source_url": "https://www.youtube.com/watch?v=zzzzzzzzzzz", "created": "2026-04-20"})
        self.assertEqual([(l["episode"], l["rule"]) for l in r["links"]], [("ep_gu", "title_and_date")])


if __name__ == "__main__":
    unittest.main(verbosity=2)
