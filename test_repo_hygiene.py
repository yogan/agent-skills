#!/usr/bin/env python3
"""Repo-wide conventions that are cheap to check and expensive to notice by hand.

Right now that is one thing: what tests are allowed to do to the filesystem. Tests here do
write real files — `test_visualize.py` covers a CLI whose entire contract is "write a file
and print its path", so there is nothing to test without one — and the rule is not "don't",
it is:

    a test removes exactly the files it created, and never a pattern.

Both halves have been broken already. `test_findings.py` called `tempfile.mkdtemp()` and
removed nothing, leaking three directories per suite run until 460 had piled up. And the
tempting fix for the other half — sweeping `/tmp/*diagram*` — would have deleted real
`visualize.py` output, because the CLI writes there by design and its files are
indistinguishable from a test's unless the test gives itself a name of its own.

Run: `python3 test_repo_hygiene.py`
"""
import os
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

REPO_ROOT = Path(__file__).resolve().parent
TESTS = [p for p in REPO_ROOT.rglob("test_*.py") if "__pycache__" not in p.parts]

# Any of these means the file has taken responsibility for what it created.
CLEANS_UP = ("addCleanup", "rmtree", "tearDown", ".cleanup()", "os.unlink", "os.remove")


class TestTemporaryFilesAreCleanedUp(unittest.TestCase):
    def test_every_mkdtemp_is_paired_with_a_cleanup(self):
        """`TemporaryDirectory` cleans itself up; bare `mkdtemp` does not, and that is the
        one that leaked."""
        guilty = []
        for path in TESTS:
            text = path.read_text(encoding="utf-8")
            if "mkdtemp(" in text and not any(token in text for token in CLEANS_UP):
                guilty.append(str(path.relative_to(REPO_ROOT)))
        self.assertEqual(guilty, [], "mkdtemp() with nothing removing it")

    def test_every_delete_false_temp_file_is_unlinked(self):
        """`delete=False` is the other way to opt out of automatic cleanup."""
        guilty = []
        for path in TESTS:
            text = path.read_text(encoding="utf-8")
            if "delete=False" in text and not any(t in text for t in ("unlink", "remove")):
                guilty.append(str(path.relative_to(REPO_ROOT)))
        self.assertEqual(guilty, [], "delete=False with nothing unlinking it")


class TestNoTestDeletesByPattern(unittest.TestCase):
    """The dangerous direction. A leak wastes disk; a pattern delete destroys real work."""

    # `rmtree(x)` / `unlink(x)` where x is built by globbing or by joining a wildcard.
    SWEEPS = re.compile(r"(rmtree|unlink|remove)\s*\(\s*[^)]*(glob|\*)", re.S)

    def test_no_test_removes_a_glob(self):
        guilty = []
        for path in TESTS:
            for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if self.SWEEPS.search(line):
                    guilty.append(f"{path.relative_to(REPO_ROOT)}:{line_no}")
        self.assertEqual(guilty, [], "a test deleting by pattern can hit real output")

    def test_no_test_removes_anything_under_the_shared_tmp_by_name(self):
        """`/tmp/<date>-diagram-<slug>.svg` is where the CLI writes for real. A test may
        delete such a file only when the slug is its own — which is checked by the one test
        that does it carrying a title no real diagram would have."""
        allowed = {"skills/visualize/scripts/test_visualize.py"}
        guilty = []
        for path in TESTS:
            rel = str(path.relative_to(REPO_ROOT))
            if rel in allowed:
                continue
            text = path.read_text(encoding="utf-8")
            if re.search(r"(unlink|remove|rmtree)\s*\(\s*[\"']?/tmp/", text):
                guilty.append(rel)
        self.assertEqual(guilty, [])


class TestTheSuiteLeavesNothingBehind(unittest.TestCase):
    """The static checks above can be satisfied and still leak, so this measures.

    The measurement runs the target with a TMPDIR of its own and then asks whether that
    directory is empty. Diffing the SHARED temp dir instead was the obvious first attempt and
    it was wrong in a way worth recording: the runner executes test files concurrently, so
    the diff picked up directories other files had legitimately created and in flight, and
    the check failed at random. A test about isolation has to be isolated itself.

    The sandbox also makes this precise rather than heuristic — anything left behind is the
    target's, whatever it chose to name it.
    """

    # Left in TMPDIR by tools we shell out to, not by us. Listed by name rather than skipped
    # by a loose pattern, so anything NEW that appears fails the check and has to be looked
    # at — including, one day, a leak of our own with an unfamiliar name.
    NOT_OURS = {"node-compile-cache"}

    def _leftovers_after(self, rel):
        import subprocess
        import tempfile

        with tempfile.TemporaryDirectory() as sandbox:
            proc = subprocess.run(
                [sys.executable, str(REPO_ROOT / rel)], capture_output=True, text=True,
                env={**os.environ, "TMPDIR": sandbox})
            self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
            return sorted(p.name for p in Path(sandbox).iterdir()
                          if p.name not in self.NOT_OURS)

    def test_the_file_that_leaked_leaves_its_temp_dir_empty(self):
        """Only this one file is re-run, and the choice is a cost decision worth stating.

        Measuring `test_visualize.py` the same way was the first version and it doubled the
        whole suite — 14s to 27s — because the check re-ran a 10s file to learn something its
        own `tearDownModule` already exercises on every ordinary run. A guard that costs more
        than the bug it prevents gets switched off. This file is the one with a history, it
        runs in under a second, and everything else is covered statically above.
        """
        self.assertEqual(
            self._leftovers_after("skills/review-mr/scripts/test_findings.py"), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
