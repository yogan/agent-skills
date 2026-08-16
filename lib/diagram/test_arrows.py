#!/usr/bin/env python3
"""The three rules about an arrow, on the geometry d2 really emits.

No d2 and no browser for most of it: a route is a path string, and every number in the
fixtures below is one measured off the corpus — a corner spans 8px per axis, ELK leaves 12px
between its last cross-run and the box it points at, an arrowhead is 10px long.

The last class renders both corpora, because the claim worth pinning is not "this function
returns 4" but "no drawing this repo ships has an arrowhead painted across a turn it could
have avoided". What buys that is `d2.ELK_EDGE_LADDER`, so this file is where a change to that
number gets caught.

Run: `python3 lib/diagram/test_arrows.py`
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.diagram import arrows  # noqa: E402

# The shape ELK produces for an edge that steps sideways on its way into a box: a long run, a
# rounded corner, a short cross-run, a second corner 8px on each axis, and 4px of straight line
# into the head. Transcribed from the reference architecture's `read · write`.
TURNS_AT_THE_HEAD = ("M 146.5 404 L 146.5 673 S 146.5 683 156.5 683 L 230.9 683 "
                     "S 238.9 683 238.9 691 L 239 695")
CONN = ('<path d="{d}" stroke="var(--d-muted)" fill="none" class="connection" '
        'style="stroke-width:2;"{marker} mask="url(#m)" />')


def svg(paths, holes=()):
    """One SVG: `paths` as (d, marker attribute), `holes` as (x, y, w, h) mask rectangles."""
    body = "".join(CONN.format(d=d, marker=marker) for d, marker in paths)
    rects = "".join(f'<rect x="{r[0]}" y="{r[1]}" width="{r[2]}" height="{r[3]}" '
                    f'fill="black"></rect>\n' for r in holes)
    return (f'<svg viewBox="0 0 800 800" width="800" height="800">{body}'
            f'<mask id="m" maskUnits="userSpaceOnUse" x="0" y="0" width="800" height="800">\n'
            f'<rect x="0" y="0" width="800" height="800" fill="white"></rect>\n'
            f"{rects}</mask></svg>")


HEAD = ' marker-end="url(#head)"'
BOTH = ' marker-start="url(#tail)" marker-end="url(#head)"'


class TestReadingARoute(unittest.TestCase):
    def test_a_near_vertical_leg_still_counts_as_vertical(self):
        """ELK drifts a fraction of a px over a long leg (`M 344.49 439 L 344.02 546`), and
        calling those diagonal would exclude exactly the legs `edgelabel` exists to use."""
        legs = arrows.legs(arrows.points("M 344.49 439 L 344.02 546"))
        self.assertEqual([leg[0] for leg in legs], ["v"])

    def test_a_corner_curve_contributes_no_leg(self):
        """Only the endpoint of a curve is kept, so a rounded corner becomes one short
        off-axis step — which can never host a label and is not a run."""
        legs = arrows.legs(arrows.points("M 0 0 L 0 100 S 0 110 10 110 L 60 110"))
        self.assertEqual([(leg[0], round(leg[3])) for leg in legs], [("v", 100), ("h", 50)])


class TestDiagonals(unittest.TestCase):
    """A rounded corner and a diagonal run look identical to anything that reads endpoints.

    Telling them apart is the only reason `CORNER` exists, and the number that matters is the
    widest corner d2 draws — 17.2px per axis in the corpus, a 24.2px step. A threshold below
    that reports every corner in every figure as a broken rule.
    """

    def test_the_widest_corner_d2_draws_is_not_a_diagonal(self):
        route = "M 0 0 L 0 100 S 0 117.2 17.2 117.2 L 200 117.2"
        self.assertEqual(arrows.defects(svg([(route, HEAD)])), [])

    def test_a_run_that_really_is_diagonal_is_reported(self):
        route = "M 0 0 L 0 100 L 120 220 L 300 220"
        found = arrows.defects(svg([(route, HEAD)]))
        self.assertEqual([d.rule for d in found], ["diagonal"])

    def test_the_threshold_leaves_room_above_the_corner_it_has_to_admit(self):
        """Stated as a relationship rather than a number, so raising one raises the other."""
        self.assertGreater(arrows.CORNER, 17.2)


class TestTurnBeforeAHead(unittest.TestCase):
    def test_a_head_on_four_px_of_straight_line_is_reported(self):
        found = arrows.defects(svg([(TURNS_AT_THE_HEAD, HEAD)]))
        self.assertEqual([d.rule for d in found], ["turn"])

    def test_a_long_approach_is_not(self):
        self.assertEqual(arrows.defects(svg([("M 0 0 L 0 300", HEAD)])), [])

    def test_a_connection_with_no_arrowhead_has_no_approach_to_check(self):
        """A sequence diagram's lifelines are `class="connection"` paths with no marker on
        them. They are not arrows and there is nothing at their ends to strand."""
        route = "M 0 0 L 0 100 S 0 108 8 108 L 20 108 S 28 108 28 112 L 28 116"
        self.assertEqual(arrows.defects(svg([(route, "")])), [])

    def test_two_runs_on_one_axis_are_one_approach(self):
        """A route may be split at a point that is not a turn; reading that as two runs would
        report a turn where the line is dead straight."""
        self.assertEqual(arrows.defects(svg([("M 0 0 L 0 40 L 0 300", HEAD)])), [])


class TestGapsAtAHead(unittest.TestCase):
    """What may not be cut out of the line, and the arithmetic `edgelabel` scores against.

    One rectangle in the mask hides every connection crossing it — d2's mask is not per-edge —
    so this is about a piece of canvas, not about whose label it is.
    """

    def test_a_gap_over_the_last_px_of_a_line_is_reported(self):
        found = arrows.defects(svg([("M 100 0 L 100 300", HEAD)], [(70, 285, 60, 17)]))
        self.assertEqual([d.rule for d in found], ["gap"])

    def test_a_gap_in_the_middle_is_not(self):
        self.assertEqual(arrows.defects(svg([("M 100 0 L 100 300", HEAD)],
                                            [(70, 140, 60, 17)])), [])

    def test_the_shortfall_is_the_px_of_line_the_head_is_owed(self):
        """`MIN_RUN` px must survive past the arrowhead's own reach; the number reported is how
        many of them are missing, so a placement search can prefer the least bad spot."""
        zones = arrows.ends(svg([("M 100 0 L 100 300", HEAD)]))
        # Bottom edge at 285: the head reaches back to 290, so 5px of line survive and one is
        # missing. Ten px lower and none survive at all.
        self.assertEqual(arrows.shortfall(arrows.Box((70, 268, 130, 285)), zones), 1)
        self.assertEqual(arrows.shortfall(arrows.Box((70, 278, 130, 295)), zones), 11)

    def test_a_head_at_the_start_of_a_two_ended_arrow_counts_too(self):
        found = arrows.defects(svg([("M 100 0 L 100 300", BOTH)], [(70, 5, 60, 17)]))
        self.assertEqual([d.rule for d in found], ["gap"])

    def test_an_end_with_no_head_still_wants_the_line_to_start_somewhere(self):
        """No arrowhead to strand, so no `HEAD_REACH` allowance — but a line whose first px are
        inside a gap reads as starting late."""
        zones = arrows.ends(svg([("M 100 0 L 100 300", HEAD)]))
        self.assertEqual(arrows.shortfall(arrows.Box((70, 0, 130, 17)), zones), 6)
        self.assertEqual(arrows.shortfall(arrows.Box((70, 10, 130, 27)), zones), 0)

    def test_leaves_a_stub_is_the_same_rule_said_as_a_verdict(self):
        zones = arrows.ends(svg([("M 100 0 L 100 300", HEAD)]))
        self.assertFalse(arrows.leaves_a_stub(arrows.Box((70, 278, 130, 295)), zones))
        self.assertTrue(arrows.leaves_a_stub(arrows.Box((70, 140, 130, 157)), zones))


class TestARouteAcrossAShape(unittest.TestCase):
    """The invariant, on boxes handed straight in — `through` reads no geometry of its own.

    Zero is the answer on everything this repo draws, because ELK does not route through a
    box. That is exactly why these exist: a check whose only observed value is zero has not
    been shown to be capable of any other, and would go on reporting success after the day it
    stopped working.
    """

    BOX = arrows.Box((100, 100, 200, 200))

    def test_a_run_drawn_across_a_box_is_reported(self):
        found = arrows.through(svg([("M 0 150 L 400 150", HEAD)]), [self.BOX])
        self.assertEqual([d.rule for d in found], ["through"])

    def test_a_route_that_ends_at_a_box_may_of_course_touch_it(self):
        """Every arrow in every figure does this, so getting it wrong makes the check noise."""
        self.assertEqual(arrows.through(svg([("M 0 150 L 150 150", HEAD)]), [self.BOX]), [])

    def test_a_route_that_ends_just_short_of_its_box_is_still_its_box(self):
        """d2 stops the line clear of the border it points at — see `TERMINAL_REACH`."""
        self.assertEqual(arrows.through(svg([("M 0 150 L 96 150", HEAD)]), [self.BOX]), [])

    def test_a_self_loop_belongs_to_the_box_at_both_ends(self):
        route = "M 150 100 L 150 60 L 260 60 L 260 150 L 200 150"
        self.assertEqual(arrows.through(svg([(route, HEAD)]), [self.BOX]), [])

    def test_one_route_clipping_one_box_twice_is_one_thing_wrong(self):
        route = "M 0 120 L 300 120 L 300 180 L 0 180"
        found = arrows.through(svg([(route, HEAD)]), [self.BOX])
        self.assertEqual(len(found), 1)

    def test_a_box_the_route_misses_is_not_reported(self):
        self.assertEqual(arrows.through(svg([("M 0 300 L 400 300", HEAD)]), [self.BOX]), [])

    def test_the_reach_covers_the_gap_d2_really_leaves_at_an_end(self):
        """Measured off the reference architecture: `read · write` stops 3.1px short of
        PostgreSQL's border and starts 4px clear of the GraphQL API's. Stated as a
        relationship so a reach tightened below what d2 does fails here."""
        self.assertGreater(arrows.TERMINAL_REACH, 4)


class TestAgainstTheCorpus(unittest.TestCase):
    """Both corpora, measured for all three rules. Zero IS the expected number, now.

    It was not. Two arrowheads on the reference architecture sat on a curve because the only
    remedy was the wider edge spacing and that figure cannot buy it at any rung — it needs 24,
    and its text falls under the readability floor at 21. The remedy is no longer the only one:
    `route.straighten` slides the last step of a route back along the run it comes off, which
    costs no page at all, and eleven of the twelve tails in the two corpora take it.

    The twelfth is the repo state machine, whose label is nearly as long as the run it would
    slide along — so it still buys the fix with `d2.ELK_EDGE_LADDER`, and still ships clean.
    That is why this list is empty rather than deleted: it is the shape an exception takes, and
    the next figure that cannot pay goes here with what it cannot pay named.

    These are unplaced renders, as in `test_reference`: placement is minutes and this is
    tenths.
    """

    KNOWN = {}

    @classmethod
    def setUpClass(cls):
        from lib.diagram import render
        if render.d2_version() is None:
            raise unittest.SkipTest("d2 is not installed (brew install d2)")
        from lib.diagram.examples import REFERENCE
        from lib.diagram.examples_repo import REPO
        cls.drawn = {f"{group}/{name}": render.render(spec, name=f"{group}-{name}")
                     for group, specs in (("reference", REFERENCE), ("repo", REPO))
                     for name, spec in specs.items()}

    def counted(self, key, rule):
        return len([d for d in arrows.defects(self.drawn[key]) if d.rule == rule])

    def test_no_arrowhead_is_painted_across_a_turn_it_could_have_avoided(self):
        for key in self.drawn:
            self.assertEqual(self.counted(key, "turn"), self.KNOWN.get(key, {}).get("turn", 0),
                             f"{key}: {[d.detail for d in arrows.defects(self.drawn[key])]}")

    def test_no_run_of_any_arrow_is_diagonal(self):
        for key in self.drawn:
            self.assertEqual(self.counted(key, "diagonal"), 0, key)

    def test_only_the_known_gaps_land_on_an_arrowhead(self):
        for key in self.drawn:
            self.assertEqual(self.counted(key, "gap"), self.KNOWN.get(key, {}).get("gap", 0),
                             f"{key}: {[d.detail for d in arrows.defects(self.drawn[key])]}")

    def test_no_arrow_is_drawn_across_a_box_it_does_not_end_at(self):
        """The invariant, and it has no exceptions — unlike the three rules above, nothing
        here trades it away, and there is no figure that cannot afford it."""
        from lib.diagram import edgelabel
        for key, svg_text in self.drawn.items():
            found = arrows.through(svg_text, edgelabel.route_obstacles(svg_text))
            self.assertEqual(found, [], f"{key}: {[d.detail for d in found]}")

    def test_the_invariant_can_still_report_a_real_figures_box(self):
        """Zero everywhere is only reassuring from a check that would say otherwise. This
        drives a run straight through the biggest box in a real drawing, using the obstacle
        geometry the corpus test above relies on, which is what a route adjustment with its
        bounds wrong would look like."""
        from lib.diagram import edgelabel
        svg_text = self.drawn["reference/arch"]
        boxes = edgelabel.route_obstacles(svg_text)
        target = max(boxes, key=lambda b: b.w * b.h)
        middle = ((target[0] + target[2]) / 2, (target[1] + target[3]) / 2)
        run = CONN.format(d=f"M {target[0] - 300:.0f} {middle[1]:.0f} "
                            f"L {target[2] + 300:.0f} {middle[1]:.0f}", marker=HEAD)
        found = arrows.through(svg_text.replace("</svg>", run + "</svg>"), boxes)
        self.assertIn((round(middle[0]), round(middle[1])),
                      [(round(d.at[0]), round(d.at[1])) for d in found])


if __name__ == "__main__":
    unittest.main(verbosity=2)
