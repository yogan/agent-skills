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


class FixRefsTest(unittest.TestCase):
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

    def test_a_saved_link_gives_its_start_and_version(self):
        url = "http://gitlab.test/g/p/-/merge_requests/3/diffs?diff_id=17&start_sha=abc1234def"
        self.assertEqual(fix_refs.from_diff_url(url), ("abc1234def", 17))
        self.assertIsNone(fix_refs.from_diff_url("http://gitlab.test/g/p/-/merge_requests/3"))
        self.assertIsNone(fix_refs.from_diff_url(None))


if __name__ == "__main__":
    unittest.main()
