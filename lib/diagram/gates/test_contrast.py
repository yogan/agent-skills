#!/usr/bin/env python3
"""Tests for the contrast gate.

Three things get the most attention: the WCAG maths itself (against values from the spec),
the mask exclusion (d2's full-canvas white mask rect would otherwise be picked as the
backdrop for every glyph and report a confident bogus pass), and the refusal to report
success with no text to measure.

Run: `python3 lib/diagram/gates/test_contrast.py`
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))))

from lib.diagram.gates import GateError
from lib.diagram.gates import contrast


class TestWcagMaths(unittest.TestCase):
    def test_black_on_white_is_twentyone_to_one(self):
        self.assertAlmostEqual(
            contrast.ratio((0, 0, 0), (255, 255, 255)), 21.0, places=2)

    def test_identical_colours_are_one_to_one(self):
        self.assertAlmostEqual(contrast.ratio((80, 80, 80), (80, 80, 80)), 1.0)

    def test_the_ratio_is_symmetric(self):
        a, b = (26, 26, 26), (250, 250, 248)
        self.assertAlmostEqual(contrast.ratio(a, b), contrast.ratio(b, a))

    def test_a_known_pair_matches_the_measured_value(self):
        """#2c6b30 on white — the store table colour, measured at 6.46:1."""
        self.assertAlmostEqual(
            contrast.ratio(contrast.parse_color("#2c6b30"),
                           contrast.parse_color("#ffffff")), 6.46, places=1)


class TestColourParsing(unittest.TestCase):
    def test_six_digit_hex(self):
        self.assertEqual(contrast.parse_color("#1a1a1a"), (26, 26, 26))

    def test_three_digit_hex_expands(self):
        self.assertEqual(contrast.parse_color("#fff"), (255, 255, 255))

    def test_named_colours(self):
        self.assertEqual(contrast.parse_color("white"), (255, 255, 255))

    def test_rgb_function(self):
        self.assertEqual(contrast.parse_color("rgb(10, 20, 30)"), (10, 20, 30))

    def test_none_and_transparent_are_not_colours(self):
        self.assertIsNone(contrast.parse_color("none"))
        self.assertIsNone(contrast.parse_color("transparent"))

    def test_case_is_ignored(self):
        self.assertEqual(contrast.parse_color("#1A1A1A"), (26, 26, 26))

    def test_an_unresolved_var_falls_back_to_the_default(self):
        self.assertIsNone(contrast.parse_color("var(--nope)"))


class TestBackdropResolution(unittest.TestCase):
    def test_text_over_a_shape_is_measured_against_that_shape(self):
        svg = ('<svg width="100" height="100" viewBox="0 0 100 100">'
               '<rect x="0" y="0" width="100" height="100" fill="#000000"/>'
               '<text x="50" y="50" font-size="13" fill="#000000">x</text></svg>')
        worst = contrast.worst_in_theme(svg, "light")
        self.assertAlmostEqual(worst[0], 1.0, places=2)

    def test_text_with_nothing_behind_it_uses_the_page_background(self):
        """This is how dark-mode breakage surfaces: fine on white, invisible on #16181d.

        Deliberately an UNMAPPED near-white (#f7f7f2, not #fafaf8): a literal the palette
        claims would be re-themed per theme, which is the fix rather than the bug.
        """
        svg = ('<svg width="100" height="100" viewBox="0 0 100 100">'
               '<text x="50" y="50" font-size="13" fill="#f7f7f2">x</text></svg>')
        self.assertLess(contrast.worst_in_theme(svg, "light")[0], 1.1)
        self.assertGreater(contrast.worst_in_theme(svg, "dark")[0], 10)

    def test_the_last_matching_shape_in_document_order_wins(self):
        svg = ('<svg width="100" height="100" viewBox="0 0 100 100">'
               '<rect x="0" y="0" width="100" height="100" fill="#000000"/>'
               '<rect x="0" y="0" width="100" height="100" fill="#ffffff"/>'
               '<text x="50" y="50" font-size="13" fill="#ffffff">x</text></svg>')
        self.assertAlmostEqual(contrast.worst_in_theme(svg, "light")[0], 1.0, places=2)

    def test_a_shape_declared_after_the_text_is_ignored(self):
        svg = ('<svg width="100" height="100" viewBox="0 0 100 100">'
               '<text x="50" y="50" font-size="13" fill="#ffffff">x</text>'
               '<rect x="0" y="0" width="100" height="100" fill="#ffffff"/></svg>')
        # measured against the light page (#fafaf8), not the later white rect
        self.assertLess(contrast.worst_in_theme(svg, "light")[0], 1.1)

    def test_a_mask_rect_is_never_treated_as_a_backdrop(self):
        """d2 puts a full-canvas rect in a mask; it is visibility data, never paint.

        Black text over a black MASK rect: if the mask were mistaken for a backdrop this
        reads as 1.0:1 and fails, and that failure would be pure fiction. Excluded, the
        text is correctly measured against the light page and scores ~19:1.
        """
        svg = ('<svg width="100" height="100" viewBox="0 0 100 100">'
               '<mask id="m"><rect x="0" y="0" width="100" height="100" fill="#000000">'
               '</rect></mask>'
               '<text x="50" y="50" font-size="13" fill="#000000">x</text></svg>')
        self.assertGreater(contrast.worst_in_theme(svg, "light")[0], 15)

    def test_a_class_scoped_fill_is_resolved(self):
        svg = ('<svg width="100" height="100" viewBox="0 0 100 100">'
               '<style>.t{fill:#000000}</style>'
               '<rect x="0" y="0" width="100" height="100" fill="#000000"/>'
               '<text x="50" y="50" font-size="13" class="t">x</text></svg>')
        self.assertAlmostEqual(contrast.worst_in_theme(svg, "light")[0], 1.0, places=2)


class TestLargeTextAllowance(unittest.TestCase):
    def base(self, font, scale=1.0):
        svg = ('<svg width="100" height="100" viewBox="0 0 100 100">'
               f'<text x="50" y="50" font-size="{font}" fill="#767676">x</text></svg>')
        return contrast.worst_in_theme(svg, "light", scale=scale)

    def test_large_text_needs_only_three_to_one(self):
        self.assertEqual(self.base(20)[1], 3.0)

    def test_normal_text_needs_four_and_a_half(self):
        self.assertEqual(self.base(13)[1], 4.5)

    def test_the_threshold_uses_the_RENDERED_size_not_the_declared_one(self):
        """A diagram scaled down to fit loses the large-text allowance with the size."""
        self.assertEqual(self.base(20, scale=0.5)[1], 4.5)


class TestBothThemes(unittest.TestCase):
    def test_an_unthemed_dark_colour_passes_in_light_and_fails_in_dark(self):
        """The exact bug the both-themes rule exists for: #111111 has no palette entry, so
        it survives substitution unchanged and stays near-black on a near-black page."""
        svg = ('<svg width="100" height="100" viewBox="0 0 100 100">'
               '<text x="50" y="50" font-size="13" fill="#111111">x</text></svg>')
        result = contrast.check(svg)
        self.assertFalse(result.ok)
        self.assertEqual(len(result.problems), 1)
        self.assertTrue(result.problems[0].startswith("dark:"), result.problems)

    def test_the_same_colour_passes_both_themes_once_it_is_mapped(self):
        """#1a1a1a IS mapped (--d-fg), so it flips to #e8e6e1 in dark mode. This is what
        the literal -> var substitution buys, stated as a test."""
        svg = ('<svg width="100" height="100" viewBox="0 0 100 100">'
               '<text x="50" y="50" font-size="13" fill="#1a1a1a">x</text></svg>')
        self.assertTrue(contrast.check(svg).ok)

    def test_a_themed_colour_passes_in_both(self):
        """var(--d-fg) resolves per theme, which is the whole point of the substitution."""
        svg = ('<svg width="100" height="100" viewBox="0 0 100 100">'
               '<text x="50" y="50" font-size="13" fill="var(--d-fg)">x</text></svg>')
        self.assertTrue(contrast.check(svg).ok)

    def test_the_detail_reports_both_themes(self):
        svg = ('<svg width="100" height="100" viewBox="0 0 100 100">'
               '<text x="50" y="50" font-size="13" fill="var(--d-fg)">x</text></svg>')
        detail = contrast.check(svg).detail
        self.assertIn("l ", detail)
        self.assertIn("d ", detail)


class TestRefusesToGuess(unittest.TestCase):
    def test_no_text_raises_rather_than_passing(self):
        """A contrast gate with nothing to measure must not report success."""
        with self.assertRaisesRegex(GateError, "no <text>"):
            contrast.check('<svg width="10" height="10" viewBox="0 0 10 10"><g/></svg>')

    def test_whitespace_only_text_counts_as_no_text(self):
        with self.assertRaises(GateError):
            contrast.check('<svg width="10" height="10" viewBox="0 0 10 10">'
                           '<text x="1" y="1"> </text></svg>')

    def test_no_svg_raises(self):
        with self.assertRaisesRegex(GateError, "no <svg>"):
            contrast.check("nothing here")


if __name__ == "__main__":
    unittest.main(verbosity=2)
