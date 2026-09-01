"""Tests for scripts/gen_terminal.py — stdlib unittest, no network.

The contract under test is ndl.au's GET /api/public/now:

    {"playing": {"track", "artist", ...} | null,
     "reading": {"title", "authors", "also_reading", ...} | null,
     "degraded": ["playing" | "reading"]?}

`null` means "nothing to report"; `null` plus a `degraded` entry means that
source is down. Keeping those apart is the whole point — conflating them is
what left the card silently stale for weeks.

Run: python3 -m unittest discover -s tests
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
import xml.dom.minidom
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import gen_terminal as gt  # noqa: E402

PLAYING = {
    "track": "Animal Rights",
    "artist": "deadmau5",
    "album": "4x4=12",
    "played_at": "2026-09-01T00:11:00.000Z",
    "spotify_url": "https://open.spotify.com/track/x",
}
READING = {
    "title": "True Conservatism",
    "authors": ["Anthony T. Kronman"],
    "started": "2026-08-14",
    "also_reading": 1,
}
NOW = {"playing": PLAYING, "reading": READING}


class FormatPlaying(unittest.TestCase):
    def test_renders_track_and_artist(self):
        self.assertEqual(gt.format_playing(NOW), "Animal Rights — deadmau5")

    def test_null_means_nothing_playing(self):
        self.assertIsNone(gt.format_playing({"playing": None, "reading": READING}))

    def test_degraded_is_an_error_not_an_empty_line(self):
        payload = {"playing": None, "reading": READING, "degraded": ["playing"]}
        with self.assertRaises(gt.SourceError):
            gt.format_playing(payload)

    def test_another_sources_degradation_does_not_affect_this_line(self):
        payload = {"playing": PLAYING, "reading": None, "degraded": ["reading"]}
        self.assertEqual(gt.format_playing(payload), "Animal Rights — deadmau5")

    def test_missing_artist_raises(self):
        with self.assertRaises(gt.SourceError):
            gt.format_playing({"playing": {"track": "Orphan"}})

    def test_wrong_type_raises(self):
        with self.assertRaises(gt.SourceError):
            gt.format_playing({"playing": "Animal Rights"})

    def test_long_values_are_truncated_to_fit_the_card(self):
        payload = {"playing": {"track": "T" * 60, "artist": "A" * 60}}
        self.assertEqual(len(gt.format_playing(payload)), 46)


class FormatReading(unittest.TestCase):
    def test_renders_title_and_surname_with_the_extra_count(self):
        self.assertEqual(gt.format_reading(NOW), ("True Conservatism — Kronman", 1))

    def test_null_means_nothing_being_read(self):
        self.assertEqual(gt.format_reading({"reading": None}), (None, 0))

    def test_degraded_is_an_error_not_an_empty_line(self):
        with self.assertRaises(gt.SourceError):
            gt.format_reading({"reading": None, "degraded": ["reading"]})

    def test_missing_authors_keeps_the_title(self):
        payload = {"reading": {"title": "Solo", "authors": [], "also_reading": 0}}
        self.assertEqual(gt.format_reading(payload), ("Solo", 0))

    def test_multiple_authors_uses_the_first_ones_surname(self):
        payload = {"reading": {"title": "Insurgent", "authors": ["Brian Andrews", "Jeffrey Wilson"]}}
        self.assertEqual(gt.format_reading(payload)[0], "Insurgent — Andrews")

    def test_missing_title_raises(self):
        with self.assertRaises(gt.SourceError):
            gt.format_reading({"reading": {"authors": ["A B"]}})

    def test_a_nonsense_count_does_not_cost_us_the_title(self):
        payload = {"reading": {"title": "Solo", "authors": ["A B"], "also_reading": -4}}
        self.assertEqual(gt.format_reading(payload), ("Solo — B", 0))


class FetchNow(unittest.TestCase):
    def _urlopen(self, body):
        r = mock.MagicMock()
        r.__enter__.return_value = r
        r.read.return_value = body.encode()
        return r

    def test_parses_the_payload(self):
        with mock.patch.object(gt.urllib.request, "urlopen", return_value=self._urlopen(json.dumps(NOW))):
            self.assertEqual(gt.fetch_now(), NOW)

    def test_retries_then_raises(self):
        with mock.patch.object(gt.urllib.request, "urlopen", side_effect=OSError("boom")) as urlopen, \
             mock.patch.object(gt.time, "sleep") as sleep:
            with self.assertRaises(OSError):
                gt.fetch_now(attempts=3)
        self.assertEqual(urlopen.call_count, 3)  # all attempts used
        self.assertEqual(sleep.call_count, 2)  # backoff between attempts only

    def test_a_non_object_body_is_an_error(self):
        with mock.patch.object(gt.urllib.request, "urlopen", return_value=self._urlopen("[1, 2]")), \
             mock.patch.object(gt.time, "sleep"):
            with self.assertRaises(gt.SourceError):
                gt.fetch_now(attempts=1)

    def test_html_instead_of_json_is_an_error(self):
        # What a 404/redirect to the site shell would look like.
        with mock.patch.object(gt.urllib.request, "urlopen", return_value=self._urlopen("<!DOCTYPE html>")), \
             mock.patch.object(gt.time, "sleep"):
            with self.assertRaises(Exception):
                gt.fetch_now(attempts=1)


class Refresh(unittest.TestCase):
    STATE = {
        "playing": "Old Track — Old Artist",
        "reading": "Old Book — Author",
        "extra": 3,
        "updated": {"playing": "2026-08-01T00:00:00+00:00", "reading": "2026-08-01T00:00:00+00:00"},
    }

    def test_both_lines_fresh(self):
        with mock.patch.object(gt, "fetch_now", lambda: NOW):
            values, errors = gt.refresh({})
        self.assertEqual(errors, {})
        self.assertEqual(values["playing"], "Animal Rights — deadmau5")
        self.assertEqual(values["reading"], "True Conservatism — Kronman")
        self.assertEqual(values["extra"], 1)
        self.assertEqual(set(values["updated"]), {"playing", "reading"})

    def test_a_failed_request_stalls_both_lines_without_blanking_them(self):
        with mock.patch.object(gt, "fetch_now", mock.Mock(side_effect=OSError("ndl.au down"))):
            values, errors = gt.refresh(self.STATE)
        self.assertEqual(sorted(errors), ["playing", "reading"])
        self.assertEqual(values["playing"], "Old Track — Old Artist")  # stale, not blank
        self.assertEqual(values["reading"], "Old Book — Author")
        self.assertEqual(values["extra"], 3)
        self.assertEqual(values["updated"], self.STATE["updated"])  # timestamps untouched

    def test_one_degraded_source_costs_only_its_own_line(self):
        payload = {"playing": None, "reading": READING, "degraded": ["playing"]}
        with mock.patch.object(gt, "fetch_now", lambda: payload):
            values, errors = gt.refresh(self.STATE)
        self.assertEqual(list(errors), ["playing"])
        self.assertEqual(values["playing"], "Old Track — Old Artist")  # kept
        self.assertEqual(values["updated"]["playing"], "2026-08-01T00:00:00+00:00")  # unchanged
        self.assertEqual(values["reading"], "True Conservatism — Kronman")  # still refreshed
        self.assertNotEqual(values["updated"]["reading"], "2026-08-01T00:00:00+00:00")

    def test_a_malformed_field_costs_only_its_own_line(self):
        with mock.patch.object(gt, "fetch_now", lambda: {"playing": {"track": "No Artist"}, "reading": READING}):
            values, errors = gt.refresh(self.STATE)
        self.assertEqual(list(errors), ["playing"])
        self.assertEqual(values["reading"], "True Conservatism — Kronman")

    def test_genuinely_empty_is_not_an_error(self):
        with mock.patch.object(gt, "fetch_now", lambda: {"playing": None, "reading": None}):
            values, errors = gt.refresh(self.STATE)
        self.assertEqual(errors, {})
        self.assertEqual((values["playing"], values["reading"], values["extra"]), ("—", "—", 0))

    def test_no_state_and_a_dead_endpoint_falls_back_to_placeholders(self):
        with mock.patch.object(gt, "fetch_now", mock.Mock(side_effect=OSError("x"))):
            values, errors = gt.refresh({})
        self.assertEqual((values["playing"], values["reading"], values["extra"]), ("—", "—", 0))
        self.assertEqual(sorted(errors), ["playing", "reading"])

    def test_corrupt_state_values_do_not_propagate(self):
        with mock.patch.object(gt, "fetch_now", mock.Mock(side_effect=OSError("x"))):
            values, _ = gt.refresh({"extra": "not a number", "updated": None})
        self.assertEqual(values["extra"], 0)
        self.assertEqual(values["updated"], {})


class State(unittest.TestCase):
    def test_round_trips(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "live.json")
            gt.save_state({"playing": "a — b", "reading": "c — d", "extra": 2, "updated": {}}, path)
            self.assertEqual(gt.load_state(path)["playing"], "a — b")

    def test_missing_or_corrupt_state_is_just_no_history(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(gt.load_state(os.path.join(d, "absent.json")), {})
            bad = os.path.join(d, "bad.json")
            with open(bad, "w", encoding="utf-8") as f:
                f.write("{not json")
            self.assertEqual(gt.load_state(bad), {})


class BuildOutput(unittest.TestCase):
    def test_emits_wellformed_svg_with_live_values(self):
        svg = gt.build("Kerala — Bonobo", "New Book — Public", 1)
        xml.dom.minidom.parseString(svg)  # raises if not well-formed XML
        self.assertTrue(svg.startswith("<svg"))
        for needle in ("nicklambourne", "things-ive-made/", "Kerala — Bonobo", "New Book — Public"):
            self.assertIn(needle, svg)

    def test_escapes_markup_in_live_values(self):
        svg = gt.build("<script> — & co", "Q&A — Author", 0)
        xml.dom.minidom.parseString(svg)  # would raise on unescaped markup
        self.assertIn("&lt;script&gt;", svg)


class Main(unittest.TestCase):
    """The exit code is the whole point: a stale line must turn the run red."""

    @staticmethod
    def _main(*args, fetch):
        """Run main() with its stdout swallowed. These cases deliberately walk
        the failure path, which prints `::error::` — and GitHub turns anything
        a step prints in that form into a run annotation. Letting them through
        would hang error annotations off every GREEN refresh, which is how you
        teach yourself to ignore them (the same way the old ::warning:: got
        ignored)."""
        with mock.patch.object(gt, "fetch_now", fetch), \
             contextlib.redirect_stdout(io.StringIO()):
            return gt.main(*args)

    def _run(self, d, fetch):
        svg, state, errs = (os.path.join(d, n) for n in ("terminal.svg", "live.json", "errors.md"))
        return self._main(svg, state, errs, fetch=fetch), svg, state, errs

    @staticmethod
    def _read(path):
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_success_writes_svg_and_state_and_exits_zero(self):
        with tempfile.TemporaryDirectory() as d:
            code, svg, state, errs = self._run(d, lambda: NOW)
            self.assertEqual(code, 0)
            self.assertIn("Animal Rights — deadmau5", self._read(svg))
            self.assertEqual(json.loads(self._read(state))["extra"], 1)
            self.assertFalse(os.path.exists(errs))  # no error report on a clean run

    def test_failure_still_writes_the_card_but_exits_nonzero(self):
        payload = {"playing": None, "reading": READING, "degraded": ["playing"]}
        with tempfile.TemporaryDirectory() as d:
            code, svg, _, errs = self._run(d, lambda: payload)
            self.assertEqual(code, 1)  # workflow goes red instead of failing silently
            # The good line still updated, and the failed one is named in the report.
            self.assertIn("True Conservatism — Kronman", self._read(svg))
            self.assertIn("degraded", self._read(errs))
            self.assertIn("playing", self._read(errs))

    def test_state_survives_a_run_that_could_not_refresh(self):
        with tempfile.TemporaryDirectory() as d:
            code, _, state, _ = self._run(d, lambda: NOW)
            self.assertEqual(code, 0)
            code = self._main(
                os.path.join(d, "terminal.svg"), state, os.path.join(d, "errors.md"),
                fetch=mock.Mock(side_effect=OSError("down")),
            )
            self.assertEqual(code, 1)
            # Last-good values are still there for the next run to fall back on.
            self.assertEqual(json.loads(self._read(state))["playing"], "Animal Rights — deadmau5")


if __name__ == "__main__":
    unittest.main()
