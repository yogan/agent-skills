#!/usr/bin/env python3
"""Tests for screenshot.py's reading of a capture: cells, their styles, the sidebar's crop.
No browser: drawing the page is the browser's, and only reading the capture is ours."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import screenshot as S  # noqa: E402


class Cells(unittest.TestCase):
    def test_a_colour_holds_until_reset(self):
        row = S.cells("a\x1b[38;2;1;2;3mbc\x1b[0md")[0]
        self.assertEqual([(c[0], c[1].get("fg")) for c in row],
                         [("a", None), ("b", "#010203"), ("c", "#010203"), ("d", None)])

    def test_the_palette_and_its_bright_half(self):
        row = S.cells("\x1b[31ma\x1b[94mb\x1b[38;5;196mc")[0]
        self.assertEqual([c[1]["fg"] for c in row], [S.PALETTE16[1], S.PALETTE16[12], "#ff0000"])

    def test_dim_and_bold_end_together_as_the_terminal_ends_them(self):
        row = S.cells("\x1b[1;2ma\x1b[22mb")[0]
        self.assertEqual((row[0][1], row[1][1]), ({"bold": True, "faint": True}, {}))

    def test_a_link_leaves_only_its_text(self):
        self.assertEqual("".join(c[0] for c in S.cells("\x1b]8;id=1;http://x\x1b\\!3\x1b]8;;\x1b\\")[0]), "!3")

    def test_a_wide_character_takes_two_cells(self):
        row = S.cells("中a")[0]
        self.assertEqual((row[0][0], row[1], row[2][0]), ("中", None, "a"))

    def test_a_nerd_font_symbol_takes_one(self):
        self.assertEqual(len(S.cells("\U000f084d")[0]), 1)


class Crop(unittest.TestCase):
    SCREEN = "\n".join(["chat" + " " * 16 + "│" + "panel"] * 28 + ["─" * 40, "❯ prompt"])

    def test_the_sidebar_is_right_of_the_divider_and_stops_where_it_does(self):
        rows = S.crop(S.cells(self.SCREEN), "panel")
        self.assertEqual(len(rows), 28)
        self.assertEqual("".join(c[0] for c in rows[0]), "panel")

    def test_the_chat_is_left_of_it(self):
        self.assertEqual("".join(c[0] for c in S.crop(S.cells(self.SCREEN), "main")[0]).rstrip(), "chat")


if __name__ == "__main__":
    unittest.main()
