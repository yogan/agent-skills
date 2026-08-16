#!/usr/bin/env python3
"""Moving the last step of a route, on the geometry d2 really emits.

No d2 and no browser. `TAIL` below is transcribed verbatim from the reference architecture's
`read · write` at the tight rung — the shape all twelve short approaches in the two corpora
turned out to have — so what is exercised here is the real thing rather than a sketch of it.

The corpus-wide claim, that no arrowhead is left on a curve that could have been avoided, is
`test_arrows.TestAgainstTheCorpus`. What is worth pinning here is every bound on the move,
because each of them is a defect that would otherwise be traded for the one being fixed.

Run: `python3 lib/diagram/test_route.py`
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.diagram import arrows, edgelabel, route  # noqa: E402

# `read · write` into PostgreSQL: 309px across, a 24px step down, 3.5px into the head.
TAIL = ("M 470.000000 153.332993 L 779.000000 153.332993 S 789.000000 153.332993 789.000000 "
        "163.332993 L 789.000000 177.248958 S 789.000000 184.750000 796.500000 184.875000 "
        "L 800.000555 184.933343")
STRAIGHT = "M 100.000000 100.000000 L 100.000000 400.000000"

CONN = ('<path d="{d}" stroke="var(--d-muted)" fill="none" class="connection" '
        'style="stroke-width:2;" marker-end="url(#head)" mask="url(#m)" />')


def svg(paths, holes=()):
    body = "".join(CONN.format(d=d) for d in paths)
    rects = "".join(f'<rect x="{r[0]}" y="{r[1]}" width="{r[2]}" height="{r[3]}" '
                    f'fill="black"></rect>\n' for r in holes)
    return (f'<svg viewBox="0 0 1000 400" width="1000" height="400">{body}'
            f'<mask id="m" maskUnits="userSpaceOnUse" x="0" y="0" width="1000" height="400">\n'
            f'<rect x="0" y="0" width="1000" height="400" fill="white"></rect>\n'
            f"{rects}</mask></svg>")


def approach(d):
    """The straight run into the arrowhead of a single path."""
    tag, points = arrows.connections(svg([d]))[0]
    return arrows.approaches(tag, points)[0][1]


def moved(d, holes=(), obstacles=()):
    return route._slide(d, [arrows.Box(h) for h in holes], [arrows.Box(o) for o in obstacles])


class TestTheStepSlidesBack(unittest.TestCase):
    def test_the_head_ends_up_on_straight_line(self):
        self.assertLess(approach(TAIL), arrows.APPROACH)
        self.assertGreaterEqual(approach(moved(TAIL)), arrows.APPROACH)

    def test_it_aims_past_the_floor_rather_than_at_it(self):
        """`APPROACH` is where the defect stops being reported, which is a poor thing to aim
        at — see `COMFORTABLE`."""
        self.assertGreaterEqual(approach(moved(TAIL)), route.COMFORTABLE)

    def test_the_tip_does_not_move(self):
        """Where the arrow POINTS is ELK's and is not up for revision here; only where it
        turned on the way is."""
        self.assertEqual(arrows.points(moved(TAIL))[-1], arrows.points(TAIL)[-1])

    def test_the_far_end_does_not_move_either(self):
        self.assertEqual(arrows.points(moved(TAIL))[0], arrows.points(TAIL)[0])

    def test_the_route_is_still_orthogonal(self):
        """A rigid shift along one axis cannot tilt a run, and this says so out loud: a
        diagonal is the one defect that would make the drawing worse than it started."""
        self.assertEqual([d.rule for d in arrows.defects(svg([moved(TAIL)]))], [])

    def test_the_step_keeps_its_shape(self):
        """The corners are d2's and are not redrawn — the same five commands come back, with
        the same distance across."""
        before, after = route._commands(TAIL), route._commands(moved(TAIL))
        self.assertEqual([c for c, _ in before], [c for c, _ in after])
        self.assertAlmostEqual(before[-3][1][-1][1] - before[-5][1][-1][1],
                               after[-3][1][-1][1] - after[-5][1][-1][1], places=3)


class TestWhatItLeavesAlone(unittest.TestCase):
    def test_a_head_already_on_straight_line(self):
        self.assertIsNone(moved(STRAIGHT))

    def test_a_route_that_is_not_that_shape(self):
        """Only the tail ELK produces going into a box is recognised; anything else is not
        understood well enough to be moved."""
        self.assertIsNone(moved("M 0 0 L 100 0 L 100 50 L 200 50"))

    def test_a_route_too_short_to_have_a_tail(self):
        self.assertIsNone(moved("M 0 0 L 40 0"))

    def test_a_drawing_with_nothing_to_fix_comes_back_identical(self):
        """Byte-identical, so a figure that needs none of this has no diff at all."""
        page = svg([STRAIGHT])
        self.assertIs(route.straighten(page, []), page)


class TestWhatBoundsTheMove(unittest.TestCase):
    def test_the_words_on_the_run_keep_room_to_sit(self):
        """What has to survive is what `edgelabel._candidates` needs before it will put a
        label on a leg at all — the label's extent plus `CENTRE_SLACK`."""
        hole = (500, 145, 200, 17)                       # 200px of label on the long run
        out = route._commands(moved(TAIL, holes=[(500, 145, 700, 162)]))
        left = abs(out[-5][1][-1][0] - out[0][1][-1][0])
        self.assertGreaterEqual(left, hole[2] + edgelabel.CENTRE_SLACK)

    def test_a_run_all_but_filled_by_its_label_is_not_moved(self):
        """The repo state machine, which is why one figure still buys this with page."""
        self.assertIsNone(moved(TAIL, holes=[(475, 145, 775, 162)]))

    def test_the_run_has_to_survive_as_a_run(self):
        """`KEEP_RUN` — what is left still has to hold the corner at the end of it."""
        out = route._commands(moved(TAIL))
        self.assertGreaterEqual(abs(out[-5][1][-1][0] - out[0][1][-1][0]), route.KEEP_RUN)


class TestItMayNotCrossAShape(unittest.TestCase):
    """The invariant guarding the move — see `arrows.crosses`, which also audits the result."""

    BLOCKER = (700, 180, 760, 190)      # squarely on the approach a full-length move would take

    def test_it_backs_off_rather_than_giving_up(self):
        near = moved(TAIL, obstacles=[self.BLOCKER])
        self.assertIsNotNone(near, "a smaller move was available and should have been taken")
        self.assertGreaterEqual(approach(near), arrows.APPROACH)
        self.assertLess(approach(near), approach(moved(TAIL)),
                        "the move it settled for should be smaller than the unobstructed one")

    def test_what_it_settles_for_crosses_nothing(self):
        self.assertEqual(arrows.crosses(moved(TAIL, obstacles=[self.BLOCKER]),
                                        [arrows.Box(self.BLOCKER)]), [])

    def test_the_shape_at_its_own_end_is_not_an_obstacle(self):
        """Every arrow touches the box it points at, so reading that as a crossing would
        refuse every move there is."""
        self.assertIsNotNone(moved(TAIL, obstacles=[(804, 146, 916, 260)]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
