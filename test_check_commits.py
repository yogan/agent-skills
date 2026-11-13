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

    def test_a_step_is_told_which_commit_it_checks(self):
        """The tests step runs what that commit's own change reaches: `{commit}^` names its
        parent."""
        steps = [("say", ["sh", "-c", 'test "$0" = "%s^" || exit 1' % self.commits[1], "{commit}^"])]
        ok, report, _ = C.check(self.commits[1], self.root, steps, self.repo)
        self.assertTrue(ok, report)

    def test_the_tests_step_is_the_commit_level_not_the_whole_suite(self):
        tests = dict(C.checks(1))["tests"]
        self.assertIn("--changed-since={commit}^", tests)
        self.assertNotIn("--force", tests)

    def test_a_commit_whose_runner_predates_the_selection_gets_the_whole_suite(self):
        old = os.path.join(self._tmp.name, "old")
        new = os.path.join(self._tmp.name, "new")
        for tree, text in ((old, "args = sys.argv  # --changed only\n"),
                           (new, 'if a.startswith("--changed-since="):\n')):
            os.makedirs(tree)
            with open(os.path.join(tree, "run_tests.py"), "w") as fh:
                fh.write(text)
        self.assertFalse(C.runner_selects(old))
        self.assertTrue(C.runner_selects(new))

    def test_a_commit_that_passed_today_is_remembered_and_only_today(self):
        rec = os.path.join(self._tmp.name, "passed.json")
        self.assertEqual(C.passed_today(rec), set())
        C.record_passes(["a1"], rec)
        C.record_passes(["b2"], rec)
        self.assertEqual(C.passed_today(rec), {"a1", "b2"})
        with open(rec) as fh:
            old = fh.read().replace(C.time.strftime("%Y-%m-%d"), "2000-01-01")
        with open(rec, "w") as fh:
            fh.write(old)
        self.assertEqual(C.passed_today(rec), set())

    def test_no_worktree_is_left_behind(self):
        for c in self.commits:
            C.check(c, self.root, self.steps, self.repo)
        self.assertEqual(sh(self.repo, "git", "worktree", "list").count("\n"), 0)

    def run_folder(self, pid):
        root = tempfile.mkdtemp(prefix=C.PREFIX, dir=self._tmp.name)
        with open(os.path.join(root, C.OWNER), "w") as fh:
            fh.write(str(pid))
        tree = os.path.join(root, "tree")
        sh(self.repo, "git", "worktree", "add", "-q", "--detach", tree, self.commits[0])
        return root, tree

    def test_a_run_killed_outright_is_cleaned_up_by_the_next_and_a_live_one_left_alone(self):
        gone = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"],
                              capture_output=True, text=True).stdout.strip()
        dead_root, dead = self.run_folder(int(gone))
        _, live = self.run_folder(os.getpid())
        C.sweep(self.repo, self._tmp.name)
        trees = sh(self.repo, "git", "worktree", "list")
        self.assertNotIn(dead, trees)
        self.assertFalse(os.path.exists(dead_root))           # nothing but its record was left
        self.assertIn(os.path.realpath(live), {os.path.realpath(ln.split()[0]) for ln in trees.splitlines()})

    def test_a_stopped_run_removes_its_worktrees(self):
        root, tree = self.run_folder(0)
        code = (f"import sys, time; sys.path.insert(0, {C.HERE!r}); import check_commits as C; "
                f"C.on_stop({self.repo!r}, {root!r}); print('ready', flush=True); time.sleep(30)")
        p = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
        self.assertEqual(p.stdout.readline().strip(), "ready")
        p.terminate()
        self.assertEqual(p.wait(timeout=10), 128 + 15)
        p.stdout.close()
        self.assertNotIn(tree, sh(self.repo, "git", "worktree", "list"))
        self.assertFalse(os.path.exists(root))

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
