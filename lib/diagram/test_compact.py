#!/usr/bin/env python3
"""Tests for the sequence-diagram row compaction.

Most of these run on a hand-written SVG that reproduces the parts of d2's output the
compaction depends on — the shared `userSpaceOnUse` mask, one label mask rect per row, a
self-message whose path spans more than its baseline. The last class renders through real d2
so the fixture cannot drift away from what d2 actually emits without something failing.

The two cases worth keeping honest are `test_no_group_gains_a_transform` (a translate is
what broke this first: the mask window travels with it and arrows silently vanish) and the
`CompactError` cases (an unrecognised structure must refuse to move anything, because a
half-moved diagram passes every gate while being wrong).

Run: `python3 lib/diagram/test_compact.py`
"""
import os
import re
import sys
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.diagram import compact, palette, render
from lib.diagram.examples import REFERENCE


def fixture(rows=(200, 286), lifeline_end=400, canvas=500, self_row=None):
    """A minimal stand-in for d2's sequence output: two actors, two lifelines, N rows."""
    head = (f'<svg viewBox="0 0 300 {canvas}">'
            f'<svg class="d2-1 d2-svg" width="300" height="{canvas}" '
            f'viewBox="0 0 300 {canvas}">'
            f'<rect x="0" y="0" width="300" height="{canvas}" class="fill-N7"/>')
    actors = ""
    for x in (20, 200):
        actors += (f'<g class="a{x}"><g class="shape">'
                   f'<rect x="{x}" y="10" width="80" height="34" class="fill-B5"/>'
                   f'<text x="{x + 40}" y="31" class="text">actor</text></g></g>')
    lines = ""
    for x in (60, 240):
        lines += (f'<g class="l{x}"><path d="M {x}.000000 44.000000 L {x}.000000 '
                  f'{lifeline_end}.000000" class="connection" '
                  f'style="stroke-width:2;stroke-dasharray:12.000000,11.838767;"/></g>')
    body, masks = "", ""
    for i, y in enumerate(rows):
        loop = ""
        if self_row == i:
            loop = f' S 280.000000 {y}.000000 280.000000 {y + 40}.000000'
        body += (f'<g class="m{i}"><path d="M 62.000000 {y}.000000 L 238.000000 '
                 f'{y}.000000{loop}" class="connection" mask="url(#d2-1)"/>'
                 f'<text x="150.000000" y="{y + 5}.000000" class="text-italic" '
                 f'style="text-anchor:middle;font-size:13px">msg {i}</text></g>')
        masks += f'<rect x="100.000000" y="{y - 8}.000000" width="60" height="17" fill="black"/>'
    mask = (f'<mask id="d2-1" maskUnits="userSpaceOnUse" x="0" y="0" width="300" '
            f'height="{canvas}"><rect x="0" y="0" width="300" height="{canvas}" '
            f'fill="white"/>{masks}</mask>')
    return head + actors + lines + body + mask + "</svg></svg>"


def canvas_height(svg):
    return float(re.search(r'viewBox="[-\d.]+ [-\d.]+ [\d.]+ ([\d.]+)"', svg).group(1))


def content_bottom(svg):
    """The lowest y that is still on the canvas, in the coordinates the drawing uses.

    d2's *inner* `<svg>` carries the offset: the root viewBox starts at 0 while the drawing
    itself starts at the pad offset, so a bare `y <= height` comparison is off by the pad.
    """
    inner = svg.find("<svg", svg.find("<svg") + 1)
    box = re.search(r'viewBox="[-\d.]+ ([-\d.]+) [\d.]+ ([\d.]+)"', svg[inner:])
    return float(box.group(1)) + float(box.group(2))


def row_ys(svg):
    """The y of every horizontal message path, in document order."""
    out = []
    for m in re.finditer(r'<path[^>]*\sd="M ([\d.]+) ([\d.]+) L ([\d.]+) ', svg):
        if m.group(1) != m.group(3):                  # skip the vertical lifelines
            out.append(float(m.group(2)))
    return out


def lifeline_ends(svg):
    out = set()
    for m in re.finditer(r'<path d="M ([\d.]+) [\d.]+ L ([\d.]+) ([\d.]+)"', svg):
        if m.group(1) == m.group(2):
            out.add(float(m.group(3)))
    return out


class TestCompaction(unittest.TestCase):
    def test_rows_are_restacked_below_the_actor_boxes(self):
        out = compact.compact_sequence(fixture())
        self.assertEqual(row_ys(out), [44 + compact.HEAD,
                                       44 + compact.HEAD + compact.GAP])

    def test_the_pitch_collapses_to_the_configured_gap(self):
        out = compact.compact_sequence(fixture(rows=(200, 286, 372), lifeline_end=500,
                                               canvas=600))
        rows = row_ys(out)
        self.assertEqual({b - a for a, b in zip(rows, rows[1:])}, {float(compact.GAP)})

    def test_no_group_gains_a_transform(self):
        """A translate would move the mask window with the row; see `_shift`."""
        self.assertNotIn("transform", compact.compact_sequence(fixture()))

    def test_a_label_mask_moves_with_its_row(self):
        out = compact.compact_sequence(fixture())
        masked = [float(y) for y in re.findall(r'<rect x="100.000000" y="([\d.]+)"', out)]
        self.assertEqual(masked, [y - 8 for y in row_ys(out)])

    def test_the_label_keeps_its_offset_from_the_line(self):
        out = compact.compact_sequence(fixture())
        for row, text in zip(row_ys(out),
                             re.findall(r'<text x="150.000000" y="([\d.]+)"', out)):
            self.assertAlmostEqual(float(text) - row, 5, places=3)

    def test_a_self_message_keeps_its_whole_loop_and_the_next_row_clears_it(self):
        out = compact.compact_sequence(fixture(rows=(200, 300), self_row=0,
                                               lifeline_end=420, canvas=520))
        first, second = row_ys(out)
        self.assertIn(f' S 280.000000 {first:.6f} 280.000000 {first + 40:.6f}', out)
        self.assertEqual(second - (first + 40), float(compact.GAP))

    def test_the_lifelines_end_just_below_the_last_row(self):
        out = compact.compact_sequence(fixture())
        self.assertEqual(lifeline_ends(out), {row_ys(out)[-1] + compact.TAIL})

    def test_the_dashes_are_resized_to_a_whole_number_per_lane(self):
        """A lane a third as long keeps d2's 12px dashes unless they are recomputed, and
        then it reads as a row of strokes rather than a dashed line."""
        out = compact.compact_sequence(fixture())
        patterns = set(re.findall(r"stroke-dasharray:([\d.]+),([\d.]+)", out))
        self.assertEqual(len(patterns), 1)
        dash, gap = (float(n) for n in patterns.pop())
        self.assertEqual(dash, compact.DASH)
        lane = lifeline_ends(out).pop() - 44
        periods = lane / (dash + gap)
        self.assertAlmostEqual(periods, round(periods), places=6)

    def test_every_element_stating_the_canvas_height_shrinks_together(self):
        """Four `height` attributes (inner svg, background, mask, mask rect) and two
        viewBoxes. Miss one and the drawing either keeps the whitespace or clips itself."""
        out = compact.compact_sequence(fixture())
        height = canvas_height(out)
        self.assertLess(height, 500)
        self.assertEqual(len(re.findall(rf'height="{height:g}"', out)), 4)
        self.assertEqual(len(re.findall(rf'viewBox="0 0 300 {height:g}"', out)), 2)

    def test_the_result_is_well_formed_xml(self):
        ET.fromstring(compact.compact_sequence(fixture()))

    def test_compacting_twice_changes_nothing(self):
        once = compact.compact_sequence(fixture())
        self.assertEqual(compact.compact_sequence(once), once)

    def test_a_diagram_with_no_messages_is_returned_unchanged(self):
        svg = fixture(rows=())
        self.assertEqual(compact.compact_sequence(svg), svg)

    def test_a_callout_is_left_where_d2_put_it(self):
        """It is anchored to a participant, not to the canvas."""
        svg = fixture().replace(
            '<g class="l60">',
            '<g class="callout"><rect x="120" y="60" width="90" height="30"/>'
            '<foreignObject x="120" y="60" width="90" height="30">'
            '<div class="md"><p>new here</p></div></foreignObject></g><g class="l60">')
        out = compact.compact_sequence(svg)
        self.assertIn('<foreignObject x="120" y="60" width="90" height="30">', out)


class TestDetailLines(unittest.TestCase):
    """d2 emits a two-line label as one <text> of two <tspan>s and cannot style them apart."""

    TWO_LINE = ('<text x="90" y="60" class="text" style="font-size:13px">'
                '<tspan x="90" dy="0.000000">FE routing</tspan>'
                '<tspan x="90" dy="15.000000">AppRoutes / react-router</tspan></text>')
    ONE_LINE = ('<text x="20" y="60" class="text" style="font-size:13px">'
                '<tspan x="20" dy="0.000000">User</tspan></text>')

    def test_the_second_line_shrinks_and_mutes(self):
        out = compact.style_detail_lines(self.TWO_LINE)
        second = out[out.index("AppRoutes") - 120:out.index("AppRoutes")]
        self.assertIn(f"font-size:{compact.DETAIL_FONT}px", second)
        self.assertIn(palette.MUTED, second)

    def test_the_first_line_is_untouched(self):
        out = compact.style_detail_lines(self.TWO_LINE)
        first = out[:out.index("FE routing")]
        self.assertNotIn(f"font-size:{compact.DETAIL_FONT}px", first)
        self.assertNotIn(palette.MUTED, first)

    def test_the_baseline_gap_tightens(self):
        self.assertIn(f'dy="{compact.DETAIL_DY}.000000"',
                      compact.style_detail_lines(self.TWO_LINE))

    def test_a_single_line_label_is_left_alone(self):
        self.assertEqual(compact.style_detail_lines(self.ONE_LINE), self.ONE_LINE)

    def test_a_wrapped_edge_label_keeps_its_size(self):
        """It is one phrase that wrapped, not a subtitle — and shrinking it to the legibility
        floor would cost the whole diagram its downscale headroom, which is exactly what
        wrapping a long edge label is meant to buy."""
        wrapped = ('<text x="90" y="60" class="text-italic" style="font-size:13px">'
                   '<tspan x="90" dy="0.000000">plain</tspan>'
                   '<tspan x="90" dy="15.000000">filesystem</tspan></text>')
        self.assertEqual(compact.style_detail_lines(wrapped), wrapped)

    def test_it_stays_well_formed(self):
        ET.fromstring(compact.style_detail_lines(self.TWO_LINE))


class TestGroupLegend(unittest.TestCase):
    """Lane colour says which side of the wire a lane is on, and nothing said what the colours
    meant — the first reader of a real figure guessed "probably FE/BE"."""

    # One lane of `browser`, then two of `server` — the smallest arrangement in which a group
    # is actually grouping something. Two groups of one lane each draws nothing; see below.
    LANES = [("browser", "#3b6fd4", "#27548f"),
             ("server", "#7c4dbd", "#6a3fa8"), ("server", "#7c4dbd", "#6a3fa8")]

    def svg(self, lanes=3):
        boxes = "".join(
            f'<g><rect x="{i * 200}" y="43" width="120" height="48" class="shape"/></g>'
            for i in range(lanes))
        return ('<?xml version="1.0"?>'
                '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 626 339">'
                '<svg class="d2-1" width="626" height="339" viewBox="3 43 626 339">'
                f'<rect x="3" y="43" width="626" height="339" fill="transparent"/>'
                f"{boxes}</svg></svg>")

    def test_two_groups_get_a_rule_and_a_name_each(self):
        out = compact.add_group_legend(self.svg(), self.LANES)
        self.assertIn(">browser</text>", out)
        self.assertIn(">server</text>", out)
        self.assertEqual(out.count(f'fill-opacity="{compact.LEGEND_OPACITY}"'), 2)

    def test_the_rule_takes_the_border_colour_and_the_name_the_text_colour(self):
        """A border colour painting a name is what a two-group figure never caught: the third
        group is green, and #3f9142 is 3.76:1 on white."""
        out = compact.add_group_legend(self.svg(), self.LANES)
        self.assertIn('fill="#3b6fd4" fill-opacity=', out)      # rule: the lane's border
        self.assertIn('fill="#27548f" style="text-anchor', out)  # name: text-grade

    def test_the_rule_has_rounded_ends(self):
        out = compact.add_group_legend(self.svg(), self.LANES)
        self.assertIn(f'rx="{compact.LEGEND_RULE / 2:g}"', out)

    def test_one_group_explains_nothing_and_is_left_alone(self):
        svg = self.svg()
        self.assertEqual(compact.add_group_legend(svg, [self.LANES[1]] * 3), svg)

    def test_a_group_of_one_lane_is_not_grouping_and_draws_nothing(self):
        """Three lanes reading Reviewer / Backend / Postgres do not need to be told they are
        browser / server / db: the rule would be an underline under a single box, and the name
        a second name for something already named."""
        svg = self.svg()
        one_each = [("a", "#111111", "#111111"), ("b", "#222222", "#222222"),
                    ("c", "#333333", "#333333")]
        self.assertEqual(compact.add_group_legend(svg, one_each), svg)

    def test_ungrouped_lanes_are_left_alone(self):
        svg = self.svg()
        self.assertEqual(compact.add_group_legend(svg, [(None, None, None)] * 3), svg)

    def test_the_canvas_grows_so_the_band_is_not_cropped(self):
        out = compact.add_group_legend(self.svg(), self.LANES)
        self.assertIn(f'viewBox="0 0 626 {339 + compact.LEGEND_BAND}"', out)
        self.assertIn(f'viewBox="3 43 626 {339 + compact.LEGEND_BAND}"', out)
        self.assertIn(f'height="{339 + compact.LEGEND_BAND}"', out)

    def test_the_backdrop_grows_too_so_a_standalone_image_paints_the_band(self):
        """render.standalone fills that rect with the page colour; left un-grown, the legend
        sits on an unpainted strip."""
        out = compact.add_group_legend(self.svg(), self.LANES)
        self.assertIn(f'height="{339 + compact.LEGEND_BAND:.6f}" fill="transparent"', out)

    def test_a_split_group_is_drawn_as_two_spans(self):
        """Honest: the rule marks where those lanes actually are. spec.py already advises
        against splitting one."""
        a, b = ("a", "#111111", "#111111"), ("b", "#222222", "#222222")
        out = compact.add_group_legend(self.svg(4), [a, a, b, a])
        self.assertEqual(out.count(">a</text>"), 2)

    def test_a_group_name_is_escaped(self):
        out = compact.add_group_legend(self.svg(), [("a<b", "#111111", "#111111"),
                                                    ("c&d", "#222222", "#222222"),
                                                    ("c&d", "#222222", "#222222")])
        self.assertIn(">a&lt;b</text>", out)
        self.assertIn(">c&amp;d</text>", out)

    def test_more_lanes_than_actor_boxes_raises(self):
        with self.assertRaises(compact.CompactError):
            compact.add_group_legend(self.svg(1), self.LANES)

    def test_a_missing_viewbox_raises_rather_than_cropping_the_band(self):
        svg = self.svg().replace(' viewBox="3 43 626 339"', "")
        with self.assertRaises(compact.CompactError):
            compact.add_group_legend(svg, self.LANES)

    def test_the_pad_matches_the_one_the_renderer_asks_d2_for(self):
        """Both annotations are placed relative to the canvas edge, so a change to d2's --pad
        without a change here would leave them floating."""
        import inspect

        from lib.diagram import render
        self.assertEqual(compact.D2_PAD,
                         inspect.signature(render.compile_source).parameters["pad"].default)


class TestStartMarker(unittest.TestCase):
    """A reader could not tell where the machine begins: being the top node is implicit, and a
    state with no incoming transition looks like every other state."""

    GREY = "#6b6b6b"

    def svg(self, boxes=((200, 100, 109, 62),), ids=("connecting",)):
        """A d2-shaped state diagram. d2 tags each node's group with base64 of its id and
        offers no other handle on which box is which."""
        import base64

        body = "".join(
            f'<g class="{base64.b64encode(i.encode()).decode()} client">'
            f'<g class="shape"><rect x="{b[0]}" y="{b[1]}" width="{b[2]}" '
            f'height="{b[3]}"/></g></g>'
            for i, b in zip(ids, boxes))
        return ('<?xml version="1.0"?>'
                '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 500 400">'
                '<svg class="d2-1" width="500" height="400" viewBox="-8 -9 500 400">'
                '<rect x="-8" y="-9" width="500" height="400" fill="transparent"/>'
                f"{body}</svg></svg>")

    def test_the_dot_lands_left_of_the_box_at_its_middle(self):
        out = compact.add_start_marker(self.svg(), "connecting", self.GREY)
        cx = 200 - compact.START_ARROW - compact.START_R
        self.assertIn(f'<circle cx="{cx:.1f}" cy="131.0" r="{compact.START_R}"', out)

    def test_a_margin_wide_enough_costs_no_canvas_at_all(self):
        """The whole reason it goes beside the box rather than above: a rank of canvas is
        114px on the reference machine, and the margin beside the top box is already there."""
        out = compact.add_start_marker(self.svg(boxes=((200, 100, 109, 62),)), "connecting",
                                       self.GREY)
        self.assertIn('viewBox="0 0 500 400"', out)
        self.assertIn('viewBox="-8 -9 500 400"', out)

    def test_a_box_flush_with_the_canvas_edge_grows_it_by_the_deficit(self):
        out = compact.add_start_marker(self.svg(boxes=((0, 100, 109, 62),)), "connecting",
                                       self.GREY)
        grew = compact.START_ARROW + 2 * compact.START_R + compact.D2_PAD - 8
        self.assertIn(f'viewBox="{-grew:g} 0 {500 + grew:g} 400"', out)
        self.assertIn(f'viewBox="{-8 - grew:g} -9 {500 + grew:g} 400"', out)

    def test_a_box_on_the_left_pushes_the_marker_to_the_right(self):
        out = compact.add_start_marker(
            self.svg(boxes=((200, 100, 109, 62), (100, 100, 109, 62)),
                     ids=("connecting", "other")), "connecting", self.GREY)
        cx = 200 + 109 + compact.START_ARROW + compact.START_R
        self.assertIn(f'<circle cx="{cx:.1f}"', out)

    def test_boxes_on_both_sides_raise_rather_than_draw_over_one(self):
        with self.assertRaises(compact.CompactError):
            compact.add_start_marker(
                self.svg(boxes=((200, 100, 109, 62), (100, 100, 109, 62),
                                (320, 100, 109, 62)),
                         ids=("connecting", "left", "right")), "connecting", self.GREY)

    def test_a_box_beside_but_not_level_is_not_in_the_way(self):
        """The strip only matters at the dot's own height — a box two ranks down and to the
        left shares an x range with the marker and nothing else."""
        out = compact.add_start_marker(
            self.svg(boxes=((200, 100, 109, 62), (100, 300, 109, 62)),
                     ids=("connecting", "other")), "connecting", self.GREY)
        self.assertIn(f'<circle cx="{200 - compact.START_ARROW - compact.START_R:.1f}"', out)

    def test_vertical_puts_the_dot_above_the_box_centred_on_it(self):
        """Laid out to the right, every px of width is scaled away in the content column, so
        the marker spends height instead."""
        out = compact.add_start_marker(self.svg(boxes=((200, 100, 109, 62),)), "connecting",
                                       self.GREY, vertical=True)
        cy = 100 - compact.START_ARROW_ABOVE - compact.START_R
        self.assertIn(f'<circle cx="254.5" cy="{cy:.1f}" r="{compact.START_R}"', out)

    def test_vertical_grows_the_top_of_the_canvas_not_its_width(self):
        """A box flush with the top of the drawing, which is where a `right` layout puts the
        first state. Width is untouched — that is the whole point of going this way."""
        out = compact.add_start_marker(self.svg(boxes=((200, 0, 109, 62),)), "connecting",
                                       self.GREY, vertical=True)
        grew = compact.START_ARROW_ABOVE + 2 * compact.START_R + compact.D2_PAD - 9
        self.assertIn(f'viewBox="0 {-grew:g} 500 {400 + grew:g}"', out)
        self.assertIn(f'viewBox="-8 {-9 - grew:g} 500 {400 + grew:g}"', out)

    def test_vertical_falls_back_below_when_a_box_sits_above(self):
        out = compact.add_start_marker(
            self.svg(boxes=((200, 100, 109, 62), (200, 0, 109, 62)),
                     ids=("connecting", "other")), "connecting", self.GREY, vertical=True)
        cy = 162 + compact.START_ARROW_ABOVE + compact.START_R
        self.assertIn(f'<circle cx="254.5" cy="{cy:.1f}"', out)

    def test_an_unknown_state_raises(self):
        with self.assertRaises(compact.CompactError):
            compact.add_start_marker(self.svg(), "nosuchstate", self.GREY)


class TestRefusals(unittest.TestCase):
    def test_an_unbalanced_group_raises(self):
        with self.assertRaises(compact.CompactError):
            compact.compact_sequence(fixture().replace('<mask id', '<g class="stray">'
                                                       '<mask id'))

    def test_an_unknown_element_in_a_row_raises(self):
        svg = fixture().replace('class="text-italic"', 'class="text-italic"/><circle cx="1"')
        with self.assertRaises(compact.CompactError):
            compact.compact_sequence(svg)

    def test_a_label_with_no_matching_mask_rect_raises(self):
        """Moving the row would leave its label background behind."""
        svg = fixture().replace('<rect x="100.000000" y="192.000000"',
                                '<rect x="100.000000" y="777.000000"')
        with self.assertRaises(compact.CompactError):
            compact.compact_sequence(svg)

    def test_lifelines_ending_at_different_depths_raise(self):
        svg = fixture().replace("L 240.000000 400.000000", "L 240.000000 390.000000")
        with self.assertRaises(compact.CompactError):
            compact.compact_sequence(svg)

    def test_a_label_with_no_font_size_raises(self):
        """The mask offset is derived from it, so a guess would move the wrong rect."""
        svg = fixture().replace('style="text-anchor:middle;font-size:13px"', "")
        with self.assertRaises(compact.CompactError):
            compact.compact_sequence(svg)

    def test_odd_coordinate_counts_raise(self):
        svg = fixture().replace("M 62.000000 200.000000 L 238.000000 200.000000",
                                "M 62.000000 200.000000 L 238.000000")
        with self.assertRaises(compact.CompactError):
            compact.compact_sequence(svg)


@unittest.skipUnless(render.d2_version() is not None, "d2 is not installed")
class TestAgainstRealD2(unittest.TestCase):
    """The fixture above is a claim about d2's output. These check the claim."""

    @classmethod
    def setUpClass(cls):
        from lib.diagram import d2, palette
        source = d2.emit(REFERENCE["sequence"], background=palette.CANVAS)
        cls.raw = render.compile_source(source, pad=render.STANDALONE_PAD)
        cls.out = compact.compact_sequence(cls.raw)

    def test_it_gets_shorter_by_the_pitch_it_removes(self):
        """A ratio here would quietly track whatever GAP is tuned to today. The claim that
        stays true is the arithmetic: one pitch saved per gap between rows, d2's 86px
        against ours. The exact height is pinned in test_reference.MEASURED."""
        pitches = len(REFERENCE["sequence"]["messages"]) - 1
        saved = canvas_height(self.raw) - canvas_height(self.out)
        self.assertGreaterEqual(saved, pitches * (86 - (compact.GAP + 17)))

    def test_no_ink_is_left_below_the_canvas(self):
        limit = content_bottom(self.out)
        for d in re.findall(r'\sd="([^"]*)"', self.out):
            for y in [float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", d)][1::2]:
                self.assertLessEqual(y, limit, f"path ink at y={y} below a {limit}px canvas")

    def test_every_message_survives(self):
        for svg in (self.raw, self.out):
            self.assertEqual(len(re.findall(r'class="connection[^"]*"', svg)),
                             len(re.findall(r'class="connection[^"]*"', self.raw)))

    def test_d2_still_dashes_its_lifelines_and_the_dashes_get_resized(self):
        self.assertIn("stroke-dasharray:12", self.raw)
        self.assertIn(f"stroke-dasharray:{compact.DASH:.6f}", self.out)
        self.assertNotIn("stroke-dasharray:12", self.out)

    def test_d2_really_emits_a_two_line_label_as_two_tspans(self):
        """`style_detail_lines` rests entirely on this, and the fixture above only asserts my
        hand-written idea of d2's output. Deriving d2's shape by hand is exactly what produced
        the mask bug, so the claim is checked against the real thing here.
        """
        from lib.diagram import d2, palette
        spec = {"kind": "sequence",
                "participants": [{"id": "user", "group": "browser"},
                                 {"id": "routing", "label": "FE routing", "group": "browser",
                                  "detail": "AppRoutes / react-router"}],
                "messages": [{"from": "user", "to": "routing", "label": "navigate()"}]}
        raw = render.compile_source(d2.emit(spec, background=palette.CANVAS),
                                    pad=render.STANDALONE_PAD)
        holder = next(m.group(0) for m in re.finditer(r"<text\b[^>]*>.*?</text>", raw, re.S)
                      if "AppRoutes" in m.group(0))
        self.assertEqual(len(re.findall(r"<tspan\b", holder)), 2, holder[:200])

        out = compact.style_detail_lines(raw)
        styled = next(m.group(0) for m in re.finditer(r"<text\b[^>]*>.*?</text>", out, re.S)
                      if "AppRoutes" in m.group(0))
        first, second = re.findall(r"<tspan\b[^>]*>.*?</tspan>", styled, re.S)
        self.assertIn("FE routing", first)
        self.assertNotIn(f"font-size:{compact.DETAIL_FONT}px", first)
        self.assertIn(f"font-size:{compact.DETAIL_FONT}px", second)
        self.assertIn(palette.MUTED, second)
        self.assertIn(f'dy="{compact.DETAIL_DY}.000000"', second)

    def test_the_arrowhead_marker_is_untouched(self):
        marker = compact.MARKER.search(self.raw)
        self.assertIsNotNone(marker, "d2 no longer inlines a marker — check _shift")
        self.assertIn(marker.group(0), self.out)

    def test_the_result_is_well_formed_xml(self):
        ET.fromstring(self.out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
