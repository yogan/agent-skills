#!/usr/bin/env python3
"""Re-cutting a callout box to the text it holds, on d2's own output shapes.

No browser: the one thing that needs one is measuring how wide a string renders, and that is
`browser.text_widths`, which this module caches. Everything below seeds the cache and checks
the geometry, because the geometry is where this can go wrong silently — a box trimmed from the
wrong side moves the pointer off the thing the note is about, and the note then points at a
neighbour.

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

# d2's own geometry for a 20-character note, per anchor: the box, the pointer triangle and the
# foreignObject that holds the text. 152 wide with a 132 foreignObject — 10px of padding a side.
SHAPES = {
    # pointer under the box, 20px in from the LEFT corner
    "top-left": ('<rect x="12" y="-42" width="152" height="44" rx="4" ry="4"/>'
                 '<path d="M 28 2 L 36 2 L 32 10 Z"/>'
                 '<foreignObject x="22" y="-32" width="132" height="24">'),
    # pointer under the box, at its MIDDLE
    "top-center": ('<rect x="-24" y="-42" width="152" height="44" rx="4" ry="4"/>'
                   '<path d="M 48 2 L 56 2 L 52 10 Z"/>'
                   '<foreignObject x="-14" y="-32" width="132" height="24">'),
    # pointer under the box, 20px in from the RIGHT corner
    "top-right": ('<rect x="-60" y="-42" width="152" height="44" rx="4" ry="4"/>'
                  '<path d="M 68 2 L 76 2 L 72 10 Z"/>'
                  '<foreignObject x="-50" y="-32" width="132" height="24">'),
    # pointer on the box's RIGHT edge, box hanging off to the left
    "center-left": ('<rect x="-150" y="21" width="152" height="44" rx="4" ry="4"/>'
                    '<path d="M 2 39 L 2 47 L 10 43 Z"/>'
                    '<foreignObject x="-140" y="31" width="132" height="24">'),
    # pointer on the box's LEFT edge, box hanging off to the right
    "center-right": ('<rect x="102" y="21" width="152" height="44" rx="4" ry="4"/>'
                     '<path d="M 102 39 L 102 47 L 94 43 Z"/>'
                     '<foreignObject x="112" y="31" width="132" height="24">'),
}

NOTE = "the only entry point"


def svg(anchor, note=NOTE):
    body = SHAPES[anchor] + f'<div class="md"><p>{note}</p>\n</div></foreignObject>'
    return f'<svg viewBox="0 0 400 400"><g class="positioned-tooltip">{body}</g></svg>'


def boxes(out):
    """(rect x, rect width, foreignObject x, foreignObject width) after the re-cut."""
    rect = re.search(r'<rect x="([-\d.]+)" y="[-\d.]+" width="([\d.]+)"', out)
    obj = re.search(r'<foreignObject x="([-\d.]+)" y="[-\d.]+" width="([\d.]+)"', out)
    return (float(rect.group(1)), float(rect.group(2)),
            float(obj.group(1)), float(obj.group(2)))


def pointer(out):
    """The x coordinates of the pointer triangle, which must never move."""
    return [float(v) for v in
            re.search(r'<path d="M ([-\d.]+) [-\d.]+ L ([-\d.]+) [-\d.]+ '
                      r'L ([-\d.]+) [-\d.]+ Z"', out).groups()]


class CalloutCase(unittest.TestCase):
    """Every case seeds the module cache and clears it again — it is global by design."""

    def setUp(self):
        self.original = dict(callout._WIDTHS)

    def tearDown(self):
        callout._WIDTHS.clear()
        callout._WIDTHS.update(self.original)


class TestTheBoxFitsTheText(CalloutCase):
    def test_the_padding_ends_up_equal_on_both_sides(self):
        """The defect: d2 measures the note in its font, the page fills the box in another, and
        all 22px of the difference collects on one side.
        """
        callout._WIDTHS[NOTE] = 114.0
        rect_x, rect_w, obj_x, obj_w = boxes(callout.fit(svg("center-right")))
        self.assertEqual(obj_w, 114.0)
        self.assertEqual(obj_x - rect_x, callout.PAD)
        self.assertEqual((rect_x + rect_w) - (obj_x + obj_w), callout.PAD)

    def test_the_box_is_the_text_plus_two_paddings(self):
        callout._WIDTHS[NOTE] = 114.0
        _rect_x, rect_w, _obj_x, _obj_w = boxes(callout.fit(svg("center-right")))
        self.assertEqual(rect_w, 114.0 + 2 * callout.PAD)


class TestThePointerNeverMoves(CalloutCase):
    """It points AT the thing the note is about, so it is the one part that may not shift."""

    def test_every_anchor_keeps_its_pointer(self):
        callout._WIDTHS[NOTE] = 114.0
        for anchor in SHAPES:
            before = pointer(svg(anchor))
            after = pointer(callout.fit(svg(anchor)))
            self.assertEqual(before, after, anchor)

    def test_a_box_hanging_off_to_the_left_gives_way_on_its_far_side(self):
        """`center-left` puts the pointer on the box's RIGHT edge, so that edge stays."""
        callout._WIDTHS[NOTE] = 114.0
        rect_x, rect_w, _obj_x, _obj_w = boxes(callout.fit(svg("center-left")))
        self.assertEqual(rect_x + rect_w, -150 + 152, "the pointer edge must not move")

    def test_a_box_hanging_off_to_the_right_gives_way_on_its_far_side(self):
        callout._WIDTHS[NOTE] = 114.0
        rect_x, _rect_w, _obj_x, _obj_w = boxes(callout.fit(svg("center-right")))
        self.assertEqual(rect_x, 102, "the pointer edge must not move")

    def test_a_centred_box_closes_in_from_both_sides(self):
        callout._WIDTHS[NOTE] = 114.0
        rect_x, rect_w, _obj_x, _obj_w = boxes(callout.fit(svg("top-center")))
        self.assertAlmostEqual(rect_x + rect_w / 2, -24 + 152 / 2, delta=0.01)

    def test_a_corner_anchored_box_keeps_the_corner_the_pointer_is_near(self):
        callout._WIDTHS[NOTE] = 114.0
        left_x, _w, _ox, _ow = boxes(callout.fit(svg("top-left")))
        self.assertEqual(left_x, 12)
        right_x, right_w, _ox, _ow = boxes(callout.fit(svg("top-right")))
        self.assertEqual(right_x + right_w, -60 + 152)


class TestItDeclines(CalloutCase):
    def test_an_unmeasured_note_is_left_exactly_as_d2_drew_it(self):
        """Every fast test in the suite renders without priming, and must get d2's own box.
        Estimating the width from a character count was the alternative and is the wrong risk:
        the text is `nowrap` in an `overflow:visible` box, so an underestimate spills the note
        out over the drawing rather than clipping it.
        """
        self.assertEqual(callout.fit(svg("top-left")), svg("top-left"))

    def test_a_note_wider_than_its_box_is_left_alone(self):
        """Growing would paper over a note that is already overflowing; the fix is fewer
        words, and the clipping gate says so."""
        callout._WIDTHS[NOTE] = 200.0
        self.assertEqual(callout.fit(svg("top-left")), svg("top-left"))

    def test_a_trim_too_small_to_see_is_not_made(self):
        callout._WIDTHS[NOTE] = 132.0 - callout.MIN_TRIM / 2
        self.assertEqual(callout.fit(svg("top-left")), svg("top-left"))

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
