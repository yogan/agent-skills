#!/usr/bin/env python3
"""Where an edge label ends up, on hand-built SVGs with the geometry the real ones have.

No d2 and no browser: this module reads coordinates out of an SVG and writes coordinates back
into it, so a fixture is a few hundred bytes of the same shape d2 emits. What is worth pinning
is every rule the module has, because each one is a defect it shipped before the rule existed:

  * a label on a horizontal leg masks its whole length, so it moves to a vertical one;
  * it stays CENTRED there, because a label beside a line stops saying which line it is for;
  * the mask travels with it, or the drawing keeps a hole where nothing is standing;
  * the canvas is the MASK's box and not the root viewBox — they differ by up to 100px;
  * a label with nothing to gain does not move, so a clean figure renders byte-identically.

Run: `python3 lib/diagram/test_edgelabel.py`
"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.diagram import edgelabel  # noqa: E402

CONN = ('<path d="{d}" stroke="var(--d-muted)" fill="none" class="connection" '
        'style="stroke-width:2;" mask="url(#m)" />'
        '<text x="{x}" y="{y}" fill="var(--d-muted)" class="text-italic" '
        'style="text-anchor:middle;font-size:13px">{text}</text>')


def svg(conns, holes, canvas=(0, 0, 400, 400), shapes="", box=None):
    """One SVG: `conns` as (d, cx, baseline, text), `holes` as (x, y, w, h) mask rects."""
    x, y, w, h = canvas
    body = "".join(CONN.format(d=d, x=cx, y=by, text=t) for d, cx, by, t in conns)
    rects = "".join(f'<rect x="{r[0]:f}" y="{r[1]:f}" width="{r[2]:g}" height="{r[3]:g}" '
                    f'fill="black"></rect>\n' for r in holes)
    view = box or canvas
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{view[0]} {view[1]} '
            f'{view[2]} {view[3]}" width="{view[2]}" height="{view[3]}">'
            f"{shapes}{body}"
            f'<mask id="m" maskUnits="userSpaceOnUse" x="{x}" y="{y}" width="{w}" '
            f'height="{h}">\n<rect x="{x}" y="{y}" width="{w}" height="{h}" '
            f'fill="white"></rect>\n{rects}</mask></svg>')


def moved(out):
    """The (dx, dy) each label was translated by, in document order."""
    return [(float(m.group(1)), float(m.group(2)))
            for m in re.finditer(r'<g transform="translate\(([-\d.]+),([-\d.]+)\)">', out)]


def holes_of(out):
    mask = edgelabel._MASK.search(out)
    return [tuple(float(g) for g in r.group(1, 2, 3, 4))
            for r in edgelabel._MASK_RECT.finditer(mask.group(6))]


# A route with two long vertical legs and a short horizontal jog between them — the shape ELK
# produces whenever an edge steps sideways between layers, and the one where it parks the label
# on the jog. 100px down, 40px across, 100px down.
TURNING = "M 100 20 L 100 120 S 100 130 110 130 L 140 130 S 150 130 150 140 L 150 240"


class TestMovesOffAHorizontalLeg(unittest.TestCase):
    def setUp(self):
        # Label 80x17 centred on the jog: exactly what d2 emits for this route.
        self.out = edgelabel.reposition(
            svg([(TURNING, 125, 134, "one drawing")], [(85, 121, 80, 17)]))

    def test_the_label_moves(self):
        self.assertEqual(len(moved(self.out)), 1)

    def test_it_lands_centred_on_a_vertical_leg(self):
        """Centred across the line, not beside it — the label must still name its own arrow."""
        dx, _dy = moved(self.out)[0]
        self.assertIn(125 + dx, (100, 150), "the label's centre must be ON one of the legs")

    def test_the_mask_hole_travels_with_it(self):
        (dx, dy), = moved(self.out)
        self.assertEqual(holes_of(self.out), [(85 + dx, 121 + dy, 80, 17)])

    def test_it_masks_the_labels_own_height_instead_of_the_whole_jog(self):
        """The whole point, in the only unit that matters: how much arrow disappeared. The jog
        is 30px of straight line between two 10px corner curves, and the label ate all of it.
        On a vertical leg it costs its own 17px of height instead.
        """
        before = edgelabel._Box((85, 121, 165, 138))
        (dx, dy), = moved(self.out)
        after = edgelabel._Box((85 + dx, 121 + dy, 165 + dx, 138 + dy))
        legs = edgelabel._path_boxes(TURNING)
        self.assertAlmostEqual(sum(before.overlap(leg) for leg in legs) / 2, 30, delta=1)
        self.assertAlmostEqual(sum(after.overlap(leg) for leg in legs) / 2, 17, delta=1)


class TestStaysPut(unittest.TestCase):
    def test_a_label_already_centred_on_a_straight_run_does_not_move(self):
        """Most labels are this, and a pass that jiggles them all would make every figure's
        diff unreadable for no gain."""
        out = edgelabel.reposition(
            svg([("M 100 20 L 100 240", 100, 134, "compile")], [(70, 121, 60, 17)]))
        self.assertEqual(moved(out), [])

    def test_a_leg_too_short_to_hold_the_label_is_not_offered(self):
        """ELK leaves 3px stubs between corners; a label centred on one has no line either
        side of it, and then the break IS the leg."""
        route = "M 100 20 L 100 23 S 100 33 110 33 L 130 33"
        out = edgelabel.reposition(svg([(route, 120, 37, "a very long label indeed")],
                                       [(60, 24, 120, 17)]))
        self.assertEqual(moved(out), [], "no leg here is 128px long; there is nowhere to go")


class TestObstacles(unittest.TestCase):
    def test_a_label_slides_along_its_leg_to_clear_a_container_border(self):
        """The reference `GraphQL` case: ELK's own placement ends 5px inside the Kubernetes
        container's top border, and there are 180px of leg to slide up."""
        border = ('<rect x="10.000000" y="200.000000" width="380.000000" height="180.000000" '
                  'stroke="var(--d-grp-br)" fill="var(--d-grp-bg)" style="stroke-width:2;" />')
        out = edgelabel.reposition(
            svg([("M 100 20 L 100 200", 100, 199, "GraphQL")], [(70, 186, 60, 17)],
                shapes=border))
        (_dx, dy), = moved(out)
        self.assertLess(186 + dy + 17, 200, "the label must end above the container's border")

    def test_a_candidate_off_the_canvas_loses_to_staying_put(self):
        """Scored first and absolutely: a label outside the drawing is not a placement."""
        out = edgelabel.reposition(
            svg([("M 100 20 L 100 240", 100, 134, "x")], [(70, 121, 60, 17)],
                canvas=(0, 0, 400, 400)))
        self.assertEqual(moved(out), [])


class TestCanvasIsTheMaskBox(unittest.TestCase):
    def test_the_mask_box_wins_over_the_viewbox(self):
        """d2 emits `viewBox="0 0 432 591"` for a drawing whose geometry runs -101..331. Read
        the viewBox and a label has 101px of room on the right that does not exist — which is
        how the reference state machine's `401 invalid` came out 27px past the edge.
        """
        # Canvas -100..100; the viewBox claims 0..400. Sliding right would be legal under the
        # viewBox and is not under the mask, so the label must stay on its leg near x=-50.
        route = "M -50 20 L -50 120 S -50 130 -40 130 L -10 130 S 0 130 0 140 L 0 240"
        out = edgelabel.reposition(
            svg([(route, -25, 134, "wide label here")], [(-65, 121, 80, 17)],
                canvas=(-100, 0, 200, 400), box=(0, 0, 400, 400)))
        for dx, dy in moved(out):
            self.assertLessEqual(-65 + dx + 80, 100, "must stay inside the MASK's canvas")


class TestRowAlignment(unittest.TestCase):
    """Labels that land near the same height get pulled onto exactly that height.

    Three arrows leaving a row of boxes otherwise get three individually optimal heights, which
    collectively looks like nothing was decided.
    """

    @staticmethod
    def runs(labels):
        """Parallel vertical routes, each with its label where ELK would put it.

        The label starts at its route's own midpoint, because that is where the base search
        wants it too — a fixture that starts it anywhere else measures the base search moving
        it back, not the alignment.
        """
        conns, holes = [], []
        for x, top, bottom, width, text in labels:
            middle = (top + bottom) / 2
            conns.append((f"M {x} {top} L {x} {bottom}", x, middle + 4.5, text))
            holes.append((x - width / 2, middle - 8.5, width, 17))
        return svg(conns, holes, canvas=(0, 0, 600, 320))

    @staticmethod
    def centres(out, labels):
        """Every label's final centre height."""
        deltas = dict(zip(range(len(labels)), moved(out)))
        heights = []
        for index, (_x, top, bottom, _w, _t) in enumerate(labels):
            heights.append((top + bottom) / 2 + (deltas.get(index, (0, 0))[1]))
        return heights

    def test_near_heights_are_pulled_onto_one(self):
        labels = [(100, 20, 240, 50, "a"), (250, 36, 256, 50, "b"), (420, 42, 262, 50, "c")]
        out = edgelabel.reposition(self.runs(labels))
        self.assertEqual(len(moved(out)), 3, "all three should have shifted")
        heights = self.centres(out, labels)
        self.assertLessEqual(max(heights) - min(heights), 0.01,
                             f"the row must share one height, got {heights}")

    def test_far_apart_labels_are_left_alone(self):
        """Alignment is for labels that already look like a row, not for gathering the page."""
        out = edgelabel.reposition(self.runs(
            [(100, 20, 100, 50, "a"), (250, 110, 190, 50, "b"), (420, 200, 280, 50, "c")]))
        self.assertEqual(moved(out), [])

    def test_a_label_that_would_touch_another_keeps_its_own_place(self):
        """The case that has to survive, and the one a real figure hit: `read · write` and
        `verify JWT` overlap in x, so they cannot share a height. The nearer one takes the row
        and the other stays where it was — a partial row beats a collision, and beats
        abandoning the row.
        """
        labels = [(100, 20, 240, 140, "left"), (150, 36, 256, 140, "wide"),
                  (450, 30, 250, 50, "far")]
        out = edgelabel.reposition(self.runs(labels))
        heights = self.centres(out, labels)
        self.assertNotEqual(heights[0], heights[1],
                            "two labels overlapping in x must not share a height")

    def test_a_label_that_cannot_reach_the_row_keeps_its_own_place(self):
        """Its leg is 40px long, so its label can travel 7.5px; the row is 90px away."""
        labels = [(100, 20, 300, 50, "long"), (250, 50, 90, 50, "short")]
        out = edgelabel.reposition(self.runs(labels))
        for _dx, dy in moved(out):
            self.assertLessEqual(abs(dy), 8, "a short leg cannot reach a distant row")


class TestGeometryHelpers(unittest.TestCase):
    def test_a_near_vertical_leg_still_counts_as_vertical(self):
        """ELK drifts a fraction of a px over a long leg (`M 344.49 439 L 344.02 546`), and
        calling those diagonal would exclude exactly the legs this module exists to use."""
        legs = edgelabel._legs(edgelabel.points("M 344.49 439 L 344.02 546"))
        self.assertEqual([leg[0] for leg in legs], ["v"])

    def test_a_corner_curve_contributes_no_leg(self):
        """Only the endpoint of a curve is kept, so a rounded corner becomes one short
        diagonal — which is off-axis and can never host a label."""
        legs = edgelabel._legs(edgelabel.points("M 0 0 L 0 100 S 0 110 10 110 L 60 110"))
        self.assertEqual([(leg[0], round(leg[3])) for leg in legs], [("v", 100), ("h", 50)])

    def test_the_midpoint_is_measured_along_the_route_not_across_it(self):
        """It is the tie-break that keeps a label near where ELK meant it to be."""
        self.assertEqual(edgelabel._midpoint([(0, 0), (0, 100), (100, 100)]), (0.0, 100.0))


class TestTermination(unittest.TestCase):
    """The property that rules out the bug this module had twice: it never iterates.

    Placement runs once, rows are grouped once, each row moves once. Nothing here reads a
    position it has also written, so there is no fixed point to chase and no pair of labels
    that can trade places forever. Both earlier designs could: snapping to already-placed
    labels needed a second sweep to align in both directions, and the second sweep made two
    labels swap heights on every pass.
    """

    def test_running_it_again_changes_nothing(self):
        """Idempotence is the cheap observable form of it. If a second run moved anything, the
        first run's output was not a fixed point and some caller would eventually loop.
        """
        route = "M 100 20 L 100 120 S 100 130 110 130 L 140 130 S 150 130 150 140 L 150 240"
        once = edgelabel.reposition(svg([(route, 125, 134, "one drawing")],
                                        [(85, 121, 80, 17)]))
        self.assertEqual(edgelabel.reposition(once), once)

    def test_a_row_of_labels_settles_in_one_pass(self):
        labels = [(100, 20, 240, 50, "a"), (250, 36, 256, 50, "b"), (420, 42, 262, 50, "c")]
        once = edgelabel.reposition(TestRowAlignment.runs(labels))
        self.assertEqual(edgelabel.reposition(once), once)


class TestSafety(unittest.TestCase):
    def test_an_svg_with_no_mask_is_returned_unchanged(self):
        plain = '<svg viewBox="0 0 10 10"><path d="M 0 0 L 1 1"/></svg>'
        self.assertEqual(edgelabel.reposition(plain), plain)

    def test_an_unlabelled_connection_is_not_paired_with_the_next_label(self):
        """d2 emits the path and its label as adjacent siblings; an edge with no label has no
        `<text>` after it, and matching across the gap would move somebody else's words."""
        conns = ('<path d="M 0 0 L 0 100" class="connection" mask="url(#m)" />'
                 + CONN.format(d="M 200 20 L 200 240", x=200, y=134, text="mine"))
        out = edgelabel.reposition(
            f'<svg viewBox="0 0 400 400">{conns}'
            f'<mask id="m" maskUnits="userSpaceOnUse" x="0" y="0" width="400" height="400">\n'
            f'<rect x="0" y="0" width="400" height="400" fill="white"></rect>\n'
            f'<rect x="170.000000" y="121.000000" width="60" height="17" fill="black">'
            f"</rect>\n</mask></svg>")
        self.assertEqual(moved(out), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
