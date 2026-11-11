#!/usr/bin/env python3
"""Tests for a diff's size as every skill writes it — see diffsize.py.

Run: `python3 lib/test_diffsize.py` (stdlib only).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib import diffsize  # noqa: E402


class TestText(unittest.TestCase):
    def test_the_form(self):
        self.assertEqual(diffsize.text(28, 1136, 1046), " 28 files +1136 −1046")

    def test_one_file(self):
        self.assertEqual(diffsize.text(1, 0, 2), " 1 file +0 −2")

    def test_of_a_stat_sums_its_files(self):
        self.assertEqual(diffsize.of_stat([("a", 2, 1), ("b", 1, 0)]), " 2 files +3 −1")


class TestCounts(unittest.TestCase):
    def test_rows_line_up_on_the_removed_count(self):
        rows = [diffsize.counts(a, r, 2) for a, r in ((12, 1), (0, 2))]
        self.assertEqual(rows, ["+12 −1", "+0  −2"])
        self.assertEqual(rows[0].index("−"), rows[1].index("−"))


if __name__ == "__main__":
    unittest.main()
