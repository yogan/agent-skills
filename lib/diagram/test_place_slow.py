#!/usr/bin/env python3
"""End-to-end callout placement: genuinely slow, and slow for a real reason.

An exhaustive two-callout search is 64 candidates, each of which is a d2 compile plus a
browser measurement. That is ~12s for one diagram and there is no way to fake it down —
the whole point of the search is that the numbers come from a real browser laying out real
`<foreignObject>` text. Mocking it would test the mock.

The search *logic* is covered quickly in test_place.py by substituting the measurement step;
this file is what checks that the real thing agrees. Skipped by `run_tests.py` unless you
pass `--slow` — run it directly when touching place.py or the harness geometry.

Run: `python3 lib/diagram/test_place_slow.py`
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.diagram import browser, place, render
from lib.diagram.examples import ER, REFERENCE
from lib.diagram.gates import clipping

HAVE_BROWSER = browser.available()
HAVE_D2 = render.d2_version() is not None


@unittest.skipUnless(HAVE_D2 and HAVE_BROWSER, "needs d2 and a browser")
class TestPlacementAgainstRealDiagrams(unittest.TestCase):
    def test_placement_beats_the_hand_picked_reference_anchors(self):
        """The justification for having a search at all.

        The reference ER diagram's anchors were chosen by eye and looked fine. Measured,
        they clip 3px against the svg box and overlap more than the alternative. Hand
        placement is not reliable, which is why this is not left to an author.
        """
        _, report = place.place(ER, name="er")
        self.assertEqual(place.unplaceable(report), [], f"search still clips: {report}")

        by_hand = place._measure_candidates(ER, "er", [("top-left", "top-right")], "light")
        _, hand_clip, hand_overlap = place._score(by_hand[0][1])
        self.assertGreater(hand_clip, 0, "the reference anchors were expected to clip")
        self.assertGreater(hand_overlap, report[0]["overlap"])

    def test_every_reference_diagram_places_without_clipping(self):
        for name, spec in REFERENCE.items():
            _, report = place.place(spec, name=name)
            self.assertEqual(place.unplaceable(report), [], f"{name}: {report}")

    def test_placed_diagrams_pass_the_clipping_gate(self):
        """Placement and the gate use different boundaries on purpose (svg box vs card),
        so agreeing is worth checking rather than assuming."""
        svgs = {}
        for name, spec in REFERENCE.items():
            placed, _ = place.place(spec, name=name)
            svgs[name] = render.render(placed, name=f"d2--{name}")
        bad = [r for r in clipping.check_many(svgs) if not r.ok]
        self.assertEqual(bad, [], f"{[(r.name, r.problems) for r in bad]}")

    def test_placement_is_deterministic(self):
        """Same spec, same anchors — otherwise a regenerated document churns for no reason."""
        first, _ = place.place(ER, name="er")
        second, _ = place.place(ER, name="er")
        self.assertEqual([s["near"] for s in place.note_sites(first)],
                         [s["near"] for s in place.note_sites(second)])


if __name__ == "__main__":
    unittest.main(verbosity=2)
