#!/usr/bin/env python3
"""Tests for the browser bridge.

Mostly about failing usefully: every error a caller can hit here is an environment problem,
and the message has to say what to install. The measurement smoke test needs node, a
browser and d2, and skips visibly without them.

Run: `python3 lib/diagram/test_browser.py`
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib import parallel
from lib.diagram import browser, render
from lib.diagram.examples import STATE

HAVE_BROWSER = browser.available()
HAVE_D2 = render.d2_version() is not None


class TestAvailability(unittest.TestCase):
    def test_the_measure_script_ships_with_the_library(self):
        self.assertTrue(os.path.exists(browser.MEASURE_JS), browser.MEASURE_JS)

    def test_requirements_is_empty_when_everything_is_present(self):
        if not HAVE_BROWSER:
            self.skipTest("no browser toolchain here")
        self.assertEqual(browser.requirements(), [])

    def test_an_empty_batch_short_circuits_without_launching_anything(self):
        self.assertEqual(browser.measure([]), [])


class TestErrorMessages(unittest.TestCase):
    def setUp(self):
        self.real_node = browser.node_available

    def tearDown(self):
        browser.node_available = self.real_node

    def test_a_missing_node_is_named_in_requirements(self):
        browser.node_available = lambda: False
        problems = browser.requirements()
        self.assertTrue(any("node" in p for p in problems), problems)

    def test_measuring_without_node_raises_browser_error(self):
        browser.node_available = lambda: False
        with self.assertRaisesRegex(browser.BrowserError, "node"):
            browser.measure([{"key": "a", "html": "<html></html>"}])


class TestWeightsAndConstants(unittest.TestCase):
    def test_labels_cost_more_to_cover_than_shape_bodies(self):
        """Unweighted, the search buries a label to keep off a big rectangle."""
        weights = browser.OVERLAP_WEIGHTS
        self.assertGreater(weights["text"], weights["path"])
        self.assertGreater(weights["path"], weights["rect"])

    def test_callout_text_is_weighted_like_other_text(self):
        self.assertEqual(browser.OVERLAP_WEIGHTS["foreignobject"],
                         browser.OVERLAP_WEIGHTS["text"])

    def test_the_shadow_allowance_covers_both_themes_shadows(self):
        """Light spends offset 2 + blur 5; dark's glow spends blur 5. 8 covers both."""
        self.assertGreaterEqual(browser.SHADOW_PX, 7)


@unittest.skipUnless(HAVE_BROWSER and HAVE_D2, "needs node, a browser and d2")
class TestRealMeasurement(unittest.TestCase):
    def test_it_measures_a_real_diagram(self):
        svg = render.render(STATE, name="state")
        results = browser.measure([{"key": "state", "html": render.harness_html(svg)}])
        self.assertEqual(len(results), 1)
        measured = results[0]
        self.assertEqual(measured["key"], "state")
        self.assertGreater(measured["svg"]["width"], 0)
        self.assertEqual(len(measured["callouts"]), 1)

    def test_the_keys_come_back_in_the_order_they_were_sent(self):
        svg = render.render(STATE, name="state")
        jobs = [{"key": f"j{i}", "html": render.harness_html(svg)} for i in range(3)]
        self.assertEqual([r["key"] for r in browser.measure(jobs)], ["j0", "j1", "j2"])

    def test_a_batch_split_across_browsers_still_comes_back_in_order(self):
        """Past `SHARD_MIN` the batch runs in several browsers at once, and the placement
        search zips the results against the anchors that produced them — so a shard landing
        out of order would pair a measurement with somebody else's candidate.

        Small pages rather than real diagrams: what is under test is the splitting, and 24
        real renders would make this the slowest test in the file for no extra coverage.
        """
        svg = render.render(STATE, name="state")
        count = 3 * browser.SHARD_MIN
        jobs = [{"key": f"j{i:02d}", "html": render.harness_html(svg)} for i in range(count)]
        self.assertGreater(min(parallel.WORKERS, count // browser.SHARD_MIN), 1,
                           "this machine must shard for the test to mean anything")
        self.assertEqual([r["key"] for r in browser.measure(jobs)],
                         [j["key"] for j in jobs])

    def test_the_card_is_wider_than_the_svg_it_contains(self):
        """The harness has to reproduce the real card, since the card is what clips."""
        svg = render.render(STATE, name="state")
        measured = browser.measure(
            [{"key": "s", "html": render.harness_html(svg)}])[0]
        self.assertGreater(measured["card"]["width"], measured["svg"]["width"])

    def test_a_malformed_harness_raises_rather_than_returning_zeros(self):
        with self.assertRaises(browser.BrowserError):
            browser.measure([{"key": "bad", "html": "<html>no diagram here</html>"}])


if __name__ == "__main__":
    unittest.main(verbosity=2)
