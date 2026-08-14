#!/usr/bin/env python3
"""Tests for the SVG post-processing. Everything here works on hand-written SVG snippets,
so it needs no d2 binary — test_reference.py covers the real pipeline.

The `pin_intrinsic` cases are the important ones. Without that step a page's
`max-width:100%` is not a cap at all and the browser enlarges the drawing, which is a bug
that looks exactly like "the diagram's font is too big".

Run: `python3 lib/diagram/test_render.py`
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.diagram import d2, render


class TestPinIntrinsic(unittest.TestCase):
    def test_width_and_height_come_from_the_viewbox(self):
        out = render.pin_intrinsic('<svg viewBox="0 0 474 560"><g/></svg>')
        self.assertIn('width="474"', out)
        self.assertIn('height="560"', out)

    def test_an_existing_intrinsic_size_is_left_alone(self):
        svg = '<svg width="100" height="50" viewBox="0 0 474 560"><g/></svg>'
        self.assertEqual(render.pin_intrinsic(svg), svg)

    def test_a_percentage_width_is_replaced(self):
        """`width="100%"` is not an intrinsic size — the browser still stretches it."""
        out = render.pin_intrinsic('<svg width="100%" viewBox="0 0 474 560"><g/></svg>')
        self.assertNotIn('width="100%"', out)
        self.assertIn('width="474"', out)

    def test_a_negative_viewbox_origin_is_handled(self):
        """d2 pads its output, so the origin is routinely negative."""
        out = render.pin_intrinsic('<svg viewBox="-8 -8 474 560"><g/></svg>')
        self.assertIn('width="474"', out)

    def test_without_a_viewbox_it_gives_up_rather_than_inventing_a_size(self):
        svg = "<svg><g/></svg>"
        self.assertEqual(render.pin_intrinsic(svg), svg)

    def test_the_body_of_the_svg_is_untouched(self):
        out = render.pin_intrinsic('<svg viewBox="0 0 10 20"><rect fill="#e8effc"/></svg>')
        self.assertIn('<rect fill="#e8effc"/>', out)


class TestNamespaceIds(unittest.TestCase):
    """d2 scopes its CSS per diagram but not its marker ids, so two diagrams on one page
    collide and the second borrows the first's arrowheads."""

    def test_ids_are_prefixed(self):
        out = render.postprocess('<svg id="root"><marker id="arrow"/></svg>', "one",
                                 theme_vars=False)
        self.assertIn('id="one-root"', out)
        self.assertIn('id="one-arrow"', out)

    def test_url_references_follow_the_rename(self):
        out = render.postprocess('<svg><marker id="a"/><path marker-end="url(#a)"/></svg>',
                                 "one", theme_vars=False)
        self.assertIn("url(#one-a)", out)

    def test_href_references_follow_the_rename(self):
        out = render.postprocess('<svg><use href="#a"/></svg>', "one", theme_vars=False)
        self.assertIn('href="#one-a"', out)

    def test_two_diagrams_do_not_collide(self):
        svg = '<svg><marker id="arrow"/></svg>'
        one = render.postprocess(svg, "one", theme_vars=False)
        two = render.postprocess(svg, "two", theme_vars=False)
        self.assertNotIn('id="one-arrow"', two)
        self.assertNotIn('id="two-arrow"', one)


class TestCalloutRetarget(unittest.TestCase):
    """d2 paints the callout plain white with a grey hairline and offers no styling hook,
    so its colours are retargeted by matching that exact attribute pair."""

    SVG = f'<svg><rect {render.CALLOUT_ATTRS} x="1"/></svg>'

    def test_the_callout_gets_the_page_colours(self):
        out = render.postprocess(self.SVG, "d", theme_vars=False)
        self.assertIn("var(--d-callout-bg)", out)
        self.assertIn("var(--d-callout-br)", out)

    def test_the_callout_is_tagged_so_host_css_can_select_it(self):
        out = render.postprocess(self.SVG, "d", theme_vars=False)
        self.assertIn('class="d2-callout"', out)

    def test_d2s_own_paint_is_gone(self):
        out = render.postprocess(self.SVG, "d", theme_vars=False)
        self.assertNotIn(render.CALLOUT_ATTRS, out)

    def test_the_retarget_runs_before_colour_substitution(self):
        """Otherwise #DEE1EB is already a var() and the attribute pair no longer matches."""
        out = render.postprocess(self.SVG, "d", theme_vars=True)
        self.assertIn("var(--d-callout-bg)", out)
        self.assertIn('class="d2-callout"', out)

    def test_an_ordinary_white_shape_is_not_mistaken_for_a_callout(self):
        out = render.postprocess('<svg><rect fill="white" stroke="#6b6b6b"/></svg>', "d",
                                 theme_vars=False)
        self.assertNotIn("d2-callout", out)


class TestPostprocessContract(unittest.TestCase):
    def test_content_before_the_svg_tag_is_dropped(self):
        """d2 emits an XML declaration that must not end up mid-page."""
        out = render.postprocess('<?xml version="1.0"?><svg viewBox="0 0 1 1"/>', "d",
                                 theme_vars=False)
        self.assertTrue(out.startswith("<svg"))

    def test_missing_svg_raises_rather_than_returning_junk(self):
        with self.assertRaises(render.RenderError):
            render.postprocess("not an svg at all", "d")

    def test_theme_vars_can_be_switched_off(self):
        svg = '<svg viewBox="0 0 1 1"><rect fill="#e8effc"/></svg>'
        self.assertIn('fill="#e8effc"', render.postprocess(svg, "d", theme_vars=False))
        self.assertIn("var(--d-client-bg)", render.postprocess(svg, "d", theme_vars=True))


class TestNaturalSize(unittest.TestCase):
    def test_it_prefers_the_pinned_size(self):
        self.assertEqual(render.natural_size('<svg width="10" height="20" '
                                             'viewBox="0 0 100 200"/>'), (10.0, 20.0))

    def test_it_falls_back_to_the_viewbox(self):
        self.assertEqual(render.natural_size('<svg viewBox="0 0 100 200"/>'),
                         (100.0, 200.0))

    def test_points_are_converted_to_css_pixels(self):
        width, height = render.natural_size('<svg width="75pt" height="150pt"/>')
        self.assertAlmostEqual(width, 100.0)
        self.assertAlmostEqual(height, 200.0)

    def test_an_unmeasurable_svg_reports_none(self):
        self.assertEqual(render.natural_size("<svg/>"), (None, None))


class TestHostCss(unittest.TestCase):
    """The SVG cannot fix these itself; an SVG opened standalone still clips its callouts."""

    def test_it_resets_the_callout_paragraph_margin(self):
        self.assertIn("margin:0", render.HOST_CSS)

    def test_it_supplies_a_font_family_for_the_callout(self):
        self.assertIn("font-family", render.HOST_CSS)

    def test_it_lets_the_callout_shadow_escape_the_viewbox(self):
        self.assertIn("overflow:visible", render.HOST_CSS)

    def test_callout_fill_opacity_stays_at_or_above_the_measured_floor(self):
        """The contrast gate measures the solid colour, so heavier transparency drifts."""
        import re
        for value in re.findall(r"fill-opacity:\.(\d+)", render.HOST_CSS):
            self.assertGreaterEqual(float(f"0.{value}"), 0.94)

    def test_page_css_bundles_the_vars_and_the_host_rules(self):
        css = render.page_css()
        self.assertIn("--d-callout-bg", css)
        self.assertIn(".d2-callout", css)


class TestLayoutChoice(unittest.TestCase):
    """`render()` measures every candidate layout and keeps the better one. Choosing by kind was
    a guess that had to be wrong half the time: the reference diagrams are too big to lay out
    wide, a four-box one is too small to lay out tall."""

    def setUp(self):
        self.calls = []
        self._compile = render.compile_source
        self._post = render.postprocess

        def compile_source(source, pad=8, binary="d2", layers=None):
            self.calls.append(source)
            # Landscape comes out wide and short, portrait tall and narrow — enough for the
            # ranking to have something to prefer. `self.natural` scales both, so a test can
            # make every candidate fail the column.
            w, h = (600, 300) if "direction: right" in source else (300, 600)
            w, h = w * self.natural, h * self.natural
            return (f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}">'
                    '<text font-size="13">x</text></svg>')

        self.natural = 1

        render.compile_source = compile_source
        render.postprocess = lambda svg, name, theme_vars=True: svg

    def tearDown(self):
        render.compile_source = self._compile
        render.postprocess = self._post

    ARCH = {"kind": "architecture", "nodes": [{"id": "a", "role": "svc"}], "edges": []}

    def test_an_explicit_direction_is_honoured_without_a_search(self):
        render.render(dict(self.ARCH, direction="up"), name="x")
        self.assertEqual(len(self.calls), 1)
        self.assertIn("direction: up", self.calls[0])

    def test_a_sequence_is_not_searched_because_d2_ignores_its_direction(self):
        render.render({"kind": "sequence", "participants": [{"id": "a"}, {"id": "b"}],
                       "messages": [{"from": "a", "to": "b", "label": "x"}]}, name="x")
        self.assertEqual(len(self.calls), 1)

    def test_both_orientations_are_tried(self):
        render.render(self.ARCH, name="x")
        directions = {"down" if "direction: down" in c else "right" for c in self.calls}
        self.assertEqual(directions, {"down", "right"})

    def test_the_shorter_layout_wins(self):
        svg = render.render(self.ARCH, name="x")
        self.assertIn('height="300"', svg)      # the landscape stub

    def test_heights_within_a_bucket_are_decided_by_glyph_size(self):
        """Scaling a drawing down makes it physically shorter, so comparing heights exactly
        rewards shrinking: an 848px figure at 0.92 (11.9px text) beat the same one wrapped to
        722px at full size (13.0px). Only a real difference in height should outrank text."""
        self.assertEqual(render.HEIGHT_BUCKET, 100)
        near = [round(h / render.HEIGHT_BUCKET) for h in (186, 202)]
        self.assertEqual(near[0], near[1], "16px apart should tie on height")
        far = [round(h / render.HEIGHT_BUCKET) for h in (93, 643)]
        self.assertLess(far[0], far[1], "a 550px difference should not tie")

    def test_a_diagram_that_fits_keeps_its_labels_as_written(self):
        """Wrapping is a width lever, not an improvement: it is the last tie-break, so an
        equally good layout with the author's own labels wins."""
        spec = {"kind": "architecture",
                "nodes": [{"id": "a", "role": "svc"}, {"id": "b", "role": "svc"}],
                "edges": [{"from": "a", "to": "b", "label": "a rather long edge label"}]}
        svg = render.render(spec, name="x")
        self.assertEqual(len(self.calls), len(render.CANDIDATES))
        self.assertIn('height="300"', svg)       # landscape, unwrapped

    def test_every_candidate_is_measured_rather_than_stopping_at_the_first_that_fits(self):
        """An early exit got this exactly wrong once: a four-box chain fits stacked downward,
        so the search stopped and never found that wrapping fits it landscape and 7x shorter."""
        spec = {"kind": "architecture",
                "nodes": [{"id": "a", "role": "svc"}, {"id": "b", "role": "svc"}],
                "edges": [{"from": "a", "to": "b", "label": "a rather long edge label"}]}
        render.render(spec, name="x")
        self.assertEqual(len(self.calls), len(render.CANDIDATES),
                         "every candidate that differs must still be measured")

    def test_a_wrap_level_that_changes_nothing_is_not_compiled_twice(self):
        """`self.ARCH` has no edges at all, so no wrap width can alter its source. Compiling
        the same characters again cannot measure differently, so the rung is skipped — two
        renders for two directions instead of six. Not a heuristic: a dedup on the emitted
        source, which is why it cannot change which layout wins."""
        render.render(self.ARCH, name="x")
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(len(set(self.calls)), 2, "and they are the two directions")


class TestToolchain(unittest.TestCase):
    def test_a_missing_binary_is_reported_not_raised(self):
        problems = render.check_toolchain(binary="d2-does-not-exist")
        self.assertEqual(len(problems), 1)
        self.assertIn("not found", problems[0])

    def test_a_missing_binary_raises_when_actually_compiling(self):
        with self.assertRaises(render.RenderError):
            render.compile_source("a: A", binary="d2-does-not-exist")

    def test_the_pinned_version_is_recorded(self):
        """The recipe leans on undocumented behaviour, so the version is part of it."""
        self.assertEqual(render.PINNED_VERSION, "0.8.1")


if __name__ == "__main__":
    unittest.main(verbosity=2)
