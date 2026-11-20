#!/usr/bin/env python3
"""Tests for a pushed fix kept in git — see fix_refs.py. A real repository in a temporary
directory, removed with the test.

Run: `python3 lib/test_fix_refs.py` (stdlib and git only).
"""
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib import fix_refs  # noqa: E402


def git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True,
                          check=True).stdout.strip()


class TestFixRefs(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.repo = self._dir.name
        git(self.repo, "init", "-q")
        # The repository's own setting must not leave the patch without its prefixes.
        git(self.repo, "config", "diff.noprefix", "true")
        for k, v in (("user.name", "T"), ("user.email", "t@example.invalid"), ("commit.gpgsign", "false")):
            git(self.repo, "config", k, v)
        self.write("src/old_name.ts", "one\ntwo\nthree\n")
        self.write("notes.md", "a\n")
        self.before = self.commit("first")
        git(self.repo, "mv", "src/old_name.ts", "src/new_name.ts")
        self.write("src/new_name.ts", "one\nTWO\nthree\n")
        self.write("notes.md", "a\nb\n")
        self.after = self.commit("the fix")

    def tearDown(self):
        self._dir.cleanup()

    def test_a_fix_limited_to_some_files_leaves_the_others_out(self):
        """A fix that came with a rebase shows the MR's own files alone."""
        self.assertEqual([p for p, _, _ in fix_refs.stat(self.repo, (self.before, self.after), ["notes.md"])],
                         ["notes.md"])
        self.assertEqual(len(fix_refs.stat(self.repo, (self.before, self.after))), 2)

    def write(self, path, text):
        full = os.path.join(self.repo, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as fh:
            fh.write(text)

    def commit(self, msg):
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", msg)
        return git(self.repo, "rev-parse", "HEAD")

    def test_kept_ends_come_back_by_topic(self):
        self.assertTrue(fix_refs.keep(self.repo, "rework-mr", 3, "t1", self.before, self.after))
        self.assertEqual(fix_refs.kept(self.repo, "rework-mr", 3), {"t1": (self.before, self.after)})
        # Another MR's, or another skill's, are not this one's.
        self.assertEqual(fix_refs.kept(self.repo, "rework-mr", 4), {})
        self.assertEqual(fix_refs.kept(self.repo, "review-mr", 3), {})

    def test_a_topics_pushes_are_kept_beside_its_one_fix(self):
        fix_refs.keep(self.repo, "rework-mr", 3, "t1", self.before, self.after)
        fix_refs.keep(self.repo, "rework-mr", 3, "t1/push2", self.after, self.before)
        self.assertEqual(fix_refs.kept(self.repo, "rework-mr", 3),
                         {"t1": (self.before, self.after), "t1/push2": (self.after, self.before)})

    def test_the_ref_holds_a_commit_no_branch_reaches(self):
        fix_refs.keep(self.repo, "rework-mr", 3, "t1", self.before, self.after)
        # A force push leaves the old head on no branch: the ref is what keeps it.
        git(self.repo, "reset", "-q", "--hard", self.before)
        self.assertIn(self.after, git(self.repo, "for-each-ref", "--format=%(objectname)"))

    def test_a_commit_git_does_not_have_keeps_nothing(self):
        self.assertFalse(fix_refs.keep(self.repo, "rework-mr", 3, "t1", self.before, "0" * 40))
        self.assertEqual(fix_refs.kept(self.repo, "rework-mr", 3), {})

    def test_the_patch_has_its_prefixes_and_the_rename(self):
        text = fix_refs.patch(self.repo, (self.before, self.after))
        self.assertIn("rename from src/old_name.ts", text)
        self.assertIn("+++ b/src/new_name.ts", text)
        self.assertEqual(sorted(fix_refs.stat(self.repo, (self.before, self.after))),
                         [("notes.md", 1, 0), ("src/new_name.ts", 1, 1)])

    def test_drop_removes_the_mrs_fixes_only(self):
        fix_refs.keep(self.repo, "rework-mr", 3, "t1", self.before, self.after)
        fix_refs.keep(self.repo, "rework-mr", 3, "t2", self.before, self.after)
        fix_refs.keep(self.repo, "rework-mr", 4, "t1", self.before, self.after)
        self.assertEqual(fix_refs.drop(self.repo, "rework-mr", 3), 4)
        self.assertEqual(fix_refs.kept(self.repo, "rework-mr", 3), {})
        self.assertEqual(set(fix_refs.kept(self.repo, "rework-mr", 4)), {"t1"})

    def rebase_with_a_fix(self):
        """The target branch moves (a docs change, and a line of a file the branch changes
        too), and the branch is rebased onto it and fixed in one push: (old head, target,
        new head)."""
        old = self.after
        git(self.repo, "checkout", "-q", "-b", "target", self.before)
        self.write("README.md", "docs\n")
        self.write("src/old_name.ts", "zero\none\ntwo\nthree\n")
        onto = self.commit("docs and a line from the target branch")
        git(self.repo, "checkout", "-q", "-b", "branch", old)
        git(self.repo, "rebase", "-q", "target")
        self.write("src/new_name.ts", "zero\none\nTWO fixed\nthree\n")
        return old, onto, self.commit("the fix, squashed into its commit")

    def test_a_push_that_came_with_a_rebase_keeps_the_fix_alone(self):
        old, onto, new = self.rebase_with_a_fix()
        # Without the target branch's base, the rebase's changes are in it.
        fix_refs.keep(self.repo, "rework-mr", 3, "t1/push1", old, new)
        self.assertIn("README.md", [p for p, _, _ in fix_refs.stat(
            self.repo, fix_refs.kept(self.repo, "rework-mr", 3)["t1/push1"])])
        self.assertTrue(fix_refs.keep(self.repo, "rework-mr", 3, "t1/push1", old, new, onto=onto))
        ends = fix_refs.kept(self.repo, "rework-mr", 3)["t1/push1"]
        self.assertEqual(fix_refs.stat(self.repo, ends), [("src/new_name.ts", 1, 1)])
        self.assertIn("-TWO\n+TWO fixed", fix_refs.patch(self.repo, ends))
        # The same commit every time: a later keep finds the fix as kept.
        self.assertEqual(fix_refs.rebased(self.repo, old, onto), ends[0])

    def test_a_push_with_no_rebase_keeps_its_own_start(self):
        self.assertTrue(fix_refs.keep(self.repo, "rework-mr", 3, "t1", self.before, self.after,
                                      onto=self.before))
        self.assertEqual(fix_refs.kept(self.repo, "rework-mr", 3), {"t1": (self.before, self.after)})

    def test_a_rebase_that_conflicts_keeps_the_head_before_it(self):
        old = self.after
        git(self.repo, "checkout", "-q", "-b", "target", self.before)
        self.write("src/old_name.ts", "one\ndeux\nthree\n")
        onto = self.commit("the same line, changed otherwise")
        self.assertEqual(fix_refs.rebased(self.repo, old, onto), old)

    def test_a_saved_link_gives_its_start_and_version(self):
        url = "http://gitlab.test/g/p/-/merge_requests/3/diffs?diff_id=17&start_sha=abc1234def"
        self.assertEqual(fix_refs.from_diff_url(url), ("abc1234def", 17))
        self.assertIsNone(fix_refs.from_diff_url("http://gitlab.test/g/p/-/merge_requests/3"))
        self.assertIsNone(fix_refs.from_diff_url(None))


if __name__ == "__main__":
    unittest.main()
