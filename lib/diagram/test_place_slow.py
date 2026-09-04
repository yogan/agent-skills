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

from lib.diagram import arrows, browser, callout, figure, place, render
from lib.diagram.browser import OVERLAP_WEIGHTS
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

        The numbers below are an order of magnitude smaller than they once were, and that is
        the same story again: a callout is charged for the LINE it hides rather than for being
        inside a route's bounding box, so the bad anchor here reads 122 where it used to read
        four figures. What it means is unchanged and now literal — 122 units is 30px of route
        line, at 2px of stroke and weight 2 — and the corners it also covers are no longer
        part of that figure at all, being a rank of their own.

        So the bad anchor is pinned on both counts, and the line half comparatively: what has
        to hold is that it covers more than the winner, not that it covers some particular
        number of px.
        """
        _, report = self.placed["er"]
        self.assertEqual(place.unplaceable(report), [], f"search still clips: {report}")
        self.assertEqual(max(row["clip"] for row in report), 0)
        found = sum(row["overlap"] for row in report) / len(report)
        self.assertLess(found, 1, f"the search should cover nothing here, not {found:.0f}")

        # Proof it had something to get wrong. `center-right` puts the new-table callout across
        # both arrows leaving `presence_sessions`.
        measured = place._measure_candidates(ER, "er", [("top-left", "center-right")], "light")
        poor = measured[0][1]
        _, poor_clip, poor_overlap = place._score(poor)
        self.assertEqual(poor_clip, 0, "the point is that it covers, not that it clips")
        self.assertGreater(poor_overlap, 20 * arrows.STROKE * OVERLAP_WEIGHTS["path"],
                           f"an anchor across both arrows measured {poor_overlap:.0f} — if "
                           "nothing on this diagram can be covered, the search proves nothing")
        self.assertGreater(poor_overlap, found, "and more than the one the search picked")
        self.assertGreater(poor["landmarks"], 0,
                           "it also covers a corner or an end, which is what the search ranks "
                           "above everything it is willing to trade")

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


# THREE notes, which is the one thing neither sample set has: above `place.JOINT_MAX` the
# search stops being exhaustive and settles one note at a time, so nothing measured on the
# two sample sets exercises that path at all. Crowded on purpose — a note on the box every
# arrow passes, so no position for it is clear and the least bad one has to ship.
THREE_NOTES = {
    "kind": "architecture",
    "title": "Three notes on a crowded drawing",
    "nodes": [
        {"id": "shop", "label": "Storefront", "children": [
            {"id": "cart", "label": "Cart", "role": "client"},
            {"id": "pay", "label": "PayButton", "role": "client", "note": "new component"},
        ]},
        {"id": "svc", "label": "Payments service", "children": [
            {"id": "api", "label": "capture API", "role": "svc", "note": "rate limited now"},
            {"id": "worker", "label": "settlement worker", "role": "svc"},
        ]},
        {"id": "psp", "label": "Card processor", "role": "ext", "shape": "hexagon",
         "note": "sandbox in staging"},
        {"id": "ledger", "label": "Ledger", "role": "store", "shape": "cylinder"},
    ],
    "edges": [
        {"from": "shop.cart", "to": "shop.pay", "label": "checkout"},
        {"from": "shop.pay", "to": "svc.api", "label": "POST /capture"},
        {"from": "svc.api", "to": "psp", "label": "authorize"},
        {"from": "svc.api", "to": "ledger", "label": "reserve"},
        {"from": "svc.worker", "to": "ledger", "label": "settle"},
        {"from": "psp", "to": "svc.worker", "label": "webhook"},
    ],
}


@unittest.skipUnless(HAVE_D2 and HAVE_BROWSER, "needs d2 and a browser")
class TestMoreNotesThanTheGridCanAfford(unittest.TestCase):
    """One placement pass over three notes: 24 candidates, about a third of the exhaustive
    two-note search this file's other cases pay for.

    What it is here to catch is a whole-pipeline claim that nothing else makes: that a
    drawing where no placement is clear still ships the least bad one AND says so. Both
    halves were silent before — the search reported only a note it could not fit without
    cutting it off, so a note sitting across an arrow reached the reader with nothing said.
    """

    @classmethod
    def setUpClass(cls):
        cls.figure = list(figure.draw({"crowded": THREE_NOTES}, target="embed",
                                      theme="light"))[0]

    def test_every_note_is_settled_and_none_is_cut_off(self):
        placed = callout.notes(self.figure.svg)
        self.assertEqual(len(placed), 3, "a note was dropped by the greedy path")
        self.assertEqual(self.figure.placement, [], "nothing here should fail to fit")

    def test_a_note_that_could_not_be_placed_clear_is_said_out_loud(self):
        covering = [text for text, box in callout.notes(self.figure.svg)
                    if arrows.hides(self.figure.svg, box).line]
        self.assertTrue(covering, "this diagram is meant to be too crowded to place clear — "
                                  "if it no longer is, the case has stopped testing anything")
        said = " ".join(self.figure.advice)
        for text in covering:
            self.assertIn(repr(text), said,
                          f"{text!r} covers line and nothing told the author")

    def test_it_is_advice_rather_than_a_failure(self):
        """The drawing is the best available, so it must not read as broken: `visualize`
        prints advice as a warning and does not count it against its exit code."""
        self.assertEqual([p for p in self.figure.problems if "covers" in p], [])
        self.assertTrue(self.figure.ok, self.figure.problems + self.figure.blocked)


if __name__ == "__main__":
    unittest.main(verbosity=2)
