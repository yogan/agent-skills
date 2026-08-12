#!/usr/bin/env python3
"""Tests for the colour substitution. The mask exclusion is the one that matters most:
get it wrong and the whole drawing goes blank, which is why it has its own cases.

Run: `python3 lib/diagram/test_palette.py`
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.diagram import palette


class TestSubstitution(unittest.TestCase):
    def test_a_known_literal_becomes_a_var(self):
        self.assertEqual(palette.to_vars('fill="#e8effc"'), 'fill="var(--d-client-bg)"')

    def test_substitution_is_case_insensitive(self):
        """d2 emits #DEE1EB in upper case and its ambient colours in lower."""
        self.assertEqual(palette.to_vars('stroke="#DEE1EB"'), 'stroke="var(--d-grp-bg)"')

    def test_an_unknown_literal_is_left_alone(self):
        self.assertEqual(palette.to_vars('fill="#123456"'), 'fill="#123456"')

    def test_light_and_dark_resolve_to_different_values(self):
        self.assertEqual(palette.to_light('fill="#e8effc"'), 'fill="#e8effc"')
        self.assertEqual(palette.to_dark('fill="#e8effc"'), 'fill="#1e2a44"')

    def test_longer_literals_win_over_prefixes(self):
        """#fffffe and #fffffd must not be eaten by a shorter overlapping match."""
        out = palette.to_vars('a="#fffffe" b="#fffffd" c="#ffffff"')
        self.assertIn("var(--d-tbl-bg)", out)
        self.assertIn("var(--d-tbl-title)", out)
        self.assertIn("var(--d-surface)", out)


class TestMaskExclusion(unittest.TestCase):
    """A mask works on luminance — white shows, black hides. Rewriting a colour inside
    one inverts it and blanks the drawing."""

    SVG = ('<svg><mask id="m"><rect fill="#ffffff"></rect></mask>'
           '<rect fill="#ffffff"></rect></svg>')

    def test_colours_inside_a_mask_are_untouched(self):
        out = palette.to_vars(self.SVG)
        mask = out[out.index("<mask"):out.index("</mask>")]
        self.assertIn('fill="#ffffff"', mask)
        self.assertNotIn("var(", mask)

    def test_colours_outside_the_mask_are_still_substituted(self):
        out = palette.to_vars(self.SVG)
        after = out[out.index("</mask>"):]
        self.assertIn("var(--d-surface)", after)

    def test_theme_resolution_also_skips_masks(self):
        out = palette.to_dark(self.SVG)
        mask = out[out.index("<mask"):out.index("</mask>")]
        self.assertIn('fill="#ffffff"', mask)

    def test_unmapped_ignores_mask_contents(self):
        """Mask colours need no mapping, so reporting them would be a false alarm."""
        svg = '<svg><mask id="m"><rect fill="#000000"></rect></mask></svg>'
        self.assertEqual(palette.unmapped(svg), {})

    def test_several_masks_are_all_skipped(self):
        svg = ('<svg><mask><rect fill="#ffffff"/></mask><rect fill="#ffffff"/>'
               '<mask><rect fill="#ffffff"/></mask></svg>')
        out = palette.to_dark(svg)
        self.assertEqual(out.count('fill="#ffffff"'), 2)
        self.assertEqual(out.count('fill="#1f2229"'), 1)


class TestVarResolution(unittest.TestCase):
    """The gates run on the shipped SVG, where colours are already var() references."""

    def test_a_var_reference_resolves_per_theme(self):
        self.assertEqual(palette.to_dark('fill="var(--d-client-bg)"'), 'fill="#1e2a44"')
        self.assertEqual(palette.to_light('fill="var(--d-client-bg)"'), 'fill="#e8effc"')

    def test_callout_vars_resolve_even_though_they_have_no_literal(self):
        """These exist only as var refs — render.py inserts them directly."""
        self.assertEqual(palette.to_light('fill="var(--d-callout-bg)"'), 'fill="#fff4e8"')
        self.assertEqual(palette.to_dark('fill="var(--d-callout-bg)"'), 'fill="#2a2114"')

    def test_an_unknown_var_is_left_alone_rather_than_guessed(self):
        self.assertEqual(palette.to_light('fill="var(--nope)"'), 'fill="var(--nope)"')

    def test_round_tripping_a_literal_through_vars_preserves_the_theme_value(self):
        once = palette.to_light('fill="#e8effc"')
        twice = palette.to_light(palette.to_vars('fill="#e8effc"'))
        self.assertEqual(once, twice)


class TestUnmapped(unittest.TestCase):
    def test_an_unmapped_literal_is_counted(self):
        self.assertEqual(palette.unmapped('a="#1e1e2e" b="#1e1e2e"'), {"#1e1e2e": 2})

    def test_mapped_literals_are_not_reported(self):
        self.assertEqual(palette.unmapped('fill="#e8effc"'), {})

    def test_the_shape_code_colours_are_reported(self):
        """Why `shape: code` is not in the spec's shape list."""
        found = palette.unmapped('<svg><rect fill="#1e1e2e"/><text fill="#cdd6f4"/></svg>')
        self.assertEqual(set(found), {"#1e1e2e", "#cdd6f4"})


class TestRoleColours(unittest.TestCase):
    def test_every_role_has_a_fill_and_a_stroke(self):
        for role in ("client", "svc", "store", "cache", "ext", "neutral"):
            fill, stroke = palette.vars_for(role)
            self.assertIn(fill, palette.ALL)
            self.assertIn(stroke, palette.ALL)

    def test_every_role_has_a_text_grade_colour_for_tables(self):
        for role in ("client", "svc", "store", "cache", "ext", "neutral"):
            self.assertIn(palette.vars_for(role, table=True), palette.TABLE_ROLES)

    def test_a_table_colour_differs_from_the_shape_fill(self):
        """A pastel fill would be invisible as member text, which is what `fill` becomes."""
        for role in ("client", "svc", "store", "cache", "ext"):
            self.assertNotEqual(palette.vars_for(role)[0],
                                palette.vars_for(role, table=True))

    def test_an_unknown_role_raises(self):
        with self.assertRaises(KeyError):
            palette.vars_for("database")
        with self.assertRaises(KeyError):
            palette.vars_for("database", table=True)


class TestCssBlock(unittest.TestCase):
    def test_it_defines_both_themes(self):
        css = palette.css_block()
        self.assertIn(":root{", css)
        self.assertIn("[data-theme=dark]{", css)

    def test_every_var_the_svg_can_reference_is_defined(self):
        css = palette.css_block()
        for var in palette.BY_VAR:
            self.assertIn(f"{var}:", css)

    def test_the_two_themes_define_the_same_var_set(self):
        light, dark = palette.css_block().strip().split("\n")
        names = lambda block: sorted(p.split(":")[0] for p in
                                     block[block.index("{") + 1:-1].split(";") if p)
        self.assertEqual(names(light), names(dark))


class TestWcagOfThePaletteItself(unittest.TestCase):
    """The role pairs are load-bearing: a table's fill is border, header background AND
    member text at once, so each must clear AA against the surfaces it lands on."""

    def test_every_table_colour_clears_aa_in_both_themes(self):
        from lib.diagram.gates.contrast import parse_color, ratio
        for literal, (var, light, dark) in palette.TABLE_ROLES.items():
            # light: member text on the light body background (--d-tbl-bg)
            self.assertGreaterEqual(
                ratio(parse_color(light), parse_color("#ffffff")), 4.5, f"{var} light")
            # dark: member text on the dark body background, and header title behind it
            for surface in ("#1f2229", "#16181d"):
                self.assertGreaterEqual(
                    ratio(parse_color(dark), parse_color(surface)), 4.5,
                    f"{var} dark on {surface}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
