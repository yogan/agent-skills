import hashlib
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
        # Line 11 became two: the stretch now ends on the second.
        self.assertEqual(code_view.touched(self.REWRITE, 10, 11), (True, 10, 12))

    def test_a_hit_carries_the_stretch_to_what_took_its_place(self):
        """Above the stretch the push added lines and inside it rewrote one: the next push
        is checked against the lines the author left, not the numbers it had before."""
        diff = "@@ -2,0 +3,2 @@\n+n1\n+n2\n@@ -20,3 +22,2 @@\n a20\n-a21\n+b21\n a22\n"
        self.assertEqual(code_view.touched(diff, 20, 22), (True, 22, 24))

    def test_a_stretch_removed_whole_lands_on_the_line_after_it(self):
        self.assertEqual(code_view.touched("@@ -10,3 +9,0 @@\n-a\n-b\n-c\n", 10, 12), (True, 10, 10))

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


FUNC = ["def shipping_cost(order):",
        "    weight = sum(item.weight for item in order.items)",
        "    if weight > order.carrier.limit:",
        "        raise TooHeavy(weight)",
        "    rate = order.carrier.rate_for(order.zone)",
        "    return round(weight * rate, 2)"]


def removed(at, lines):
    """A diff removing `lines` from line `at` on."""
    return f"@@ -{at},{len(lines)} +{at - 1},0 @@\n" + "".join(f"-{x}\n" for x in lines)


def added(at, lines, context=()):
    """A diff adding `lines` at line `at`, the file's own `context` lines just above them."""
    n = len(context)
    return (f"@@ -{at - n},{n} +{at - n},{n + len(lines)} @@\n" + "".join(f" {x}\n" for x in context)
            + "".join(f"+{x}\n" for x in lines))


class TestMovedTo(unittest.TestCase):
    """Where a push moved the lines a comment is on, read off GitLab's diffs of the files it
    changed: the block they were removed with, found among the lines it added."""

    GONE = removed(20, FUNC)                       # lines 20-25; the comment on 22, the `if`

    def test_a_function_moved_into_another_file_takes_the_comment_s_line_along(self):
        got = code_view.moved_to(self.GONE, 22, 22, [("pricing.py", self.GONE, True),
                                                     ("carriers.py", added(40, FUNC, ["", ""]), False)])
        self.assertEqual(got, [{"file": "carriers.py", "first": 42, "last": 42}])

    def test_into_a_new_file(self):
        new = f"@@ -0,0 +1,{len(FUNC)} @@\n" + "".join(f"+{x}\n" for x in FUNC)
        self.assertEqual(code_view.moved_to(self.GONE, 21, 23, [("shipping.py", new, False)]),
                         [{"file": "shipping.py", "first": 2, "last": 4}])

    def test_moved_and_the_commented_line_itself_edited_on_the_way(self):
        edited = [*FUNC[:2], "    if weight > order.carrier.max_weight:", *FUNC[3:]]
        self.assertEqual(code_view.moved_to(self.GONE, 22, 22, [("carriers.py", added(40, edited), False)]),
                         [{"file": "carriers.py", "first": 42, "last": 42}])

    def test_a_deletion_is_no_move_however_much_boilerplate_was_added_elsewhere(self):
        other = added(5, ["import os", "", "}", "return", "    pass", FUNC[4], "else:"])
        self.assertEqual(code_view.moved_to(self.GONE, 22, 22, [("carriers.py", other, False)]), [])

    def test_the_same_lines_added_far_apart_are_no_move(self):
        """Three of the deleted function's lines turn up in one file, each in a change of
        its own far from the others: no block moved there."""
        other = "".join(added(at, [FUNC[k], f"    log.debug('step {k}')"] + ["    pass"] * 8)
                        for at, k in ((10, 1), (60, 2), (110, 4)))
        self.assertEqual(code_view.moved_to(self.GONE, 22, 22, [("carriers.py", other, False)]), [])

    def test_lines_the_destination_had_already_are_no_move(self):
        """The same function was in the other file before the push: removing this copy is
        a deletion of a duplicate, not a move."""
        kept = "@@ -38,6 +38,7 @@\n+# shared\n" + "".join(f" {x}\n" for x in FUNC)
        self.assertEqual(code_view.moved_to(self.GONE, 22, 22, [("carriers.py", kept, False)]), [])

    def test_split_into_two_files_each_takes_its_part(self):
        more = ["def handling_fee(order):", "    fee = order.carrier.base_fee",
                "    if order.fragile:", "        fee += order.carrier.fragile_fee", "    return fee"]
        gone = removed(20, FUNC + more)                # 20-25 and 26-30
        got = code_view.moved_to(gone, 22, 28, [("carriers.py", added(10, FUNC), False),
                                                ("fees.py", added(3, more), False)])
        self.assertEqual(got, [{"file": "carriers.py", "first": 12, "last": 15},
                               {"file": "fees.py", "first": 3, "last": 5}])

    def test_moved_further_down_the_same_file(self):
        diff = self.GONE + "@@ -60,0 +55,6 @@\n" + "".join(f"+{x}\n" for x in FUNC)
        self.assertEqual(code_view.moved_to(diff, 22, 22, [("pricing.py", diff, True)]),
                         [{"file": "pricing.py", "first": 57, "last": 57}])

    def test_a_rewrite_in_place_is_no_move(self):
        diff = (f"@@ -20,{len(FUNC)} +20,{len(FUNC)} @@\n" + "".join(f"-{x}\n" for x in FUNC)
                + "".join(f"+{x}  \n" for x in FUNC))
        self.assertEqual(code_view.moved_to(diff, 22, 22, [("pricing.py", diff, True)]), [])

    def test_lines_the_push_left_alone_move_nowhere(self):
        self.assertEqual(code_view.moved_to(self.GONE, 5, 6, [("carriers.py", added(40, FUNC), False)]), [])


class TestLineAnchor(unittest.TestCase):
    """A line's anchor on an MR's Changes page, as GitLab's own link to a line names it."""
    ROWS = [[" ", 90, 99, "a"], ["-", 91, None, "b"], ["+", None, 100, "c"], [" ", 92, 101, "d"]]

    def test_a_kept_line_has_both_its_numbers(self):
        self.assertEqual(code_view.line_anchor(self.ROWS, "src/x.ts", 101),
                         hashlib.sha1(b"src/x.ts").hexdigest() + "_92_101")

    def test_an_added_line_counts_as_the_old_line_that_comes_next(self):
        self.assertTrue(code_view.line_anchor(self.ROWS, "src/x.ts", 100).endswith("_92_100"))

    def test_a_line_not_in_the_file_has_none(self):
        self.assertIsNone(code_view.line_anchor(self.ROWS, "src/x.ts", 7))


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

    def test_a_pushed_fix_s_file_is_its_change_from_before_to_after_not_the_disk(self):
        repo, before = self.repo(list("abcdef"), list("abCdef"))
        with open(os.path.join(repo, "o.md"), "w") as f:
            f.write("x\n")
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-qm", "the fix")
        after = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        with open(os.path.join(repo, "t.md"), "w") as f:
            f.write("changed again on disk\n")         # later work: not the fix's
        files = [["o.md", 1, 0], ["t.md", 1, 1]]
        thread = {"file": "t.md", "line": 3, "head_sha": before, "side": "new"}
        v = code_view.fix_view(repo, thread, files, None, before, after)
        self.assertEqual([r[0] + r[3] for r in v["rows"]], [" a", " b", "-c", "+C", " d", " e", " f"])
        # The comment's file leads the list, whatever order the fix's files came in.
        self.assertEqual((v["commented"], v["fix_files"], v["line"]), ("t.md", files[::-1], 3))
        # Another of the fix's files picked: shown with no comment on it.
        v = code_view.fix_view(repo, thread, files, "o.md", before, after)
        self.assertEqual((v["file"], v["line"], v["at"]), ("o.md", None, [1, 1]))

    def test_a_fix_that_moved_the_code_opens_where_it_went(self):
        """The push moved the commented function into another file: the comment's file
        shows only its removal, so the view opens on the function where it is now, its line
        marked, and the comment's file still leads the list."""
        repo, before = self.repo(["# pricing", *FUNC, "# end"], ["# pricing", "# end"])
        with open(os.path.join(repo, "o.md"), "w") as f:
            f.write("".join(f"{x}\n" for x in ["# carriers", "", *FUNC]))
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-qm", "move it")
        after = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        files = [["o.md", 8, 0], ["t.md", 0, 6]]
        thread = {"file": "t.md", "line": 4, "head_sha": before, "side": "new"}
        v = code_view.fix_view(repo, thread, files, None, before, after,
                               moved=[{"file": "o.md", "first": 5, "last": 5}])
        self.assertEqual((v["file"], v["line"], v["fix_files"]), ("o.md", 5, files[::-1]))
        self.assertEqual(code_view.new_lines(v)[v["line"] - 1], FUNC[2])
        # Picked by hand, the comment's file shows the removal, as before.
        self.assertEqual(code_view.fix_view(repo, thread, files, "t.md", before, after,
                                            moved=[{"file": "o.md", "first": 5, "last": 5}])["file"], "t.md")

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

    def test_a_file_renamed_since_the_comment_is_shown_under_its_name_now(self):
        # A fix for another topic renamed the file and edited one line; the comment was on
        # line 6 of the old name.
        lines = [f"line {i} of a file long enough for git to see the rename" for i in range(1, 11)]
        repo, sha = self.repo(lines, lines)
        self.git(repo, "mv", "t.md", "moved.md")
        with open(os.path.join(repo, "moved.md"), "w") as f:
            f.write("".join(f"{x}\n" for x in ["new first line"] + lines))
        v = code_view.view(repo, "t.md", 6, since=sha)
        self.assertEqual((v["file"], v["renamed_from"], v["line"]), ("moved.md", "t.md", 7))
        self.assertEqual(code_view.new_lines(v)[6], lines[5])
        self.assertEqual(v["changed"], [1])

    def test_what_changed_on_a_comment_s_lines_follows_the_rename(self):
        lines = [f"line {i} of a file long enough for git to see the rename" for i in range(1, 11)]
        repo, sha = self.repo(lines, lines)
        self.git(repo, "mv", "t.md", "moved.md")
        edited = lines[:5] + ["line 6, rewritten"] + lines[6:]
        with open(os.path.join(repo, "moved.md"), "w") as f:
            f.write("".join(f"{x}\n" for x in edited))
        fence = code_view.since_fence(repo, sha, "t.md", 6, 6)
        self.assertEqual(fence[0], "```diff path=moved.md")
        self.assertIn("+line 6, rewritten", fence)
        self.assertEqual(len([x for x in fence if x.startswith("-")]), 1)     # one line, not the file
        # Only renamed, the comment's lines are untouched: nothing to show.
        self.assertIsNone(code_view.since_fence(repo, sha, "t.md", 2, 2))

    SHORT = ["export const fooKeys = {", "  all: ['foo'] as const,",
             "  one: (fooId: string) => ['foo', fooId] as const,", "};"]
    EDITED = ["export const fooQueryKeys = {", "  all: ['foo', 'v2'] as const,",
              "  one: (id: string) => ['foo', id] as const,", "};"]

    def write(self, repo, name, lines):
        with open(os.path.join(repo, name), "w") as f:
            f.write("".join(f"{x}\n" for x in lines))

    def test_a_rename_too_edited_for_git_is_a_removal_said_in_no_lines(self):
        # Renamed and rewritten in one go: less than half alike, so git sees one file removed
        # and another added, and so does GitLab. Nothing guesses the new one is it.
        repo, sha = self.repo(self.SHORT, self.SHORT)
        self.git(repo, "mv", "t.md", "foo-keys.md")
        self.write(repo, "foo-keys.md", self.EDITED)
        for committed in (False, True):
            with self.subTest(committed=committed):
                if committed:
                    self.git(repo, "commit", "-qam", "rename")
                self.assertEqual(code_view.now_at(repo, sha, "t.md"), "t.md")
                self.assertTrue(code_view.removed_since(repo, sha, "t.md"))
                self.assertIsNone(code_view.since_fence(repo, sha, "t.md", 3, 3))
                # The sidebar still shows the code the comment is on, as it was reviewed,
                # and says it is gone: only a thread on the MR itself goes without code.
                v = code_view.view(repo, "t.md", 3, sha, start=2, base=sha)
                self.assertEqual((v["gone"], v["file"], v["at"], v["line"], v["start"]), (True, "t.md", [2, 3], 3, 2))
                self.assertEqual([r[3] for r in v["rows"]], self.SHORT)
                self.assertEqual({r[0] for r in v["rows"]}, {" "})

    def test_a_rename_then_a_rewrite_is_followed_commit_by_commit(self):
        # As `git log --follow` does: renamed in one commit, rewritten in the next, where the
        # diff over both is one file removed and another added.
        lines = [f"line {i} of a file long enough for git to see the rename" for i in range(1, 11)]
        repo, base = self.repo(lines, lines)
        self.git(repo, "mv", "t.md", "moved.md")
        self.git(repo, "commit", "-qm", "rename")
        rewritten = [f"rewritten {i}" for i in range(1, 9)] + lines[8:]
        self.write(repo, "moved.md", rewritten)
        self.git(repo, "commit", "-qam", "rewrite")
        names = subprocess.run(["git", "-C", repo, "diff", "-M", "--name-status", base],
                               capture_output=True, text=True).stdout
        self.assertNotIn("R", names[:1])        # the case: the whole range does not pair them
        self.assertFalse(code_view.removed_since(repo, base, "t.md"))
        v = code_view.view(repo, "t.md", 10, since=base, base=base)
        self.assertEqual((v["file"], v["renamed_from"], v["line"], v["whole"]),
                         ("moved.md", "t.md", 10, None))
        # A card's heading names the file as the sidebar does, not as the comment was posted.
        thread = {"file": "t.md", "line": 10, "side": "new", "head_sha": base}
        self.assertEqual(code_view.place_now(repo, thread), "moved.md:10")
        # The line is carried as the sidebar carries it: line 2 was rewritten with the eight
        # above line 9, and lands on the rewrite's first line.
        self.assertEqual(code_view.place_now(repo, {**thread, "line": 2}), "moved.md:1")
        self.assertEqual(code_view.place_now(repo, {"file": None}), "")
        fence = code_view.since_fence(repo, base, "t.md", 2, 2)
        self.assertEqual(fence[0], "```diff path=moved.md")
        self.assertIn("+rewritten 2", fence)
        self.assertNotIn("-" + lines[9], fence)          # not the whole old file

    def test_a_committed_rename_rewritten_on_disk_marks_only_what_changed(self):
        lines = [f"line {i} of a file long enough for git to see the rename" for i in range(1, 11)]
        repo, sha = self.repo(lines, lines)
        self.git(repo, "mv", "t.md", "moved.md")
        self.git(repo, "commit", "-qm", "rename")
        self.write(repo, "moved.md", [f"rewritten {i}" for i in range(1, 9)] + lines[8:])
        fence = code_view.since_fence(repo, sha, "t.md", 2, 2)
        self.assertEqual(fence[0], "```diff path=moved.md")
        self.assertIn("+rewritten 2", fence)
        self.assertNotIn("-" + lines[9], fence)

    def test_a_removed_file_beside_one_new_one_is_not_guessed_to_be_it(self):
        repo, sha = self.repo(self.SHORT, self.SHORT)
        self.git(repo, "rm", "-q", "t.md")
        self.write(repo, "other.md", ["x"])
        self.git(repo, "add", ".")
        self.git(repo, "commit", "-qm", "replace")
        self.assertEqual(code_view.now_at(repo, sha, "t.md"), "t.md")

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
