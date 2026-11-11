#!/usr/bin/env python3
"""check_commits.py on a throwaway repository whose middle commit is broken: each commit is
checked on its own, the broken one is named with what failed, and nothing is left behind.

Run: `python3 test_check_commits.py`
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import check_commits as C  # noqa: E402


def sh(cwd, *args):
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    assert r.returncode == 0, (args, r.stdout, r.stderr)
    return r.stdout.strip()


class TestEachCommitAlone(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = os.path.join(self._tmp.name, "repo")
        os.makedirs(self.repo)
        sh(self.repo, "git", "init", "-q", "-b", "main")
        for k, v in (("user.name", "Test"), ("user.email", "test@example.invalid")):
            sh(self.repo, "git", "config", k, v)
        self.commits = []
        # The second commit uses a name the third defines: broken alone, fine on top.
        for name, text in (("a.txt", "ok\n"), ("uses.txt", "needs defined.txt\n"),
                           ("defined.txt", "here\n")):
            with open(os.path.join(self.repo, name), "w") as fh:
                fh.write(text)
            sh(self.repo, "git", "add", "-A")
            sh(self.repo, "git", "commit", "-q", "-m", f"add {name}")
            self.commits.append(sh(self.repo, "git", "rev-parse", "HEAD"))
        self.root = tempfile.mkdtemp(dir=self._tmp.name)
        self.steps = [("lint", ["sh", "-c", "test ! -e uses.txt || test -e defined.txt "
                                "|| { echo 'uses.txt: defined.txt is missing'; exit 1; }"])]

    def test_only_the_commit_broken_alone_fails_and_says_what_failed(self):
        results = [C.check(c, self.root, self.steps, self.repo) for c in self.commits]
        self.assertEqual([ok for ok, _, _ in results], [True, False, True])
        ok, report, log = results[1]
        self.assertEqual(report, ["lint failed:", "  uses.txt: defined.txt is missing"])
        self.assertTrue(os.path.isfile(log))

    def test_no_worktree_is_left_behind(self):
        for c in self.commits:
            C.check(c, self.root, self.steps, self.repo)
        self.assertEqual(sh(self.repo, "git", "worktree", "list").count("\n"), 0)

    def test_the_ruff_it_runs_is_the_one_the_hook_pins(self):
        with open(os.path.join(C.HERE, ".githooks", "pre-commit")) as fh:
            self.assertIn(f"RUFF={C.ruff_version()}\n", fh.read())

    def test_a_worktree_gets_the_mods_engine_types(self):
        types = os.path.join(self.repo, "mods", "m", ".claude-plugin", "types")
        os.makedirs(types)
        open(os.path.join(types, "tsconfig.json"), "w").close()
        dest = os.path.join(self._tmp.name, "wt")
        os.makedirs(os.path.join(dest, "mods", "m", ".claude-plugin"))
        C.copy_untracked(dest, self.repo)
        self.assertTrue(os.path.isfile(os.path.join(dest, "mods", "m", ".claude-plugin", "types",
                                                    "tsconfig.json")))
        shutil.rmtree(dest)


if __name__ == "__main__":
    unittest.main(verbosity=2)
