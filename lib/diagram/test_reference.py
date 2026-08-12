#!/usr/bin/env python3
"""End-to-end: the six reference diagrams must render and pass every non-browser gate.

This is the regression test for the d2 recipe itself. The expected geometry below was
measured by hand during the prototype, so a change in these numbers means either the
emitter changed or d2 did — and the second one is the reason the numbers are pinned at all,
since the recipe depends on undocumented d2 behaviour.

Needs `d2` on PATH, and skips (visibly) when it is absent, so the repo's suite still runs
on a machine without it. That is a test-convenience skip and nothing more: the gates
themselves raise GateError rather than pass when they cannot measure something.

Run: `python3 lib/diagram/test_reference.py`
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.diagram import palette, render
from lib.diagram.examples import REFERENCE
from lib.diagram.gates import contrast, size, theming
from lib.diagram.spec import content_warnings

# name -> (natural width, natural height), measured in prototypes/diagram-stacks. `sequence`
# is the one that has moved since: 668x687 before its participant boxes were given an
# explicit height and its rows were re-stacked by compact.py.
MEASURED = {
    "arch": (887, 771),
    "sequence": (663, 420),
    "er": (324, 722),
    "class": (474, 560),
    "state": (375, 796),
    "animated": (763, 390),
}

HAVE_D2 = render.d2_version() is not None


@unittest.skipUnless(HAVE_D2, "d2 is not installed (brew install d2)")
class TestReferenceCorpus(unittest.TestCase):
    """One rendered SVG per reference diagram, rendered once and shared."""

    svgs = {}

    @classmethod
    def setUpClass(cls):
        for name, spec in REFERENCE.items():
            cls.svgs[name] = render.render(spec, name=f"d2--{name}")

    def test_the_installed_d2_matches_the_pinned_version(self):
        """Deliberately strict. The recipe depends on undocumented d2 behaviour, so an
        upgrade is a decision, not a background event: re-pin PINNED_VERSION only after
        the rest of this file still passes on the new version. Without this the first
        symptom of a drift is a geometry assertion failing by a few pixels, which reads
        like an emitter bug rather than a new d2."""
        self.assertEqual(render.check_toolchain(), [],
                         f"installed d2 is {render.d2_version()}")

    def test_every_reference_diagram_renders(self):
        self.assertEqual(sorted(self.svgs), sorted(MEASURED))
        for name, svg in self.svgs.items():
            self.assertIn("<svg", svg, f"{name} produced no svg")

    def test_geometry_matches_what_the_prototype_measured(self):
        """Drift here means the emitter or d2 changed — find out which before proceeding."""
        for name, svg in self.svgs.items():
            width, height = render.natural_size(svg)
            expected_w, expected_h = MEASURED[name]
            self.assertAlmostEqual(width, expected_w, delta=1, msg=f"{name} width")
            self.assertAlmostEqual(height, expected_h, delta=1, msg=f"{name} height")

    def test_the_standalone_target_turns_the_layout_landscape(self):
        """The other half of `d2.DIRECTION`, in geometry rather than emitted source.

        MEASURED above pins the embedded renders, which are portrait because a landscape
        drawing is scaled into the 777px column until its text drops under 11px. Standalone
        has no column, so the same three specs come out wide — and `architecture` does not,
        being the one kind where `right` reads worse.
        """
        for name in ("er", "class", "state"):
            svg = render.standalone(REFERENCE[name], name=f"s--{name}")
            width, height = render.natural_size(svg)
            self.assertGreater(width, height, f"{name} standalone should be landscape")
            self.assertLess(*MEASURED[name], msg=f"{name} embedded should be portrait")
        arch = render.standalone(REFERENCE["arch"], name="s--arch")
        width, height = render.natural_size(arch)
        self.assertAlmostEqual(width / height, MEASURED["arch"][0] / MEASURED["arch"][1],
                               delta=0.02, msg="architecture should not change with target")

    def test_all_six_pass_the_size_gates(self):
        for name, svg in self.svgs.items():
            result = size.check(svg, name)
            self.assertTrue(result.ok, f"{name}: {result.problems}")

    def test_all_six_pass_wcag_aa_in_both_themes(self):
        for name, svg in self.svgs.items():
            result = contrast.check(svg, name)
            self.assertTrue(result.ok, f"{name}: {result.problems}")

    def test_all_six_are_fully_themeable(self):
        for name, svg in self.svgs.items():
            result = theming.check(svg, name)
            self.assertTrue(result.ok, f"{name}: {result.problems}")

    def test_no_reference_diagram_trips_a_content_warning(self):
        """The corpus is also the worked example of the content limits, so it must obey them."""
        for name, spec in REFERENCE.items():
            self.assertEqual(content_warnings(spec), [], f"{name}")

    def test_intrinsic_size_is_pinned_so_the_browser_cannot_upscale(self):
        for name, svg in self.svgs.items():
            tag = svg[:svg.find(">") + 1]
            self.assertRegex(tag, r'\swidth="\d+"', f"{name} has no pinned width")
            self.assertRegex(tag, r'\sheight="\d+"', f"{name} has no pinned height")

    def test_ids_are_namespaced_per_diagram_so_two_can_share_a_page(self):
        """d2 scopes its CSS per diagram but NOT its marker ids, so two diagrams on one
        page would otherwise share arrowheads."""
        import re
        for name, svg in self.svgs.items():
            prefix = f"d2--{name}-"
            ids = re.findall(r'\sid="([^"]*)"', svg)
            self.assertTrue(ids, f"{name} declares no ids at all")
            unscoped = [i for i in ids if not i.startswith(prefix)]
            self.assertEqual(unscoped, [], f"{name} has un-namespaced ids: {unscoped}")
            # Every internal reference must point at the renamed id, or the marker is
            # simply missing rather than shared.
            for ref in re.findall(r"url\(#([^)]*)\)", svg):
                self.assertTrue(ref.startswith(prefix), f"{name}: dangling url(#{ref})")

    def test_callouts_are_retargeted_and_tagged(self):
        """d2 paints the callout plain white; untouched, it is invisible in dark mode."""
        for name in ("arch", "sequence", "er", "class", "state"):
            svg = self.svgs[name]
            self.assertIn("d2-callout", svg, f"{name} callout was not tagged")
            self.assertNotIn(render.CALLOUT_ATTRS, svg,
                             f"{name} still carries d2's own callout paint")

    def test_the_animated_diagram_really_animates(self):
        """A steps diagram must come out as multiboard output with CSS animation."""
        self.assertIn("animation", self.svgs["animated"])

    def test_every_annotated_diagram_has_a_visible_callout_not_just_a_title(self):
        """`tooltip` alone is a hover-only <title>; only `tooltip.near` is visible."""
        for name in ("arch", "sequence", "er", "class", "state"):
            self.assertIn("foreignObject", self.svgs[name],
                          f"{name} has no callout foreignObject")


@unittest.skipUnless(HAVE_D2, "d2 is not installed (brew install d2)")
class TestThemeResolution(unittest.TestCase):
    """The gates run on the shipped SVG, where every colour is a var() reference."""

    def test_the_shipped_svg_carries_no_bare_role_literals(self):
        svg = render.render(REFERENCE["arch"], name="arch")
        self.assertNotIn("#e8effc", svg)      # client fill, now var(--d-client-bg)
        self.assertIn("var(--d-client-bg)", svg)

    def test_resolving_gives_different_colours_per_theme(self):
        svg = render.render(REFERENCE["arch"], name="arch")
        light, dark = palette.to_light(svg), palette.to_dark(svg)
        self.assertNotEqual(light, dark)
        self.assertIn("#e8effc", light)
        self.assertIn("#1e2a44", dark)

    def test_mask_contents_survive_substitution_unchanged(self):
        """Rewriting a colour inside a <mask> inverts it and blanks the drawing."""
        svg = render.render(REFERENCE["er"], name="er")
        self.assertIn("<mask", svg)
        for form in (svg, palette.to_light(svg), palette.to_dark(svg)):
            mask = form[form.index("<mask"):form.index("</mask>")]
            self.assertNotIn("var(--", mask)


if __name__ == "__main__":
    unittest.main(verbosity=2)
