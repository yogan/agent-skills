#!/usr/bin/env python3
"""Tests for the theming gate — the cheapest check here and the one most likely to catch
a d2 upgrade, since an unmapped literal just keeps its light-mode value on a dark page.

Run: `python3 lib/diagram/gates/test_theming.py`
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))))

from lib.diagram.gates import GateError
from lib.diagram.gates import theming


class TestTextWithNoFontToTake(unittest.TestCase):
    """d2 scopes its embedded font to `.text` / `.text-italic` and to nothing else, so any
    <text> this repo adds without one of those classes inherits whatever font the HOST PAGE
    sets. On the explainer that is Georgia — a serif — and it shipped that way in a sequence
    diagram's group legend for as long as the legend has existed.

    It survived because both of the places you would look are clean: the standalone render has
    no page to inherit from, and every review sheet is built on a sans-bodied page of its own.
    """

    def test_a_text_with_no_class_fails(self):
        result = theming.check('<svg><text x="1" y="2">library</text></svg>')
        self.assertFalse(result.ok)
        self.assertIn("no class", result.problems[0])

    def test_the_failure_says_what_it_will_look_like(self):
        result = theming.check('<svg><text x="1" y="2">library</text></svg>')
        self.assertIn("serif", result.problems[0])

    def test_d2s_own_classes_pass(self):
        for cls in ("text", "text-italic"):
            self.assertTrue(theming.check(f'<svg><text class="{cls}">a</text></svg>').ok, cls)

    def test_it_does_not_fire_on_a_textpath(self):
        """`<textPath>` starts with the same five characters and is not a text element."""
        self.assertTrue(theming.check('<svg><textPath href="#p">a</textPath></svg>').ok)


class TestUnmappedLiterals(unittest.TestCase):
    def test_a_fully_substituted_svg_passes(self):
        self.assertTrue(theming.check('<svg><rect fill="var(--d-client-bg)"/></svg>').ok)

    def test_an_unmapped_literal_fails(self):
        result = theming.check('<svg><rect fill="#1e1e2e"/></svg>')
        self.assertFalse(result.ok)
        self.assertIn("#1e1e2e", result.problems[0])

    def test_the_failure_explains_the_consequence(self):
        result = theming.check('<svg><rect fill="#1e1e2e"/></svg>')
        self.assertIn("light-mode value on a dark page", result.problems[0])

    def test_the_shape_code_case_is_caught(self):
        """This gate is why `shape: code` is not in the spec's allowed shapes."""
        result = theming.check('<svg><rect fill="#1e1e2e"/><text fill="#cdd6f4"/></svg>')
        self.assertFalse(result.ok)
        for colour in ("#1e1e2e", "#cdd6f4"):
            self.assertIn(colour, result.problems[0])

    def test_counts_are_reported_so_the_worst_offender_is_first(self):
        result = theming.check('<svg><a fill="#1e1e2e"/><b fill="#1e1e2e"/>'
                               '<c fill="#cdd6f4"/></svg>')
        self.assertLess(result.problems[0].index("#1e1e2e"),
                        result.problems[0].index("#cdd6f4"))

    def test_a_literal_inside_a_mask_is_exempt(self):
        """Mask colours are luminance data and must stay exactly as d2 authored them."""
        svg = '<svg><mask><rect fill="#000000"></rect></mask></svg>'
        self.assertTrue(theming.check(svg).ok)


class TestUndefinedVars(unittest.TestCase):
    """The mirror-image failure: an undefined var resolves to nothing and the attribute is
    dropped, so the element renders unpainted rather than mis-coloured."""

    def test_an_undefined_var_fails(self):
        result = theming.check('<svg><rect fill="var(--d-nope)"/></svg>')
        self.assertFalse(result.ok)
        self.assertIn("--d-nope", result.problems[0])

    def test_every_var_the_palette_defines_is_accepted(self):
        from lib.diagram import palette
        body = "".join(f'<rect fill="var({v})"/>' for v in palette.BY_VAR)
        self.assertTrue(theming.check(f"<svg>{body}</svg>").ok)

    def test_the_callout_vars_are_accepted(self):
        """They have no literal of their own — render.py inserts them directly."""
        svg = ('<svg><rect fill="var(--d-callout-bg)" '
               'stroke="var(--d-callout-br)"/></svg>')
        self.assertTrue(theming.check(svg).ok)


class TestReporting(unittest.TestCase):
    def test_the_detail_counts_vars_and_unmapped(self):
        detail = theming.check('<svg><rect fill="var(--d-fg)"/>'
                               '<rect fill="#1e1e2e"/></svg>').detail
        self.assertIn("1 vars", detail)
        self.assertIn("1 unmapped", detail)

    def test_both_problem_kinds_are_reported_together(self):
        result = theming.check('<svg><a fill="#1e1e2e"/><b fill="var(--d-nope)"/></svg>')
        self.assertEqual(len(result.problems), 2)

    def test_no_svg_raises(self):
        with self.assertRaisesRegex(GateError, "no <svg>"):
            theming.check("just some text")


if __name__ == "__main__":
    unittest.main(verbosity=2)
