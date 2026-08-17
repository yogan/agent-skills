#!/usr/bin/env python3
"""The parts of `show_limits.py` that decide something, held to what they decide.

Nothing here launches a browser or runs d2. Whether the document reads well is what you open it
to find out — but four things in it are silently wrong if they drift, and all four have been
wrong in a tool like this one:

  * a rung captioned with a text size it is not rendered at, because the column quietly scaled
    it back to fit;
  * a natural width worked out from the display factor instead of the authored text size, which
    is wrong by the scale itself;
  * a figure shown in a card of this file's own devising rather than the one it ships in, which
    is how a font bug survived a whole session of review sheets;
  * a limit typed into the prose instead of printed from the code, so the page keeps quoting a
    number the gate stopped holding.

Run: `python3 test_show_limits.py`
"""
import os
import re
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import show_limits as sl                                     # noqa: E402
from lib.diagram import render                                # noqa: E402
from lib.diagram.gates import size as size_gate               # noqa: E402


def at_scale(scale):
    """The natural width a drawing must have to be scaled to `scale` by the real column.

    Written as a ratio rather than a literal, because the literals in this file were derived from
    a column width that turned out to be 55px short, and every one of them then described a
    different scale than its name claimed.
    """
    return size_gate.AVAIL_W / scale


def svg(width, height, primary=14.0, edge=None, detail=None):
    """A drawing with just enough in it for the size gate to measure it honestly.

    The three kinds of text are tagged the way the renderer tags them, since telling them apart
    is exactly what `gates/size.analyse` does and a fixture that skipped the tags would be
    measured as three primary labels.
    """
    parts = [f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
             f'<text font-size="{primary}">a box</text>']
    if edge:
        parts.append(f'<text class="text-italic" style="font-size:{edge}px">holds</text>')
    if detail:
        parts.append(f'<tspan class="d2-detail" style="font-size:{detail}px">module.py</tspan>')
    return "".join(parts) + "</svg>"


class TestScaled(unittest.TestCase):
    def test_the_intrinsic_size_moves_and_the_view_box_does_not(self):
        """The viewBox staying put is the whole mechanism: the same user units mapped into a
        smaller box is what takes the text down with the drawing."""
        out = sl.scaled(svg(500, 400), 0.5)
        self.assertIn('width="250.00"', out)
        self.assertIn('height="200.00"', out)
        self.assertIn('viewBox="0 0 500 400"', out)

    def test_a_rung_above_the_authored_size_grows_the_drawing(self):
        out = sl.scaled(svg(500, 400), 1.2)
        self.assertIn('width="600.00"', out)

    def test_nothing_but_the_outer_tag_is_touched(self):
        """A rung's honesty rests on the browser scaling the CONTENTS, which only a browser can
        confirm — so what is pinned here is that this function gives it the chance: the drawing
        below the outer tag has to come through byte for byte."""
        original = svg(500, 400)
        body = original[original.index(">") + 1:]
        self.assertTrue(sl.scaled(original, 0.5).endswith(body))


class TestRungs(unittest.TestCase):
    def test_a_rung_the_column_would_scale_back_is_skipped_not_shown(self):
        """A drawing already scaled down on the page cannot reach a text size above the one it
        renders at there, and showing it anyway captions a rung with a lie."""
        shown, skipped = sl.rungs_for(svg(at_scale(0.93), 257, primary=14.0), rungs=(14, 12))
        self.assertEqual([t for t, _f, _w in skipped], [14])
        self.assertEqual([t for t, _f, _w in shown], [12])

    def test_a_narrow_drawing_reaches_every_rung(self):
        shown, skipped = sl.rungs_for(svg(300, 900, primary=13.0))
        self.assertEqual(skipped, [])
        self.assertEqual(len(shown), len(sl.TEXT_RUNGS))

    def test_the_natural_width_is_the_one_the_gate_would_measure_that_size_at(self):
        """The caption promises "a drawing this wide lands here". Checked against the gate's own
        scaling rather than against the formula restated: a figure authored at 14px and built to
        the width the caption gives has to measure 10px in the column.

        Which is also what stops the width being taken from the display factor — the stand-in is
        400px wide and the answer is nearer 1100, so the two cannot be confused by accident.
        """
        (_target, factor, natural), = sl.rungs_for(svg(400, 300, primary=14.0), rungs=(10,))[0]
        real = svg(round(natural), 300, primary=14.0)
        self.assertAlmostEqual(size_gate.analyse(real)["fmin"], 10.0, places=1)
        self.assertNotAlmostEqual(natural, 400 * factor, places=1)


class TestCard(unittest.TestCase):
    def test_the_card_is_the_one_the_explainer_itself_emits(self):
        """Pinned against the explainer's own regex for its own wrapper. If that markup changes,
        this fails here rather than showing a figure in a card no article ever uses."""
        explainer = sl.explainer()
        wrapped = f'<div class="diagram">{sl.card("<svg></svg>")}</div>'
        self.assertTrue(explainer._REDUNDANT_DIAGRAM_WRAPPER_RE.search(wrapped))


class TestHeightLines(unittest.TestCase):
    def test_a_line_is_offset_by_the_card_padding(self):
        """Absolutely positioned inside the card, the offset counts from the padding box — the
        drawing starts one padding lower, so a line without that term sits above the figure."""
        out = sl.rule(820, "dashed", "#000", "target")
        self.assertIn(f"calc({render.CARD_PADDING_REM}rem + 820px)", out)

    def test_a_label_can_sit_on_either_edge(self):
        """Two lines can land within a few pixels — 904px against a 900px ceiling is the case
        worth looking at — and two labels on one edge print over each other."""
        self.assertIn("left:0", sl.rule(904, "solid", "#000", "figure", side="left"))
        self.assertIn("right:0", sl.rule(900, "solid", "#000", "ceiling"))

    def test_the_figures_own_height_is_marked_as_well_as_the_limits(self):
        """A card held open to the hard ceiling gives no clue where the drawing stops, and that is
        the only question this ladder asks."""
        variants = sl.height_variants([(15, svg(497, 744, primary=14.0))],
                                      svg(497, 744, primary=14.0))
        figure = variants[0][-1]
        self.assertIn("744px figure", figure)
        self.assertIn(f"{size_gate.MAX_H:.0f}px target", figure)
        self.assertIn(f"{size_gate.MAX_TOTAL_H:.0f}px ceiling", figure)

    def test_a_target_breach_is_not_coloured_like_a_ceiling_breach(self):
        """Only the ceiling refuses. Colouring both the same tells the reader the target is a wall,
        and the drawing beside it already says otherwise — dashed accent for one, solid red for
        the other."""
        past_target = sl.height_variants(
            [(40, svg(497, int(size_gate.MAX_H + 20), primary=14.0))],
            svg(497, 744, primary=14.0))[0][4]
        self.assertIn(f'class="size {sl.WARN}"', past_target)
        self.assertNotIn(f'class="size {sl.BAD}"', past_target)

        past_ceiling = sl.height_variants(
            [(85, svg(497, int(size_gate.MAX_TOTAL_H + 20), primary=14.0))],
            svg(497, 744, primary=14.0))[0][4]
        self.assertIn(f'class="size {sl.BAD}"', past_ceiling)

    def test_a_text_floor_breach_stays_the_hard_colour(self):
        """Under a floor the gate refuses the figure outright, so that one really is a wall."""
        variants, _skipped = sl.text_variants("s", svg(454, 647, primary=13.0))
        smallest = variants[-1][4]
        self.assertIn(f'class="size {sl.BAD}"', smallest)
        self.assertNotIn(f'class="size {sl.WARN}"', smallest)

    def test_a_scale_that_cannot_vary_is_not_reported(self):
        """This ladder's figure is narrower than the column, so it is 1.00 on every variant. A
        number that never moves is furniture, not a measurement."""
        narrow = sl.height_variants([(15, svg(497, 744, primary=14.0))],
                                    svg(497, 744, primary=14.0))
        self.assertNotIn("1.00", narrow[0][4])
        wide = svg(at_scale(0.5), 744, primary=14.0)
        self.assertIn("scaled to 0.50", sl.height_variants([(15, wide)], wide)[0][4])

    def test_the_width_is_not_reported_at_all(self):
        """The whole page is about how far down a figure reaches. Its width answers nothing here,
        and it was the noisiest thing in the box."""
        variants = sl.height_variants([(15, svg(497, 744, primary=14.0))],
                                      svg(497, 744, primary=14.0))
        self.assertNotIn("497", variants[0][4])


class TestExhibitChoice(unittest.TestCase):
    def test_only_a_figure_carrying_a_subtitle_can_show_all_three_floors(self):
        measured = [("no/subtitle", svg(100, 100, primary=13.0, edge=12.0)),
                    ("has/subtitle", svg(760, 200, primary=13.0, edge=12.0, detail=11.0))]
        self.assertEqual(sl.pick_three_kinds(measured)[0], "has/subtitle")

    def test_between_two_carriers_the_one_with_more_rungs_wins(self):
        narrow = ("tall/narrow", svg(300, 900, primary=13.0, edge=12.0, detail=11.0))
        # Exactly the column width, so by construction it cannot be shown at any size above the
        # one it already renders at — which is what costs it the top of the range.
        wide = ("short/wide", svg(at_scale(1.0), 200, primary=13.0, edge=12.0, detail=11.0))
        self.assertEqual(sl.pick_three_kinds([wide, narrow])[0], "tall/narrow")
        self.assertEqual(sl.pick_three_kinds([narrow, wide])[0], "tall/narrow")

    def test_with_no_subtitle_anywhere_it_still_picks_something(self):
        measured = [("only/two-kinds", svg(300, 300, primary=13.0, edge=12.0))]
        self.assertEqual(sl.pick_three_kinds(measured)[0], "only/two-kinds")

    def test_the_closest_to_a_floor_is_measured_per_kind_not_by_raw_size(self):
        """An 11px edge label has 2px of room where an 11px box name has 1, so comparing the
        sizes themselves picks the wrong figure — and the wrong figure here is the exhibit the
        whole decision is read off."""
        roomy_name = ("a/label-is-tight", svg(700, 200, primary=12.0, edge=9.2))
        tight_name = ("b/name-is-tight", svg(700, 200, primary=10.4))
        self.assertEqual(sl.pick_at_the_floor([tight_name, roomy_name])[0], "a/label-is-tight")


class TestWhichVariantIsReal(unittest.TestCase):
    """Each switcher mixes one drawing the renderer would produce with several it never would. A
    reader who cannot tell them apart is judging a limit against evidence that does not exist, so
    the real one and the forced ones are marked — in the caption AND in the switcher, since the
    list is where a reader decides which one to look at."""

    def marks(self, variants):
        return [mark for _key, _label, mark, *_parts in variants]

    def test_the_variant_the_renderer_lands_on_is_named_with_its_true_size(self):
        """Named with the measured size, not just circled: the switcher offers whole pixels and a
        real figure lands wherever its layout puts it, so an exact match must not be implied."""
        # A drawing scaled to 10/13 renders its 13px text at exactly 10px.
        variants, _skipped = sl.text_variants(
            "s", svg(at_scale(10 / 13), 226, primary=13.0, edge=13.0))
        captions = " ".join(aside for *_head, aside, _fig in variants)
        self.assertIn("the size this figure really renders at, 10px", captions)
        self.assertEqual(self.marks(variants).count("what ships"), 1)

    def test_a_variant_under_the_floor_says_so(self):
        """Every size below the primary floor is unreachable in practice, and a switcher that
        did not say so would read as if all of them were attainable."""
        variants, _skipped = sl.text_variants("s", svg(454, 647, primary=13.0))
        self.assertIn("under the floor", self.marks(variants))

    def test_the_height_variant_the_renderer_lands_on_matches_the_shipped_drawing(self):
        """Matched by measurement against the real pipeline's own output, not by being first in
        the list — the ladder pins the layout direction, and if that ever stopped reproducing
        what ships, nothing should claim to be it."""
        variants = sl.height_variants([(15, svg(497, 744, primary=14.0)),
                                       (40, svg(497, 844, primary=14.0))],
                                      svg(497, 744, primary=14.0))
        self.assertEqual(self.marks(variants), ["what ships", "forced, 40px rows"])

    def test_a_height_ladder_reproducing_nothing_that_ships_claims_nothing(self):
        variants = sl.height_variants([(40, svg(497, 844, primary=14.0))],
                                      svg(497, 744, primary=14.0))
        self.assertEqual(self.marks(variants), ["forced, 40px rows"])


class TestPanel(unittest.TestCase):
    """The switcher is this page's own furniture. What it must not get wrong is which figure is
    on screen, because every variant is in the document at once."""

    VARIANTS = [("a", "14px", sl.SHIPS, {"primary": "14px"}, "<i>aside a</i>", "<b>fig a</b>"),
                ("b", "8px", "", {"primary": "8px"}, "<i>aside b</i>", "<b>fig b</b>")]

    def test_exactly_one_variant_starts_visible(self):
        out = sl.panel([("fig", self.VARIANTS)])
        self.assertIn('<div class="variant" data-v="a">', out)
        self.assertIn('<div class="variant" data-v="b" hidden>', out)

    def test_it_opens_on_the_variant_the_renderer_really_produces(self):
        """Not on the first in the list, which is a forced one. Opening on a drawing that does not
        exist invites judging a floor against it before the marks have been noticed."""
        forced_first = [("a", "14px", "", {}, "x", "y"),
                        ("b", "13px", sl.SHIPS, {}, "x", "y")]
        out = sl.panel([("fig", forced_first)])
        self.assertIn('<button data-v="b" class=on>', out)
        self.assertIn('<div class="variant" data-v="b">', out)
        self.assertIn('<div class="variant" data-v="a" hidden>', out)

    def test_with_nothing_marked_it_falls_back_to_the_first(self):
        out = sl.panel([("fig", [("a", "14px", "", {}, "x", "y"),
                                 ("b", "8px", "", {}, "x", "y")])])
        self.assertIn('<button data-v="a" class=on>', out)

    def test_the_switcher_state_agrees_with_the_figure_on_screen(self):
        """Across groups too. A button marked active over a hidden variant is a page lying about
        what you are looking at."""
        out = sl.panel([("one", self.VARIANTS),
                        ("two", [("c", "10px", "", {}, "x", "y")])])
        self.assertEqual(out.count("class=on"), 1)
        active = re.search(r'<button data-v="([^"]+)"[^>]* class=on>', out).group(1)
        self.assertIn(f'<div class="variant" data-v="{active}">', out)
        self.assertNotIn(f'<div class="variant" data-v="{active}" hidden>', out)

    def test_a_mark_reaches_the_switcher_and_not_only_the_rail(self):
        out = sl.panel([("fig", self.VARIANTS)])
        self.assertIn('<span class="mark">what ships</span>', out)

    def test_nothing_a_variant_contributes_lands_in_the_content_column(self):
        """The column is the measurement. Everything switched belongs to the rail or is the
        drawing — a note in the column reads as part of the article and narrows nothing, but it
        was a pill there and that is the shape this is guarding against."""
        out = sl.panel([("fig", self.VARIANTS)])
        column = out[out.index("</div>", out.index('rail rail-right')):]
        self.assertNotIn("aside a", column)

    def test_a_button_carries_the_values_selecting_it_implies(self):
        """What makes the table at the foot follow the menus. Rendered as data on the button so
        the script has nothing to recompute and cannot disagree with the caption."""
        out = sl.panel([("fig", self.VARIANTS)])
        self.assertIn('data-call-primary="14px"', out)

    def test_every_variant_is_its_own_card_so_the_zoom_still_binds(self):
        """The lightbox resolves one <svg> per `.diagram-embed` at load. Variants sharing a card
        would all enlarge whichever came first, which is the opposite of what a zoom is for."""
        variants, _skipped = sl.text_variants("s", svg(300, 400, primary=13.0))
        out = sl.panel([("fig", variants)])
        self.assertEqual(out.count("diagram-embed"), len(variants))


class TestTheLimitsArePrintedNotTyped(unittest.TestCase):
    """Every limit on the page comes out of `gates/size.py`, prose included.

    A page of evidence quoting a number the gate has stopped holding is worse than no page,
    because it is read as current — which is exactly what happened to the write-up this tool
    replaces: it still asked for a decision on a 800px ceiling and an 11px floor months after
    both had moved.

    Pinned by moving every limit at once and requiring that none of the real values is left
    anywhere in the document. Checking the generated page rather than the source is what keeps
    this honest about incidental numbers: a comment or an illustration may say "9px" freely, and
    only a value presented AS a limit must track the code.
    """
    # Deliberately nothing like the real values, and none a coincidence of the others: a patched
    # number that happened to equal a measurement off a figure would pass while proving nothing.
    MOVED = {"MIN_READABLE": 33.0, "MIN_READABLE_EDGE": 22.0, "MIN_READABLE_DETAIL": 11.5,
             "MAX_H": 4444.0, "MAX_TOTAL_H": 5555.0, "RESCUE_H": 1111.0}
    # How each APPEARS as a limit, which is not the same as how the number appears. The page is
    # full of rendered text sizes on the same scale as the floors — a variant really does render
    # at 10px — so a bare "10px" proves nothing either way. Only the labelled form does.
    SHOWN = {"MIN_READABLE": "floor {:.0f}px", "MIN_READABLE_EDGE": "floor {:.0f}px",
             "MIN_READABLE_DETAIL": "floor {:.1f}px"}

    def document(self):
        """The document as a READER sees it — tags and inline styles stripped.

        A limit is a claim made to a person, so only the visible text can carry one. Scanning the
        markup instead finds every CSS length on the page: a pill with `border-radius:999px` on
        it contains "9px", and a guard that fired on that would be trained away rather than
        fixed.
        """
        ships = svg(497, 744, primary=14.0)
        figure = svg(454, 647, primary=13.0, edge=13.0, detail=11.0)
        markup = "".join(part["html"] for part in (
            sl.section_text([("repo/arch", figure, "why")]),
            sl.section_height([(15, ships)], ships), sl.section_values()))
        return re.sub(r"<[^>]+>", " ", markup)

    def test_moving_every_limit_moves_every_number_on_the_page(self):
        was = {name: getattr(size_gate, name) for name in self.MOVED}
        with mock.patch.multiple(size_gate, **self.MOVED):
            body = self.document()
        for name, moved in self.MOVED.items():
            shown = self.SHOWN.get(name, "{:.0f}px")
            self.assertIn(shown.format(moved), body,
                          f"{name} is not printed from gates/size.py")
            self.assertNotIn(shown.format(was[name]), body,
                             f"{name}'s old value survives in the prose — print it instead")

    def test_every_limit_the_gate_holds_is_named_on_the_page(self):
        """Named, not just valued. A bare number is a question the reader has to ask back, and
        the answer they need is which constant to go and edit — which is the whole reason the
        selected-values table carries the constant names now that the summary section is gone."""
        body = self.document()
        for constant in ("MIN_READABLE", "MIN_READABLE_EDGE", "MIN_READABLE_DETAIL", "MAX_H"):
            self.assertIn(constant, body, f"{constant} is not named on the page")


class TestSelectedValues(unittest.TestCase):
    """The table at the foot is filled by the switchers, not rendered. What must hold is that
    every cell a button writes to exists, or a selection silently goes nowhere."""

    def test_every_cell_the_switchers_write_to_is_on_the_page(self):
        figure = svg(454, 647, primary=13.0, edge=13.0, detail=11.0)
        variants, _skipped = sl.text_variants("f", figure)
        heights = sl.height_variants([(15, svg(497, 744, primary=14.0))],
                                     svg(497, 744, primary=14.0))
        written = {field for *_h, call, _a, _f in
                   [(k, l, m, c, a, f) for k, l, m, c, a, f in variants + heights]
                   for field in call}
        page = sl.section_values()["html"]
        for field in written:
            self.assertIn(f'data-cell="{field}"', page,
                          f"a switcher writes {field} and no cell receives it")

    def test_a_variant_writes_every_field_its_panel_governs(self):
        """Including the ones it has nothing to say about. A variant that wrote only what it knew
        left the previous figure's subtitle standing in the table — a number nobody selected."""
        no_subtitle, _skipped = sl.text_variants("s", svg(700, 200, primary=13.0, edge=13.0))
        for _k, _l, _m, call, _a, _f in no_subtitle:
            self.assertEqual(set(call), {"primary", "edge", "detail"})
            self.assertEqual(call["detail"], sl.NOT_SET)

    def test_it_starts_empty_rather_than_showing_a_value_nobody_picked(self):
        """Filled by script from whichever button starts active, so a rendered value here would
        be a second source of truth — and the one that cannot follow a click."""
        self.assertNotIn("13px", sl.section_values()["html"])
        self.assertIn(sl.NOT_SET, sl.section_values()["html"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
