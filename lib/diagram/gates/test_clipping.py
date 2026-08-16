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

from lib.diagram import browser, d2, render
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
    def test_the_reference_corpus_is_clean_even_without_the_placement_pass(self):
        """Rendered raw — no `place.place` — which is the cheap path and also a real one:
        `visualize --no-place` takes it, and every fast test in the suite does.

        This asserted CLIPPING only for a long time, on the reasoning that a raw render makes no
        promise about where a callout lands. That was true while the corpus pinned anchors
        nobody had measured. It pins the ones `place` measures now (see `examples.py`), so the
        stronger claim holds and is worth holding: a spec in this corpus is readable with or
        without the pass.

        It is not free of consequence. Removing those pins to make the corpus match the advice
        given to authors — leave `near` out, let the pass decide — put a callout across 59% of
        `presence deploy ×2`, 88% of `publish` and 165% of `implements`, and every other test in
        the suite stayed green. This is the one that caught it.
        """
        svgs = {name: render.render(spec, name=f"d2--{name}")
                for name, spec in REFERENCE.items()}
        bad = [(r.name, p) for r in clipping.check_many(svgs) for p in r.problems]
        self.assertEqual(bad, [])

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

        Pinning the state machine's callout to `center-left` reproduces it — and which anchor
        does that is a fact about the LAYOUT, not about the anchor. This test used to pin
        `bottom-left`, across 69% of `max attempts`, while that figure was portrait; it is
        landscape now (see `examples.py`) and the two have swapped jobs exactly, `bottom-left`
        being what the pass picks and `center-left` what lands on a word.

        The overlap it fires on is 2% where the old one was 69%, so this is a weaker fixture
        for the gate's sensitivity than it looks — but it is the real one for the drawing that
        ships, and a manufactured 69% would be measuring a diagram nobody renders. Of the eight
        anchors this is now the only one that hides anything at all.
        """
        import copy
        spec = copy.deepcopy(REFERENCE["state"])
        next(s for s in spec["states"] if s.get("note"))["near"] = "center-left"
        result = clipping.check(render.render(spec, name="buried"), "buried")
        self.assertFalse(result.ok)
        self.assertTrue(any("HIDDEN TEXT" in p and "transport" in p for p in result.problems),
                        result.problems)

    def test_the_standalone_target_gets_the_spacing_ladder_too(self):
        """The defect this exists for. The ladder lived only in `_pick_layout`, which the
        standalone path does not go through — so every image the `visualize` skill wrote of the
        reference ER had its cardinality label sitting on the `presence_sessions` table, while
        the same diagram came out clean in the explainers. The gate said so on every run; the
        renderer simply had no answer for it.

        The ER alone, because it was the one figure in the corpus that ever needed a rung, and
        every rung is a compile plus a browser measurement — checking all five would spend
        seconds of the fast suite to assert `15` four more times.

        **It climbs one rung, and which ladder it climbs has moved twice.** `edgelabel`
        slides a label along its own leg until it is clear, which took this figure off the
        layer ladder entirely for a while; then it climbed the EDGE ladder instead, for an
        arrowhead sitting on a curve. `route.straighten` fixes that arrowhead out of space the
        drawing already has, so the edge ladder stands down — and what is left underneath is
        the case the layer ladder is actually for: a cardinality with nowhere on its own leg
        that both starts the line and clears the head (`render._cramped`). It climbs to 30 and
        comes out at 916x281, which is 10px NARROWER than the 926 the edge rung cost.

        Pinned as `ELK_SPACING_LADDER[1]` rather than as `30`, so the relationship survives a
        change to the numbers. If this ever drops back to the tight rung, the label pass has
        started covering a case it did not, and the `_cramped` branch has gone quiet.
        """
        svg = render.standalone(REFERENCE["er"], name="alone", theme="dark")
        result = clipping.check(svg, "alone", theme="dark", standalone=True)
        self.assertEqual([p for p in result.problems if "HIDDEN TEXT" in p], [],
                         result.problems)
        self.assertEqual(render.choose_standalone_spacing(REFERENCE["er"], name="alone",
                                                          theme="dark")[0],
                         d2.ELK_SPACING_LADDER[1],
                         "the ER climbs one layer rung for a cardinality with nowhere to sit; "
                         "if that stops, `render._cramped` has stopped seeing it")

        # The wiring, driven directly: with the measurement forced to report hidden text, the
        # standalone path must climb. Without this the assertion above is equally satisfied by
        # a ladder that was deleted.
        original = render._faults
        seen = []
        try:
            render._faults = (
                lambda svg, **kw: (seen.append(kw.get("standalone")) or True, 0.0))
            self.assertEqual(render.choose_standalone_spacing(REFERENCE["er"], name="rung",
                                                              theme="dark")[0],
                             d2.ELK_SPACING_LADDER[-1],
                             "hidden text at every rung must exhaust the ladder, not stop")
        finally:
            render._faults = original
        self.assertEqual(len(seen), len(d2.ELK_SPACING_LADDER))
        self.assertTrue(all(seen), "the standalone path must measure against its own canvas")

        # `layers` pins rather than measures — the ladder can only choose if a caller can hold
        # a rung still.
        wide = render.standalone(REFERENCE["er"], name="wide", theme="dark", layers=40)
        self.assertGreater(render.natural_size(wide)[0], render.natural_size(svg)[0])

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
