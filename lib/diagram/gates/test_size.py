#!/usr/bin/env python3
"""Tests for the size gates.

The refusal cases carry the most weight. A size gate that cannot find an intrinsic size,
or cannot find any text, has to raise — because the plausible fallbacks (assume
shrink-only, assume no text is fine) are precisely how the prototype's first version
reported a diagram rendering at 1.34x as passing.

Run: `python3 lib/diagram/gates/test_size.py`
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))))

from lib.diagram.gates import GateError
from lib.diagram.gates import size


def svg(width, height, fonts=(13,), extra=""):
    text = "".join(f'<text font-size="{f}">x</text>' for f in fonts)
    return (f'<svg width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}"{extra}>{text}</svg>')


class TestRefusesToGuess(unittest.TestCase):
    def test_no_intrinsic_size_raises(self):
        """The browser may ENLARGE such an SVG, so shrink-only maths would be a lie."""
        with self.assertRaisesRegex(GateError, "no intrinsic width/height"):
            size.analyse('<svg viewBox="0 0 400 300"><text font-size="13">x</text></svg>')

    def test_the_error_mentions_the_fix(self):
        with self.assertRaisesRegex(GateError, "pin_intrinsic"):
            size.analyse('<svg viewBox="0 0 400 300"><text font-size="13">x</text></svg>')

    def test_a_percentage_width_counts_as_no_size(self):
        with self.assertRaises(GateError):
            size.analyse('<svg width="100%" viewBox="0 0 400 300">'
                         '<text font-size="13">x</text></svg>')

    def test_no_svg_raises(self):
        with self.assertRaisesRegex(GateError, "no <svg>"):
            size.analyse("<html></html>")

    def test_no_font_declarations_raises(self):
        """Otherwise all three glyph gates pass vacuously."""
        with self.assertRaisesRegex(GateError, "no font-size"):
            size.analyse('<svg width="10" height="10" viewBox="0 0 10 10"><g/></svg>')

    def test_a_degenerate_size_raises(self):
        with self.assertRaisesRegex(GateError, "degenerate"):
            size.analyse('<svg width="0" height="0" viewBox="0 0 0 0">'
                         '<text font-size="13">x</text></svg>')


class TestScale(unittest.TestCase):
    def test_a_narrow_diagram_renders_at_natural_size(self):
        self.assertEqual(size.analyse(svg(400, 300))["scale"], 1.0)

    def test_an_over_wide_diagram_is_scaled_down(self):
        m = size.analyse(svg(1554, 400))
        self.assertAlmostEqual(m["scale"], 0.5, places=2)
        self.assertAlmostEqual(m["rend_h"], 200, delta=1)

    def test_scale_never_exceeds_one(self):
        """Pinning the intrinsic size is what makes max-width a cap rather than a stretch."""
        self.assertEqual(size.analyse(svg(100, 100))["scale"], 1.0)

    def test_glyphs_shrink_with_the_diagram(self):
        m = size.analyse(svg(1554, 400, fonts=(20,)))
        self.assertAlmostEqual(m["fmax"], 10.0, places=1)

    def test_point_units_are_converted_to_css_pixels(self):
        """d2 emits user units, but the graphviz path this replaces emitted points, so a
        mis-measurement during that migration would be silent."""
        m = size.analyse('<svg width="75pt" height="150pt" viewBox="0 0 75 150">'
                         '<text font-size="9">x</text></svg>')
        self.assertAlmostEqual(m["nat_w"], 100.0)
        self.assertAlmostEqual(m["nat_h"], 200.0)
        self.assertAlmostEqual(m["fmax"], 12.0)   # 9pt is legible, 9px would not be


class TestGates(unittest.TestCase):
    def test_a_well_behaved_diagram_passes(self):
        self.assertTrue(size.check(svg(400, 300)).ok)

    def test_a_too_tall_diagram_fails(self):
        result = size.check(svg(400, 900))
        self.assertFalse(result.ok)
        self.assertIn("TALL", result.problems[0])

    def test_the_height_failure_advises_splitting_rather_than_shrinking(self):
        """d2 cannot compact a diagram after the fact, so shrinking is not an option."""
        self.assertIn("split it", size.check(svg(400, 900)).problems[0])

    def test_a_glyph_larger_than_an_h2_fails(self):
        result = size.check(svg(400, 300, fonts=(30,)))
        self.assertFalse(result.ok)
        self.assertTrue(any("GLYPH" in p for p in result.problems))

    def test_a_modal_glyph_larger_than_body_text_fails(self):
        """The size MOST of the diagram is set in, not just its largest outlier."""
        result = size.check(svg(400, 300, fonts=(21, 21, 21, 13)))
        self.assertTrue(any("BODY" in p for p in result.problems))

    def test_one_large_glyph_among_small_ones_does_not_trip_the_body_gate(self):
        result = size.check(svg(400, 300, fonts=(13, 13, 13, 22)))
        self.assertFalse(any("BODY" in p for p in result.problems))

    def test_text_below_eleven_pixels_fails(self):
        result = size.check(svg(400, 300, fonts=(9,)))
        self.assertTrue(any("TINY" in p for p in result.problems))

    def test_downscaling_can_push_legible_text_under_the_floor(self):
        """This is the loophole the four gates close together: fitting by shrinking."""
        result = size.check(svg(1554, 400, fonts=(13,)))
        self.assertTrue(any("TINY" in p for p in result.problems),
                        f"expected a legibility failure, got {result.problems}")

    def test_a_downscaled_failure_says_how_much_narrower_it_has_to_be(self):
        """"TINY" on its own does not say whether to drop a box, shorten a label or split the
        diagram — and at half the column width, 13px text renders at 6.5px, so the width that
        puts it back on the floor is half of what it is."""
        tiny = next(p for p in size.check(svg(1554, 400, fonts=(13,))).problems if "TINY" in p)
        self.assertIn("1554px wide", tiny)
        # 1554 * 6.5/11 — which is also 777 * 13/11, the widest a 13px-text diagram can be.
        self.assertIn("~918px", tiny)
        self.assertIn("41% less width", tiny)

    def test_authored_tiny_text_is_not_told_to_get_narrower(self):
        """At scale 1.0 the width is not what makes it small, so the advice would be wrong."""
        tiny = next(p for p in size.check(svg(400, 300, fonts=(9,))).problems if "TINY" in p)
        self.assertNotIn("less width", tiny)


def with_subtitle(width, height, subtitle_px=11, primary_px=13):
    """An SVG whose only small text is a tagged subtitle span, as compact.py emits it."""
    return (f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
            f'<text font-size="{primary_px}">label</text>'
            f'<text><tspan class="d2-detail" style="font-size:{subtitle_px}px">sub</tspan>'
            "</text></svg>")


def with_edge_label(width, height, edge_px=13, primary_px=14):
    """An SVG whose edge label is smaller than its box text, as `er` and `class` are."""
    return (f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
            f'<text class="text" style="font-size:{primary_px}px">table row</text>'
            f'<text class="text-italic" style="font-size:{edge_px}px">1 doc : n</text></svg>')


class TestEdgeLabelFloor(unittest.TestCase):
    """An edge label qualifies a relationship the arrow already draws, so it is annotation and
    has its own floor. It matters because d2 sets it at 13px against a table's 14px rows, which
    makes it the smallest text in an `er` diagram and therefore the thing deciding the layout."""

    def test_an_edge_label_below_the_primary_floor_is_fine(self):
        self.assertEqual(size.check(with_edge_label(935, 300)).problems, [])

    def test_an_edge_label_below_its_own_floor_fails(self):
        result = size.check(with_edge_label(1100, 300))       # 13 * 0.71 = 9.2px
        self.assertTrue(any("edge label" in p for p in result.problems), result.problems)

    def test_it_is_not_counted_as_the_modal_glyph(self):
        m = size.analyse(with_edge_label(400, 300))
        self.assertEqual(m["fmodal"], 14.0)
        self.assertEqual(m["fmin_edge"], 13.0)


class TestSubtitleFloor(unittest.TestCase):
    """A subtitle is supplementary text and has its own, lower floor. Measured with the
    primary text it was the most expensive thing in the diagram: authored AT the floor, it
    could not survive any downscale, so a figure carrying one had to fit the column exactly."""

    def test_a_subtitle_under_the_primary_floor_is_fine(self):
        result = size.check(with_subtitle(848, 300))       # scale 0.92 -> subtitle 10.1px
        self.assertEqual(result.problems, [])

    def test_a_subtitle_under_its_own_floor_fails(self):
        result = size.check(with_subtitle(1160, 300))      # scale 0.67 -> subtitle 7.4px
        self.assertTrue(any("subtitle" in p for p in result.problems), result.problems)

    def test_the_primary_floor_still_binds_first(self):
        """9px is chosen so the subtitle never fails before the label does: 13px primary text
        hits 11px at 918px of width, where an 11px subtitle is still 9.3px."""
        result = size.check(with_subtitle(930, 300))
        self.assertTrue(any(p.startswith("TINY") and "subtitle" not in p
                            for p in result.problems), result.problems)
        self.assertFalse(any("subtitle" in p for p in result.problems), result.problems)

    def test_the_subtitle_is_not_counted_as_the_modal_glyph(self):
        m = size.analyse(with_subtitle(400, 300))
        self.assertEqual(m["fmodal"], 13.0)
        self.assertEqual(m["fmin"], 13.0)
        self.assertEqual(m["fmin_detail"], 11.0)

    def test_a_diagram_without_subtitles_reports_none(self):
        self.assertIsNone(size.analyse(svg(400, 300))["fmin_detail"])

    def test_css_declared_font_sizes_are_counted_too(self):
        one = ('<svg width="400" height="300" viewBox="0 0 400 300">'
               '<style>.a{font-size:30px}</style><text class="a">x</text></svg>')
        self.assertTrue(any("GLYPH" in p for p in size.check(one).problems))

    def test_the_detail_line_reports_what_was_measured(self):
        detail = size.check(svg(400, 300)).detail
        self.assertIn("400x300", detail)
        self.assertIn("modal", detail)

    def test_the_result_carries_the_diagram_name(self):
        self.assertEqual(size.check(svg(400, 300), "arch").name, "arch")


class TestThresholdsMatchThePageGeometry(unittest.TestCase):
    def test_the_thresholds_are_the_measured_page_values(self):
        self.assertEqual(size.AVAIL_W, 777.0)     # 880 - 57 - 45.6, rounded
        self.assertEqual(size.H2_PX, 26.6)        # 1.4rem at a 19px root
        self.assertEqual(size.BODY_PX, 19.0)      # 1rem at a 19px root
        self.assertEqual(size.MIN_READABLE, 11.0)

    def test_an_h2_is_larger_than_body_text(self):
        self.assertGreater(size.H2_PX, size.BODY_PX)


if __name__ == "__main__":
    unittest.main(verbosity=2)
