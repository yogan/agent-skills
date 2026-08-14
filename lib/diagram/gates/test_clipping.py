#!/usr/bin/env python3
"""Tests for the clipping gate.

Two things are worth more than the rest:

  * it must **raise** when the browser is unavailable, never quietly pass. Everything this
    gate catches is invisible to the other five, so a skipped run leaves a diagram with its
    callout text sliced off looking entirely green.
  * it must actually **fire** on a real clip. A gate that has only ever printed "ok" is
    indistinguishable from a gate that cannot fail, which is the exact trap the prototype
    fell into.

Run: `python3 lib/diagram/gates/test_clipping.py`
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))))

from lib.diagram import browser, render
from lib.diagram.examples import REFERENCE
from lib.diagram.gates import GateError
from lib.diagram.gates import clipping

HAVE_BROWSER = browser.available()
HAVE_D2 = render.d2_version() is not None

# A wide diagram whose right-most node carries a callout too long to fit beside it.
CLIPPED_SPEC = {
    "kind": "architecture",
    "direction": "right",
    "nodes": [
        {"id": "a", "label": "Client application", "role": "client"},
        {"id": "b", "label": "Gateway service", "role": "svc"},
        {"id": "c", "label": "Primary datastore cluster", "role": "store",
         "note": "this callout is deliberately far too long to fit anywhere",
         "near": "center-right"},
    ],
    "edges": [{"from": "a", "to": "b", "label": "request"},
              {"from": "b", "to": "c", "label": "persist"}],
}


class TestFailsLoudlyWhenItCannotRun(unittest.TestCase):
    def setUp(self):
        self.real = browser.measure

    def tearDown(self):
        browser.measure = self.real

    def test_a_browser_failure_becomes_a_gate_error(self):
        def boom(*args, **kwargs):
            raise browser.BrowserError("no system Chrome found")
        browser.measure = boom
        with self.assertRaisesRegex(GateError, "could not run"):
            clipping.check("<svg/>", "x")

    def test_the_underlying_reason_is_preserved(self):
        def boom(*args, **kwargs):
            raise browser.BrowserError("puppeteer-core not found")
        browser.measure = boom
        with self.assertRaisesRegex(GateError, "puppeteer-core not found"):
            clipping.check("<svg/>", "x")

    def test_an_empty_batch_is_not_an_error(self):
        """Nothing to check is different from being unable to check."""
        self.assertEqual(clipping.check_many({}), [])


class TestVerdicts(unittest.TestCase):
    """Substitutes the measurement so the verdict logic is tested on its own."""

    def setUp(self):
        self.real = browser.measure

    def tearDown(self):
        browser.measure = self.real

    def fake(self, overflow, offenders=(), callouts=1):
        def measure(jobs, **kwargs):
            return [{"key": job["key"], "overflow": overflow,
                     "offenders": list(offenders),
                     "svg": {"width": 400, "height": 300},
                     "callouts": [{}] * callouts} for job in jobs]
        browser.measure = measure

    def test_no_overflow_passes(self):
        self.fake({"left": 0, "right": 0, "top": 0, "bottom": 0})
        self.assertTrue(clipping.check("<svg/>", "d").ok)

    def test_subpixel_overflow_is_tolerated_as_layout_noise(self):
        self.fake({"left": 0, "right": 0.4, "top": 0, "bottom": 0})
        self.assertTrue(clipping.check("<svg/>", "d").ok)

    def test_a_real_overflow_fails(self):
        self.fake({"left": 0, "right": 27, "top": 0, "bottom": 0})
        result = clipping.check("<svg/>", "d")
        self.assertFalse(result.ok)
        self.assertIn("right 27px", result.problems[0])

    def test_the_failure_names_the_offending_element(self):
        self.fake({"left": 0, "right": 27, "top": 0, "bottom": 0},
                  offenders=[{"tag": "foreignobject", "text": "new table",
                              "callout": True, "over": {}}])
        problem = clipping.check("<svg/>", "d").problems[0]
        self.assertIn("foreignobject(callout)", problem)
        self.assertIn("new table", problem)

    def test_the_failure_says_what_to_do_about_it(self):
        """The remedy is editorial — a narrower callout fits where a wide one cannot."""
        self.fake({"left": 0, "right": 27, "top": 0, "bottom": 0})
        self.assertIn("shorten the note text", clipping.check("<svg/>", "d").problems[0])

    def test_every_side_is_reported(self):
        self.fake({"left": 5, "right": 6, "top": 7, "bottom": 8})
        problem = clipping.check("<svg/>", "d").problems[0]
        for side in ("left 5px", "right 6px", "top 7px", "bottom 8px"):
            self.assertIn(side, problem)

    def test_a_batch_returns_one_result_per_diagram_in_order(self):
        self.fake({"left": 0, "right": 0, "top": 0, "bottom": 0})
        results = clipping.check_many({"a": "<svg/>", "b": "<svg/>"})
        self.assertEqual([r.name for r in results], ["a", "b"])

    def test_the_detail_reports_the_size_and_callout_count(self):
        self.fake({"left": 0, "right": 0, "top": 0, "bottom": 0}, callouts=2)
        self.assertIn("2 callout(s)", clipping.check("<svg/>", "d").detail)


@unittest.skipUnless(HAVE_D2 and HAVE_BROWSER, "needs d2 and a browser")
class TestAgainstRealDiagrams(unittest.TestCase):
    def test_the_reference_corpus_is_not_clipped(self):
        """CLIPPING only, on purpose — the corpus is rendered raw here, and a raw render makes
        no promise about where a callout lands.

        A callout has to sit somewhere, and wherever a spec's default anchor puts it, it may
        land on a label; only measuring the eight alternatives finds one that does not, which is
        the entire reason `place` exists. So the hidden-text half of this gate is asserted where
        placement has actually run — `test_place_slow.test_placed_diagrams_pass_the_clipping_gate`
        — and placing five diagrams here as well cost 70s of the fast suite to duplicate it.
        """
        svgs = {name: render.render(spec, name=f"d2--{name}")
                for name, spec in REFERENCE.items()}
        clipped = [(r.name, p) for r in clipping.check_many(svgs)
                   for p in r.problems if p.startswith("CLIPPED")]
        self.assertEqual(clipped, [])

    def test_the_gate_really_fires_on_a_clipped_callout(self):
        """Proof it can fail. Without this the passing corpus above proves nothing."""
        svg = render.render(CLIPPED_SPEC, name="clipbait")
        result = clipping.check(svg, "clipbait")
        self.assertFalse(result.ok, "a callout that overflows the card must be caught")
        self.assertIn("CLIPPED", result.problems[0])

    def test_it_sees_foreignobject_callout_text_that_a_rasteriser_would_drop(self):
        svg = render.render(CLIPPED_SPEC, name="clipbait")
        result = clipping.check(svg, "clipbait")
        self.assertIn("foreignobject", result.problems[0])

    def test_it_catches_a_label_a_callout_is_sitting_on_top_of(self):
        """The occluded half of the hidden-text check: geometry painted over a word.

        Pinning the state machine's callout back to `bottom-left` reproduces it exactly — that
        anchor puts the callout across 69% of `max attempts`, which is why `examples.py` no
        longer pins it and lets the placement pass measure instead.
        """
        import copy
        spec = copy.deepcopy(REFERENCE["state"])
        next(s for s in spec["states"] if s.get("note"))["near"] = "bottom-left"
        result = clipping.check(render.render(spec, name="buried"), "buried")
        self.assertFalse(result.ok)
        self.assertTrue(any("HIDDEN TEXT" in p and "max attempts" in p for p in result.problems),
                        result.problems)

    def test_a_label_merely_crossing_a_pale_container_is_not_called_hidden(self):
        """The other half, and the reason this is not just an overlap check. The architecture's
        `GraphQL` and `WebSocket` labels stray onto the Kubernetes cluster's pale fill, and are
        perfectly readable there — flagging them made the layout search widen the whole diagram
        past the 800px height gate to fix a non-problem. Text ON TOP of a shape counts only when
        the contrast against that shape fails AA.
        """
        svg = render.render(REFERENCE["arch"], name="pale")
        result = clipping.check(svg, "pale")
        self.assertEqual([p for p in result.problems if "HIDDEN TEXT" in p], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
