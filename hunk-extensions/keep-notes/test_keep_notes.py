#!/usr/bin/env python3
"""Runs the keep-notes Hunk extension's own tests (`bun test`) as part of the repo's suite.

Run: `python3 hunk-extensions/keep-notes/test_keep_notes.py`. The extension is TypeScript,
tested by bun, which is the runtime Hunk itself loads extensions in; this file is the bridge
that lets `run_tests.py` see those tests. Without bun the case says so as a skip rather
than passing. The bun tests write only inside a temporary directory of their own and remove
exactly that directory.
"""
import os
import shutil
import subprocess
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))


class KeepNotesTests(unittest.TestCase):
    def test_the_extensions_own_tests_pass(self):
        if not shutil.which("bun"):
            self.skipTest("bun is not installed: the extension's tests cannot run here")
        r = subprocess.run(["bun", "test", "./src"], cwd=HERE, capture_output=True,
                           text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
