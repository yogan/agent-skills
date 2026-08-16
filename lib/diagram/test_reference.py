#!/usr/bin/env python3
"""End-to-end: every reference diagram must render and pass every non-browser gate.

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

from lib.diagram import compact, palette, render
from lib.diagram.examples import REFERENCE
from lib.diagram.gates import contrast, size, theming
from lib.diagram.spec import content_warnings

# name -> (natural width, natural height), measured in prototypes/diagram-stacks. `sequence`
# is the one that has moved least: 668x687 before its participant boxes were given an
# explicit height and its rows were re-stacked by compact.py.
#
# Every number here moved when the renderer settled on ELK as its only layout engine — see
# `d2.ELK_OPTS`. The dagre figures they replace are kept alongside, because the trade is the
# reason for the change: ELK spends page height and buys back text size, and dagre's wider
# drawings were losing a fifth of their glyph size to being scaled into the content column.
#
# `er` also sits one rung up the spacing ladder (`d2.ELK_SPACING_LADDER`): at the tight default
# its cardinality label was unreadable, and it is the only reference figure that needs the room.
#
# These are UNPLACED renders — `render.render` on the spec as written, with no `place.place`
# ahead of it, because placement is 64 measured candidates and minutes where this file runs in
# tenths of a second (the placed corpus is checked in `test_place_slow.py`). They are still the
# shipped geometry, because `examples.py` pins each callout to the anchor the pass measures for
# it; see that file's docstring on why it pins at all.
MEASURED = {
    # dagre: 887x771 at 11.4px, scaled to 0.88. 579 while the two callouts were pinned
    # `center-right`, which is not where the pass puts them.
    # 767 while containers had an even padding. The top now carries enough room for an arrow
    # to clear a container title and still keep a length of line above its arrowhead
    # (`d2.ELK_OPTS["paddingTop"]`).
    #
    # 493x818 portrait until the text floor came down to a measured 10px. The landscape
    # candidate had always existed and had always been refused, for text landing at 10.4px
    # against a floor of 11 — half the height for a quarter of the glyph size, a trade nobody
    # had ever actually looked at. It was looked at, and this is what came back.
    "arch": (971, 478),
    # d2's own sequence engine lays this one out; the layout engine never touches it, and
    # dagre and elk output are byte-identical. 420 before the group legend added LEGEND_BAND.
    "sequence": (663, 442),
    # dagre: 935x285 at 11.6px — and its arrows pointed at the TABLE. `documents.owner_id` was
    # accepted and silently dropped, so the picture never showed the column-level fact the spec
    # asserted. The arrow now leaves the column, from a figure that is also smaller.
    #
    # 855x260 at 12.7px while every cardinality was folded at its colon. That fold is gone —
    # it was added believing it kept `1 doc : n sessions` from running under the
    # `presence_sessions` table, and the thing that actually keeps it clear is the layer
    # spacing. On one line the labels cost 37px of width and half a pixel of glyph, and read
    # as the single phrase they are. See `d2.wrap_edge_label`.
    #
    # 892 while this was the one figure that climbed the LAYER spacing ladder. It no longer
    # needs to: `edgelabel.reposition` slides the cardinality along its own leg until it is off
    # the table, which is what the extra 30px of layer gap was buying.
    #
    # 862 while `n sessions : 1 doc` cut the line 2px INTO the arrowhead it names and two more
    # heads were painted across their corner. The 40px here are a rung of `d2.ELK_EDGE_LADDER`,
    # bought for exactly that and priced in glyph size: 12.6px becomes 12.4px.
    #
    # 902x257 until the layout search learned to prefer a less extreme shape
    # (`render.ASPECT_BAND`): 3.51:1 becomes 3.35:1 for 3px of height.
    #
    # 871 while the extra edge spacing above was still being paid. It is not: `route.straighten`
    # slides the last step of a route back along the run it comes off, which puts the same
    # arrowheads on straight line out of space the drawing already had.
    #
    # 861x260 while its cardinalities were folded. Nothing made them fold — an unwrapped
    # candidate was sitting there passing every gate, and the fold was bought for a slightly
    # less extreme SHAPE. `render._folds` ranks an unbroken label above that, so all three read
    # on one line now and the figure is 31px wider for it.
    "er": (892, 257),
    # dagre: 899x357 at 12.1px. This is the biggest single gain in the corpus — barely half
    # the width, and 14.0px text because none of it is scaled away. 411 while its callout was
    # pinned `bottom-left`; `center-right`, where the pass puts it, reaches out to the side.
    # 450 before the edge-spacing rung that takes the arrowhead on `implements` off its corner.
    # 490 while that rung was the only way to buy it — `route.straighten` now does the same
    # thing for nothing, so the 40px come back and this is the 450 it was before.
    "class": (498, 450),
    # dagre: 376x796, same 13.0px text. 205px shorter for nothing given up. 327 while this
    # pinned no anchor at all, which put its callout across 26% of `transport error`.
    # 591 before the edge-spacing rung — three arrowheads here were painted across their turn,
    # and this is the figure that pays most for them (`d2.ELK_EDGE_LADDER`).
    #
    # 432x691 PORTRAIT, and it was that shape by accident. Those arrowheads pushed it to the
    # wider rung, where the wide candidate's text falls under the floor — so the portrait won
    # by being the only one left, not by being preferred. `route.straighten` fixes the heads at
    # the tight rung, the accident goes, and the ranking's actual choice ships: 505px shorter
    # for text at 10.8px instead of 13.0, which is the trade `render.HEIGHT_BUCKET` exists to
    # make. The wrapping that comes with it is not free either — see `d2.wrap_edge_label`.
    #
    # 980x235 while `retry (max 30s)` folded onto THREE lines. It was not folded for nothing:
    # measured, putting it back on one line costs 32px of width, so the fold is paying for
    # itself. But the THIRD break took its longest line from 9 characters to 5 and bought only
    # 5px for it. `d2.WRAP_SLACK` lets one line run over rather than spend another, so it folds
    # in two now — 5px wider, 6px shorter, and the only figure in either corpus that moves.
    "state": (985, 229),
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

    def test_the_standalone_target_asks_for_a_wider_layout(self):
        """The other half of `d2.DIRECTION`, in geometry rather than emitted source.

        Standalone has no content column to fit into, so `er`, `class` and `state` ask for
        `right` — and `architecture` does not, being the one kind where it reads worse.

        What it ASKS for and what it GETS are two different things, and this test has been
        narrowed to the difference. `direction` tells the engine which way to rank the graph,
        not what aspect the drawing ends up at, and ELK honours the first without promising
        the second: `class` is pinned `right` and still comes out 777x838, because five boxes
        chained by their types do not fit side by side.

        The per-kind default is therefore checked as EMITTED SOURCE, in test_d2's
        TestDirectionPerTarget, which is where the claim can be stated exactly. What is worth
        pinning geometrically is only what survives that, and it is `architecture`: the one
        kind that does NOT ask for `right` as a file, so it comes out portrait there, while
        embedded the layout search has the content column to fill and lays it out wide. That
        is a difference between the targets rather than between two runs.

        `state` used to be pinned here too, as portrait embedded and landscape as a file. It
        is landscape on BOTH now — `route.straighten` took away the arrowheads that pushed it
        to a wider rung, and the rung was the only thing keeping the wide candidate out (see
        `MEASURED`). So it no longer tells the two targets apart, and asserting it would be
        pinning a coincidence. Its standalone half stays, because that one is the direction
        default doing its job.
        """
        width, height = render.natural_size(
            render.standalone(REFERENCE["state"], name="s--state"))
        self.assertGreater(width, height, "state standalone should be landscape")
        arch = render.standalone(REFERENCE["arch"], name="s--arch")
        width, height = render.natural_size(arch)
        self.assertLess(width, height, "architecture should be portrait as a file")
        self.assertGreater(MEASURED["arch"][0], MEASURED["arch"][1],
                           "architecture should be landscape embedded")

    def test_the_standalone_start_marker_spends_height_not_width(self):
        """The standalone target's direction is a default in `d2.DIRECTION`, not a key in the
        spec — so reading `spec["direction"]` here put the dot beside the first state on a
        landscape drawing, added 61px of width, and pushed a callout off the canvas. The
        clipping gate caught it; this pins the geometry so it cannot come back quietly.
        """
        import copy

        plain = copy.deepcopy(REFERENCE["state"])
        self.assertTrue(plain["states"][0].pop("start"), "the reference marks its start")
        before = render.natural_size(render.standalone(plain, name="s--nostart"))
        marked = render.standalone(REFERENCE["state"], name="s--start")
        after = render.natural_size(marked)
        self.assertIn("<circle", marked, "the marker must actually be drawn")
        self.assertEqual(before[0], after[0], "the marker must not widen a landscape drawing")
        # Height is what it may spend, and on this drawing it spends nothing: ELK leaves the
        # first state far enough from the top edge that the dot fits in margin already there.
        self.assertLessEqual(after[1], before[1] + compact.START_ARROW_ABOVE)

    def test_every_diagram_passes_the_size_gates(self):
        for name, svg in self.svgs.items():
            result = size.check(svg, name)
            self.assertTrue(result.ok, f"{name}: {result.problems}")

    def test_every_diagram_passes_wcag_aa_in_both_themes(self):
        for name, svg in self.svgs.items():
            result = contrast.check(svg, name)
            self.assertTrue(result.ok, f"{name}: {result.problems}")

    def test_every_diagram_is_fully_themeable(self):
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
