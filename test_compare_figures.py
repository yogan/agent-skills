#!/usr/bin/env python3
"""The parts of `compare_figures.py` that decide something, held to what they decide.

Nothing here launches a browser or runs d2. What is worth pinning is not that the sheet
renders — you look at that, it is the whole point of the tool — but the three things that are
silently wrong if they drift: which figures appear and in what order, how wide a panel gets,
and whether a gate problem from the capture is visible on the sheet. A comparison sheet that
quietly drops a figure, or that scales the "after" down until a regression looks like a tidy-up,
is worse than no sheet, because it is trusted.

Run: `python3 test_compare_figures.py`
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import compare_figures as cf  # noqa: E402


def pair(*keys):
    """A (before, after) pair in which every named figure differs — the only ones a sheet
    shows, so the ordering cases have to be built from them."""
    return ({k: "before" for k in keys}, {k: "after" for k in keys})


class TestPanelOrder(unittest.TestCase):
    def test_notes_order_wins_over_alphabetical(self):
        """The figure the change is FOR comes first, whatever it is called."""
        before, after = pair("reference/arch", "repo/arch")
        order = cf.panel_order(before, after, {"repo/arch": {}, "reference/arch": {}})
        self.assertEqual(order, ["repo/arch", "reference/arch"])

    def test_unnoted_figures_still_appear_after_the_noted_ones(self):
        before, after = pair("repo/er", "repo/arch", "repo/state")
        self.assertEqual(cf.panel_order(before, after, {"repo/er": {}}),
                         ["repo/er", "repo/arch", "repo/state"])

    def test_a_figure_missing_from_either_capture_is_dropped(self):
        """Half a pair is not a comparison, and showing it as one would imply it changed."""
        before, after = pair("a", "b")
        del after["b"]
        self.assertEqual(cf.panel_order(before, after, {}), ["a"])

    def test_a_note_for_a_figure_nobody_captured_is_ignored(self):
        before, after = pair("a")
        self.assertEqual(cf.panel_order(before, after, {"ghost": {}, "a": {}}), ["a"])

    def test_figures_that_did_not_change_are_dropped(self):
        """A sheet where eight of ten panels are the same picture twice buries the two that
        are not."""
        before = {"a": "<svg/>", "b": "<svg/>"}
        after = {"a": "<svg/>", "b": "<svg id='2'/>"}
        self.assertEqual(cf.panel_order(before, after, {}), ["b"])

    def test_showing_everything_is_still_possible(self):
        before = {"a": "<svg/>", "b": "<svg/>"}
        after = {"a": "<svg/>", "b": "<svg id='2'/>"}
        self.assertEqual(cf.panel_order(before, after, {}, changed_only=False), ["a", "b"])


class TestColumn(unittest.TestCase):
    def test_the_wider_of_the_two_sets_the_column(self):
        """A change that GREW the figure has to look grown, not be scaled back to fit."""
        self.assertEqual(cf.column([(400, 100), (520, 100)]), 520 + 2 * cf.CARD_PADDING)

    def test_a_huge_figure_is_capped_and_scaled_by_the_card(self):
        self.assertEqual(cf.column([(4000, 100), (10, 10)]), cf.MAX_COL)

    def test_a_tiny_figure_still_leaves_room_for_its_annotation(self):
        self.assertEqual(cf.column([(40, 20), (40, 20)]), cf.MIN_COL)


class TestBullets(unittest.TestCase):
    def test_gate_problems_are_marked_so_they_cannot_read_as_prose(self):
        """A sheet that looks better while a gate complains must not pass for a win."""
        out = cf._bullets(["moved the label"], ["arch: CLIPPED right 12px"])
        self.assertIn('<li class="gate">arch: CLIPPED right 12px</li>', out)
        self.assertIn("<li>moved the label</li>", out)

    def test_a_side_with_nothing_to_say_says_so(self):
        self.assertIn("nothing noted", cf._bullets([], []))

    def test_note_text_is_escaped(self):
        """Notes are prose about markup and routinely contain angle brackets."""
        self.assertIn("&lt;text&gt;", cf._bullets(["<text> moved"], []))


class TestCaptureLayout(unittest.TestCase):
    """`_load` against a directory this test writes and removes, file by file."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="compare-figures-test-")
        self.original, cf.ROOT = cf.ROOT, self.tmp
        self.made = []

    def tearDown(self):
        cf.ROOT = self.original
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, tag, files, meta=None):
        directory = os.path.join(self.tmp, tag)
        os.makedirs(directory, exist_ok=True)
        for name, body in files.items():
            with open(os.path.join(directory, name), "w") as handle:
                handle.write(body)
        with open(os.path.join(directory, "meta.json"), "w") as handle:
            json.dump(meta or {}, handle)

    def test_a_capture_round_trips_to_corpus_slash_name_keys(self):
        self._write("t", {"repo.arch.svg": "<svg/>", "reference.er.svg": "<svg id='2'/>"})
        svgs, _meta = cf._load("t")
        self.assertEqual(sorted(svgs), ["reference/er", "repo/arch"])
        self.assertEqual(svgs["repo/arch"], "<svg/>")

    def test_the_gate_verdict_survives_the_round_trip(self):
        """It is read back onto the sheet, so losing it loses the only automatic warning."""
        self._write("t", {"repo.arch.svg": "<svg/>"},
                    {"repo/arch": {"size": [10, 10], "problems": ["arch: HIDDEN TEXT"]}})
        _svgs, meta = cf._load("t")
        self.assertEqual(cf._gates(meta, "repo/arch"), ["arch: HIDDEN TEXT"])

    def test_a_figure_with_no_meta_entry_reports_no_gate_problems(self):
        self.assertEqual(cf._gates({}, "repo/arch"), [])

    def test_an_unknown_tag_is_refused_rather_than_compared_against_nothing(self):
        with self.assertRaises(SystemExit):
            cf._load("never-captured")


class TestMarks(unittest.TestCase):
    """Ringing the reported flaw on the before side. Both failures this guards against draw
    something plausible rather than raising, which is why they survived a hand-rolled version:
    the ring appeared, just several times over and a few pixels off the thing it points at."""

    # A drawing shaped the way d2 emits one: an outer <svg> and a nested one holding the paths,
    # the nested one carrying its own viewBox origin.
    NESTED = ('<svg viewBox="0 0 200 100" width="200" height="100">'
              '<svg class="d2-1 d2-svg" viewBox="3 3 200 100" width="200" height="100">'
              '<path d="M 10 10 L 90 10"/></svg></svg>')

    def test_one_point_draws_one_circle(self):
        """`str.replace` with no count hits every `</svg>`, and d2 nests one — so a plain replace
        drew the ring two or three times, each in a different coordinate space."""
        self.assertEqual(cf.ring(self.NESTED, [(50, 50)]).count("<circle"), 1)

    def test_every_point_gets_its_own_circle(self):
        self.assertEqual(cf.ring(self.NESTED, [(1, 2), (3, 4), (5, 6)]).count("<circle"), 3)

    def test_the_circle_lands_in_the_space_the_paths_are_in(self):
        """Inside the NESTED svg, which is where the path coordinates live. Placed in the outer
        one it sits off by the nested viewBox's origin — close enough to look deliberate."""
        out = cf.ring(self.NESTED, [(50, 50)])
        self.assertLess(out.index("<circle"), out.index("</svg>"))
        self.assertIn('<path d="M 10 10 L 90 10"/><circle', out)

    def test_no_points_leaves_the_drawing_alone(self):
        self.assertEqual(cf.ring(self.NESTED, []), self.NESTED)

    def test_a_note_with_no_mark_rings_nothing(self):
        """The common case, and the default: a change aiming at a different arrangement has no
        single place to point at."""
        self.assertEqual(cf.points_to_mark(self.NESTED, None), [])

    def test_explicit_points_are_passed_through(self):
        self.assertEqual(cf.points_to_mark(self.NESTED, [[12, 34]]), [(12, 34)])


class TestChrome(unittest.TestCase):
    def test_both_themes_are_styled_so_a_dark_render_is_not_shown_on_a_light_page(self):
        self.assertEqual(sorted(cf.CHROME), ["dark", "light"])
        for theme, values in cf.CHROME.items():
            self.assertEqual(len(values), 5, theme)


if __name__ == "__main__":
    unittest.main(verbosity=2)
