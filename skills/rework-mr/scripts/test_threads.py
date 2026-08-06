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


class TestNoteRendering(unittest.TestCase):
    """A reviewer's own code has to render as code.

    GitLab reviewers paste a ```suggestion block or a 4-space indented snippet. Inside the
    `> ` blockquote both come out flat — the suggestion's info string is a language no
    highlighter knows, and an indented block has no language at all — so the proposed code
    read as grey prose.
    """

    NOTE = ("Minor:\n\nDer Test schaut nicht wirklich ob die Reihenfolge aus `fields` "
            "übernommen wird. Entweder:\n\n"
            "```suggestion:-0+0\n  it('lists multiple changed leaves', () => {\n```\n\n"
            "Oder `ExtractedData` umdrehen?\n\n"
            "    const original: ExtractedData = {\n"
            "      money_related: object({ summe: scalar(10) }),\n"
            "    }\n")

    def test_suggestion_is_lifted_and_re_fenced(self):
        out = T._note_md("Jan", self.NOTE, "src/x.test.ts", 184)
        self.assertIn("\n```ts\n  it('lists multiple changed leaves", out)
        self.assertNotIn("> ```", out)            # never left inside the quote
        self.assertNotIn("suggestion:-0+0", out)  # replaced by a caption

    def test_suggestion_caption_names_the_lines_it_replaces(self):
        self.assertIn("_suggested replacement for line 184:_",
                      T._note_md("Jan", self.NOTE, "src/x.test.ts", 184))
        self.assertIn("_suggested replacement for lines 182–187:_",
                      T._note_md("Jan", "```suggestion:-2+3\nx\n```\n", "src/x.ts", 184))

    def test_suggestion_without_an_anchor_still_gets_a_caption(self):
        self.assertIn("_suggested replacement:_",
                      T._note_md("Jan", "```suggestion\nx\n```\n", "src/x.ts", None))

    def test_a_tab_indented_snippet_is_code_too(self):
        """Markdown counts a tab as four spaces; a space-only check missed it."""
        out = T._note_md("Jan", "So:\n\n\tconst a = 1\n\tconst b = 2\n", "src/x.ts", 5)
        self.assertIn("```ts\nconst a = 1\nconst b = 2\n```", out)

    def test_caption_clamps_at_the_top_of_the_file(self):
        """`suggestion:-99+0` near the top produced "lines -94–5"."""
        self.assertIn("lines 1–5:", T._suggestion_caption("suggestion:-99+0", 5))

    def test_an_empty_suggestion_block_is_skipped(self):
        """No block, and no caption promising one."""
        self.assertEqual(T._note_md("Jan", "```suggestion\n```", "src/x.ts", 3),
                         "> **Jan**")

    def test_indented_snippet_becomes_a_fenced_block(self):
        out = T._note_md("Jan", self.NOTE, "src/x.test.ts", 184)
        self.assertIn("```ts\nconst original: ExtractedData = {", out)   # and dedented
        self.assertNotIn(">     const original", out)

    def test_prose_stays_quoted_and_keeps_the_author(self):
        out = T._note_md("Jan", self.NOTE, "src/x.test.ts", 184)
        self.assertTrue(out.startswith("> **Jan**\n>\n> Minor:"))
        self.assertIn("> Oder `ExtractedData` umdrehen?", out)

    def test_no_empty_quote_line_after_a_lifted_fence(self):
        """A `>` directly after a fence renders as a stray empty quote bar."""
        out = T._note_md("Jan", self.NOTE, "src/x.test.ts", 184)
        self.assertNotIn("```\n>\n", out)

    def test_list_continuation_is_not_code(self):
        """Indentation under a bullet is list continuation — fencing it would break the
        list and misrepresent prose as code."""
        note = "Zwei Punkte:\n\n- erstens\n    weiter im Listenpunkt\n- zweitens\n"
        out = T._note_md("Jan", note, "src/x.ts", 10)
        self.assertNotIn("```", out)
        self.assertIn(">     weiter im Listenpunkt", out)

    def test_an_explicit_language_is_preserved(self):
        out = T._note_md("Jan", "So:\n\n```bash\nnpm test\n```\n", "src/x.ts", 5)
        self.assertIn("```bash\nnpm test", out)

    def test_a_diff_in_a_note_is_fenced_as_a_diff(self):
        out = T._note_md("Jan", "```\n-  a\n+  b\n```\n", "src/x.ts", 5)
        self.assertIn("```diff\n", out)

    def test_plain_prose_is_unchanged(self):
        self.assertEqual(T._note_md("Jan", "Sieht gut aus.\n"),
                         "> **Jan**\n>\n> Sieht gut aus.")

    def test_quote_passes_the_file_and_anchor_through(self):
        state = {"iid": 1, "threads": {"d1": {
            "author": "Jan", "file": "src/x.test.ts", "line": 184, "body": self.NOTE,
            "resolved": False, "note_count": 1, "url": "http://gl/1",
            "notes": [{"author": "Jan", "body": self.NOTE}]}},
            "topics": [{"id": "t2", "summary": "order test", "thread_ids": ["d1"],
                        "state": None}]}
        out = T.render_quote(state, "t2")
        self.assertIn("_suggested replacement for line 184:_", out)
        self.assertIn("```ts\n", out)


class TestSyncRefreshesThreads(unittest.TestCase):
    """Every fetched field must reach a thread that is ALREADY in the state file.

    This is the bug that made two rounds of `quote` improvements invisible in a live
    rework: sync copied a hardcoded allowlist of keys onto an existing thread, so
    `side`/`head_sha`/`line_range` only ever landed on threads seen for the first time.
    A fresh session on a days-old MR kept rendering "(working tree)" with no span.
    """

    POSITION = {"new_path": "src/x.ts", "old_path": "src/x.ts", "new_line": 22,
                "head_sha": "abc123def456", "start_sha": "999base", "base_sha": "888base",
                "line_range": {"start": {"new_line": 12, "old_line": None},
                               "end": {"new_line": 22, "old_line": None}}}

    def live(self, position=None, body="Unit test?"):
        note = {"resolvable": True, "id": 7361603, "author": {"name": "Jan"},
                "body": body, "resolved": False,
                "position": self.POSITION if position is None else position}
        orig = T.api
        T.api = lambda *a, **k: [{"id": "d1", "notes": [note]}]
        try:
            return T.fetch_threads({"enc": "x", "web": "http://gl"}, 575, "me")
        finally:
            T.api = orig

    def old_shaped_state(self):
        """A thread as it was stored before side/head_sha/line_range existed."""
        return {"iid": 575, "title": "T", "threads": {"d1": {
            "author": "Jan", "file": "src/x.ts", "line": 22, "body": "Unit test?",
            "resolved": False, "url": "http://gl/1", "note_count": 1,
            "last_author": "Jan", "awaiting": "you"}},
            "topics": [{"id": "t3", "summary": "s", "thread_ids": ["d1"], "state": None}]}

    def test_new_fields_reach_an_existing_thread(self):
        state = self.old_shaped_state()
        T.sync(state, self.live())
        x = state["threads"]["d1"]
        self.assertEqual(x["side"], "new")
        self.assertEqual(x["head_sha"], "abc123def456")
        self.assertEqual((x["line_start"], x["line_end"]), (12, 22))

    def test_every_fetched_field_lands(self):
        """Guards the class of bug, not just the three fields that hit it."""
        state = self.old_shaped_state()
        live = self.live()
        T.sync(state, live)
        for k, v in live["d1"].items():
            self.assertEqual(state["threads"]["d1"][k], v, k)

    def test_a_range_that_disappears_is_dropped(self):
        """The reviewer edited a multi-line comment down to one line — an update() would
        have left the old span behind."""
        state = self.old_shaped_state()
        T.sync(state, self.live())
        single = dict(self.POSITION)
        single.pop("line_range")
        T.sync(state, self.live(position=single))
        self.assertNotIn("line_start", state["threads"]["d1"])

    def test_a_thread_gone_upstream_is_marked_resolved(self):
        state = self.old_shaped_state()
        state["threads"]["dead"] = {"resolved": False, "file": "src/y.ts", "line": 1}
        T.sync(state, self.live())
        self.assertTrue(state["threads"]["dead"]["resolved"])

    def test_local_fields_survive_a_fetch(self):
        state = self.old_shaped_state()
        try:
            T.LOCAL_THREAD_FIELDS = ("mine_only",)
            state["threads"]["d1"]["mine_only"] = "keep me"
            T.sync(state, self.live())
            self.assertEqual(state["threads"]["d1"]["mine_only"], "keep me")
            self.assertEqual(state["threads"]["d1"]["head_sha"], "abc123def456")
        finally:
            T.LOCAL_THREAD_FIELDS = ()

    def test_a_partial_range_is_not_stored(self):
        pos = dict(self.POSITION,
                   line_range={"start": {"new_line": None}, "end": {"new_line": 22}})
        state = self.old_shaped_state()
        T.sync(state, self.live(position=pos))
        self.assertNotIn("line_start", state["threads"]["d1"])

    def test_an_old_side_comment_takes_the_base_blob(self):
        pos = {"new_path": "src/x.ts", "old_path": "src/old.ts", "new_line": None,
               "old_line": 40, "head_sha": "head", "start_sha": "start",
               "line_range": {"start": {"old_line": 35}, "end": {"old_line": 40}}}
        state = self.old_shaped_state()
        T.sync(state, self.live(position=pos))
        x = state["threads"]["d1"]
        self.assertEqual((x["side"], x["file"], x["line"]), ("old", "src/old.ts", 40))
        self.assertEqual(x["base_sha"], "start")
        self.assertEqual((x["line_start"], x["line_end"]), (35, 40))


class TestReplyDraft(unittest.TestCase):
    """The draft lives in the state file, and `reply` is the only way out of it."""

    def state(self, reply=None):
        t = {"id": "t1", "summary": "retry unbounded", "thread_ids": ["d1"], "state": None}
        if reply is not None:
            t["reply"] = reply
        return {"iid": 7, "title": "x", "topics": [t], "threads": {"d1": {
            "author": "Jan", "file": "src/client.py", "line": 88, "body": "unbounded retry",
            "resolved": False, "note_count": 1, "url": "http://gl/y/1",
            "notes": [{"author": "Jan", "body": "unbounded retry"}]}}}

    def test_body_round_trips_shell_hazards(self):
        """A quoted heredoc into stdin is why this text survives at all: as a double-quoted
        `--reply "…"` argument the backticks would have been executed."""
        body = 'Gebunden über `MAX_RETRIES`, kostet $0 extra, "wirklich".\n'
        self.assertEqual(T.reply_body(self.state(body), "t1"), body)

    def test_missing_draft_dies_with_the_command_to_run(self):
        with self.assertRaises(SystemExit):
            T.reply_body(self.state(), "t1")

    def test_internal_handle_is_refused(self):
        """The guard sits in reply_body, so the post path cannot bypass it."""
        with self.assertRaises(SystemExit):
            T.reply_body(self.state("Wie in t5 besprochen.\n"), "t1")

    def test_legacy_file_is_still_read(self):
        """A session already in flight has its draft in reply-<t>.md."""
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "reply-t1.md"), "w") as f:
                f.write("Aus der alten Datei.\n")
            self.assertEqual(T.reply_body(self.state(), "t1", d), "Aus der alten Datei.\n")

    def test_state_wins_over_the_legacy_file(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "reply-t1.md"), "w") as f:
                f.write("stale\n")
            self.assertEqual(T.reply_body(self.state("fresh\n"), "t1", d), "fresh\n")

    def test_prose_is_blockquoted(self):
        self.assertEqual(T._quote_draft("Kurz.\n"), "> Kurz.")

    def test_fences_stay_at_line_start(self):
        """`> ```python` loses its highlighting, and the code is the point of the reply."""
        out = T._quote_draft("Danke:\n\n```python\nx = 1\n```\n\nPasst?\n", "src/a.py")
        self.assertEqual(out, "> Danke:\n\n```python\nx = 1\n```\n\n> Passt?")

    def test_untagged_draft_fence_gets_the_files_language(self):
        self.assertIn("```python", T._quote_draft("```\nx = 1\n```\n", "src/a.py"))

    def test_reply_view_carries_every_part(self):
        out = T.render_reply_view(self.state("Gefixt.\n"), "t1", "Gefixt.\n")
        for part in ("◈ t1", "src/client.py:88", "http://gl/y/1", "unbounded retry",
                     "**Draft reply:**", "> Gefixt.", "Thread (to post on):",
                     "**`c`** copy to clipboard"):
            self.assertIn(part, out)

    def test_reply_view_signature_matches_the_stop_hook_gate(self):
        out = T.render_reply_view(self.state("Gefixt.\n"), "t1", "Gefixt.\n")
        for sig in ("**Draft reply:**", "Thread (to post on):", "**`c`** copy to clipboard"):
            self.assertIn(sig, out)


class TestCodeContext(unittest.TestCase):
    """`render_code_context` reads real git objects, so these run in a throwaway repo."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.repo = cls.tmp.name
        cls.file = "src/app.ts"
        os.makedirs(os.path.join(cls.repo, "src"))
        cls._write("\n".join(f"const line{i} = {i}" for i in range(1, 31)) + "\n")
        cls.big = "src/big.ts"
        with open(os.path.join(cls.repo, cls.big), "w") as f:
            f.write("\n".join(f"const big{i} = {i}" for i in range(1, 201)) + "\n")
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

    def test_span_is_marked_without_a_pointer(self):
        """GitLab said "lines +8 to +14"; a `►` on one of them claimed a precision the
        position never had and pointed at the closing brace."""
        out = T.render_code_context(self.thread(line=14, line_start=8, line_end=14))
        self.assertNotIn("►", out)
        self.assertIn("┃  8 | const line8 = 8", out)
        self.assertIn("┃ 14 | const line14 = 14", out)
        self.assertIn(f"`{self.file}:8–14`", out)

    def test_span_keeps_context_tight_below(self):
        """The complaint that started this: 12 lines of unrelated declarations under a
        comment about one function."""
        out = T.render_code_context(self.thread(line=14, line_start=8, line_end=14))
        self.assertIn("   5 | const line5 = 5", out)      # 3 above the span
        self.assertNotIn("line4 =", out)
        self.assertIn("  15 | const line15 = 15", out)    # 1 below, no more
        self.assertNotIn("line16 =", out)

    def test_doc_comment_above_a_span_is_pulled_in(self):
        """`/** … */` explains the marked code; stopping one line short of it is the
        difference between context and a fragment."""
        self._write("\n".join(
            ["const a = 1", "", "/**", " * why this exists", " */",
             "function f() {", "  return 1", "}"]) + "\n")
        self._git("commit", "-qam", "doc")
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.repo,
                             capture_output=True, text=True).stdout.strip()
        try:
            out = T.render_code_context(self.thread(line=8, line_start=6, line_end=8,
                                                    head_sha=sha))
            self.assertIn("/**", out)
            self.assertIn("* why this exists", out)
            self.assertNotIn("const a = 1", out)          # blank line ends the block
        finally:
            self._write("\n".join(f"const line{i} = {i}" for i in range(1, 31)) + "\n")
            self._git("commit", "-qam", "restore")

    def test_a_range_disagreeing_with_the_anchor_is_distrusted(self):
        out = T.render_code_context(self.thread(line=20, line_start=8, line_end=14))
        self.assertIn("► 20 |", out)
        self.assertNotIn("┃", out)

    def test_a_reversed_or_partial_range_falls_back_to_the_anchor(self):
        for bad in ({"line_start": 14, "line_end": 8}, {"line_start": 8},
                    {"line_end": 14}, {"line_start": "8", "line_end": "14"}):
            out = T.render_code_context(self.thread(line=10, **bad))
            self.assertIn("► 10 |", out, bad)
            self.assertNotIn("┃", out, bad)

    def test_a_range_past_the_end_of_the_file_falls_back(self):
        out = T.render_code_context(self.thread(line=30, line_start=25, line_end=9999))
        self.assertIn("► 30 |", out)

    def test_a_single_line_range_is_not_treated_as_a_span(self):
        out = T.render_code_context(self.thread(line=10, line_start=10, line_end=10))
        self.assertIn("► 10 |", out)
        self.assertNotIn("┃", out)

    def test_a_huge_span_collapses_its_middle(self):
        """A reviewer can select a 200-line file; the block must stay readable."""
        out = T.render_code_context(self.thread(file=self.big, line=200,
                                                line_start=1, line_end=200))
        self.assertIn("200 lines in total", out)
        self.assertIn("┃   1 | const big1 = 1", out)
        self.assertIn("┃ 200 | const big200 = 200", out)
        self.assertNotIn("big100 =", out)
        self.assertLess(len(out.splitlines()), T.MAX_BODY + 10)

    def test_fetch_to_sync_to_quote_renders_the_span(self):
        """The whole chain, because the parts were each right while the seam was not: a
        GitLab position with a line_range, through fetch and sync, into `quote`."""
        note = {"resolvable": True, "id": 1, "author": {"name": "Jan"}, "resolved": False,
                "body": "Ganze Funktion — Test?", "position": {
                    "new_path": self.file, "old_path": self.file, "new_line": 14,
                    "head_sha": self.sha, "start_sha": self.sha,
                    "line_range": {"start": {"new_line": 8}, "end": {"new_line": 14}}}}
        orig = T.api
        T.api = lambda *a, **k: [{"id": "d9", "notes": [note]}]
        try:
            live = T.fetch_threads({"enc": "x", "web": "http://gl"}, 1, "me")
        finally:
            T.api = orig
        state = {"iid": 1, "title": "T", "threads": {}, "topics": []}
        T.sync(state, live)
        out = T.render_quote(state, state["topics"][0]["id"])
        self.assertIn("┃  8 | const line8 = 8", out)
        self.assertIn("┃ 14 | const line14 = 14", out)
        self.assertNotIn("►", out)
        self.assertIn("as reviewed", out)          # read from the blob, not the working tree
        self.assertLess(out.index("const line14"), out.index("Ganze Funktion"))

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
