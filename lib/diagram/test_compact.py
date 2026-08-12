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

from lib.diagram import compact, render
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

    def test_the_arrowhead_marker_is_untouched(self):
        marker = compact.MARKER.search(self.raw)
        self.assertIsNotNone(marker, "d2 no longer inlines a marker — check _shift")
        self.assertIn(marker.group(0), self.out)

    def test_the_result_is_well_formed_xml(self):
        ET.fromstring(self.out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
