"""Tests for scripts/gen_terminal.py — stdlib unittest, no network.

Run: python3 -m unittest discover -s tests
"""

import os
import sys
import unittest
import xml.dom.minidom
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import gen_terminal as gt  # noqa: E402

SPOTIFY = {"recent_tracks": [{"track_name": "Kerala", "artist_name": "Bonobo"}]}
BOOKS = [
    {"status": "reading", "date_started": "2026-01-05", "book": {"title": "Old Book", "authors": ["Jane Roe"]}},
    {"status": "reading", "date_started": "2026-03-10", "book": {"title": "New Book", "authors": ["John Q. Public"]}},
    {"status": "finished", "date_started": "2025-01-01", "book": {"title": "Done", "authors": ["X Y"]}},
]


def fake_fetch(payloads):
    def _fetch(url, attempts=3):
        if "spotify" in url:
            return payloads["spotify"]
        if "books" in url:
            return payloads["books"]
        raise AssertionError(f"unexpected url {url}")

    return _fetch


class LiveExtraction(unittest.TestCase):
    def test_extracts_latest_track_and_most_recent_read(self):
        with mock.patch.object(gt, "fetch", fake_fetch({"spotify": SPOTIFY, "books": BOOKS})):
            playing, reading, extra = gt.live()
        self.assertEqual(playing, "Kerala — Bonobo")
        self.assertEqual(reading, "New Book — Public")  # latest date_started, surname only
        self.assertEqual(extra, 1)  # one other current read

    def test_empty_payloads_fall_back_to_placeholder(self):
        with mock.patch.object(gt, "fetch", fake_fetch({"spotify": {"recent_tracks": []}, "books": []})):
            playing, reading, extra = gt.live()
        self.assertEqual((playing, reading, extra), ("—", "—", 0))

    def test_missing_author_keeps_title_only(self):
        books = [{"status": "reading", "date_started": "2026-01-01", "book": {"title": "Solo", "authors": []}}]
        with mock.patch.object(gt, "fetch", fake_fetch({"spotify": SPOTIFY, "books": books})):
            _, reading, extra = gt.live()
        self.assertEqual(reading, "Solo")
        self.assertEqual(extra, 0)

    def test_fetch_failure_propagates_so_caller_keeps_last_good(self):
        def boom(url, attempts=3):
            raise OSError("ndl.au unreachable")

        with mock.patch.object(gt, "fetch", boom):
            with self.assertRaises(OSError):
                gt.live()


class FetchRetry(unittest.TestCase):
    def test_retries_then_raises(self):
        with mock.patch.object(gt.urllib.request, "urlopen", side_effect=OSError("boom")) as urlopen, \
             mock.patch.object(gt.time, "sleep") as sleep:
            with self.assertRaises(OSError):
                gt.fetch("https://ndl.au/api/spotify", attempts=3)
        self.assertEqual(urlopen.call_count, 3)  # all attempts used
        self.assertEqual(sleep.call_count, 2)  # backoff between attempts only


class BuildOutput(unittest.TestCase):
    def test_emits_wellformed_svg_with_live_values(self):
        with mock.patch.object(gt, "fetch", fake_fetch({"spotify": SPOTIFY, "books": BOOKS})):
            svg = gt.build()
        xml.dom.minidom.parseString(svg)  # raises if not well-formed XML
        self.assertTrue(svg.startswith("<svg"))
        for needle in ("nicklambourne", "things-ive-made/", "Kerala — Bonobo", "New Book — Public"):
            self.assertIn(needle, svg)

    def test_failsafe_build_raises_when_a_fetch_fails(self):
        def boom(url, attempts=3):
            raise OSError("down")

        with mock.patch.object(gt, "fetch", boom):
            with self.assertRaises(OSError):
                gt.build()


if __name__ == "__main__":
    unittest.main()
