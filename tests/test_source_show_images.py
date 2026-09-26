"""videos/source_show.py: the screenshot that goes to Claude next to the JSON
evidence. No network: requests.post is mocked and the records carry no
YouTube SOURCE, so youtube_info() is never reached.

    python tests/test_source_show_images.py
"""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from videos import source_show as ss  # noqa: E402

LARGE = "https://v5.airtableusercontent.com/large/shot.jpg"
FULL = "https://v5.airtableusercontent.com/full/shot.jpg"
THUMB = "https://v5.airtableusercontent.com/large/thumb.png"


def jpeg(url=FULL, large=LARGE):
    a = {"id": "attJPEG", "type": "image/jpeg", "filename": "IMG_1.jpeg", "size": 250000, "url": url}
    if large:
        a["thumbnails"] = {"small": {"url": "s"}, "large": {"url": large, "width": 512, "height": 530}, "full": {"url": url}}
    return a


def mp4():
    return {"id": "attMP4", "type": "video/mp4", "filename": "clip.mp4", "size": 9000000, "url": "https://v5.airtableusercontent.com/clip.mp4"}


def record(rid="recTHIN", title="Anatoly: does decentralization matter?", attachment=None, thumbnail=None, **fields):
    f = {ss.F["title"]: title}
    if attachment is not None:
        f[ss.F["attachment"]] = attachment
    if thumbnail is not None:
        f[ss.F["thumbnail"]] = thumbnail
    for k, v in fields.items():
        f[ss.F[k]] = v
    return {"id": rid, "fields": f}


class FakeResponse:
    def __init__(self, status, payload=None, text=""):
        self.status_code, self._payload, self.text = status, payload, text or json.dumps(payload or {})

    def json(self):
        return self._payload


def answer(ids):
    body = {rid: {"show": "The Peel", "episode": None, "url": None} for rid in ids}
    return FakeResponse(200, {"stop_reason": "end_turn", "content": [{"type": "text", "text": json.dumps(body)}]})


class ImageUrl(unittest.TestCase):
    def test_attachment_large_thumbnail_first(self):
        self.assertEqual(ss.image_url(record(attachment=[jpeg()], thumbnail=[jpeg(large=THUMB)])), LARGE)

    def test_file_url_when_no_thumbnails(self):
        self.assertEqual(ss.image_url(record(attachment=[jpeg(large=None)])), FULL)

    def test_thumbnail_field_when_attachment_empty(self):
        self.assertEqual(ss.image_url(record(attachment=[], thumbnail=[jpeg(large=THUMB)])), THUMB)

    def test_non_image_attachment_is_skipped(self):
        # a video file first, the screenshot second: the screenshot wins
        self.assertEqual(ss.image_url(record(attachment=[mp4(), jpeg()])), LARGE)
        # only a video file: no image at all, not the mp4
        self.assertIsNone(ss.image_url(record(attachment=[mp4()])))
        # a video in Attachment must not hide an image in Thumbnail
        self.assertEqual(ss.image_url(record(attachment=[mp4()], thumbnail=[jpeg(large=THUMB)])), THUMB)

    def test_no_attachments(self):
        self.assertIsNone(ss.image_url(record()))


class ContentBlocks(unittest.TestCase):
    def test_thin_video_with_image_gets_one_image_block(self):
        batch = [ss.evidence(record(attachment=[jpeg()]))]
        self.assertTrue(ss.thin(batch[0]))
        blocks, n = ss.content_blocks(batch, ["Lightspeed"])
        self.assertEqual(n, 1)
        self.assertEqual([b["type"] for b in blocks], ["text", "text", "image"])
        self.assertIn("Lightspeed", blocks[0]["text"])
        self.assertIn('"recTHIN"', blocks[0]["text"])
        self.assertNotIn(LARGE, blocks[0]["text"], "the URL is not part of the JSON evidence")
        self.assertEqual(blocks[1]["text"], "Video recTHIN screenshot:")
        self.assertEqual(blocks[2], {"type": "image", "source": {"type": "url", "url": LARGE}})

    def test_video_without_image_is_text_only(self):
        blocks, n = ss.content_blocks([ss.evidence(record())], [])
        self.assertEqual(n, 0)
        self.assertEqual([b["type"] for b in blocks], ["text"])

    def test_non_image_attachment_sends_no_image(self):
        blocks, n = ss.content_blocks([ss.evidence(record(attachment=[mp4()]))], [])
        self.assertEqual(n, 0)
        self.assertEqual([b["type"] for b in blocks], ["text"])

    def test_rich_text_evidence_skips_the_image(self):
        rec = record(attachment=[jpeg()], tweet="\"Great quote\"\n— @toly, Co-Founder of Solana, on @ThePeelPod")
        v = ss.evidence(rec)
        self.assertFalse(ss.thin(v))
        blocks, n = ss.content_blocks([v], [])
        self.assertEqual(n, 0)
        self.assertEqual(len(blocks), 1)

    def test_images_off(self):
        blocks, n = ss.content_blocks([ss.evidence(record(attachment=[jpeg()]))], [], images=False)
        self.assertEqual((n, len(blocks)), (0, 1))

    def test_mixed_batch_keeps_order_and_one_image_per_video(self):
        batch = [ss.evidence(record("recA", attachment=[jpeg(), jpeg(large="https://x/second.jpg")])),
                 ss.evidence(record("recB")),
                 ss.evidence(record("recC", thumbnail=[jpeg(large=THUMB)]))]
        blocks, n = ss.content_blocks(batch, [])
        self.assertEqual(n, 2)
        self.assertEqual([b["type"] for b in blocks], ["text", "text", "image", "text", "image"])
        self.assertEqual(blocks[1]["text"], "Video recA screenshot:")
        self.assertEqual(blocks[2]["source"]["url"], LARGE)
        self.assertEqual(blocks[3]["text"], "Video recC screenshot:")
        self.assertEqual(blocks[4]["source"]["url"], THUMB)


class Ask(unittest.TestCase):
    def setUp(self):
        self.batch = [ss.evidence(record("recIMG", attachment=[jpeg()])), ss.evidence(record("recTXT"))]

    def test_request_carries_image_block_and_count(self):
        with mock.patch.dict(os.environ, {"SOURCE_SHOW_IMAGES": "1"}), \
                mock.patch.object(ss.requests, "post", return_value=answer(["recIMG", "recTXT"])) as post:
            out, n = ss.ask("sk-test", self.batch, ["The Peel"])
        self.assertEqual(n, 1)
        self.assertEqual(out["recIMG"]["show"], "The Peel")
        self.assertEqual(out["recTXT"]["show"], "The Peel")
        post.assert_called_once()
        body = post.call_args.kwargs["json"]
        self.assertEqual(body["max_tokens"], 4000)
        content = body["messages"][0]["content"]
        self.assertIsInstance(content, list)
        self.assertEqual([b["type"] for b in content], ["text", "text", "image"])
        self.assertEqual(content[2]["source"], {"type": "url", "url": LARGE})
        self.assertEqual(post.call_args.args[0], ss.ANTHROPIC)

    def test_env_switch_off_sends_text_only(self):
        with mock.patch.dict(os.environ, {"SOURCE_SHOW_IMAGES": "0"}), \
                mock.patch.object(ss.requests, "post", return_value=answer(["recIMG", "recTXT"])) as post:
            out, n = ss.ask("sk-test", self.batch, [])
        self.assertEqual(n, 0)
        content = post.call_args.kwargs["json"]["messages"][0]["content"]
        self.assertEqual([b["type"] for b in content], ["text"])

    def test_rejected_image_retries_without_images(self):
        bad = FakeResponse(400, {"error": {"type": "invalid_request_error", "message": "Could not process image"}})
        with mock.patch.dict(os.environ, {"SOURCE_SHOW_IMAGES": "1"}), \
                mock.patch.object(ss.requests, "post", side_effect=[bad, answer(["recIMG", "recTXT"])]) as post:
            out, n = ss.ask("sk-test", self.batch, [])
        self.assertEqual(post.call_count, 2)
        self.assertEqual(n, 0)
        first = post.call_args_list[0].kwargs["json"]["messages"][0]["content"]
        second = post.call_args_list[1].kwargs["json"]["messages"][0]["content"]
        self.assertEqual([b["type"] for b in first], ["text", "text", "image"])
        self.assertEqual([b["type"] for b in second], ["text"])
        self.assertEqual(out["recIMG"]["show"], "The Peel")

    def test_other_errors_still_raise(self):
        with mock.patch.dict(os.environ, {"SOURCE_SHOW_IMAGES": "1"}), \
                mock.patch.object(ss.requests, "post", return_value=FakeResponse(529, text="overloaded")):
            with self.assertRaises(RuntimeError):
                ss.ask("sk-test", self.batch, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
