#!/usr/bin/env python3
"""Tests for rework-mr's presentation layer — fences, change/diff views, code context.

Run: `python3 skills/rework-mr/scripts/test_threads.py` (stdlib only).

These cover the rendering that has to survive a markdown renderer, which is where the bugs
were: a change illustration wrapped in a second fence lost its highlighting and spilled its
tail as prose, and a code anchor read against the wrong version shows unrelated lines.
"""
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import threads as T                                   # noqa: E402


class TestLooksLikeDiff(unittest.TestCase):
    def test_unified_diff(self):
        self.assertTrue(T.looks_like_diff("-  const a = 1\n+  const a = 2\n   const b = 3\n"))

    def test_diff_with_headers(self):
        self.assertTrue(T.looks_like_diff("diff --git a/x b/x\n@@ -1 +1 @@\nwhatever\n"))

    def test_plain_snippet_is_not_a_diff(self):
        """A before/after snippet wants the file's language, not `diff`."""
        self.assertFalse(T.looks_like_diff("export function foo() {\n  return 1\n}\n"))

    def test_indented_snippet_without_markers_is_not_a_diff(self):
        self.assertFalse(T.looks_like_diff("  return 1\n  return 2\n"))

    def test_prose_is_not_a_diff(self):
        self.assertFalse(T.looks_like_diff("Drop the weaker test and keep the other one.\n"))

    def test_index_assignment_is_not_a_diff_header(self):
        """git's header is `index abc1234..def5678`; `index = 0` is just code."""
        self.assertFalse(T.looks_like_diff("index = 0\nwhile (index < n) {\n"))
        self.assertTrue(T.looks_like_diff("index 1a2b3c4..5d6e7f8 100644\n-a\n+b\n"))

    def test_empty(self):
        self.assertFalse(T.looks_like_diff(""))


class TestFence(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(T.fence("x = 1", "python"), "```python\nx = 1\n```")

    def test_widens_for_a_nested_fence(self):
        out = T.fence("```bash\necho hi\n```", "markdown")
        self.assertTrue(out.startswith("````markdown\n"))
        self.assertTrue(out.endswith("\n````"))

    def test_widens_for_an_indented_closer(self):
        """Inside a diff every line carries a prefix, so a fence arrives as "` ```"` — a
        valid closer that a column-0 scan misses. This was a real miss."""
        diff = "-```bash\n+```sh\n echo hi\n ```\n"
        self.assertTrue(T.fence(diff, "diff").startswith("````diff\n"))

    def test_widens_past_four(self):
        self.assertTrue(T.fence("````\nx\n````", "").startswith("`````\n"))

    def test_no_language(self):
        self.assertEqual(T.fence("x", ""), "```\nx\n```")


class TestRenderChange(unittest.TestCase):
    def test_bare_diff_gets_a_diff_fence(self):
        out = T.render_change("-  a\n+  b\n")
        self.assertEqual(out, "```diff\n-  a\n+  b\n```")

    def test_bare_snippet_uses_the_paths_language(self):
        out = T.render_change("export const a = 1\n", "src/a.ts")
        self.assertEqual(out, "```ts\nexport const a = 1\n```")

    def test_snippet_without_a_path_is_untagged(self):
        self.assertEqual(T.render_change("hello\n"), "```\nhello\n```")

    def test_already_fenced_content_is_not_wrapped_again(self):
        """The bug this whole thing exists for: prose + its own ```diff block. Wrapping it
        closed the outer fence on the inner one — flat text, and the tail leaked out."""
        src = "Drop it:\n\n```diff\n-  a\n+  b\n```\n\nNet: one test.\n"
        out = T.render_change(src)
        self.assertEqual(out, src.strip("\n"))
        self.assertEqual(out.count("```"), 2)

    def test_untagged_inner_fence_gets_a_language(self):
        out = T.render_change("Before:\n\n```\n-  a\n+  b\n```\n", "src/a.ts")
        self.assertIn("```diff\n", out)          # sniffed from the block's own content

    def test_untagged_inner_snippet_gets_the_paths_language(self):
        out = T.render_change("Before:\n\n```\nconst a = 1\n```\n", "src/a.ts")
        self.assertIn("```ts\n", out)

    def test_dangling_fence_is_closed(self):
        """A model that forgets the closing ``` would otherwise swallow the `Agreed?`."""
        out = T.render_change("Before:\n\n```ts\nconst a = 1\n")
        self.assertEqual(out.count("```"), 2)
        self.assertTrue(out.rstrip().endswith("```"))

    def test_existing_language_is_preserved(self):
        out = T.render_change("```python\nx = 1\n```\n", "src/a.ts")
        self.assertIn("```python\n", out)
        self.assertNotIn("```ts", out)


class TestViews(unittest.TestCase):
    def test_change_view_shape(self):
        out = T.render_change_view("t1", "-  a\n+  b\n")
        self.assertTrue(out.startswith("**Change (◈ t1):**"))
        self.assertTrue(out.rstrip().endswith("Agreed?"))
        self.assertIn("```diff", out)

    def test_diff_view_shape(self):
        out = T.render_diff_view("t3", "diff --git a/x b/x\n@@ -1 +1 @@\n-a\n+b\n")
        self.assertTrue(out.startswith("**Diff (◈ t3):**"))
        self.assertTrue(out.rstrip().endswith("ACK to fix up and push?"))
        self.assertIn("```diff", out)

    def test_view_signatures_match_the_stop_hook_gates(self):
        """The gate spec keys off these literals; renaming a header silently disables it."""
        self.assertIn("**Change (", T.render_change_view("t1", "-a\n+b\n"))
        diff = T.render_diff_view("t1", "-a\n+b\n")
        self.assertIn("**Diff (", diff)
        self.assertIn("```diff", diff)

    def test_diff_view_signature_survives_a_widened_fence(self):
        """A diff of a markdown file widens the fence to ````diff — which still contains
        the literal ```diff the gate looks for."""
        out = T.render_diff_view("t1", "-```bash\n+```sh\n echo hi\n ```\n")
        self.assertIn("````diff", out)
        self.assertIn("```diff", out)


class TestCodeContext(unittest.TestCase):
    """`render_code_context` reads real git objects, so these run in a throwaway repo."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.repo = cls.tmp.name
        cls.file = "src/app.ts"
        os.makedirs(os.path.join(cls.repo, "src"))
        cls._write("\n".join(f"const line{i} = {i}" for i in range(1, 31)) + "\n")
        cls._git("init", "-q", ".")
        cls._git("config", "user.email", "t@example.com")
        cls._git("config", "user.name", "T")
        cls._git("add", "-A")
        cls._git("commit", "-qm", "seed")
        cls.sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=cls.repo,
                                 capture_output=True, text=True).stdout.strip()
        cls.cwd = os.getcwd()
        os.chdir(cls.repo)                # the helpers shell out to git in cwd

    @classmethod
    def tearDownClass(cls):
        os.chdir(cls.cwd)
        cls.tmp.cleanup()

    @classmethod
    def _write(cls, text):
        with open(os.path.join(cls.repo, cls.file), "w") as f:
            f.write(text)

    @classmethod
    def _git(cls, *args):
        subprocess.run(["git", *args], cwd=cls.repo, capture_output=True, check=True)

    def thread(self, **kw):
        base = {"file": self.file, "line": 10, "side": "new",
                "head_sha": self.sha, "base_sha": self.sha}
        base.update(kw)
        return base

    def test_window_is_centred_and_marked(self):
        out = T.render_code_context(self.thread())
        self.assertIn("► 10 | const line10 = 10", out)
        self.assertIn("   4 | const line4 = 4", out)      # 6 lines of context
        self.assertIn("  16 | const line16 = 16", out)
        self.assertNotIn("line3 =", out)
        self.assertIn("```ts", out)
        self.assertIn(f"`{self.file}:10`", out)

    def test_clamped_at_the_top_of_the_file(self):
        """Window 1..8, so the gutter is one digit wide — no lines above line 1."""
        out = T.render_code_context(self.thread(line=2))
        self.assertIn("  1 | const line1 = 1", out)
        self.assertIn("► 2 | const line2 = 2", out)
        self.assertIn("  8 | const line8 = 8", out)

    def test_reads_the_reviewed_blob_not_the_working_tree(self):
        """The line number belongs to the version commented on. Once the author edits, a
        working-tree read would render unrelated lines with no warning."""
        self._write("// header added on top\n"
                    + "\n".join(f"const line{i} = {i}" for i in range(1, 31)) + "\n")
        try:
            out = T.render_code_context(self.thread())
            self.assertIn("► 10 | const line10 = 10", out)     # not line9, as reviewed
            self.assertIn("working tree differs", out)
        finally:
            self._write("\n".join(f"const line{i} = {i}" for i in range(1, 31)) + "\n")

    def test_no_drift_note_when_the_tree_matches(self):
        self.assertNotIn("working tree differs", T.render_code_context(self.thread()))

    def test_falls_back_to_the_working_tree_without_shas(self):
        """Threads stored before the shas were recorded still get code."""
        out = T.render_code_context(self.thread(head_sha=None, base_sha=None))
        self.assertIn("working tree", out)
        self.assertIn("► 10 | const line10 = 10", out)

    def test_none_without_a_position(self):
        self.assertIsNone(T.render_code_context({"file": None, "line": None}))
        self.assertIsNone(T.render_code_context({"file": self.file, "line": None}))

    def test_none_when_the_anchor_is_past_the_end(self):
        self.assertIsNone(T.render_code_context(self.thread(line=9999)))

    def test_none_for_a_deleted_file(self):
        self.assertIsNone(T.render_code_context(self.thread(file="src/gone.ts")))

    def test_quote_puts_the_code_above_the_note(self):
        state = {"iid": 1, "title": "x", "threads": {"d1": dict(
            self.thread(), author="Jan", body="ist äquivalent?", resolved=False,
            note_count=1, url="http://gl/x#note_1",
            notes=[{"author": "Jan", "body": "ist äquivalent?"}])},
            "topics": [{"id": "t1", "summary": "dupe test", "thread_ids": ["d1"],
                        "state": None}]}
        out = T.render_quote(state, "t1")
        self.assertLess(out.index("const line10"), out.index("ist äquivalent?"))
        self.assertLess(out.index("http://gl/x#note_1"), out.index("const line10"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
