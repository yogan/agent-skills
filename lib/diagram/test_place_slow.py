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
    """The placement pass IS the cost of this file, so it runs once for the whole corpus and
    the assertions read off the same result.

    Three of these tests used to place all five reference diagrams for themselves — thirteen
    searches to check five placements, which is most of why this file took six minutes rather
    than four. Sharing the result loses nothing: `place.place` is deterministic, and the one
    test that says so is the one test that still calls it twice on purpose.
    """

    @classmethod
    def setUpClass(cls):
        cls.placed = {name: place.place(spec, name=name)
                      for name, spec in REFERENCE.items()}

    def test_placement_is_never_worse_than_the_hand_picked_reference_anchors(self):
        """The justification for having a search at all.

        The reference ER diagram's anchors were chosen by eye. This test asked the search to
        BEAT them, and for a long time it did — by a factor of fourteen under dagre (216
        against 3123), by 1.3 under ELK at the tight default spacing.

        It no longer beats them: it agrees with them exactly. The hidden-text check moved this
        one diagram a rung up `d2.ELK_SPACING_LADDER`, and at 30 the drawing is roomy enough
        that `top-left` / `top-right` is simply the optimum — the search measures 3121 and so
        does the hand pick, the same number, because they are the same two anchors. Asserting a
        strict improvement was asserting that the author had got it wrong, which is not a
        property worth pinning; what must hold is that a measured anchor never covers MORE than
        one chosen by eye.

        This compared CLIPPING until the day `_score` started measuring against the card
        rather than the svg box, at which point the hand-picked anchors stopped clipping too:
        they overhang the canvas, but not the card, so nothing is cut off on the page. That
        left overlap as the honest comparison — and it is the sharper one anyway, since it is
        what the reader actually loses.

        A tie against the hand pick could equally mean the search has stopped discriminating,
        so the second half measures the spread it is choosing within: `center-left` for the new
        table is the anchor the module docstrings keep citing, and it is six times worse (19132
        against 3121) while shrinking every glyph in the figure from 12.7px to 11.5px. The
        search has plenty to get wrong here and does not.
        """
        _, report = self.placed["er"]
        self.assertEqual(place.unplaceable(report), [], f"search still clips: {report}")
        self.assertEqual(max(row["clip"] for row in report), 0)
        found = sum(row["overlap"] for row in report) / len(report)

        # Both alternatives in one call: the cost here is the browser launch, not the extra
        # candidate.
        measured = place._measure_candidates(
            ER, "er", [("top-left", "top-right"), ("bottom-left", "center-left")], "light")
        _, hand_clip, hand_overlap = place._score(measured[0][1])
        _, _, poor_overlap = place._score(measured[1][1])
        self.assertEqual(hand_clip, 0, "the hand pick overhangs the canvas but not the card")
        self.assertLessEqual(found, hand_overlap,
                             f"search {found:.0f} vs hand {hand_overlap:.0f} — a measured "
                             "anchor must not cover more of the drawing than one chosen by eye")
        self.assertLess(found * 2, poor_overlap,
                        f"search {found:.0f} vs a poor anchor {poor_overlap:.0f} — the spread "
                        "has collapsed, so the tie above no longer says anything")

    def test_every_reference_diagram_places_without_clipping(self):
        for name, (_placed, report) in self.placed.items():
            self.assertEqual(place.unplaceable(report), [], f"{name}: {report}")

    def test_placed_diagrams_pass_the_clipping_gate(self):
        """Placement and the gate use different boundaries on purpose (svg box vs card),
        so agreeing is worth checking rather than assuming."""
        svgs = {name: render.render(placed, name=f"d2--{name}")
                for name, (placed, _report) in self.placed.items()}
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
