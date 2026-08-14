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

    def test_placement_covers_nothing_on_a_diagram_where_something_could_be_covered(self):
        """The justification for having a search at all, and it has been rewritten twice as
        what the search is measuring got more honest.

        It began by asking the search to BEAT the ER's hand-picked anchors, and for a long time
        it did — by a factor of fourteen under dagre (216 against 3123), by 1.3 under ELK at the
        tight default spacing. Then the hidden-text check moved this diagram a rung up
        `d2.ELK_SPACING_LADDER` and the by-eye anchors became the optimum, so it asked only that
        the search never do WORSE.

        Now it cannot ask either, because `examples.ER` pins what the search finds and comparing
        those is comparing the search against itself. What is left is the claim that actually
        matters: on this diagram the search lands a callout where it covers NOTHING — not a
        little, none — and it is not getting that for free, because an anchor is available that
        covers a great deal.

        Overlap became this stark when it stopped being measured against the callout's grown
        box; see `js/measure.js`. Before that a callout was charged for its own drop-shadow
        grazing a neighbour, so seven anchors that occlude nothing at all scored between 2907
        and 5280 and the search was ranking them by blur radius.
        """
        _, report = self.placed["er"]
        self.assertEqual(place.unplaceable(report), [], f"search still clips: {report}")
        self.assertEqual(max(row["clip"] for row in report), 0)
        found = sum(row["overlap"] for row in report) / len(report)
        self.assertLess(found, 1, f"the search should cover nothing here, not {found:.0f}")

        # Proof it had something to get wrong. `center-right` puts the new-table callout across
        # both arrows leaving `presence_sessions`, and paths are weighted 2.
        measured = place._measure_candidates(ER, "er", [("top-left", "center-right")], "light")
        _, poor_clip, poor_overlap = place._score(measured[0][1])
        self.assertEqual(poor_clip, 0, "the point is that it covers, not that it clips")
        self.assertGreater(poor_overlap, 1000,
                           f"an anchor across both arrows measured {poor_overlap:.0f} — if "
                           "nothing on this diagram can be covered, the search proves nothing")

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
