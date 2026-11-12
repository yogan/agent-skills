#!/usr/bin/env python3
"""Tests for the sidebar's basic layout checks — see layout.py.

Run: `python3 e2e/test_layout.py` (stdlib only).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import layout  # noqa: E402

# The file's lines and the thread box among them, its scrollbar beside them all at the right.
BODY = [" 1 -export const a = {", "  ╭──────────────────────────────────╮",
        "  │ Miriam Naming: this one exports… │", "  ╰──────────────────────────────────╯",
        " 2  b;"]
THUMB = ["▃", "█", "█", "\U0001fb82", " "]
GOOD = "\n".join([
    "── \U000f04c2 !3 Extract keys into factories ───────── ─",
    "\uf47c To fix",
    "  t2 Refetch the detail key",
    "",
    "── src/a.ts:1 · pushed ──────────────────── ✕ ─",
    *[f"{line:<44} {bar}" for line, bar in zip(BODY, THUMB)],
])


class TestProblems(unittest.TestCase):
    def test_a_sidebar_drawn_right_has_none(self):
        self.assertEqual(layout.problems(GOOD), [])
        self.assertEqual(layout.problems(""), [])

    def test_a_box_whose_bottom_is_cut_off(self):
        cut = "\n".join(GOOD.split("\n")[:8])
        self.assertTrue(any("no bottom border" in p for p in layout.problems(cut)))

    def test_a_box_an_area_clips_at_its_foot_is_no_cut(self):
        clipped = "\n".join(["── src/a.ts:1 ─────", " 1 a", "  ╭──────╮", "  │ text │", "", "── On the MR ──", "x"])
        self.assertEqual(layout.problems(clipped), [])

    def test_a_box_whose_top_is_cut_off(self):
        cut = "\n".join(l for i, l in enumerate(GOOD.split("\n")) if i != 6)
        self.assertTrue(any("with no top" in p for p in layout.problems(cut)))

    def test_a_thumb_broken_in_its_run(self):
        broken = GOOD.replace("\U0001fb82", "▃")
        self.assertTrue(any("starts in the middle" in p for p in layout.problems(broken)))

    def test_a_line_wrapped_onto_the_next_row(self):
        wrapped = GOOD.replace("\uf47c To fix", "              Supplier's", 1)
        self.assertTrue(any("wrapped" in p for p in layout.problems(wrapped)))

    def test_a_code_line_number_under_an_area_line_is_no_wrap(self):
        # The file's rows start with spaces and a line number, never a word.
        self.assertEqual(layout.problems(GOOD.replace(" 1 -export", "       9 export", 1)), [])


if __name__ == "__main__":
    unittest.main()
