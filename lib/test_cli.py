#!/usr/bin/env python3
"""Tests for lib/cli.py's die() — see its module docstring for why this one
tiny helper gets its own file instead of living in gitlab.py.

Run: `python3 lib/test_cli.py` (stdlib only).
"""
import io
import os
import sys
import unittest
from contextlib import redirect_stderr

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.cli import die


class TestDie(unittest.TestCase):
    def test_prints_to_stderr_and_exits_nonzero(self):
        buf = io.StringIO()
        with redirect_stderr(buf), self.assertRaises(SystemExit) as ctx:
            die("something went wrong")
        self.assertEqual(buf.getvalue(), "error: something went wrong\n")
        self.assertNotEqual(ctx.exception.code, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
