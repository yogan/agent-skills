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

from lib.diagram import arrows, edgelabel  # noqa: E402

# `marker-end` is not decoration: d2 puts one on every connection it draws as an arrow, and the
# rules about what may sit near an arrowhead are read off exactly that attribute. A fixture
# without one is a lifeline, not an arrow — see `TestTitleGaps`.
CONN = ('<path d="{d}" stroke="var(--d-muted)" fill="none" class="connection" '
        'style="stroke-width:2;" marker-end="url(#head)" mask="url(#m)" />'
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
    mask = arrows.MASK.search(out)
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
        before = arrows.Box((85, 121, 165, 138))
        (dx, dy), = moved(self.out)
        after = arrows.Box((85 + dx, 121 + dy, 165 + dx, 138 + dy))
        legs = arrows.leg_boxes(TURNING)
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

    def test_a_short_legged_label_is_not_dragged_to_a_distant_row(self):
        """What bounds a move is the label's OWN leg, as a fraction of it.

        The short one here has 40px of leg and can travel 7.5px of it; its neighbour has 280px
        and can travel 82. So the row forms at the short one's end of the range and the LONG
        label is the one that goes to meet it. That asymmetry is the point of the bound being
        relative: the same 82px is a different event on the two legs.
        """
        labels = [(100, 20, 300, 50, "long"), (250, 50, 90, 50, "short")]
        out = edgelabel.reposition(self.runs(labels))
        heights = self.centres(out, labels)
        # 64.4 rather than its 70px midpoint: the base search has already slid it clear of the
        # arrowhead at the end of its 40px leg. Alignment then takes the row to where it sits.
        self.assertAlmostEqual(heights[1], 64.4, delta=1, msg="the short leg barely moves")
        self.assertLessEqual(max(heights) - min(heights), 0.01, f"still a row: {heights}")

    def test_two_short_legs_far_apart_do_not_become_a_row(self):
        """The hard bound, with nobody able to spend it: 40px of leg each, 110px apart.

        Both labels DO shift a little — the base search slides each clear of the arrowhead at
        the end of its own leg — but neither travels toward the other, so no row forms.
        """
        labels = [(100, 150, 190, 50, "one"), (250, 260, 300, 50, "two")]
        out = edgelabel.reposition(self.runs(labels))
        heights = self.centres(out, labels)
        self.assertAlmostEqual(heights[1] - heights[0], 110, delta=1,
                               msg=f"they must stay 110px apart, got {heights}")


class TestGeometryHelpers(unittest.TestCase):
    """What reading a route gives back is `arrows`' business and `test_arrows` covers it."""

    def test_the_midpoint_is_measured_along_the_route_not_across_it(self):
        """It is the tie-break that keeps a label near where ELK meant it to be."""
        self.assertEqual(edgelabel._midpoint([(0, 0), (0, 100), (100, 100)]), (0.0, 100.0))


CONTAINER = ('<rect x="100.000000" y="10.000000" width="200.000000" height="120.000000" '
             'stroke="var(--d-grp-br)" fill="var(--d-grp-bg)" style="stroke-width:2;" />'
             '</g><text x="140.000000" y="28.000000" fill="var(--d-fg)" class="text" '
             'style="text-anchor:middle;font-size:13px">a title</text>')


class TestTitleGaps(unittest.TestCase):
    """A connection crossing a container title gets the break an edge label already gets.

    d2 punches a hole in the line under every edge label and under nothing else, so an arrow
    entering a container runs straight through its title. The title's width is not in the SVG,
    but it is derivable: d2 centres the text, so its half-width is the anchor's distance from
    the container's left edge less a fixed padding.
    """

    def test_the_box_comes_out_of_the_two_numbers_d2_gives(self):
        box, = edgelabel.title_boxes(CONTAINER)
        half = 140 - 100 - edgelabel.TITLE_PAD
        self.assertAlmostEqual(box[0], 140 - half, delta=0.01)
        self.assertAlmostEqual(box[2], 140 + half, delta=0.01)

    def test_the_band_matches_the_gaps_d2_cuts_for_its_own_labels(self):
        box, = edgelabel.title_boxes(CONTAINER)
        self.assertEqual((box[1], box.h), (28 - edgelabel.TITLE_RISE, edgelabel.TITLE_HEIGHT))

    def test_a_leaf_nodes_label_is_not_treated_as_a_title(self):
        """A leaf's label is centred in its BOX, so the same arithmetic returns half the box
        width — a hole in the arrow as wide as the node."""
        leaf = CONTAINER.replace("--d-grp-br", "--d-svc-br")
        self.assertEqual(edgelabel.title_boxes(leaf), [])

    def test_the_gap_is_cut_into_the_mask_every_connection_is_drawn_through(self):
        out = edgelabel.reposition(
            svg([("M 150 200 L 150 380", 150, 294, "x")], [(130, 281, 40, 17)],
                canvas=(0, 0, 400, 400), shapes=CONTAINER))
        self.assertIn('data-gap="title"', out)

    def test_a_gap_that_would_strand_an_arrowhead_is_not_cut(self):
        """The defect this rule exists for. On the reference architecture the `WebSocket` line
        ended 4px below `presence deploy x2`, so cutting the title out of it left the arrowhead
        floating — a triangle pointing at a box it was no longer joined to. Pushing the box
        down to make room is not available either: the container padding that buys an 8px stub
        takes that figure past the 800px viewport limit. So the title keeps its hairline.
        """
        out = edgelabel.reposition(
            svg([("M 150 5 L 150 38", 150, 60, "x")], [(130, 52, 40, 17)],
                canvas=(0, 0, 400, 400), shapes=CONTAINER))
        self.assertNotIn('data-gap="title"', out)

    def test_a_declined_gap_is_counted_as_an_unfixable_crossing(self):
        """What the layout search ranks on, so it knows a taller candidate is worth the rescue
        budget. Counting it needs no browser: the same geometry that declines the gap says the
        title is left with a line through it."""
        page = svg([("M 150 5 L 150 38", 150, 60, "x")], [(130, 52, 40, 17)],
                   canvas=(0, 0, 400, 400), shapes=CONTAINER)
        self.assertEqual(edgelabel.unfixable_crossings(page), 1)

    def test_a_title_nothing_crosses_is_not_counted(self):
        page = svg([("M 380 5 L 380 380", 380, 200, "x")], [(360, 187, 40, 17)],
                   canvas=(0, 0, 400, 400), shapes=CONTAINER)
        self.assertEqual(edgelabel.unfixable_crossings(page), 0)

    def test_a_crossing_that_gets_its_gap_is_not_counted(self):
        page = svg([("M 150 5 L 150 380", 150, 294, "x")], [(130, 281, 40, 17)],
                   canvas=(0, 0, 400, 400), shapes=CONTAINER)
        self.assertEqual(edgelabel.unfixable_crossings(page), 0)

    def test_a_gap_with_room_on_both_sides_is_cut(self):
        """Proof the rule above is not simply refusing everything."""
        out = edgelabel.reposition(
            svg([("M 150 5 L 150 380", 150, 294, "x")], [(130, 281, 40, 17)],
                canvas=(0, 0, 400, 400), shapes=CONTAINER))
        self.assertIn('data-gap="title"', out)

    def test_the_head_of_a_two_ended_arrow_counts_at_its_start_too(self):
        """A `<->` edge is painted with an arrowhead at each end, so the first px of the line
        are no more a stub than the last."""
        conn = ('<path d="M 150 8 L 150 380" stroke="var(--d-muted)" fill="none" '
                'class="connection" style="stroke-width:2;" marker-start="url(#m2)" '
                'mask="url(#m)" />'
                '<text x="150" y="294" fill="var(--d-muted)" class="text-italic" '
                'style="text-anchor:middle;font-size:13px">x</text>')
        page = svg([], [(130, 281, 40, 17)], canvas=(0, 0, 400, 400),
                   shapes=CONTAINER + conn)
        self.assertNotIn('data-gap="title"', edgelabel.reposition(page))

    def test_a_title_gap_is_not_read_back_as_a_label_box(self):
        """It sits in the same mask as the label boxes and would otherwise match one by centre
        and baseline — moving that label to fit a box that is not its own."""
        once = edgelabel.reposition(
            svg([("M 150 200 L 150 380", 150, 294, "x")], [(130, 281, 40, 17)],
                canvas=(0, 0, 400, 400), shapes=CONTAINER))
        self.assertEqual(edgelabel.reposition(once), once)

    def test_a_title_keeps_edge_labels_off_it(self):
        """The second use of the same box: a label may not settle on a container's title."""
        box, = edgelabel.title_boxes(CONTAINER)
        out = edgelabel.reposition(
            svg([("M 150 12 L 150 380", 150, 32, "x")], [(130, 19, 40, 17)],
                canvas=(0, 0, 400, 400), shapes=CONTAINER))
        (_dx, dy), = moved(out)
        self.assertGreater(19 + dy, box[3] - 1, "the label must clear the title's band")


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


class TestAgainstTheCorpus(unittest.TestCase):
    """The two tests here that cost a browser, and the only ones that check the real thing.

    Everything above is geometry on hand-built SVGs. These render the corpus and ask a browser
    what it sees, because the defect this module exists for — a line drawn through a word — is
    invisible to every other check in the repo.
    """

    @classmethod
    def setUpClass(cls):
        from lib.diagram import browser, render
        if render.d2_version() is None or not browser.available():
            raise unittest.SkipTest("needs d2 and a browser")
        cls.browser, cls.render = browser, render
        from lib.diagram.examples import REFERENCE
        from lib.diagram.examples_repo import REPO
        cls.corpus = [(f"{group}/{name}", spec)
                      for group, specs in (("reference", REFERENCE), ("repo", REPO))
                      for name, spec in specs.items()]
        # Rendered ONCE for the whole class. Both tests below want the same ten drawings, and
        # producing them costs 45 seconds of d2 compiles and Chrome launches — the single
        # largest duplicated cost in the suite when each test made its own.
        cls.drawn = {key: render.render(spec, name=key.replace("/", "-"))
                     for key, spec in cls.corpus}

    def test_every_crossing_it_has_room_to_clear_is_cleared(self):
        """The whole point, end to end. `measure.js` follows every connection and reports where
        a painted stretch lands inside a text box that is not its own label; a stretch hidden by
        a gap is not painted, so a crossing survives only where no gap was cut.

        Not "no crossings at all", because that is not achievable from here: where an arrow
        terminates immediately below a container title there is no room to break the line
        without stranding the arrowhead, and the renderer correctly declines (`MIN_RUN`). What
        must hold is that every crossing left standing is one of those — anything else means a
        gap that should have been cut was not.
        """
        drawn = self.drawn
        jobs = [{"key": key,
                 "html": self.render.harness_html(svg, theme="light")}
                for key, svg in drawn.items()]
        for result in self.browser.measure(jobs):
            svg = drawn[result["key"]]
            declined = [box for box in edgelabel.title_boxes(svg)
                        if not arrows.leaves_a_stub(box, arrows.ends(svg))]
            for crossing in (result.get("crossed") or []):
                if crossing["depth"] < 2:
                    continue
                self.assertTrue(
                    declined,
                    f"{result['key']}: an arrow is drawn {crossing['depth']:.0f}px into "
                    f"{crossing['text'][:30]!r} and nothing declined a gap there")

    def test_the_derived_title_box_matches_what_the_browser_lays_out(self):
        """`TITLE_PAD` is the one number here that was measured rather than read out of the
        SVG, so a d2 upgrade could move it and quietly cut gaps that no longer fit their words.
        """
        for key, _spec in self.corpus:
            drawn = self.drawn[key]
            titles = [m for m in edgelabel._TITLE.finditer(drawn)
                      if edgelabel._GRP.search(m.group(0))]
            if not titles:
                continue
            tagged = re.sub(r"<text ", '<text data-w="1" ', drawn)
            measured = dict(zip(
                re.findall(r"<text [^>]*>(?:<tspan[^>]*>)?([^<]*)", drawn),
                self.browser.text_widths(
                    self.render.harness_html(tagged, theme="light", standalone=True))))
            for match, box in zip(titles, edgelabel.title_boxes(drawn)):
                start = drawn.index(">", match.end()) + 1
                text = drawn[start:drawn.index("<", start)]
                self.assertAlmostEqual(
                    box.w, measured[text], delta=2.5,
                    msg=f"{text!r}: derived {box.w:.1f}px, browser {measured[text]:.1f}px — "
                        f"edgelabel.TITLE_PAD needs re-measuring")


if __name__ == "__main__":
    unittest.main(verbosity=2)
