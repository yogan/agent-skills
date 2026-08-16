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
# `fan-out` leaving Redis: 6px across and 6px down at once, which d2 can only draw as a cubic.
BUMP = ("M 591.000493 385.044390 L 580.999261 385.266423 C 575.000739 385.399593 581.000000 "
        "391.333008 575.000000 391.333008 L 307.000000 391.333008 S 297.000000 391.333008 "
        "297.000000 381.333008 L 297.000000 317.333008 S 297.000000 307.333008 307.000000 "
        "307.333008 L 323.000000 307.333008")

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


class TestADiagonalChannelChange(unittest.TestCase):
    """`fan-out` leaving Redis — the only cubic in either corpus, and the bump you can see.

    d2 draws an orthogonal corner as `S` and a diagonal one as `C`, so the command letter is
    the whole detector. This route moves 6px across and 6px down at once, which is a run that
    is neither vertical nor horizontal — the rule `arrows.defects` states and cannot enforce
    here, because `CORNER` has to tolerate 20px of off-axis before it calls anything diagonal.
    """

    CANVAS = arrows.Box((3, 3, 968, 437))

    def squared(self, canvas=None, obstacles=()):
        return route._square(BUMP, canvas or self.CANVAS,
                             [arrows.Box(o) for o in obstacles])

    def test_the_cubic_is_gone(self):
        self.assertTrue(any(c in "Cc" for c, _p in route._commands(BUMP)))
        self.assertFalse(any(c in "Cc" for c, _p in route._commands(self.squared())))

    def test_what_is_left_off_axis_is_a_corner_and_not_a_run(self):
        """`arrows.points` reads every rounded corner as one short off-axis step, so "no step
        is off axis" is false of every route ever drawn here. What can be said is that each
        one is corner-sized, and that the two runs it joins really are axis-aligned."""
        points = arrows.points(self.squared())
        for a, b in zip(points, points[1:]):
            if route._axis(a, b)[0] is None:
                self.assertLessEqual(max(abs(b[0] - a[0]), abs(b[1] - a[1])), arrows.CORNER,
                                     f"{a} -> {b} is long enough to read as a diagonal run")
        self.assertEqual(arrows.defects(svg([self.squared()])), [])

    def test_the_step_ends_up_deep_enough_to_turn_twice_in(self):
        points = arrows.points(self.squared())
        self.assertAlmostEqual(points[-5][1] - points[0][1], route.STEP_DEPTH, delta=1)

    def test_neither_end_of_the_route_moves(self):
        """The port on the box is ELK's and the arrowhead is pointing at something."""
        points, was = arrows.points(self.squared()), arrows.points(BUMP)
        self.assertEqual((points[0], points[-1]), (was[0], was[-1]))

    def test_the_corner_at_the_far_end_comes_with_it(self):
        """The run after the transition is what moves, so whatever it turns into has to
        follow — otherwise the route acquires a second diagonal where the first one was."""
        points = arrows.points(self.squared())
        self.assertAlmostEqual(points[-5][1], points[-4][1] + route.CORNER_REACH, delta=1)

    def test_a_route_with_no_cubic_is_left_alone(self):
        self.assertIsNone(route._square(TAIL, self.CANVAS, []))

    def test_it_will_not_push_the_channel_off_the_canvas(self):
        self.assertIsNone(self.squared(canvas=arrows.Box((3, 3, 968, 400))))

    def test_it_will_not_push_the_channel_through_a_shape(self):
        self.assertIsNone(self.squared(obstacles=[(400, 400, 500, 420)]))

    def test_a_transition_whose_second_run_ends_the_route_is_refused(self):
        """Moving that run would drag the arrowhead off the box it points at."""
        self.assertIsNone(route._square(
            "M 100 100 L 200 100 C 205 100 210 106 206 106 L 400 106", self.CANVAS, []))


class TestACalloutRestingOnALine(unittest.TestCase):
    """`user leaves` on the reference state machine: a callout 0.4px above it for 214px.

    `place` charges a callout for what it OCCLUDES, and a box that stops short of a line
    occludes nothing — so it pays nothing and still reads as sitting on it. Moving the callout
    was measured instead and costs 43px of page on that figure; moving the line costs none.
    """

    RUN = "M 100.000000 200.000000 L 500.000000 200.000000"
    ABOVE = arrows.Box((200, 150, 300, 199))        # rests on the run from above
    BIG = arrows.Box((0, 0, 600, 400))

    def cleared(self, callouts=None, others=(), canvas=None, obstacles=()):
        return route._clear(self.RUN, callouts or [self.ABOVE], list(others),
                            canvas or self.BIG, list(obstacles))

    def test_the_run_is_pushed_clear(self):
        line = arrows.points(self.cleared())[-1][1]
        self.assertGreaterEqual(line - self.ABOVE[3], route.CALLOUT_CLEARANCE)

    def test_it_pushes_the_least_it_can(self):
        """Pushed further the run starts crowding whatever channel is beyond it, and on the
        figure this exists for the next one is only 30px away."""
        self.assertAlmostEqual(arrows.points(self.cleared())[-1][1],
                               self.ABOVE[3] + route.CALLOUT_CLEARANCE, places=3)

    def test_it_pushes_away_from_the_callout_whichever_side_it_is_on(self):
        below = arrows.Box((200, 201, 300, 260))
        self.assertAlmostEqual(arrows.points(self.cleared([below]))[-1][1],
                               below[1] - route.CALLOUT_CLEARANCE, places=3)

    def test_the_corners_are_sized_to_the_room(self):
        """The push is ~10px and two full corners are 20, so they have to shrink — otherwise
        the straight between them runs BACKWARDS, which is what the first attempt did."""
        points = arrows.points(self.cleared())
        for a, b in zip(points, points[1:]):
            self.assertGreaterEqual((b[0] - a[0]) * 1.0, 0, f"{a} -> {b} doubles back")

    def test_the_far_end_still_arrives_where_it_did(self):
        self.assertEqual(arrows.points(self.cleared())[-1][0],
                         arrows.points(self.RUN)[-1][0])

    def test_a_run_already_clear_is_left_alone(self):
        self.assertIsNone(self.cleared([arrows.Box((200, 20, 300, 60))]))

    def test_a_callout_nowhere_near_it_along_the_run_is_ignored(self):
        self.assertIsNone(self.cleared([arrows.Box((520, 150, 580, 199))]))

    def test_it_will_not_push_onto_another_arrows_channel(self):
        """Two parallel runs into one box stop being separately followable — see
        `MIN_SEPARATION`."""
        neighbour = arrows.Box((100, 214, 500, 216))
        self.assertIsNone(self.cleared(others=[neighbour]))

    def test_it_will_not_push_the_run_off_the_canvas(self):
        self.assertIsNone(self.cleared(canvas=arrows.Box((0, 0, 600, 205))))

    def test_it_will_not_push_the_run_through_a_shape(self):
        self.assertIsNone(self.cleared(obstacles=[arrows.Box((300, 205, 400, 215))]))

    def test_a_route_with_more_than_one_run_is_left_alone(self):
        """The single-run shape is the one where stepping out needs no corner rebuilt at the
        far end. Anything else is refused rather than guessed at."""
        self.assertIsNone(route._clear(TAIL, [self.ABOVE], [], self.BIG, []))


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
