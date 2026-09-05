#!/usr/bin/env python3
"""Tests for the browser bridge.

Mostly about failing usefully: every error a caller can hit here is an environment problem,
and the message has to say what to install. The measurement smoke test needs node, a
browser and d2, and skips visibly without them.

The rest is about the browser being REUSED. That is the file's own cost as well as the
renderer's — it used to start a Chrome per test and took 47s; sharing one takes 5.

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


@unittest.skipUnless(HAVE_BROWSER, "needs node and a browser")
class TestTheBrowserPool(unittest.TestCase):
    """A browser outlives the batch it was started for, and nothing outlives the command.

    Starting Chrome costs roughly twenty times what measuring one more page in a running one
    does, and the renderer's work arrives as many small batches — so a process per batch spent
    most of a full check booting: 31 starts to draw the eleven sample diagrams, where three
    do it now.
    """

    def setUp(self):
        browser.shutdown()          # a pool left by another test would mask a start
        self.started = []
        self.real = browser.subprocess.Popen

        def spy(cmd, *args, **kwargs):
            if isinstance(cmd, list) and cmd[:1] == ["node"]:
                self.started.append(cmd)
            return self.real(cmd, *args, **kwargs)

        browser.subprocess.Popen = spy

    def tearDown(self):
        browser.subprocess.Popen = self.real
        browser.shutdown()

    def page(self):
        return {"key": "p", "html": "<html><body><span data-w>x</span></body></html>"}

    def test_a_second_batch_reuses_the_browser_the_first_one_started(self):
        browser.text_widths(self.page()["html"])
        browser.text_widths(self.page()["html"])
        self.assertEqual(len(self.started), 1, "the second batch started another browser")

    def test_every_kind_of_request_shares_the_same_browser(self):
        """Measuring, text widths and rasterising each used to start their own — three
        separate copies of the same subprocess dance, and three Chromes."""
        browser.text_widths(self.page()["html"])
        with self.assertRaises(browser.BrowserError):
            browser.measure([{"key": "bad", "html": "<html>nothing here</html>"}])
        browser.text_widths(self.page()["html"])
        self.assertEqual(len(self.started), 1)

    def test_a_page_it_cannot_measure_does_not_cost_the_browser(self):
        """The batch fails; the browser is still good. Otherwise one malformed harness makes
        the next caller pay for a Chrome."""
        with self.assertRaises(browser.BrowserError):
            browser.measure([{"key": "bad", "html": "<html>nothing here</html>"}])
        self.assertEqual(len(browser._IDLE), 1, "a measurement error retired the browser")

    def test_a_browser_that_died_between_batches_is_replaced_rather_than_raised(self):
        """A pooled browser can be gone for reasons that are nothing to do with the request —
        Chrome crashed, something reaped it — and that must not read as a failed diagram."""
        browser.text_widths(self.page()["html"])
        self.assertEqual(len(browser._IDLE), 1)
        browser._IDLE[0].proc.kill()
        browser._IDLE[0].proc.wait(timeout=10)
        self.assertEqual(browser.text_widths(self.page()["html"]), [browser.text_widths(
            self.page()["html"])[0]])
        self.assertEqual(len(self.started), 2, "it should have started exactly one more")

    def test_shutdown_leaves_nothing_running(self):
        """`atexit` calls this. A browser surviving the command is the failure mode that
        matters — it holds hundreds of MB and nobody is left to notice."""
        browser.text_widths(self.page()["html"])
        pooled = list(browser._IDLE)
        self.assertEqual(len(pooled), 1)
        browser.shutdown()
        self.assertEqual(browser._IDLE, [])
        for one in pooled:
            self.assertIsNotNone(one.proc.poll(), "the browser is still running after shutdown")

    def test_shutdown_is_safe_to_call_twice_and_on_an_empty_pool(self):
        browser.shutdown()
        browser.shutdown()
        self.assertEqual(browser._IDLE, [])


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

    def test_a_callout_is_charged_for_ink_and_not_for_a_bounding_box(self):
        """What `overlap` means, and the two ways a box got it wrong.

        An L-shaped route's bounding box is mostly the empty square inside its elbow, and a
        container's box is empty by construction — so a callout in either was charged for
        hiding nothing, while one lying along a straight run was charged the 2px sliver where
        the two boxes meet. Measured on the reference architecture, that ranked the anchor
        covering 113px of line and a turn above three anchors covering nothing at all.

        Hand-built SVGs rather than a corpus figure: the point is which geometry is charged,
        and a real drawing cannot isolate one element from the rest.
        """
        route = ('<path d="M 40 40 L 40 200 S 40 210 50 210 L 260 210" class="connection" '
                 'stroke="black" fill="none" style="stroke-width:2;"/>')
        # Deliberately under half the canvas: at 50% or more the loop skips an element
        # outright, and a container that big would make both container cases read 0 for a
        # reason that has nothing to do with what is being tested here.
        container = ('<g class="box grp"><g class="shape"><rect x="20" y="20" width="180" '
                     'height="150" stroke="black" fill="none" style="stroke-width:2;"/>'
                     "</g></g>")
        def harness(body, x, y):
            note = (f'<g class="positioned-tooltip"><rect x="{x}" y="{y}" width="90" '
                    f'height="40" class="d2-callout" fill="white" stroke="grey"/>'
                    f'<foreignObject x="{x + 10}" y="{y + 10}" width="70" height="20">'
                    '<div class="md"><p>a note</p></div></foreignObject></g>')
            return render.harness_html(
                f'<svg viewBox="0 0 320 260" width="320" height="260">{body}{note}</svg>')

        # A route that runs straight, whose box has NO THICKNESS: Chrome reports this one as
        # 200x0. Anything that rules a route out by the AREA of that box rules out every
        # straight run there is — which a bent route cannot catch, since its box has both
        # extents. That is a bug this file shipped for one commit.
        straight = ('<path d="M 40 120 L 240 120" class="connection" stroke="black" '
                    'fill="none" style="stroke-width:2;"/>')

        jobs = [
            # inside the elbow: the route's box covers it, the route itself does not
            {"key": "elbow", "html": harness(route, 120, 90)},
            # along the horizontal run, which is what a reader actually loses
            {"key": "on-the-line", "html": harness(route, 120, 190)},
            # the same, on a route that is nothing BUT a straight run
            {"key": "on-a-flat-route", "html": harness(straight, 100, 100)},
            # wholly inside the container, touching none of its border
            {"key": "in-container", "html": harness(container, 60, 60)},
            # straddling the container's left border
            {"key": "on-the-border", "html": harness(container, -25, 60)},
        ]
        by_key = {r["key"]: r["callouts"][0]["overlap"] for r in browser.measure(jobs)}
        self.assertEqual(by_key["elbow"], 0,
                         "the empty middle of a route is not something a callout can cover")
        self.assertGreater(by_key["on-the-line"], 0, "covering the line has to cost")
        self.assertGreater(by_key["on-a-flat-route"], 0,
                           "a route with no thickness to its box must still be sampled")
        self.assertEqual(by_key["in-container"], 0,
                         "a container's interior is empty; its title is charged as text")
        self.assertGreater(by_key["on-the-border"], 0, "crossing the border does cost")

    def test_a_malformed_harness_raises_rather_than_returning_zeros(self):
        with self.assertRaises(browser.BrowserError):
            browser.measure([{"key": "bad", "html": "<html>no diagram here</html>"}])


if __name__ == "__main__":
    unittest.main(verbosity=2)
