#!/usr/bin/env python3
"""Tests for the shared fence and note rendering — see fences.py's module docstring for
why both MR skills show a GitLab note through the same code.

Run: `python3 lib/test_fences.py` (stdlib only).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib import critical_manifest  # noqa: E402
from lib.fences import fence, looks_like_diff, note_md, suggestion_diff  # noqa: E402


class TestLooksLikeDiff(unittest.TestCase):
    def test_unified_diff(self):
        self.assertTrue(
            looks_like_diff("-  const a = 1\n+  const a = 2\n   const b = 3\n")
        )

    def test_diff_with_headers(self):
        self.assertTrue(
            looks_like_diff("diff --git a/x b/x\n@@ -1 +1 @@\nwhatever\n")
        )

    def test_plain_snippet_is_not_a_diff(self):
        """A before/after snippet wants the file's language, not `diff`."""
        self.assertFalse(looks_like_diff("export function foo() {\n  return 1\n}\n"))

    def test_indented_snippet_without_markers_is_not_a_diff(self):
        self.assertFalse(looks_like_diff("  return 1\n  return 2\n"))

    def test_prose_is_not_a_diff(self):
        self.assertFalse(
            looks_like_diff("Drop the weaker test and keep the other one.\n")
        )

    def test_index_assignment_is_not_a_diff_header(self):
        """git's header is `index abc1234..def5678`; `index = 0` is just code."""
        self.assertFalse(looks_like_diff("index = 0\nwhile (index < n) {\n"))
        self.assertTrue(looks_like_diff("index 1a2b3c4..5d6e7f8 100644\n-a\n+b\n"))

    def test_empty(self):
        self.assertFalse(looks_like_diff(""))


class TestFence(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(fence("x = 1", "python"), "```python\nx = 1\n```")

    def test_widens_for_a_nested_fence(self):
        out = fence("```bash\necho hi\n```", "markdown")
        self.assertTrue(out.startswith("````markdown\n"))
        self.assertTrue(out.endswith("\n````"))

    def test_widens_for_an_indented_closer(self):
        """Inside a diff every line carries a prefix, so a fence arrives as "` ```"` — a
        valid closer that a column-0 scan misses. This was a real miss."""
        diff = "-```bash\n+```sh\n echo hi\n ```\n"
        self.assertTrue(fence(diff, "diff").startswith("````diff\n"))

    def test_widens_past_four(self):
        self.assertTrue(fence("````\nx\n````", "").startswith("`````\n"))

    def test_no_language(self):
        self.assertEqual(fence("x", ""), "```\nx\n```")


class TestNoteRendering(unittest.TestCase):
    """A reviewer's own code has to render as code, inside the quote with their prose.

    GitLab reviewers paste a ```suggestion block or a 4-space indented snippet. As they are,
    both come out flat — the suggestion's info string is a language no highlighter knows,
    and an indented block has no language at all — so the proposed code read as grey prose.
    """

    NOTE = (
        "Minor:\n\nDer Test schaut nicht wirklich ob die Reihenfolge aus `fields` "
        "übernommen wird. Entweder:\n\n"
        "```suggestion:-0+0\n  it('lists multiple changed leaves', () => {\n```\n\n"
        "Oder `ExtractedData` umdrehen?\n\n"
        "    const original: ExtractedData = {\n"
        "      money_related: object({ summe: scalar(10) }),\n"
        "    }\n"
    )

    def test_every_line_stays_inside_the_quote(self):
        out = note_md("Robin", self.NOTE, "src/x.test.ts", 184)
        self.assertTrue(all(ln.startswith(">") for ln in out.splitlines()), out)

    def test_a_suggestion_without_its_source_is_code_in_the_files_language(self):
        out = note_md("Robin", self.NOTE, "src/x.test.ts", 184)
        self.assertIn("> ```ts\n>   it('lists multiple changed leaves", out)
        self.assertNotIn("suggestion", out)
        self.assertNotIn("suggested", out)          # no caption: the code says enough

    def test_a_suggestion_with_its_source_is_a_diff_against_what_it_replaces(self):
        source = ["def a():", "    return 1", "def b():"]
        out = note_md("Robin", "So:\n\n```suggestion:-0+0\n    return 2\n```",
                      "src/x.py", 2, source)
        self.assertIn("> ```diff\n> -    return 1\n> +    return 2\n> ```", out)

    def test_a_tab_indented_snippet_is_code_too(self):
        """Markdown counts a tab as four spaces; a space-only check missed it."""
        out = note_md(
            "Robin", "So:\n\n\tconst a = 1\n\tconst b = 2\n", "src/x.ts", 5
        )
        self.assertIn("> ```ts\n> const a = 1\n> const b = 2\n> ```", out)

    def test_an_empty_suggestion_without_its_source_is_skipped(self):
        """There is nothing to show and no line to say it deletes."""
        self.assertEqual(
            note_md("Robin", "```suggestion\n```", "src/x.ts", 3), "> **Robin**"
        )

    def test_indented_snippet_becomes_a_fenced_block(self):
        out = note_md("Robin", self.NOTE, "src/x.test.ts", 184)
        self.assertIn("> ```ts\n> const original: ExtractedData = {", out)  # and dedented
        self.assertNotIn(">     const original", out)

    def test_prose_stays_quoted_and_keeps_the_author(self):
        out = note_md("Robin", self.NOTE, "src/x.test.ts", 184)
        self.assertTrue(out.startswith("> **Robin**\n>\n> Minor:"))
        self.assertIn("> Oder `ExtractedData` umdrehen?", out)

    def test_code_is_marked_as_it_is_quoted(self):
        """The paste gate compares against the message, which carries the `> ` form."""
        critical_manifest.reset()
        note_md("Robin", "```python\nx = 1\n```", "src/x.py", 1)
        self.assertEqual(critical_manifest.current(), ["> x = 1"])

    def test_list_continuation_is_not_code(self):
        """Indentation under a bullet is list continuation — fencing it would break the
        list and misrepresent prose as code."""
        note = "Zwei Punkte:\n\n- erstens\n    weiter im Listenpunkt\n- zweitens\n"
        out = note_md("Robin", note, "src/x.ts", 10)
        self.assertNotIn("```", out)
        self.assertIn(">     weiter im Listenpunkt", out)

    def test_an_explicit_language_is_preserved(self):
        out = note_md("Robin", "So:\n\n```bash\nnpm test\n```\n", "src/x.ts", 5)
        self.assertIn("> ```bash\n> npm test", out)

    def test_a_diff_in_a_note_is_fenced_as_a_diff(self):
        out = note_md("Robin", "```\n-  a\n+  b\n```\n", "src/x.ts", 5)
        self.assertIn("> ```diff\n", out)

    def test_plain_prose_is_unchanged(self):
        self.assertEqual(
            note_md("Robin", "Sieht gut aus.\n"), "> **Robin**\n>\n> Sieht gut aus."
        )


class TestSuggestionDiff(unittest.TestCase):
    SOURCE = [f"line {i}" for i in range(1, 11)]

    def test_lines_it_keeps_are_context(self):
        """`-1+1` replaces lines 4–6; only the middle one changes."""
        self.assertEqual(
            suggestion_diff("line 4\nLINE 5\nline 6", "suggestion:-1+1", 5, self.SOURCE),
            " line 4\n-line 5\n+LINE 5\n line 6")

    def test_an_empty_suggestion_deletes(self):
        self.assertEqual(suggestion_diff("", "suggestion:-0+1", 3, self.SOURCE),
                         "-line 3\n-line 4")

    def test_the_range_is_clamped_to_the_file(self):
        self.assertEqual(suggestion_diff("x", "suggestion:-99+0", 2, self.SOURCE),
                         "-line 1\n-line 2\n+x")

    def test_unknown_lines_give_no_diff(self):
        for anchor, source in ((5, None), (None, self.SOURCE), (99, self.SOURCE)):
            self.assertIsNone(suggestion_diff("x", "suggestion", anchor, source))

    def test_a_plain_block_is_not_a_suggestion(self):
        self.assertIsNone(suggestion_diff("x", "python", 5, self.SOURCE))


if __name__ == "__main__":
    unittest.main()
