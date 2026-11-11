#!/usr/bin/env python3
"""fails_without.py on a file of its own: the fix is taken out for one run and put back."""
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fails_without as F  # noqa: E402

# Fails (exit 1) unless the file says "fixed".
CHECK = [sys.executable, "-c", "import sys; sys.exit(0 if 'fixed' in open(sys.argv[1]).read() else 1)"]


class TestFailsWithout(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = os.path.join(self._tmp.name, "x.py")
        with open(self.path, "w") as fh:
            fh.write("value = 'fixed'\n")

    def run_cli(self, fixed, unfixed, cmd):
        with redirect_stdout(io.StringIO()), mock.patch("sys.stderr", io.StringIO()):
            return F.main([self.path, fixed, unfixed, "--", *cmd, self.path])

    def read(self):
        with open(self.path) as fh:
            return fh.read()

    def test_a_test_that_fails_without_the_fix_holds_and_the_fix_is_back(self):
        self.assertEqual(self.run_cli("'fixed'", "'broken'", CHECK), 0)
        self.assertEqual(self.read(), "value = 'fixed'\n")

    def test_a_test_that_passes_anyway_proves_nothing(self):
        self.assertEqual(self.run_cli("value", "name", CHECK), 1)
        self.assertEqual(self.read(), "value = 'fixed'\n")

    def test_a_fix_that_is_not_there_once_is_not_tried(self):
        self.assertEqual(self.run_cli("absent", "x", CHECK), 2)

    def test_the_fix_is_back_even_when_the_run_breaks(self):
        with mock.patch.object(F.subprocess, "run", side_effect=KeyboardInterrupt), \
                self.assertRaises(KeyboardInterrupt):
            F.main([self.path, "'fixed'", "'broken'", "--", "true"])
        self.assertEqual(self.read(), "value = 'fixed'\n")


if __name__ == "__main__":
    unittest.main()
