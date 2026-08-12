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
