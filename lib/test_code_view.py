import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib import code_view  # noqa: E402


class TestCarry(unittest.TestCase):
    """A thread's line carried through the diff since the commit it was posted on."""

    def test_lines_before_a_change_stay(self):
        self.assertEqual(code_view.carry("@@ -10,2 +10,3 @@\n", 4), (4, [10, 11, 12]))

    def test_lines_after_a_change_move_by_its_size(self):
        self.assertEqual(code_view.carry("@@ -10,2 +10,3 @@\n", 20)[0], 21)
        self.assertEqual(code_view.carry("@@ -3,0 +4,2 @@\n", 9)[0], 11)     # insertion
        self.assertEqual(code_view.carry("@@ -3,2 +2,0 @@\n", 9)[0], 7)      # deletion

    def test_a_line_inside_a_rewrite_lands_on_its_first_new_line(self):
        self.assertEqual(code_view.carry("@@ -10,2 +12,3 @@\n", 11)[0], 12)

    def test_a_line_inside_a_deletion_lands_after_it(self):
        self.assertEqual(code_view.carry("@@ -10,2 +9,0 @@\n", 11)[0], 10)


class TestTouched(unittest.TestCase):
    """Whether a push changed the lines a comment is on, read off GitLab's diff of one file
    (context lines included), and where the lines went if it only moved them."""

    REWRITE = "@@ -8,7 +8,8 @@ def f():\n a8\n a9\n a10\n-a11\n+b11\n+b12\n a12\n a13\n a14\n"

    def test_a_rewritten_line_of_the_comment_s_is_a_hit(self):
        self.assertEqual(code_view.touched(self.REWRITE, 10, 11), (True, 10, 11))

    def test_context_lines_are_not_changes_and_move_with_the_hunk(self):
        self.assertEqual(code_view.touched(self.REWRITE, 13, 14), (False, 14, 15))
        self.assertEqual(code_view.touched(self.REWRITE, 3, 4), (False, 3, 4))
        self.assertEqual(code_view.touched(self.REWRITE, 20, 21), (False, 21, 22))

    def test_an_insertion_counts_inside_the_stretch_only(self):
        add = "@@ -5,0 +6,2 @@\n+x\n+y\n"
        self.assertTrue(code_view.touched(add, 5, 6)[0])
        self.assertEqual(code_view.touched(add, 6, 7), (False, 8, 9))
        self.assertEqual(code_view.touched(add, 3, 5), (False, 3, 5))

    def test_a_file_the_push_left_alone_keeps_its_lines(self):
        self.assertEqual(code_view.touched(None, 4, 6), (False, 4, 6))


class TestView(unittest.TestCase):
    def repo(self, before, after):
        """A repository whose one commit holds `before` as t.md, `after` on disk; its sha."""
        repo = tempfile.mkdtemp(prefix="test-code-view-")
        self.addCleanup(subprocess.run, ["rm", "-rf", repo])
        git = ["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@t"]
        subprocess.run(["git", "init", "-q", repo], check=True)
        with open(os.path.join(repo, "t.md"), "w") as f:
            f.write("".join(f"{x}\n" for x in before))
        subprocess.run(git + ["add", "."], check=True)
        subprocess.run(git + ["commit", "-qm", "x"], check=True)
        with open(os.path.join(repo, "t.md"), "w") as f:
            f.write("".join(f"{x}\n" for x in after))
        sha = subprocess.run(git + ["rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        return repo, sha

    def test_reads_the_file_on_disk_and_carries_the_line_from_a_commit(self):
        repo, sha = self.repo([f"line{i}" for i in range(1, 11)],
                              ["new0"] + [f"line{i}" for i in range(1, 11)])
        v = code_view.view(repo, "t.md", 5, since=sha)
        self.assertEqual((v["line"], v["changed"], v["moved"], v["at"]), (6, [1], True, [6, 6]))
        self.assertEqual(code_view.new_lines(v)[5], "line5")
        self.assertIsNone(code_view.view(repo, "missing.ts", 1))

    def test_the_mr_s_removed_lines_stand_where_they_were_with_both_numbers(self):
        repo, base = self.repo(list("abcdef"), list("aBcefg"))
        rows = code_view.view(repo, "t.md", None, base=base)["rows"]
        self.assertEqual([r[:3] for r in rows], [
            [" ", 1, 1], ["-", 2, None], ["+", None, 2], [" ", 3, 3], ["-", 4, None],
            [" ", 5, 4], [" ", 6, 5], ["+", None, 6]])

    def test_a_comment_on_a_removed_line_is_on_that_line_not_the_one_now_numbered_so(self):
        repo, base = self.repo(list("abcdef"), list("aBcefg"))
        v = code_view.view(repo, "t.md", 4, side="old", base=base)
        self.assertEqual(v["at"], [5, 5])
        self.assertEqual(v["rows"][4][3], "d")

    def test_the_comment_s_rows_are_a_hunk_of_their_own_named_in_the_fence(self):
        repo, base = self.repo(list("abcdef"), list("aBcefg"))
        v = code_view.view(repo, "t.md", 4, side="old", base=base)
        self.assertEqual(code_view.diff_fence(v, 1, len(v["rows"])), [
            "```diff path=t.md at=2",
            "@@ -1,3 +1,3 @@", " a", "-b", "+B", " c",
            "@@ -4,1 +3,0 @@", "-d",
            "@@ -5,2 +4,3 @@", " e", " f", "+g",
            "```"])

    def git(self, repo, *args):
        subprocess.run(["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@t", *args],
                       check=True, capture_output=True)

    def test_a_file_the_mr_adds_or_removes_whole_says_so(self):
        repo, base = self.repo(list("abc"), list("abc"))
        with open(os.path.join(repo, "n.md"), "w") as f:
            f.write("x\ny\n")
        self.git(repo, "add", "n.md")
        self.git(repo, "rm", "-q", "t.md")
        added = code_view.view(repo, "n.md", 1, base=base)
        self.assertEqual((added["whole"], [r[0] for r in added["rows"]]), ("added", ["+", "+"]))
        removed = code_view.view(repo, "t.md", 2, side="old", base=base)
        self.assertEqual((removed["whole"], [r[0] for r in removed["rows"]]), ("removed", ["-"] * 3))

    def test_a_renamed_file_is_no_new_file_and_marks_only_what_changed_in_it(self):
        lines = [f"line {i} of a file long enough for git to see the rename" for i in range(1, 9)]
        repo, base = self.repo(lines, lines)
        self.git(repo, "mv", "t.md", "moved.md")
        with open(os.path.join(repo, "moved.md"), "a") as f:
            f.write("one more\n")
        self.git(repo, "add", "moved.md")
        v = code_view.view(repo, "moved.md", 1, base=base)
        self.assertIsNone(v["whole"])
        self.assertEqual([r[0] for r in v["rows"]], [" "] * 8 + ["+"])

    def test_a_file_only_renamed_or_made_executable_shows_its_lines_unmarked(self):
        lines = [f"line {i} of a file long enough for git to see the rename" for i in range(1, 4)]
        repo, base = self.repo(lines, lines)
        self.git(repo, "mv", "t.md", "moved.md")
        v = code_view.view(repo, "moved.md", 2, base=base)
        self.assertEqual([r[3] for r in v["rows"]], lines)
        self.assertEqual(([r[0] for r in v["rows"]], v["at"]), ([" "] * 3, [2, 2]))
        os.chmod(os.path.join(repo, "moved.md"), 0o755)
        self.git(repo, "add", "moved.md")
        self.assertEqual([r[0] for r in code_view.view(repo, "moved.md", 1, base=base)["rows"]], [" "] * 3)

    def test_an_edited_file_is_no_whole_one(self):
        repo, base = self.repo(list("abcdef"), list("aBcefg"))
        self.assertIsNone(code_view.view(repo, "t.md", 1, base=base)["whole"])

    def test_without_the_mr_s_base_the_file_is_shown_as_it_is(self):
        repo, _ = self.repo(list("ab"), list("abc"))
        v = code_view.view(repo, "t.md", 2)
        self.assertEqual([r[0] for r in v["rows"]], [" ", " ", " "])


if __name__ == "__main__":
    unittest.main()
