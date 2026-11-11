#!/usr/bin/env python3
"""private_terms.py with a word list of its own and invented words: what blocks, what only
flags, what is left alone."""
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import private_terms as P  # noqa: E402

DIFF = """diff --git a/t.py b/t.py
--- a/t.py
+++ b/t.py
@@ -0,0 +1,4 @@
+author = "Zorbix Quell"
+# the glooping helper quxes the totals
+label = "Gloop"
+x = 1
"""


class TestLists(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = os.path.join(self._tmp.name, "terms")
        p = mock.patch.object(P, "list_path", return_value=self.path)
        p.start()
        self.addCleanup(p.stop)

    def cli(self, *argv):
        out = io.StringIO()
        with redirect_stdout(out), mock.patch("sys.stderr", io.StringIO()):
            code = P.main(list(argv))
        return code, out.getvalue()

    def test_words_are_added_to_one_list_and_dropped(self):
        self.cli("add", "block", "zorbix", "acme-qux")
        self.cli("add", "watch", "gloop", "qux")
        self.cli("add", "watch", "zorbix")              # moves: a word is on one list
        self.assertEqual(P.load(), {"block": ["acme-qux"], "watch": ["gloop", "qux", "zorbix"]})
        self.cli("drop", "QUX")
        self.assertEqual(P.load()["watch"], ["gloop", "zorbix"])

    def test_a_block_word_refuses_and_a_watch_word_only_flags(self):
        P.save({"block": ["zorbix"], "watch": ["gloop", "qux"]})
        lines = [(f"t.py:{n}", t) for _, n, t in P.added_lines(DIFF)]
        found = P.hits(lines, P.load())
        self.assertEqual([(k, w, where) for k, w, where, _ in found],
                         [("block", "zorbix", "t.py:1"), ("watch", "gloop", "t.py:3")])
        with redirect_stdout(io.StringIO()):
            self.assertEqual(P.report(found), 1)
            self.assertEqual(P.report([h for h in found if h[0] == "watch"]), 0)

    def test_a_word_matches_whole_so_qux_is_not_quxes(self):
        self.assertIsNone(P.pattern("qux").search("the helper quxes"))
        self.assertIsNotNone(P.pattern("acme-qux").search("see ACME-QUX docs"))
        self.assertIsNone(P.pattern("acme").search("acme-qux"))
        self.assertIsNotNone(P.pattern("zz9-").search("see ZZ9-1234"))
        self.assertIsNone(P.pattern("zz9-").search("xzz9-1"))

    def test_without_a_list_nothing_is_checked_and_it_says_so(self):
        err = io.StringIO()
        with mock.patch("sys.stderr", err):
            self.assertEqual(P.check(["--staged"]), 0)
        self.assertIn("no list", err.getvalue())

    def test_a_commit_message_is_checked_without_its_comment_lines(self):
        P.save({"block": ["zorbix"], "watch": []})
        msg = os.path.join(self._tmp.name, "MSG")
        with open(msg, "w") as fh:
            fh.write("fix: the cache\n\n# zorbix in a comment git strips\n")
        self.assertEqual(self.cli("check", "--message", msg)[0], 0)
        with open(msg, "a") as fh:
            fh.write("Reported by Zorbix.\n")
        self.assertEqual(self.cli("check", "--message", msg)[0], 1)

    def test_line_numbers_follow_the_hunks(self):
        diff = "+++ b/a.txt\n@@ -3,2 +10,2 @@\n context\n+added\n-removed\n"
        self.assertEqual(P.added_lines(diff), [("a.txt", 11, "added")])


if __name__ == "__main__":
    unittest.main()
