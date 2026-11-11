#!/usr/bin/env python3
"""lib/glyphs.py against a table of its own: names are looked up, never fetched here."""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib import glyphs  # noqa: E402


class TestLookup(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        cache = os.path.join(self._tmp.name, "glyphnames.json")
        with open(cache, "w") as fh:
            json.dump({"METADATA": {"version": glyphs.VERSION},
                       "md-check": {"char": "\U000f012c", "code": "f012c"},
                       "md-wrench": {"char": "\U000f05b7", "code": "f05b7"}}, fh)
        p = mock.patch.object(glyphs, "CACHE", cache)
        p.start()
        self.addCleanup(p.stop)

    def run_cli(self, *argv):
        out = io.StringIO()
        with redirect_stdout(out), mock.patch("sys.stderr", io.StringIO()):
            code = glyphs.main(list(argv))
        return code, out.getvalue()

    def test_a_name_prints_its_glyph_and_codepoint(self):
        self.assertEqual(self.run_cli("md-check"), (0, "\U000f012c  md-check  \\u{F012C}\n"))

    def test_an_unknown_name_fails(self):
        self.assertEqual(self.run_cli("md-nope")[0], 1)

    def test_a_search_lists_every_name_containing_the_word(self):
        code, out = self.run_cli("--search", "wrench")
        self.assertEqual((code, out.count("\n")), (0, 1))
        self.assertIn("md-wrench", out)


if __name__ == "__main__":
    unittest.main()
