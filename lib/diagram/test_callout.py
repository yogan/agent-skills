#!/usr/bin/env python3
"""Setting a note in the drawing's own type and re-cutting its box, on d2's output shapes.

No browser: a note's width is d2's own measurement scaled by the type ratio, and the one thing
that does need a browser — how wide a legend's words render — is `browser.text_widths`, which
this module caches. Everything below checks the geometry, because the geometry is where this
can go wrong silently: a box trimmed from the wrong side moves the pointer off the thing the
note is about, and the note then points at a neighbour.

The fixtures are d2's real output for each `tooltip.near`, copied from a render. Which side the
pointer sits on is the whole input to the decision, and inventing it would have tested the
invention.

Run: `python3 lib/diagram/test_callout.py`
"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.diagram import browser, callout  # noqa: E402

# d2's own geometry for a 20-character note, per anchor: the box, the pointer triangle, the
# box d2 puts the words in and the words themselves. 152 wide holding a 132 words-box — 10px
# of padding a side. Copied from a real render at each anchor; the pointer's side is the whole
# input to the re-cut decision, and inventing it would have tested the invention.
SHAPES = {
    # pointer under the box, 20px in from the LEFT corner
    "top-left": (
        '<rect x="146" y="-42" width="152" height="44" rx="4" ry="4" />'
        '<path d="M 162 2 L 170 2 L 166 10 Z" />'
        '<svg x="156" y="-32" width="132" height="24" viewBox="0 0 132 24" overflow="hidden">'
    ),
    # pointer under the box, at its MIDDLE
    "top-center": (
        '<rect x="124" y="-42" width="152" height="44" rx="4" ry="4" />'
        '<path d="M 196 2 L 204 2 L 200 10 Z" />'
        '<svg x="134" y="-32" width="132" height="24" viewBox="0 0 132 24" overflow="hidden">'
    ),
    # pointer under the box, 20px in from the RIGHT corner
    "top-right": (
        '<rect x="102" y="-42" width="152" height="44" rx="4" ry="4" />'
        '<path d="M 230 2 L 238 2 L 234 10 Z" />'
        '<svg x="112" y="-32" width="132" height="24" viewBox="0 0 132 24" overflow="hidden">'
    ),
    # pointer on the box's RIGHT edge, box hanging off to the left
    "center-left": (
        '<rect x="-16" y="23" width="152" height="44" rx="4" ry="4" />'
        '<path d="M 136 41 L 136 49 L 144 45 Z" />'
        '<svg x="-6" y="33" width="132" height="24" viewBox="0 0 132 24" overflow="hidden">'
    ),
    # pointer on the box's LEFT edge, box hanging off to the right
    "center-right": (
        '<rect x="264" y="23" width="152" height="44" rx="4" ry="4" />'
        '<path d="M 264 41 L 264 49 L 256 45 Z" />'
        '<svg x="274" y="33" width="132" height="24" viewBox="0 0 132 24" overflow="hidden">'
    ),
}

NOTE = "the only entry point"

# What d2 sets a note at, and what the re-cut brings it to. The trim is the ratio between them
# applied to the words-box, so every expected number below is derived rather than typed.
TRIM = 132 * (1 - callout.D2_NOTE_PX ** -1 * 13)


def svg(anchor, note=NOTE):
    """One callout, in the markup d2 emits: the words are SVG text inside its markdown block,
    which is nested inside the group — so the group's own end tag is not the first one."""
    body = (SHAPES[anchor]
            + f'<g class="md md-native"><text x="0.000" y="18.000" class="md-text text" '
              f'font-size="16.000" xml:space="preserve">{note}</text></g></svg>')
    return f'<svg viewBox="0 0 400 400"><g class="positioned-tooltip">{body}</g></svg>'


def boxes(out):
    """(rect x, rect width, words-box x, words-box width) after the re-cut."""
    rect = re.search(r'<rect x="([-\d.]+)" y="[-\d.]+" width="([\d.]+)"', out)
    obj = re.search(r'<svg x="([-\d.]+)" y="[-\d.]+" width="([\d.]+)"', out)
    return (float(rect.group(1)), float(rect.group(2)),
            float(obj.group(1)), float(obj.group(2)))


def pointer(out):
    """The x coordinates of the pointer triangle, which must never move."""
    return [float(v) for v in
            re.search(r'<path d="M ([-\d.]+) [-\d.]+ L ([-\d.]+) [-\d.]+ '
                      r'L ([-\d.]+) [-\d.]+ Z"', out).groups()]


def note_type(out):
    """(font size, baseline y) of the note's words."""
    return (float(re.search(r'font-size="([\d.]+)"', out).group(1)),
            float(re.search(r'<text[^>]*?\sy="([\d.]+)"', out).group(1)))


class CalloutCase(unittest.TestCase):
    """Every case seeds the module cache and clears it again — it is global by design."""

    def setUp(self):
        self.original = dict(callout._WIDTHS)

    def tearDown(self):
        callout._WIDTHS.clear()
        callout._WIDTHS.update(self.original)


class TestWhereTheBoxSits(CalloutCase):
    """`boxes` is what lets the placement search ask the DRAWING what a callout covers —
    which turn it hides, which leg it lies along. Those facts only exist in the drawing's
    own coordinates, which is why this is read off the SVG and not off a browser."""

    def test_it_reads_the_box_out_of_the_drawing(self):
        self.assertEqual(callout.boxes(svg("center-right")),
                         [(264.0, 23.0, 416.0, 67.0)])

    def test_the_note_comes_back_with_its_box(self):
        """Paired here rather than by a caller zipping two lists: which order d2 emits
        callouts in is d2's business, and a message that names the wrong note is worse
        than no message."""
        self.assertEqual(callout.notes(svg("center-right")),
                         [(NOTE, (264.0, 23.0, 416.0, 67.0))])

    def test_the_note_is_read_from_past_the_nested_group(self):
        """The words sit inside a `<g>` of d2's own, so a body that ends at the first close
        tag ends before them — and the note comes back empty while its box looks right."""
        self.assertEqual(callout.notes(svg("top-left"))[0][0], NOTE)

    def test_a_note_broken_over_two_lines_reads_back_as_one_string(self):
        """It is the key `known` is measured against, so anything else silently misses."""
        two = svg("top-left").replace(
            f">{NOTE}<",
            '>the only</text><text x="0" y="30" class="md-text text">entry point<')
        self.assertEqual(callout.notes(two)[0][0], NOTE)

    def test_a_callout_whose_text_cannot_be_read_still_yields_its_box(self):
        """The box is what a caller measures against; the text is for saying which note
        it was. Losing the second must not lose the first."""
        stripped = re.sub(r"<text.*?</text>", "", svg("center-right"), flags=re.S)
        self.assertEqual(callout.notes(stripped),
                         [("", (264.0, 23.0, 416.0, 67.0))])

    def test_a_box_hanging_off_to_the_left_keeps_its_negative_x(self):
        """`center-left` puts the box left of its target, so the drawing's own coordinates
        go negative — and a pattern that only matches digits silently finds no callout."""
        self.assertEqual(callout.boxes(svg("center-left")),
                         [(-16.0, 23.0, 136.0, 67.0)])

    def test_a_drawing_with_no_callout_has_none(self):
        self.assertEqual(callout.boxes('<svg viewBox="0 0 10 10"></svg>'), [])


class TestTheNoteIsSetInTheDrawingsOwnType(CalloutCase):
    """d2 sets a note several points above the labels of the drawing it annotates, so the
    annotation reads louder than the picture. `fit` re-sets it and re-cuts the box to match."""

    def test_the_words_come_out_at_the_annotation_size(self):
        from lib.diagram import render
        size, _baseline = note_type(callout.fit(svg("center-right")))
        self.assertEqual(size, render.ANNOTATION_PX)

    def test_the_baseline_follows_the_size_so_the_words_stay_centred(self):
        """Smaller type on d2's own baseline sits low in a box whose height does not change."""
        before = note_type(svg("center-right"))[1]
        after = note_type(callout.fit(svg("center-right")))[1]
        self.assertLess(after, before)

    def test_the_box_keeps_its_height(self):
        """Which is what holds the callout's own height where every pinned figure has it."""
        out = callout.fit(svg("center-right"))
        self.assertIn('height="44"', out)
        self.assertIn('height="24"', out)


class TestTheBoxFitsTheText(CalloutCase):
    def test_the_padding_ends_up_equal_on_both_sides(self):
        """The defect: the box is cut for d2's type and filled with ours, and all of the
        difference collects on one side."""
        rect_x, rect_w, obj_x, obj_w = boxes(callout.fit(svg("center-right")))
        self.assertAlmostEqual(obj_w, 132.0 - TRIM, delta=0.01)
        self.assertAlmostEqual(obj_x - rect_x, callout.PAD, delta=0.01)
        self.assertAlmostEqual((rect_x + rect_w) - (obj_x + obj_w), callout.PAD, delta=0.01)

    def test_the_box_is_the_text_plus_two_paddings(self):
        _rect_x, rect_w, _obj_x, _obj_w = boxes(callout.fit(svg("center-right")))
        self.assertAlmostEqual(rect_w, (132.0 - TRIM) + 2 * callout.PAD, delta=0.01)

    def test_the_width_is_d2s_own_measurement_scaled_not_a_browsers(self):
        """No browser is consulted: d2 measured the note in the font it embeds, and an advance
        width scales with the type size. A seeded measurement must therefore change nothing."""
        callout._WIDTHS[NOTE] = 999.0
        with_cache = callout.fit(svg("center-right"))
        callout._WIDTHS.clear()
        self.assertEqual(callout.fit(svg("center-right")), with_cache)


class TestThePointerNeverMoves(CalloutCase):
    """It points AT the thing the note is about, so it is the one part that may not shift."""

    def test_every_anchor_keeps_its_pointer(self):
        for anchor in SHAPES:
            before = pointer(svg(anchor))
            after = pointer(callout.fit(svg(anchor)))
            self.assertEqual(before, after, anchor)

    def test_a_box_hanging_off_to_the_left_gives_way_on_its_far_side(self):
        """`center-left` puts the pointer on the box's RIGHT edge, so that edge stays."""
        rect_x, rect_w, _obj_x, _obj_w = boxes(callout.fit(svg("center-left")))
        self.assertAlmostEqual(rect_x + rect_w, -16 + 152, delta=0.01,
                               msg="the pointer edge must not move")

    def test_a_box_hanging_off_to_the_right_gives_way_on_its_far_side(self):
        rect_x, _rect_w, _obj_x, _obj_w = boxes(callout.fit(svg("center-right")))
        self.assertAlmostEqual(rect_x, 264, delta=0.01, msg="the pointer edge must not move")

    def test_a_centred_box_closes_in_from_both_sides(self):
        rect_x, rect_w, _obj_x, _obj_w = boxes(callout.fit(svg("top-center")))
        self.assertAlmostEqual(rect_x + rect_w / 2, 124 + 152 / 2, delta=0.01)

    def test_a_corner_anchored_box_keeps_the_corner_the_pointer_is_near(self):
        left_x, _w, _ox, _ow = boxes(callout.fit(svg("top-left")))
        self.assertAlmostEqual(left_x, 146, delta=0.01)
        right_x, right_w, _ox, _ow = boxes(callout.fit(svg("top-right")))
        self.assertAlmostEqual(right_x + right_w, 102 + 152, delta=0.01)


class TestItDeclines(CalloutCase):
    def test_a_group_that_is_not_the_expected_shape_is_left_alone(self):
        """A partial re-cut is worse than d2's own box: the words would keep a size the box
        was not cut for."""
        no_pointer = svg("top-left").replace(
            '<path d="M 162 2 L 170 2 L 166 10 Z" />', "")
        self.assertEqual(callout.fit(no_pointer), no_pointer)

    def test_an_svg_with_no_callout_is_returned_unchanged(self):
        plain = '<svg viewBox="0 0 10 10"><rect x="0" y="0" width="1" height="1"/></svg>'
        self.assertEqual(callout.fit(plain), plain)


class TestPriming(CalloutCase):
    def test_a_missing_browser_is_silent(self):
        """A render with no browser is a render with d2's boxes, not a failed render."""
        original = browser.text_widths

        def refuse(_html, **_kw):
            raise browser.BrowserError("no node")

        browser.text_widths = refuse
        try:
            callout.prime(["something new"])
        finally:
            browser.text_widths = original
        self.assertIsNone(callout.known("something new"))

    def test_only_unknown_notes_are_measured(self):
        """The anchor search renders the same spec 64 times; measuring per render would put a
        browser launch inside the loop that exists to need only one."""
        callout._WIDTHS["known"] = 10.0
        asked = []

        original = browser.text_widths
        browser.text_widths = lambda html, **_kw: asked.append(html) or [5.0]
        try:
            callout.prime(["known", "fresh"])
        finally:
            browser.text_widths = original
        self.assertEqual(len(asked), 1)
        self.assertIn("fresh", asked[0])
        self.assertNotIn(">known<", asked[0])

    def test_nothing_to_measure_launches_nothing(self):
        original = browser.text_widths
        browser.text_widths = lambda *_a, **_kw: self.fail("should not have been called")
        try:
            callout.prime([])
            callout.prime([None, ""])
        finally:
            browser.text_widths = original

    def test_a_short_answer_is_discarded_rather_than_misaligned(self):
        """Widths come back positionally. A list of the wrong length cannot be zipped onto the
        notes without silently giving one note another's width."""
        original = browser.text_widths
        browser.text_widths = lambda *_a, **_kw: [1.0]
        try:
            callout.prime(["a", "b"])
        finally:
            browser.text_widths = original
        self.assertIsNone(callout.known("a"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
