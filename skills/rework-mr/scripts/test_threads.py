#!/usr/bin/env python3
"""Tests for rework-mr's presentation layer — fences, change/diff views, code context.

Run: `python3 skills/rework-mr/scripts/test_threads.py` (stdlib only).

These cover the rendering that has to survive a markdown renderer, which is where the bugs
were: a change illustration wrapped in a second fence lost its highlighting and spilled its
tail as prose, and a code anchor read against the wrong version shows unrelated lines.
"""

import argparse
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import threads as T  # noqa: E402
from lib import critical_manifest  # noqa: E402
os.environ["REVIEW_UI_DRAWN"] = "0"   # the views as without the review-ui mod, whatever session runs this (lib/ui.py)


def _spec():
    with open(os.path.join(HERE, "paste-gates.json")) as fh:
        return json.load(fh)


def gate_signature(key):
    """The marker strings the Stop hook looks for in a given gated command's output —
    read from the shipped spec so a render and its gate cannot drift apart silently."""
    return next(g["signature"] for g in _spec()["gates"] if g["key"] == key)


# Mirrors hooks/paste-gate.py's `_FLAGS`, and the helper below mirrors
# skills/review-mr/scripts/test_findings.py's `forbidden_rules`. The hook compiles each
# rule with its own declared flags, so a test that pins the shipped patterns has to
# compile them the same way; each side stays bound to its OWN skill's spec.
_FLAGS = {"m": re.M, "i": re.I, "s": re.S}


def forbidden_rules():
    """{key: compiled pattern} the Stop hook refuses to let reach the user, from the same
    spec — compiled WITH each rule's declared flags, as the hook compiles them.

    Matching the raw pattern string with a flagless `assertRegex` silently diverges from
    the hook the moment a rule is line-anchored with `"flags": "m"`: the hook goes on
    matching a warning that sits on its own line, this test stops, and the failure reads
    as "the producer stopped emitting the warning" when the producer never changed.
    """
    return {
        r["key"]: re.compile(r["text"], sum(_FLAGS[c] for c in r.get("flags", "")))
        for r in _spec()["forbidden"]
    }


def required_rule(key):
    """One of the spec's `required` rules, compiled as the hook compiles it."""
    r = next(x for x in _spec()["required"] if x["key"] == key)
    return re.compile(r["text"], sum(_FLAGS[c] for c in r.get("flags", "")))


class TestRenderChange(unittest.TestCase):
    def test_bare_diff_gets_a_diff_fence(self):
        out = T.render_change("-  a\n+  b\n")
        self.assertEqual(out, "```diff\n-  a\n+  b\n```")

    def test_bare_snippet_uses_the_paths_language(self):
        out = T.render_change("export const a = 1\n", "src/a.ts")
        self.assertEqual(out, "```ts\nexport const a = 1\n```")

    def test_env_file_uses_a_highlightable_language(self):
        out = T.render_change("A=1\n", "backend/local.env")
        self.assertEqual(out, "```bash\nA=1\n```")

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
        self.assertIn("```diff\n", out)  # sniffed from the block's own content

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
        self.assertTrue(
            out.rstrip().endswith("ACK to fix up and push? — or say what to change.")
        )
        self.assertIn("```diff", out)

    def test_the_ack_ask_stays_one_line_and_last(self):
        """Both shapes name the alternatives to an ACK, and both keep them on the closing
        line: the `fixup-ack` gate matches that phrase only at the END of the message, so
        anything printed after it would stop the gate recognising a legitimate ask."""
        with mock.patch.object(T, "_git", lambda *a: ""):
            pointer = T.render_diff_pointer("t1", [("a.ts", 1, 0)], "tmux window 9")
        for out, extra in (
            (pointer, "notes on the diff"),
            (T.render_diff_view("t1", "-a\n+b\n"), "say what to change"),
        ):
            last = out.rstrip().splitlines()[-1]
            self.assertTrue(last.startswith("ACK to fix up and push?"), last)
            self.assertIn(extra, last)

    def test_a_real_ask_is_recognised_as_one(self):
        """The `fixup-ack` rule is what catches an ACK asked with no diff behind it."""
        rule = required_rule("fixup-ack")
        with mock.patch.object(T, "_git", lambda *a: ""):
            pointer = T.render_diff_pointer("t1", [("a.py", 1, 0)], "tmux window 9")
        for name, block in (
            ("pointer", pointer),
            ("inline", T.render_diff_view("t1", "-a\n+b\n")),
        ):
            self.assertTrue(rule.search("Summary of the change.\n\n" + block), name)

    def test_the_ack_rule_is_not_escaped_by_anything_after_it(self):
        """It was briefly anchored to the end of the message as well, which disarmed it:
        one closing sentence — and an improvising model, the only kind this rule sees, is
        exactly the one that adds a closing sentence — walked straight through. Every case
        here was verified against the real hook to pass while that anchor was in place."""
        rule = required_rule("fixup-ack")
        for tail in (
            "\n\nI'll run the full suite right after the rebase.",
            "\n\n- tests pass\n- lint clean",
            "\n\n---",
            "\n\n✅",
            "\n\n```\nx\n```",
            "\n\n(then I'll push)",
            "\n",
        ):
            self.assertTrue(
                rule.search("Fixed t3.\n\nACK to fix up and push?" + tail), repr(tail)
            )

    def test_view_signatures_match_the_stop_hook_gates(self):
        """The gate spec keys off these literals; renaming a header silently disables it.
        Read from the spec, not retyped, so the two cannot drift apart unnoticed."""
        for key, out in (
            ("change-preview", T.render_change_view("t1", "-a\n+b\n")),
            ("diff-view", T.render_diff_view("t1", "-a\n+b\n")),
        ):
            for sig in gate_signature(key):
                self.assertIn(sig, out, key)

    def test_diff_view_signature_survives_a_widened_fence(self):
        """A diff of a markdown file widens the fence to ````diff — which still contains
        the literal ```diff the gate looks for."""
        out = T.render_diff_view("t1", "-```bash\n+```sh\n echo hi\n ```\n")
        self.assertIn("````diff", out)
        self.assertIn("```diff", out)


SAMPLE_DIFF = """diff --git a/src/a.ts b/src/a.ts
--- a/src/a.ts
+++ b/src/a.ts
@@ -1,2 +1,3 @@
 keep
-old
+new
+extra
diff --git a/old/n.ts b/new/n.ts
rename from old/n.ts
rename to new/n.ts
--- a/old/n.ts
+++ b/new/n.ts
@@ -1 +1 @@
-x
+y
diff --git a/gone.ts b/gone.ts
deleted file mode 100644
--- a/gone.ts
+++ /dev/null
@@ -1,2 +0,0 @@
-one
-two
"""


class TestDiffViewRouting(unittest.TestCase):
    """Which of the two shapes `diff-view.sh` prints. The fallback is the case that
    matters: pointing at a window that does not hold the diff would have the user approve
    a force-push against something they never saw."""

    def setUp(self):
        critical_manifest.reset()
        self._git = mock.patch.object(
            T,
            "_git",
            lambda *a: {
                ("rev-parse", "--show-toplevel"): "/repo\n",
                ("remote", "get-url", "origin"): "",
                ("rev-parse", "--abbrev-ref", "HEAD"): "feat/x\n",
            }.get(a, ""),
        )
        self._git.start()
        self.addCleanup(self._git.stop)

    def test_pointer_when_the_viewer_took_the_diff(self):
        with mock.patch.object(
            T.hunk, "show_working_diff", return_value=("sid", "9 (◈!123)")
        ):
            out = T.diff_view_block("t3", SAMPLE_DIFF)
        self.assertIn("\uf4d2 3 files +3 −4", out)
        self.assertIn("tmux window 9 (◈!123)", out)
        self.assertNotIn("```diff", out)
        self.assertTrue(out.rstrip().endswith("notes on the diff in that window."))

    def test_the_rows_are_key_and_value_under_a_yaml_fence(self):
        """The fence language is the only colour a pasted block can carry, and YAML is what
        tints a path apart from its counts. Pinned because dropping the language, or the
        colon that makes each row a key, silently turns the whole block grey again."""
        with mock.patch.object(
            T.hunk, "show_working_diff", return_value=("sid", "9 (◈!123)")
        ):
            out = T.diff_view_block("t3", SAMPLE_DIFF)
        self.assertIn("```yaml", out)
        self.assertIn("src/a.ts:  +2 −1", out)
        self.assertIn("gone.ts:   +0 −2", out)
        self.assertNotIn("|", out)

    def test_the_line_naming_the_window_cannot_be_dropped(self):
        """`fence` marks the rows, so the head line — the only one saying WHERE the diff
        is — was the one line the hook's one-dropped-line tolerance allowed a message to
        lose, leaving filenames, a question about "that window", and no window named."""
        critical_manifest.reset()
        with mock.patch.object(
            T.hunk, "show_working_diff", return_value=("sid", "9 (◈!123)")
        ):
            out = T.diff_view_block("t3", SAMPLE_DIFF)
        head = out.splitlines()[0]
        self.assertIn("tmux window", head)
        self.assertIn(head, critical_manifest.current())

    def test_an_empty_diff_is_refused_not_rendered(self):
        """Both shapes would otherwise ask for a fixup+force-push ACK over a block showing
        nothing, and the gate cannot catch that: an empty fence marks no critical lines and
        both signature strings are still present. Reachable whenever the edits are staged
        (`git diff` is unstaged-only) or a fixup already cleaned the tree."""
        with self.assertRaises(SystemExit):
            T.diff_view_block("t3", "")

    def test_notes_that_never_reached_the_viewer_are_reported(self):
        """The batch is all-or-nothing, so one bad anchor drops all three — and the prose
        above the block has already told the user the agent flagged something. Silence
        there sends them to a window with nothing in it, and then they ACK."""
        note = [{"filePath": "src/a.ts", "newLine": 1, "summary": "x"}]
        with (
            mock.patch.object(
                T.hunk, "show_working_diff", return_value=("sid", "9 (x)")
            ),
            mock.patch.object(T.hunk, "add_notes", return_value=0),
        ):
            out = T.diff_view_block("t3", SAMPLE_DIFF, notes=note)
        self.assertIn("could not be anchored", out)
        self.assertTrue(out.rstrip().endswith("in that window."))

    def test_notes_that_landed_are_not_reported(self):
        note = [{"filePath": "src/a.ts", "newLine": 1, "summary": "x"}]
        with (
            mock.patch.object(
                T.hunk, "show_working_diff", return_value=("sid", "9 (x)")
            ),
            mock.patch.object(T.hunk, "add_notes", return_value=1),
        ):
            out = T.diff_view_block("t3", SAMPLE_DIFF, notes=note)
        self.assertNotIn("could not be anchored", out)

    def test_notes_are_reported_when_the_diff_went_inline(self):
        """No viewer means nowhere to anchor them, which is a normal outcome — but the
        user still has to learn the points are only in the prose."""
        note = [{"filePath": "src/a.ts", "newLine": 1, "summary": "x"}]
        with mock.patch.object(T.hunk, "show_working_diff", return_value=None):
            out = T.diff_view_block("t3", SAMPLE_DIFF, notes=note)
        self.assertIn("could not be anchored", out)

    def test_inline_when_there_is_no_viewer(self):
        with mock.patch.object(T.hunk, "show_working_diff", return_value=None):
            out = T.diff_view_block("t3", SAMPLE_DIFF)
        self.assertIn("```diff", out)
        self.assertIn("rename to new/n.ts", out)

    def test_plain_never_reaches_the_viewer(self):
        """`diff-view.sh` passes --plain when it narrowed the diff with git arguments: the
        viewer reloads from the working tree, so it would be showing something else."""
        with mock.patch.object(T.hunk, "show_working_diff") as show:
            out = T.diff_view_block("t3", SAMPLE_DIFF, plain=True)
        show.assert_not_called()
        self.assertIn("```diff", out)

    def test_the_stat_handed_to_the_viewer_is_the_diffs_own(self):
        """That comparison is the whole verification: the viewer reads the working tree
        itself, so a window holding different files — or the same files at different line
        counts — is not this diff. It is handed the parsed stat, not a bare count."""
        with mock.patch.object(
            T.hunk, "show_working_diff", return_value=("sid", "9 (x)")
        ) as show:
            T.diff_view_block("t3", SAMPLE_DIFF)
        self.assertEqual(
            show.call_args[0][2],
            [("src/a.ts", 2, 1), ("new/n.ts", 1, 1), ("gone.ts", 0, 2)],
        )

    def test_the_window_names_the_mr_worked_on_most_recently(self):
        """Ranked by each state FILE's mtime, not its directory's. A directory's mtime only
        moves when an entry is added or removed, and the state is rewritten in place — so
        on a real store the MR being reworked today ranked below one created later and
        untouched since, and its number went onto the block a force-push is approved from.
        """
        with tempfile.TemporaryDirectory() as root:
            for name, dir_age, file_age in (
                ("acme-api--mr123", 100, 1),
                ("acme-api--mr456", 1, 100),
            ):
                d = os.path.join(root, name)
                os.makedirs(d)
                f = os.path.join(d, "topics.json")
                with open(f, "w") as fh:
                    fh.write("{}")
                os.utime(f, (0, 10_000 - file_age))
                os.utime(d, (0, 10_000 - dir_age))
            with (
                mock.patch.object(T, "STATE_ROOT", root),
                mock.patch.object(T, "project_slug", lambda: "acme-api"),
                mock.patch.object(T, "_git", lambda *a: "git@host:acme/api.git\n"),
            ):
                self.assertEqual(T.window_label(), T.TOPIC_ICON + "!123")

    def own_mr_store(self, root, states):
        """`states`: (iid, source branch or None), oldest first, written to `root`."""
        for age, (iid, branch) in enumerate(reversed(states)):
            d = os.path.join(root, f"acme-api--mr{iid}")
            os.makedirs(d)
            f = os.path.join(d, "topics.json")
            with open(f, "w") as fh:
                json.dump({"iid": iid, "source_branch": branch} if branch else {"iid": iid}, fh)
            os.utime(f, (0, 10_000 - age))

    def fake_git(self, branch, rebase_head=None):
        def git(*a):
            if a[:2] == ("branch", "--show-current"):
                return branch + "\n"
            if a[:2] == ("rev-parse", "--git-path"):
                return (rebase_head or "/nonexistent") + "\n"
            return "git@host:acme/api.git\n"
        return git

    def test_sessions_on_two_mrs_of_one_repo_each_get_their_own(self):
        """Two sessions, two worktrees of one repo, one MR each, one state folder for the
        project: the views of each must take its own MR's state however recently the
        other's was written. Taking the newest put a sibling MR's topic on a card."""
        with tempfile.TemporaryDirectory() as root:
            self.own_mr_store(root, [(3, "feat-a"), (4, "feat-b")])
            with (
                mock.patch.object(T, "STATE_ROOT", root),
                mock.patch.object(T, "project_slug", lambda: "acme-api"),
            ):
                for branch, iid in (("feat-a", 3), ("feat-b", 4)):
                    with mock.patch.object(T, "_git", self.fake_git(branch)):
                        self.assertEqual(T.local_state()[1]["iid"], iid)
                        self.assertEqual(T.window_label(), T.TOPIC_ICON + f"!{iid}")
                # A branch no state names: none of the states that name another.
                with mock.patch.object(T, "_git", self.fake_git("other")):
                    self.assertEqual(T.local_state(), (None, None))

    def test_mid_rebase_the_branch_being_rebased_decides(self):
        with tempfile.TemporaryDirectory() as root:
            self.own_mr_store(root, [(3, "feat-a"), (4, "feat-b")])
            head = os.path.join(root, "head-name")
            with open(head, "w") as fh:
                fh.write("refs/heads/feat-a\n")
            with (
                mock.patch.object(T, "STATE_ROOT", root),
                mock.patch.object(T, "project_slug", lambda: "acme-api"),
                mock.patch.object(T, "_git", self.fake_git("", rebase_head=head)),
            ):
                self.assertEqual(T.local_state()[1]["iid"], 3)

    def test_a_state_naming_no_branch_yields_to_one_naming_this(self):
        """A state written before the branch was kept says nothing either way: taken only
        where no state names the branch checked out."""
        with tempfile.TemporaryDirectory() as root:
            self.own_mr_store(root, [(3, "feat-a"), (4, None)])
            with (
                mock.patch.object(T, "STATE_ROOT", root),
                mock.patch.object(T, "project_slug", lambda: "acme-api"),
            ):
                with mock.patch.object(T, "_git", self.fake_git("feat-a")):
                    self.assertEqual(T.local_state()[1]["iid"], 3)
                with mock.patch.object(T, "_git", self.fake_git("feat-c")):
                    self.assertEqual(T.local_state()[1]["iid"], 4)

    def test_naming_the_window_never_calls_glab(self):
        """`diff-view` is independent of glab and the network by design, and this label is
        cosmetic — it must not put a subprocess, or a hang, on every diff shown."""
        with mock.patch.object(T, "context") as ctx, mock.patch.object(T, "run") as run:
            with mock.patch.object(T, "_git", lambda *a: ""):
                T.window_label()
            ctx.assert_not_called()
            run.assert_not_called()

    def test_window_label_falls_back_to_the_branch(self):
        self.assertEqual(T.window_label(), "◈ feat/x")

    def test_both_shapes_satisfy_the_stop_hook_gate(self):
        """The gate has one signature and two renders to cover; pinning it to the old
        ```diff marker would have left the pointer shape — the common one — ungated."""
        with mock.patch.object(
            T.hunk, "show_working_diff", return_value=("sid", "9 (◈!123)")
        ):
            pointer = T.diff_view_block("t3", SAMPLE_DIFF)
        with mock.patch.object(T.hunk, "show_working_diff", return_value=None):
            inline = T.diff_view_block("t3", SAMPLE_DIFF)
        for sig in gate_signature("diff-view"):
            self.assertIn(sig, pointer, "pointer render")
            self.assertIn(sig, inline, "inline render")


class TestHunkNoteHandover(unittest.TestCase):
    """The two commands that decide whether a force-push may proceed. Both read "nothing
    there" as permission, so the case worth testing is the one where nothing came back
    because nothing could be ASKED."""

    def test_a_note_is_reported_with_its_file_and_line(self):
        self.assertEqual(
            T.hunk_note_lines([("a.py", "new", 42, "why not keep the map?", "n1")]),
            ["a.py:42: why not keep the map?"],
        )

    def test_a_note_on_a_removed_line_says_so(self):
        """Without it the number reads as a line in the file the model is about to edit,
        where the deletion has since put something unrelated."""
        self.assertEqual(
            T.hunk_note_lines([("b.py", "old", 7, "why drop this?", "n2")]),
            ["b.py:7 (on a removed line): why drop this?"],
        )

    def test_an_mr_level_note_has_no_line_to_print(self):
        self.assertEqual(
            T.hunk_note_lines([("c.py", "new", None, "general point", "n3")]),
            ["c.py: general point"],
        )

    def test_a_clean_window_closes(self):
        self.assertEqual(T.hunk_close_decision([]), (None, True))

    def test_a_note_left_since_the_last_read_keeps_the_window(self):
        say, close = T.hunk_close_decision([("a.py", "new", 1, "hold on", "n1")])
        self.assertFalse(close)
        self.assertIn("hunk-notes", say)

    def test_a_viewer_that_could_not_be_asked_keeps_the_window(self):
        """The window must not be killed over a note nobody has read. A daemon that did
        not answer is not an empty window, and closing takes the diff with it."""
        say, close = T.hunk_close_decision(None)
        self.assertFalse(close)
        self.assertIn("Could not read", say)


class TestCriticalManifest(unittest.TestCase):
    """paste-gate.py no longer parses fence widths itself — it trusts whatever this
    script's own `fence()` (the single choke point every code-bearing render routes
    through) declares critical. This is where that concern actually lives now; see
    hooks/test_paste_gate.py's own note on why it moved here."""

    def setUp(self):
        critical_manifest.reset()

    def test_fence_content_is_marked_critical(self):
        T.fence("a = 1\nb = 2", "python")
        self.assertEqual(set(critical_manifest.current()), {"a = 1", "b = 2"})

    def test_fence_bars_are_not_marked_critical(self):
        """The opening/closing ``` lines are delimiters, not content the user is meant
        to judge a finding against — same convention as review-mr's code_snippet."""
        T.fence("a = 1", "python")
        self.assertNotIn("```python", critical_manifest.current())
        self.assertNotIn("```", critical_manifest.current())

    def test_multiple_embedded_fence_markers_all_stay_critical(self):
        """A single fence can legitimately contain more than one embedded backtick run
        of DIFFERENT widths — a diff touching two different markdown snippets, say.
        `fence()` widens to strictly more than the widest of them, so every line of the
        content — including the embedded ``` markers themselves, which are DATA here,
        not delimiters — must still be marked critical, not just the lines before the
        first embed."""
        content = (
            "# first illustration:\n```\nold snippet\n```\n"
            "# second illustration, a bit wider:\n````\nnewer snippet\n````\n"
            "for _ in range(MAX_RETRIES):\n    resp = self._send(req)"
        )
        out = T.fence(content, "python")
        self.assertTrue(
            out.startswith("`````python\n")
        )  # widened past the widest embed
        for line in content.splitlines():
            self.assertIn(line.strip(), critical_manifest.current())

    def test_render_table_marks_rows_not_header(self):
        state = {
            "iid": 1,
            "title": "x",
            "topics": [
                {
                    "id": "t1",
                    "thread_ids": ["d1"],
                    "summary": "s",
                    "state": None,
                    "decision": None,
                    "plan": None,
                    "diff_url": None,
                },
            ],
            "threads": {
                "d1": {"file": "a.py", "line": 1, "resolved": False, "awaiting": "you"}
            },
        }
        out = T.render_table(state)
        row = next(ln for ln in out.splitlines() if ln.startswith("| ○"))
        self.assertIn(row, critical_manifest.current())
        self.assertNotIn(
            "| Status | Topic | Location | Summary |", critical_manifest.current()
        )


class TestLocationlessThread(unittest.TestCase):
    """A thread on the merge request itself carries no diff position. Mirrors
    review-mr's TestLocationlessTopic: the table showed a visible pair of empty
    backticks and `quote` showed `None:`."""

    def _state(self, **thread):
        return {
            "iid": 1,
            "title": "x",
            "topics": [
                {
                    "id": "t1",
                    "thread_ids": ["d1"],
                    "summary": "s",
                    "state": None,
                    "decision": None,
                    "plan": None,
                    "diff_url": None,
                }
            ],
            "threads": {"d1": {"resolved": False, "awaiting": "you", **thread}},
        }

    def test_table_names_it_instead_of_empty_backticks(self):
        row = next(
            ln
            for ln in T.render_table(self._state(file=None, line=None)).splitlines()
            if ln.startswith("| ○")
        )
        self.assertIn("MR-level", row)
        self.assertNotIn("``", row)

    def test_table_still_shows_a_real_location(self):
        row = next(
            ln
            for ln in T.render_table(self._state(file="src/a.py", line=7)).splitlines()
            if ln.startswith("| ○")
        )
        self.assertIn("`a.py:7`", row)

    def test_the_further_thread_count_survives_an_mr_level_topic(self):
        """The count used to sit inside the code span, which an empty location has
        nowhere to put — `` (+1)`` was the result."""
        state = self._state(file=None, line=None)
        state["topics"][0]["thread_ids"] = ["d1", "d2"]
        state["threads"]["d2"] = {"resolved": False, "awaiting": "you"}
        row = next(
            ln for ln in T.render_table(state).splitlines() if ln.startswith("| ○")
        )
        self.assertIn("MR-level", row)
        self.assertIn("(+1)", row)
        self.assertNotIn("``", row)

    def test_quote_prints_no_none(self):
        out = T.render_quote(
            self._state(
                file=None, line=None, body="a comment", author="Someone", notes=[]
            ),
            "t1",
        )
        self.assertIn("MR-level", out)
        self.assertNotIn("None:", out)


class TestSummaryIsAuthored(unittest.TestCase):
    """A topic's Summary column is how the user tells one of fifty topics from another.
    Without this it held the reviewer's own sentence, truncated at 72 characters — three
    rows of one real MR opened with a ```suggestion fence. Mirrors review-mr's
    TestNeedsTitle, with the flag derived rather than stored (see `needs_summary`)."""

    def _state(
        self,
        summary=None,
        body="Hier passt eine Generator Function.",
        state_=None,
        diff_url=None,
    ):
        return {
            "iid": 1,
            "title": "x",
            "topics": [
                {
                    "id": "t1",
                    "thread_ids": ["d1"],
                    "summary": summary,
                    "state": state_,
                    "decision": None,
                    "plan": None,
                    "diff_url": diff_url,
                }
            ],
            "threads": {
                "d1": {
                    "resolved": False,
                    "awaiting": "you",
                    "author": "Robin",
                    "body": body,
                    "file": "src/a.py",
                    "line": 7,
                    "notes": [{"author": "Robin", "body": body}],
                }
            },
        }

    def _row(self, state):
        return next(
            ln
            for ln in T.render_table(state, show_done=True).splitlines()
            if ln.startswith("|") and "t1" in ln
        )

    def test_an_unsummarised_topic_is_flagged_in_the_table(self):
        self.assertIn("✍️ _needs summary:_", self._row(self._state()))

    def test_an_authored_summary_is_not_flagged(self):
        self.assertNotIn(
            "needs summary", self._row(self._state(summary="retry is unbounded"))
        )

    def test_the_raw_fallback_carries_no_markup(self):
        """The reported case: a comment opening with a fence put ``` into the cell."""
        row = self._row(self._state(body="```suggestion:-6+0\nREQUIRED = {1}\n```"))
        self.assertNotIn("```", row)
        self.assertIn("(no prose — code only)", row)

    def test_a_done_topic_is_exempt(self):
        """It is closed, nothing acts on it, and its row only shows with `sync --all`."""
        state = self._state()
        state["threads"]["d1"]["resolved"] = True
        self.assertNotIn("needs summary", self._row(state))

    def test_the_quote_heading_says_it_too(self):
        out = T.render_quote(self._state(), "t1")
        self.assertIn("needs an English summary", out)

    def test_a_german_summary_is_named_in_the_table(self):
        """The summary sits next to a German thread the whole time, which is the pull
        that produced a whole review with every summary in the wrong language."""
        out = T.render_table(self._state(summary="Das Feld wird nicht mehr gesetzt"))
        self.assertIn("summaries must be English", out)
        self.assertIn("◈ t1", out)

    def test_an_english_summary_is_left_alone(self):
        out = T.render_table(self._state(summary="the field is no longer set"))
        self.assertNotIn("summaries must be English", out)

    def test_both_warnings_match_the_stop_hook_rules(self):
        """The gate spec keys off these literals; rewording one silently disables it."""
        rules = forbidden_rules()
        state = self._state()
        self.assertRegex(self._row(state), rules["unsummarised-topic-in-a-table"])
        self.assertRegex(
            T.render_quote(state, "t1"), rules["unsummarised-topic-in-a-quote"]
        )
        self.assertRegex(
            T.render_table(self._state(summary="Das wird nicht gesetzt")),
            rules["summary-in-the-threads-language"],
        )


class TestCurrentTopic(unittest.TestCase):
    """The current topic is what the opener shows and what every view declares to the
    paste gate. The rules themselves are lib/test_focus.py's; this pins the wiring."""

    @staticmethod
    def state(**extra):
        th = lambda awaiting: {"author": "Robin", "body": "b", "file": "a.py",  # noqa: E731
                               "line": 1, "resolved": False, "awaiting": awaiting,
                               "note_count": 1, "url": "http://gl/1"}
        return {"iid": 1, "threads": {"d1": th("you"), "d2": th("you")},
                "topics": [{"id": "t1", "summary": "point one", "thread_ids": ["d1"],
                            "state": None, **extra},
                           {"id": "t2", "summary": "point two", "thread_ids": ["d2"],
                            "state": None}]}

    def test_the_opener_prefers_open_work_over_a_reply_left_to_write(self):
        state = self.state(diff_url="http://gl/diff")      # t1 pushed, reply pending
        state["threads"]["d1"]["awaiting"] = "reviewer"
        self.assertEqual(T.topic_status(state, state["topics"][0]), "reply_pending")
        self.assertEqual(T.next_topic(state)["id"], "t2")
        state["topics"][1]["state"] = "waiting"
        state["threads"]["d2"]["awaiting"] = "reviewer"
        self.assertEqual(T.next_topic(state)["id"], "t1")

    def test_a_jump_is_what_the_opener_shows_next_time(self):
        state = self.state()
        T.CURRENT.jump(state, "t2")
        self.assertIn("point two", T.render_present(state).split("---")[-1])

    def test_marking_it_waiting_moves_it_on(self):
        state = self.state()
        self.assertEqual(T.current_topic(state), "t1")
        state["topics"][0]["state"] = "waiting"
        state["threads"]["d1"]["awaiting"] = "reviewer"
        self.assertEqual(T.current_topic(state), "t2")

    def test_a_stateless_view_names_its_topic_but_not_the_current_one(self):
        """So the hook enforces it whatever is current: a change is never research."""
        with tempfile.NamedTemporaryFile("w", suffix=".diff", delete=False) as fh:
            fh.write("-a\n+b\n")
        self.addCleanup(os.unlink, fh.name)
        out = subprocess.run(
            [sys.executable, os.path.join(HERE, "threads.py"), "change-view", "t3",
             fh.name], capture_output=True, text=True, check=True,
            env={**os.environ, "AGENT_SKILLS_PASTE_GATE": "1"}).stdout
        payload = json.loads(out.split("<!-- paste-gate:critical\n")[1]
                             .rsplit("\n-->", 1)[0])
        self.assertEqual(payload["topic"], "t3")
        self.assertNotIn("focus", payload)


class TestCommentSource(unittest.TestCase):
    """What a reviewer's suggestion is diffed against: the blob the comment was made on."""

    def test_read_from_the_side_the_comment_is_on(self):
        x = {"file": "a.py", "side": "old", "head_sha": "H", "base_sha": "B",
             "body": "```suggestion\nx\n```"}
        with mock.patch.object(T, "_blob_text", return_value="a\nb\n") as blob:
            self.assertEqual(T._comment_source({}, x), ["a", "b"])
        blob.assert_called_once_with("B", "a.py")

    def test_a_commit_gone_locally_is_read_from_gitlab(self):
        """Your own fixup and force-push replace the version the reviewer commented on."""
        x = {"file": "a.py", "head_sha": "H", "body": "```suggestion\nx\n```"}
        with mock.patch.object(T, "_blob_text", return_value=None), \
                mock.patch.object(T, "file_at", return_value="old\n") as api:
            self.assertEqual(T._comment_source({"project": "g/r"}, x), ["old"])
        api.assert_called_once_with("g/r", "H", "a.py")

    def test_not_read_at_all_without_a_suggestion(self):
        with mock.patch.object(T, "_blob_text") as blob:
            self.assertIsNone(
                T._comment_source({}, {"file": "a.py", "body": "Sieht gut aus."})
            )
        blob.assert_not_called()


class TestQuoteNotes(unittest.TestCase):
    """The note renderer itself is lib/test_fences.py's; this pins that `quote` hands it
    the thread's file, which is what the suggestion's fence language comes from."""

    NOTE = (
        "Minor:\n\nDer Test schaut nicht wirklich ob die Reihenfolge aus `fields` "
        "übernommen wird. Entweder:\n\n"
        "```suggestion:-0+0\n  it('lists multiple changed leaves', () => {\n```\n\n"
        "Oder `ExtractedData` umdrehen?\n\n"
        "    const original: ExtractedData = {\n"
        "      money_related: object({ summe: scalar(10) }),\n"
        "    }\n"
    )

    def test_quote_passes_the_file_and_anchor_through(self):
        state = {
            "iid": 1,
            "threads": {
                "d1": {
                    "author": "Robin",
                    "file": "src/x.test.ts",
                    "line": 184,
                    "body": self.NOTE,
                    "resolved": False,
                    "note_count": 1,
                    "url": "http://gl/1",
                    "notes": [{"author": "Robin", "body": self.NOTE}],
                }
            },
            "topics": [
                {
                    "id": "t2",
                    "summary": "order test",
                    "thread_ids": ["d1"],
                    "state": None,
                }
            ],
        }
        out = T.render_quote(state, "t2")
        self.assertIn("> ```ts\n", out)                    # the file's language


class TestSyncRefreshesThreads(unittest.TestCase):
    """Every fetched field must reach a thread that is ALREADY in the state file.

    This is the bug that made two rounds of `quote` improvements invisible in a live
    rework: sync copied a hardcoded allowlist of keys onto an existing thread, so
    `side`/`head_sha`/`line_range` only ever landed on threads seen for the first time.
    A fresh session on a days-old MR kept rendering "(working tree)" with no span.
    """

    POSITION = {
        "new_path": "src/x.ts",
        "old_path": "src/x.ts",
        "new_line": 22,
        "head_sha": "abc123def456",
        "start_sha": "999base",
        "base_sha": "888base",
        "line_range": {
            "start": {"new_line": 12, "old_line": None},
            "end": {"new_line": 22, "old_line": None},
        },
    }

    def live(self, position=None, body="Unit test?"):
        note = {
            "resolvable": True,
            "id": 7361603,
            "author": {"name": "Robin"},
            "body": body,
            "resolved": False,
            "position": self.POSITION if position is None else position,
        }
        orig = T.api
        T.api = lambda *a, **k: [{"id": "d1", "notes": [note]}]
        try:
            return T.fetch_threads({"enc": "x", "web": "http://gl"}, 575, "me")
        finally:
            T.api = orig

    def old_shaped_state(self):
        """A thread as it was stored before side/head_sha/line_range existed."""
        return {
            "iid": 575,
            "title": "T",
            "threads": {
                "d1": {
                    "author": "Robin",
                    "file": "src/x.ts",
                    "line": 22,
                    "body": "Unit test?",
                    "resolved": False,
                    "url": "http://gl/1",
                    "note_count": 1,
                    "last_author": "Robin",
                    "awaiting": "you",
                }
            },
            "topics": [
                {"id": "t3", "summary": "s", "thread_ids": ["d1"], "state": None}
            ],
        }

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
        pos = dict(
            self.POSITION,
            line_range={"start": {"new_line": None}, "end": {"new_line": 22}},
        )
        state = self.old_shaped_state()
        T.sync(state, self.live(position=pos))
        self.assertNotIn("line_start", state["threads"]["d1"])

    def test_an_old_side_comment_takes_the_base_blob(self):
        pos = {
            "new_path": "src/x.ts",
            "old_path": "src/old.ts",
            "new_line": None,
            "old_line": 40,
            "head_sha": "head",
            "start_sha": "start",
            "line_range": {"start": {"old_line": 35}, "end": {"old_line": 40}},
        }
        state = self.old_shaped_state()
        T.sync(state, self.live(position=pos))
        x = state["threads"]["d1"]
        self.assertEqual((x["side"], x["file"], x["line"]), ("old", "src/old.ts", 40))
        self.assertEqual(x["base_sha"], "start")
        self.assertEqual((x["line_start"], x["line_end"]), (35, 40))


class TestReplyDraft(unittest.TestCase):
    """The draft lives in the state file, and `reply` is the only way out of it."""

    def state(self, reply=None):
        t = {
            "id": "t1",
            "summary": "retry unbounded",
            "thread_ids": ["d1"],
            "state": None,
        }
        if reply is not None:
            t["reply"] = reply
        return {
            "iid": 7,
            "title": "x",
            "topics": [t],
            "threads": {
                "d1": {
                    "author": "Robin",
                    "file": "src/client.py",
                    "line": 88,
                    "body": "unbounded retry",
                    "resolved": False,
                    "note_count": 1,
                    "url": "http://gl/y/1",
                    "notes": [{"author": "Robin", "body": "unbounded retry"}],
                }
            },
        }

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
            self.assertEqual(
                T.reply_body(self.state(), "t1", d), "Aus der alten Datei.\n"
            )

    def test_state_wins_over_the_legacy_file(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "reply-t1.md"), "w") as f:
                f.write("stale\n")
            self.assertEqual(T.reply_body(self.state("fresh\n"), "t1", d), "fresh\n")

    def test_prose_is_blockquoted(self):
        self.assertEqual(T._quote_draft("Kurz.\n"), "> Kurz.")

    def test_a_draft_is_one_quote_its_code_included(self):
        """A fence inside a quote still renders highlighted, and a blank line between the
        parts would end the quote — they are joined by a `>` line instead."""
        out = T._quote_draft("Danke:\n\n```python\nx = 1\n```\n\nPasst?\n", "src/a.py")
        self.assertEqual(out, "> Danke:\n>\n> ```python\n> x = 1\n> ```\n>\n> Passt?")

    def test_untagged_draft_fence_gets_the_files_language(self):
        self.assertIn("```python", T._quote_draft("```\nx = 1\n```\n", "src/a.py"))

    def test_reply_view_carries_every_part(self):
        out = T.render_reply_view(self.state("Gefixt.\n"), "t1", "Gefixt.\n")
        for part in (
            "◈ t1",
            "src/client.py:88",
            "http://gl/y/1",
            "unbounded retry",
            "**Draft reply:**",
            "> Gefixt.",
            "Thread (to post on):",
            "**`c`** copy to clipboard",
        ):
            self.assertIn(part, out)

    def test_reply_view_signature_matches_the_stop_hook_gate(self):
        """Read from the shipped spec rather than retyped here: a render whose label or
        prompt drifts out of the gate's signature stops being enforced, and a gate that
        never fires is invisible — nothing tells you the block went unpasted."""
        for refine in (False, True):
            out = T.render_reply_view(
                self.state("Gefixt.\n"), "t1", "Gefixt.\n", refine
            )
            for sig in gate_signature("reply-view"):
                self.assertIn(sig, out, f"--refine={refine}")

    def _shown(self, reply):
        """A state whose topic has already had its context rendered in full — which is
        what `--refine` is allowed to leave out."""
        state = self.state(reply)
        T.render_quote(state, "t1")  # records the digest, as every full view does
        return state

    def test_refine_drops_the_context_and_keeps_the_ask(self):
        """Re-showing a reworded draft repeats only what changed. The code and the thread
        were pasted in full when the topic came up and have not changed since; pasting
        them again buries the draft the user asked to see."""
        out = T.render_reply_view(
            self._shown("Kürzer.\n"), "t1", "Kürzer.\n", refine=True
        )
        for gone in ("src/client.py:88", "unbounded retry", "> **Robin**"):
            self.assertNotIn(gone, out)
        for kept in (
            "◈ t1",
            "> Kürzer.",
            "Thread (to post on): http://gl/y/1",
            "**`c`** copy to clipboard",
        ):
            self.assertIn(kept, out)

    def test_refine_of_a_drawn_reply_is_not_set_off_by_the_draft_it_rewords(self):
        """Where the mod draws, the topic's card carries its draft. A reworded draft is no
        change of context: the refined card is the draft alone."""
        with mock.patch.dict(os.environ, {"REVIEW_UI_DRAWN": "1"}), \
                mock.patch.object(T, "step_of", return_value="reply"):
            state = self._shown("Gefixt, und noch viel mehr.\n")
            T.topic_for(state, "t1")["reply"] = "Kürzer.\n"
            out = T.render_reply_view(state, "t1", "Kürzer.\n", refine=True)
        self.assertNotIn("changed since this was last shown", out)
        self.assertNotIn("unbounded retry", out)

    def test_refine_falls_back_when_the_context_was_never_shown(self):
        """The short render must not be a topic's first reply view — the user would be
        asked about a comment they have not seen. Nothing recorded, nothing to leave out."""
        out = T.render_reply_view(
            self.state("Gefixt.\n"), "t1", "Gefixt.\n", refine=True
        )
        self.assertIn("has not been shown yet", out)
        self.assertIn("unbounded retry", out)  # the thread, in full
        self.assertIn("**`c`** copy to clipboard", out)  # still the same ask

    def test_refine_falls_back_when_a_new_note_arrived(self):
        """A `sync` mid-topic can bring a reviewer note. Leaving the thread out then would
        answer a comment the user is not looking at."""
        state = self._shown("Gefixt.\n")
        thread = state["threads"]["d1"]
        thread["notes"].append(
            {"author": "Robin", "body": "Und was ist mit dem Timeout?"}
        )
        thread["note_count"] = 2
        out = T.render_reply_view(state, "t1", "Gefixt.\n", refine=True)
        self.assertIn("changed since this was last shown", out)
        self.assertIn("Und was ist mit dem Timeout?", out)

    def test_refine_falls_back_when_the_comment_was_re_anchored(self):
        """The reviewer can move their comment; the lines under it are then different
        code from the ones the draft was written against."""
        state = self._shown("Gefixt.\n")
        state["threads"]["d1"]["line"] = 120
        out = T.render_reply_view(state, "t1", "Gefixt.\n", refine=True)
        self.assertIn("changed since this was last shown", out)
        self.assertIn("src/client.py:120", out)

    def test_rewording_the_draft_does_not_count_as_a_changed_context(self):
        """The whole point: the draft is not context, so iterating on it keeps the short
        render however many times it takes."""
        state = self._shown("Erste Fassung.\n")
        for body in ("Zweite Fassung.\n", "Dritte Fassung.\n"):
            out = T.render_reply_view(state, "t1", body, refine=True)
            self.assertNotIn("in full", out)
            self.assertNotIn("unbounded retry", out)

    def test_the_comparison_leaves_no_critical_lines_behind(self):
        """The digest re-renders the topic and throws it away. Its code lines must not
        reach the manifest — the Stop hook would demand lines that are nowhere in the
        message and block a correct reply."""
        critical_manifest.reset()
        state = self.state("Gefixt.\n")
        T.context_digest(state, "t1")
        self.assertEqual(critical_manifest.current(), [])


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
        cls.sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=cls.repo, capture_output=True, text=True
        ).stdout.strip()
        cls.cwd = os.getcwd()
        os.chdir(cls.repo)  # the helpers shell out to git in cwd

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
        base = {
            "file": self.file,
            "line": 10,
            "side": "new",
            "head_sha": self.sha,
            "base_sha": self.sha,
        }
        base.update(kw)
        return base

    def test_window_is_centred_and_marked(self):
        out = T.render_code_context(self.thread())
        self.assertIn("► 10 | const line10 = 10", out)
        self.assertIn("   4 | const line4 = 4", out)  # 6 lines of context
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
        self._write(
            "// header added on top\n"
            + "\n".join(f"const line{i} = {i}" for i in range(1, 31))
            + "\n"
        )
        try:
            out = T.render_code_context(self.thread())
            self.assertIn("► 10 | const line10 = 10", out)  # not line9, as reviewed
            # The edit only moved the commented line: nothing to say about it.
            self.assertNotIn("Changed since the comment", out)
        finally:
            self._write("\n".join(f"const line{i} = {i}" for i in range(1, 31)) + "\n")

    def test_a_change_on_the_commented_lines_follows_as_a_diff(self):
        lines = [f"const line{i} = {i}" for i in range(1, 31)]
        lines[9] = "const line10 = 100"
        self._write("\n".join(lines) + "\n")
        try:
            out = T.render_code_context(self.thread())
            self.assertIn("► 10 | const line10 = 10", out)
            after = out.split("Changed since the comment", 1)[1]
            self.assertIn("-const line10 = 10", after)
            self.assertIn("+const line10 = 100", after)
        finally:
            self._write("\n".join(f"const line{i} = {i}" for i in range(1, 31)) + "\n")

    def test_a_file_renamed_since_is_said_in_a_line_not_shown_as_every_line_removed(self):
        # Another topic's fix renamed the file, pushed before this topic is reached.
        self._git("mv", self.file, "renamed.ts")
        self._git("commit", "-qm", "rename")
        try:
            out = T.render_code_context(self.thread())
            self.assertIn("_Since renamed to `renamed.ts`._", out)
            self.assertNotIn("Changed since the comment", out)
            self.assertNotIn("-const line1 = 1", out)
        finally:
            self._git("reset", "-q", "--hard", self.sha)

    def test_the_change_s_heading_says_working_tree_only_while_it_is_there(self):
        lines = [f"const line{i} = {i}" for i in range(1, 31)]
        lines[9] = "const line10 = 100"
        self._write("\n".join(lines) + "\n")
        try:
            self.assertIn("Changed since the comment, in your working tree:", T.render_code_context(self.thread()))
            self._git("commit", "-qam", "fix")         # committed, as once pushed
            out = T.render_code_context(self.thread())
            self.assertIn("_Changed since the comment:_", out)
            self.assertNotIn("working tree", out)
        finally:
            self._git("reset", "-q", "--hard", self.sha)

    def pushed_state(self, **topic):
        return {"iid": 3, "topics": [{"id": "t1", "summary": "x", "thread_ids": ["d1"], "decision": "y",
                                      "plan": "Make it 100, as the reviewer asked, and say so in the docs too.",
                                      "outcome": "fix", "diff_url": "http://gl/d", **topic}],
                "threads": {"d1": {**self.thread(), "resolved": False,
                                   "notes": [{"author": "Haas, Robin - X1", "body": "100?", "url": "http://gl/n1"},
                                             {"author": "Me", "body": "Fixed: http://gl/d", "mine": True,
                                              "url": "http://gl/n2"}]}}}

    def test_a_pushed_fix_s_card_is_its_steps_with_no_diff_of_it(self):
        lines = [f"const line{i} = {i}" for i in range(1, 31)]
        lines[9] = "const line10 = 100"
        self._write("\n".join(lines) + "\n")
        state = self.pushed_state()
        try:
            self._git("commit", "-qam", "fix")
            after = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.repo, capture_output=True,
                                   text=True).stdout.strip()
            T.fix_refs.keep(self.repo, "rework-mr", 3, "t1/push1", self.sha, after)
            state["topics"][0]["pushes"] = [{"before": self.sha, "start": self.sha, "after": after,
                                             "url": "http://gl/d", "clean": True}]
            with mock.patch.dict(os.environ, {"REVIEW_UI_DRAWN": "1"}), \
                    mock.patch.object(T, "step_of", return_value="reply"):
                out = T.render_quote(state, "t1")
            # The mod offers Show fix in hunk on it.
            self.assertTrue(T.state_json(state)["topics"][0]["fix_kept"])
        finally:
            T.fix_refs.drop(self.repo, "rework-mr", 3)
            self._git("reset", "-q", "--hard", self.sha)
        # Neither what changed since the comment nor a copy of the fix: the viewer shows it.
        self.assertNotIn("Changed since the comment", out)
        self.assertNotIn("+const line10 = 100", out)
        self.assertNotIn("Agreed plan", out)
        self.assertIn(f"{T.ui.STEP_DONE_ICON} Plan agreed: Make it 100, as the reviewer asked, and say so in the docs too.", out)
        self.assertIn(f"{T.ui.STEP_DONE_ICON} Fix pushed · {T.diffsize.text(1, 1, 1)} · [the diff {T.LINK_ICON}](http://gl/d)", out)
        # Not posted yet: nothing waits on the reviewer.
        self.assertNotIn("Reply posted", out)
        self.assertNotIn("Waits on", out)

    def test_once_its_reply_is_posted_it_waits_on_the_reviewer(self):
        # The link the Post press stored, over the thread's last comment of the user's.
        state = self.pushed_state(state="waiting", reply_posted="http://gl/n3")
        with mock.patch.dict(os.environ, {"REVIEW_UI_DRAWN": "1"}), \
                mock.patch.object(T, "step_of", return_value="none"):
            out = T.render_quote(state, "t1")
        # Pushed before the fix was kept in git: the link alone, no size.
        self.assertIn(f"{T.ui.STEP_DONE_ICON} Fix pushed · [the diff {T.LINK_ICON}](http://gl/d)", out)
        self.assertIn(f"{T.ui.STEP_DONE_ICON} Reply posted · [your reply {T.LINK_ICON}](http://gl/n3)", out)
        self.assertIn(f"{T.ui.STEP_WAITS_ICON} Waits on Robin", out)
        state["threads"]["d1"]["resolved"] = True
        with mock.patch.dict(os.environ, {"REVIEW_UI_DRAWN": "1"}), \
                mock.patch.object(T, "step_of", return_value="none"):
            self.assertNotIn("Waits on", T.render_quote(state, "t1"))

    def test_a_fix_pushed_before_the_refs_is_found_again_from_its_link(self):
        self._git("commit", "-q", "--allow-empty", "-m", "the push")
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.repo, capture_output=True,
                              text=True).stdout.strip()
        state = self.pushed_state(diff_url=f"http://gl/-/merge_requests/3/diffs?diff_id=9&start_sha={self.sha}")
        state["topics"].append({**state["topics"][0], "id": "t2",
                                "diff_url": "http://gl/-/merge_requests/3/diffs?diff_id=8&start_sha=" + "1" * 40})
        try:
            with mock.patch.object(T, "versions", return_value=[{"id": 9, "head_commit_sha": head},
                                                                {"id": 8, "head_commit_sha": self.sha}]) as vs:
                T.keep_pushed_fixes(state, {}, 3)
                self.assertEqual(T.fix_refs.kept(self.repo, "rework-mr", 3), {"t1/push1": (self.sha, head)})
                # One push: GitLab has no version between the link's two ends.
                self.assertEqual(state["topics"][0]["pushes"], [
                    {"before": self.sha, "start": self.sha, "after": head,
                     "url": state["topics"][0]["diff_url"], "clean": True}])
                # A start git no longer has: the link alone, and not asked again.
                self.assertTrue(state["topics"][1].get("fix_lost"))
                T.keep_pushed_fixes(state, {}, 3)
                self.assertEqual(vs.call_count, 1)
            # Merged: every fix of the MR kept in git goes.
            state["mr_status"] = {"state": "merged"}
            T.keep_pushed_fixes(state, {}, 3)
            self.assertEqual(T.fix_refs.kept(self.repo, "rework-mr", 3), {})
        finally:
            T.fix_refs.drop(self.repo, "rework-mr", 3)
            self._git("reset", "-q", "--hard", self.sha)

    def test_the_plan_is_cut_to_one_line_at_a_word(self):
        self.assertEqual(T._one_line("Rename the hook to useQueryKeys everywhere it is imported, and keep the old name.\nMore."),
                         "Rename the hook to useQueryKeys everywhere it is imported, and keep the…")
        self.assertEqual(T._one_line("Short.\nSecond line."), "Short.")

    def test_a_reply_posted_by_hand_is_the_thread_s_last_comment(self):
        state = self.pushed_state(state="waiting")
        self.assertEqual(T.reply_posted(state, state["topics"][0]), "http://gl/n2")
        state["topics"][0]["state"] = None
        self.assertIsNone(T.reply_posted(state, state["topics"][0]))

    def test_a_change_elsewhere_in_the_file_says_nothing(self):
        lines = [f"const line{i} = {i}" for i in range(1, 31)]
        lines[27] = "const line28 = 0"
        self._write("\n".join(lines) + "\n")
        try:
            self.assertNotIn("Changed since", T.render_code_context(self.thread()))
        finally:
            self._write("\n".join(f"const line{i} = {i}" for i in range(1, 31)) + "\n")

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
        self.assertIn("   5 | const line5 = 5", out)  # 3 above the span
        self.assertNotIn("line4 =", out)
        self.assertIn("  15 | const line15 = 15", out)  # 1 below, no more
        self.assertNotIn("line16 =", out)

    def test_doc_comment_above_a_span_is_pulled_in(self):
        """`/** … */` explains the marked code; stopping one line short of it is the
        difference between context and a fragment."""
        self._write(
            "\n".join(
                [
                    "const a = 1",
                    "",
                    "/**",
                    " * why this exists",
                    " */",
                    "function f() {",
                    "  return 1",
                    "}",
                ]
            )
            + "\n"
        )
        self._git("commit", "-qam", "doc")
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.repo, capture_output=True, text=True
        ).stdout.strip()
        try:
            out = T.render_code_context(
                self.thread(line=8, line_start=6, line_end=8, head_sha=sha)
            )
            self.assertIn("/**", out)
            self.assertIn("* why this exists", out)
            self.assertNotIn("const a = 1", out)  # blank line ends the block
        finally:
            self._write("\n".join(f"const line{i} = {i}" for i in range(1, 31)) + "\n")
            self._git("commit", "-qam", "restore")

    def test_a_range_disagreeing_with_the_anchor_is_distrusted(self):
        out = T.render_code_context(self.thread(line=20, line_start=8, line_end=14))
        self.assertIn("► 20 |", out)
        self.assertNotIn("┃", out)

    def test_a_reversed_or_partial_range_falls_back_to_the_anchor(self):
        for bad in (
            {"line_start": 14, "line_end": 8},
            {"line_start": 8},
            {"line_end": 14},
            {"line_start": "8", "line_end": "14"},
        ):
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
        out = T.render_code_context(
            self.thread(file=self.big, line=200, line_start=1, line_end=200)
        )
        self.assertIn("200 lines in total", out)
        self.assertIn("┃   1 | const big1 = 1", out)
        self.assertIn("┃ 200 | const big200 = 200", out)
        self.assertNotIn("big100 =", out)
        self.assertLess(len(out.splitlines()), T.MAX_BODY + 10)

    def test_fetch_to_sync_to_quote_renders_the_span(self):
        """The whole chain, because the parts were each right while the seam was not: a
        GitLab position with a line_range, through fetch and sync, into `quote`."""
        note = {
            "resolvable": True,
            "id": 1,
            "author": {"name": "Robin"},
            "resolved": False,
            "body": "Ganze Funktion — Test?",
            "position": {
                "new_path": self.file,
                "old_path": self.file,
                "new_line": 14,
                "head_sha": self.sha,
                "start_sha": self.sha,
                "line_range": {"start": {"new_line": 8}, "end": {"new_line": 14}},
            },
        }
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
        self.assertIn("as reviewed", out)  # read from the blob, not the working tree
        self.assertLess(out.index("const line14"), out.index("Ganze Funktion"))

    def test_quote_puts_the_code_above_the_note(self):
        state = {
            "iid": 1,
            "title": "x",
            "threads": {
                "d1": dict(
                    self.thread(),
                    author="Robin",
                    body="ist äquivalent?",
                    resolved=False,
                    note_count=1,
                    url="http://gl/x#note_1",
                    notes=[{"author": "Robin", "body": "ist äquivalent?"}],
                )
            },
            "topics": [
                {
                    "id": "t1",
                    "summary": "dupe test",
                    "thread_ids": ["d1"],
                    "state": None,
                }
            ],
        }
        out = T.render_quote(state, "t1")
        self.assertLess(out.index("const line10"), out.index("ist äquivalent?"))
        self.assertLess(out.index("http://gl/x#note_1"), out.index("const line10"))


class TestSetState(unittest.TestCase):
    """`set --state done` is the natural command after a reviewer resolves, and the
    old error only listed open/waiting — which reads like the CLI forgot a state,
    not like `done` being derived from GitLab resolution elsewhere."""

    def test_done_and_reply_pending_explain_they_are_derived(self):
        for v in ("done", "reply_pending", "reply-pending"):
            with self.assertRaises(argparse.ArgumentTypeError) as cm:
                T.settable_state(v)
            msg = str(cm.exception)
            self.assertIn("derived", msg)
            self.assertIn("sync", msg)

    def test_open_and_waiting_pass_through(self):
        for v in ("open", "waiting"):
            self.assertEqual(T.settable_state(v), v)

    def test_the_cli_prints_that_message_not_a_bare_choice_list(self):
        """End-to-end through argparse: the type error must surface as the usage
        error, ahead of the choices check that would replace it."""
        proc = subprocess.run(
            [
                sys.executable,
                os.path.join(HERE, "threads.py"),
                "set",
                "t1",
                "--state",
                "done",
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 2)
        self.assertIn("derived", proc.stderr)
        self.assertIn("sync", proc.stderr)


class TestWorktreeCleanup(unittest.TestCase):
    """The line under the table once the MR is merged, and when it must hold back."""

    FACTS = {"main": "/r/app", "path": "/r/app.wt/fix-x", "branch": "fix/x", "state": "merged",
             "source": "fix/x", "mr_sha": "abc", "tip": "abc", "dirty": False}

    def facts(self, **kw):
        return dict(self.FACTS, **kw)

    def test_merged_and_clean_offers_removal(self):
        line = T.render_cleanup(self.facts())
        self.assertIn("can be removed", line)
        self.assertIn("`/r/app.wt/fix-x`", line)
        self.assertIn("`fix/x`", line)

    def test_silent_unless_merged_worktree_on_the_mr_branch(self):
        self.assertEqual(T.render_cleanup(None), "")
        self.assertEqual(T.render_cleanup(self.facts(state="opened")), "")
        self.assertEqual(T.render_cleanup(self.facts(branch="fix/other")), "")

    def test_holds_back_what_removal_would_lose(self):
        self.assertIn("uncommitted", T.render_cleanup(self.facts(dirty=True)))
        self.assertIn("differs", T.render_cleanup(self.facts(tip="def")))

    def test_linked_worktree_is_told_apart_from_the_main_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = os.path.realpath(tmp)
            main = os.path.join(tmp, "app")
            for args in (("init", "-q", "-b", "main", main),
                         ("-C", main, "-c", "user.email=t@example.com", "-c", "user.name=t",
                          "commit", "-q", "--allow-empty", "-m", "one"),
                         ("-C", main, "worktree", "add", "-q", "-b", "fix/x",
                          f"{main}.wt/fix-x")):
                subprocess.run(["git", *args], check=True, capture_output=True)
            here = os.getcwd()
            self.addCleanup(os.chdir, here)
            os.chdir(main)
            self.assertIsNone(T.linked_worktree())
            os.chdir(f"{main}.wt/fix-x")
            self.assertEqual(T.linked_worktree(),
                             {"main": main, "path": f"{main}.wt/fix-x", "branch": "fix/x"})


class TestWhereTheModDraws(unittest.TestCase):
    """lib/ui.py, rework-mr's side: the shared state words, the opener without the table,
    and the state the review-ui panel reads."""

    def setUp(self):
        me_note = {"author": "Frank", "body": "Done, see the push.", "mine": True}
        them = {"author": "Haas, Miriam - EF13579", "body": "Naming:\nplease.", "mine": False}
        self.state = {"iid": 3, "title": "Extract keys", "topics": [
            {"id": "t1", "thread_ids": ["d1"], "summary": "Rename the factory"},
            {"id": "t2", "thread_ids": ["d2"], "summary": "Fix the detail key"},
        ], "threads": {
            "d1": {"resolved": False, "awaiting": "you", "file": "a.ts", "line": 1,
                   "notes": [them]},
            "d2": {"resolved": False, "awaiting": "you", "file": "b.ts", "line": 2,
                   "notes": [them, me_note, them]},
        }, "mr_comments": [{"author": "Haas, Miriam - EF13579", "body": "LGTM otherwise"}]}

    def test_your_move_is_new_until_you_have_replied_then_answered(self):
        self.assertEqual(T.ui_state(self.state, self.state["topics"][0]), "new")
        self.assertEqual(T.ui_state(self.state, self.state["topics"][1]), "answered")

    def test_the_panel_state_holds_the_whole_current_thread_and_the_mr_comments(self):
        got = T.state_json(self.state)
        self.assertEqual(got["current"], "t1")
        self.assertEqual([n["author"] for n in got["thread"][0]["notes"]], ["Miriam"])
        self.assertEqual(got["mr_comments"][0]["author"], "Miriam")
        self.assertEqual([t["state"] for t in got["topics"]], ["new", "answered"])

    def test_a_drawn_quote_heads_each_comment_with_its_link_and_time_and_no_bare_url(self):
        """The card's form of a comment, as review-mr's: the mod reads it to leave the
        thread out where the sidebar shows it, and the link glyph already links it."""
        url = "http://gitlab.test/g/p/-/merge_requests/3#note_7"
        self.state["threads"]["d1"]["url"] = url
        self.state["threads"]["d1"]["notes"] = [
            {"author": "Ana Lund", "body": "Naming:\nplease.", "url": url, "at": "2026-11-05T10:38:00Z"}]
        with mock.patch.dict(os.environ, {"REVIEW_UI_DRAWN": "1"}):
            drawn = T.render_quote(self.state, "t1")
        with mock.patch.dict(os.environ, {"REVIEW_UI_DRAWN": "0"}):
            plain = T.render_quote(self.state, "t1")
        self.assertNotIn(f"\n{url}\n", drawn)
        self.assertRegex(drawn, r"> \[\U000f03cc\]\(" + re.escape(url) + r"\) \*\*Ana\*\* _Nov 5, \d\d:38_")
        self.assertIn("> Naming: please.", drawn)
        self.assertIn(f"\n{url}\n", plain)

    def test_the_table_takes_the_shared_words_only_where_the_mod_draws(self):
        with mock.patch.dict(os.environ, {"REVIEW_UI_DRAWN": "1"}):
            drawn = T.render_table(self.state)
        with mock.patch.dict(os.environ, {"REVIEW_UI_DRAWN": "0"}):
            plain = T.render_table(self.state)
        self.assertIn("answered", drawn)
        self.assertNotIn("◈", drawn)
        self.assertIn("○ open | ◈ **t1**", plain)


class TestResolveState(unittest.TestCase):
    def test_a_fresh_state_by_iid_never_asks_about_the_checked_out_branch(self):
        """`--iid` on a detached HEAD (mid-rebase) with no state file yet: the MR is
        fetched by its number, since `glab mr view` has no branch to ask about there."""
        with tempfile.TemporaryDirectory() as root, \
                mock.patch.object(T, "STATE_ROOT", root), \
                mock.patch.object(T, "context", return_value={"slug": "acme-api", "path": "acme/api", "enc": "acme%2Fapi", "web": "https://gl"}), \
                mock.patch.object(T, "mr_view", side_effect=AssertionError("asked the branch")), \
                mock.patch.object(T, "mr_object", return_value={"iid": 7, "title": "x", "web_url": "https://gl/acme/api/-/merge_requests/7"}) as by_iid:
            _, iid, _, state = T.resolve_state(argparse.Namespace(iid=7, cmd="state"))
        by_iid.assert_called_once()
        self.assertEqual((iid, state["iid"]), (7, 7))


class TestGitLabCannotSayWhoYouAre(unittest.TestCase):
    """One failed "who am I" must not turn your own replies into a reviewer's: that reads as
    news, starts a judge, and puts every plan agreed on the thread out of date."""

    def refresh(self, state, me):
        with mock.patch.object(T, "resolve_state", return_value=({}, 3, "/s", state)), \
                mock.patch.object(T, "load", return_value=state), mock.patch.object(T, "save"), \
                mock.patch.object(T, "hold"), mock.patch.object(T, "current_user", return_value=me), \
                mock.patch.object(T, "fetch_threads", return_value={}) as fetch, \
                mock.patch.object(T, "mr_base", return_value=None), \
                mock.patch.object(T, "mr_object", return_value={"source_branch": "feat"}), \
                mock.patch.object(T, "mr_status", return_value={"state": "opened"}), \
                mock.patch.object(sys, "argv", ["threads.py", "refresh", "--iid", "3"]), \
                redirect_stdout(io.StringIO()) as out:
            T.main()
        return fetch, out.getvalue()

    def test_the_last_known_answer_tells_your_comments(self):
        fetch, _ = self.refresh({"topics": [], "threads": {}, "me": "ana"}, None)
        self.assertEqual(fetch.call_args.args[2], "ana")

    def test_never_known_leaves_the_threads_as_they_were(self):
        fetch, out = self.refresh({"topics": [], "threads": {}}, None)
        fetch.assert_not_called()
        self.assertTrue(json.loads(out))            # the mod still gets the state to draw

    def test_an_answer_is_kept_for_later(self):
        state = {"topics": [], "threads": {}}
        self.refresh(state, "ana")
        self.assertEqual(state["me"], "ana")

    def test_the_check_teaches_an_older_state_its_branch(self):
        """A state written before the source branch was kept learns it on the mod's next
        update check, so a session already running finds its own MR by branch."""
        state = {"topics": [], "threads": {}}
        self.refresh(state, "ana")
        self.assertEqual(state["source_branch"], "feat")


class TestRounds(unittest.TestCase):
    """Where the mod draws: the plan pass, then the fix pass, inside rounds."""

    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"REVIEW_UI_DRAWN": "1"})
        self.env.start()
        self.addCleanup(self.env.stop)
        T._WORKING.clear()
        self.addCleanup(T._WORKING.clear)

    @staticmethod
    def state(n=3):
        def th(i):
            return {"author": "Robin", "file": "a.py", "line": i, "resolved": False,
                    "awaiting": "you", "notes": [{"author": "Robin", "body": f"b{i}",
                                                  "at": f"2026-01-0{i}T00:00:00Z", "mine": False}]}
        return {"iid": 1, "threads": {f"d{i}": th(i) for i in range(1, n + 1)},
                "topics": [{"id": f"t{i}", "summary": f"point {i}", "thread_ids": [f"d{i}"],
                            "state": None} for i in range(1, n + 1)]}

    @staticmethod
    def agree(state, tid, outcome="fix"):
        t = T.topic_for(state, tid)
        t.update(decision="d", plan="p", outcome=outcome, plan_on=T.reviewer_mark(state, t))

    def test_every_plan_comes_before_any_fix(self):
        state = self.state()
        T.start_round(state)
        self.assertEqual(T.current_topic(state), "t1")
        self.agree(state, "t1")
        self.assertEqual(T.current_topic(state), "t2")     # not t1's fix
        self.agree(state, "t2")
        self.assertEqual(T.current_topic(state), "t3")
        self.agree(state, "t3")
        self.assertEqual(T.current_topic(state), "t1")     # the fix pass, from the top
        self.assertEqual(state["pass"], "fix")

    def test_a_thread_arriving_in_the_fix_pass_waits_until_it_is_through(self):
        state = self.state()
        T.start_round(state)
        for t in ("t1", "t2", "t3"):
            self.agree(state, t)
            T.current_topic(state)
        self.assertEqual(T.current_topic(state), "t1")
        state["threads"]["d4"] = {**state["threads"]["d1"], "line": 4}
        state["topics"].append({"id": "t4", "summary": "new", "thread_ids": ["d4"], "state": None})
        for t, nxt in (("t1", "t2"), ("t2", "t3"), ("t3", "t4")):
            T.topic_for(state, t)["state"] = "waiting"     # fixed, pushed, replied
            state["threads"][f"d{t[1]}"]["awaiting"] = "reviewer"
            self.assertEqual(T.current_topic(state), nxt)
        self.assertEqual(state["pass"], "plan")

    def test_a_fix_keeps_its_topic_current_through_push_and_reply(self):
        state = self.state(1)
        T.start_round(state)
        self.agree(state, "t1")
        self.assertEqual(T.current_topic(state), "t1")
        self.assertEqual(T.step_of(state, state["topics"][0]), "fix")
        state["topics"][0]["diff_url"] = "http://gl/diff"
        self.assertEqual(T.step_of(state, state["topics"][0]), "reply")
        self.assertEqual(T.current_topic(state), "t1")

    def test_a_reply_instead_of_a_plan_keeps_its_topic_current_until_it_is_posted(self):
        """The user answers a topic with a reply (a push-back, say) in the plan pass: its
        draft is shown at once and stays the step until posted, then the plan pass goes on."""
        state = self.state()
        T.start_round(state)
        self.agree(state, "t1", outcome="push-back")
        state["topics"][0]["reply"] = "Nicht in diesem MR.\n"
        T.CURRENT.jump(state, "t1")                       # reply-view
        self.assertEqual(T.step_of(state, state["topics"][0]), "reply")
        self.assertEqual(T.current_topic(state), "t1")
        state["topics"][0]["state"] = "waiting"           # posted
        state["threads"]["d1"]["awaiting"] = "reviewer"
        self.assertEqual(T.current_topic(state), "t2")
        self.assertEqual(state["pass"], "plan")

    def test_a_reviewer_writing_again_reopens_the_plan(self):
        state = self.state(1)
        self.agree(state, "t1")
        state["topics"][0]["diff_url"] = "http://gl/diff"
        state["threads"]["d1"]["notes"].append({"author": "Robin", "body": "still wrong",
                                               "at": "2026-02-01T00:00:00Z", "mine": False})
        self.assertEqual(T.step_of(state, state["topics"][0]), "plan")

    def test_your_own_reply_changes_nothing_the_plan_is_on(self):
        state = self.state(1)
        self.agree(state, "t1", outcome="reply")
        state["threads"]["d1"]["notes"].append({"author": "Me", "body": "ok",
                                               "at": "2026-02-01T00:00:00Z", "mine": True})
        self.assertEqual(T.step_of(state, state["topics"][0]), "reply")

    def test_waiting_on_the_reviewers_mark_survives_a_sync(self):
        state = self.state(1)
        t = state["topics"][0]
        t.update(state="waiting", waiting_on=T.reviewer_mark(state, t))
        live = {"d1": dict(state["threads"]["d1"])}
        T.sync(state, live)
        self.assertEqual(T.topic_status(state, t), "waiting")
        live["d1"]["notes"] = live["d1"]["notes"] + [{"author": "Robin", "body": "more",
                                                       "at": "2026-03-01T00:00:00Z", "mine": False}]
        T.sync(state, live)
        self.assertEqual(T.topic_status(state, t), "open")

    def test_the_inbox_and_the_judge_list(self):
        state = self.state(2)
        T.start_round(state)
        topics = T.state_json(state)["topics"]
        self.assertEqual([x["news"] for x in topics], [["thread"], ["thread"]])
        # The current topic is the chat's, once the model opened it; the rest the
        # background agent's.
        T.chat_opens(state, "t1")
        self.assertEqual(T.judge_list(state)["topics"], ["t2"])
        t2 = state["topics"][1]
        t2["proposal"] = {"says": "needs", "outcome": "fix", "plan": "p", "why": "w",
                          "on": T.reviewer_mark(state, t2)}
        self.assertEqual(T.judge_list(state)["topics"], [])
        self.assertEqual(T.state_json(state)["topics"][1]["proposal"]["outcome"], "fix")

    def test_a_topic_shown_without_a_proposal_says_to_store_one(self):
        state = self.state(1)
        self.assertIn("propose t1", T.proposal_note(state, "t1"))
        t1 = state["topics"][0]
        t1["proposal"] = {"says": "needs", "outcome": "fix", "plan": "p", "why": "w",
                          "on": T.reviewer_mark(state, t1)}
        self.assertIsNone(T.proposal_note(state, "t1"))
        self.agree(state, "t1")
        t1.pop("proposal")
        self.assertIsNone(T.proposal_note(state, "t1"))     # past the plan step
        with mock.patch.dict(os.environ, {"REVIEW_UI_DRAWN": "0"}):
            self.assertIsNone(T.proposal_note(self.state(1), "t1"))

    def test_a_topic_a_press_brought_up_is_the_background_agents(self):
        # A press moved on before the background agent started: nobody else prepares it.
        state = self.state(3)
        T.start_round(state)
        T.chat_opens(state, "t1")
        self.agree(state, "t1")
        self.assertEqual(T.current_topic(state), "t2")
        with mock.patch.dict(os.environ, {T.MOD_ENV: "1"}):
            T.chat_opens(state, "t2")     # the mod's own quote of the next card
        self.assertEqual(T.judge_list(state)["topics"], ["t2", "t3"])

    def test_without_the_mod_nothing_changes(self):
        with mock.patch.dict(os.environ, {"REVIEW_UI_DRAWN": "0"}):
            state = self.state()
            self.agree(state, "t1")
            self.assertEqual(T.current_topic(state), "t1")     # as before: open work first


class TestChecksCommand(unittest.TestCase):
    def test_storing_a_reading_says_what_was_stored(self):
        rec = {"hooks": [{"event": "pre-commit", "early": "git hook run pre-commit"}],
               "tests": [{"name": "frontend", "run": "npm test"}, {"name": "backend", "run": "pytest"}]}
        out = io.StringIO()
        with mock.patch.object(T, "repo_root", return_value="/repo"), \
                mock.patch.object(T.checks, "store", return_value=rec), \
                mock.patch.object(sys, "argv", ["threads.py", "checks", "--set", "-"]), \
                mock.patch.object(sys, "stdin", io.StringIO("{}")), redirect_stdout(out):
            T.main()
        self.assertEqual(out.getvalue().strip(),
                         "stored: pre-commit: run ahead; tests: frontend, backend")


class TestHeldFix(unittest.TestCase):
    """A fix made and not pushed holds its topic: no command moves away from it."""

    def run_cmd(self, *argv):
        state = {"topics": [{"id": "t1", "fix": {"summary": "x"}, "thread_ids": []}, {"id": "t2", "thread_ids": []}], "threads": {}}
        for p in (mock.patch.object(T, "resolve_state", return_value=({}, 3, "/s", state)),
                  mock.patch.object(T, "step_of", side_effect=lambda st, t, *a: "fixed" if t.get("fix") else "fix"),
                  mock.patch.object(T, "save"), mock.patch.object(T, "emit"),
                  mock.patch.object(T, "render_quote", return_value=""), mock.patch.object(T, "state_json", return_value={}),
                  mock.patch.object(sys, "argv", ["threads.py", *argv])):
            p.start()
        self.addCleanup(mock.patch.stopall)
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), mock.patch("sys.stderr", err):
            try:
                T.main()
            except SystemExit:
                return err.getvalue()
        return None

    def test_leaving_or_opening_another_topic_is_refused(self):
        for argv in (("leave",), ("open", "t2"), ("open",), ("quote", "t2", "--focus")):
            with self.subTest(argv=argv):
                self.assertIn("t1", self.run_cmd(*argv) or "")

    def test_the_held_topic_itself_still_opens(self):
        self.assertIsNone(self.run_cmd("open", "t1"))
        self.assertIsNone(self.run_cmd("quote", "t2"))     # looked at, not opened


class TestViewStep(unittest.TestCase):
    def test_a_view_names_the_step_it_shows_its_topic_at(self):
        """The mod places a new card when a turn moved the topic past the step its card
        shows: a push the model finished, with no view of its own."""
        state = {"iid": 3, "topics": [{"id": "t1", "thread_ids": []}], "threads": {}}
        with mock.patch.object(T, "step_of", return_value="fixed"), \
                mock.patch.object(T.ui, "record"), \
                mock.patch.object(T.critical_manifest, "with_manifest", return_value="") as wm, \
                redirect_stdout(io.StringIO()):
            T.emit(state, "card", "t1")
        self.assertEqual(wm.call_args.kwargs["extra"]["step"], "fixed")


class TestHandedOverPush(unittest.TestCase):
    """The model's `push --committed` finishing a press that handed it the commits."""

    def run_push(self, t1, *argv):
        state = {"topics": [t1], "threads": {}}
        patches = [mock.patch.dict(os.environ, {"REVIEW_UI_DRAWN": "1", T.PRESS_ENV: ""}),
                   mock.patch.object(T, "resolve_state", return_value=({}, 3, "/s", state)),
                   mock.patch.object(T, "push_fix", return_value=(state, "http://gl/diff")),
                   mock.patch.object(T, "post_reply", return_value="posted"),
                   mock.patch.object(T, "load", return_value=state),
                   mock.patch.object(T, "save"), mock.patch.object(T, "hold"),
                   mock.patch.object(T, "sync"), mock.patch.object(T, "fetch_threads"),
                   mock.patch.object(T, "current_user"),
                   mock.patch.object(sys, "argv", ["threads.py", "push", "t1", *argv])]
        for p in patches:
            p.start()
        self.addCleanup(mock.patch.stopall)
        self.out = io.StringIO()
        with redirect_stdout(self.out):
            T.main()
        return T.post_reply

    def test_push_handed_over_posts_nothing_and_leaves_the_card_to_the_user(self):
        post = self.run_push({"id": "t1", "push_token": "abcd1234"},
                             "--committed", "--token", "abcd1234")
        post.assert_not_called()
        self.assertIn("end the turn here", self.out.getvalue())
        self.assertIn("never post or resolve", self.out.getvalue())

    def test_push_takes_no_resolve(self):
        with mock.patch("sys.stderr", new_callable=io.StringIO), self.assertRaises(SystemExit):
            self.run_push({"id": "t1", "push_token": "abcd1234"}, "--resolve")


class TestReplyAfterTheFix(unittest.TestCase):
    """A longer reply after the fix explains it, ends on the line that links the fix, and
    never resolves: the reviewer reads it and resolves the thread."""

    def setUp(self):
        mock.patch.dict(os.environ, {"REVIEW_UI_DRAWN": "1", T.PRESS_ENV: "1"}).start()
        self.addCleanup(mock.patch.stopall)
        T._WORKING.clear()
        self.addCleanup(T._WORKING.clear)
        self.state = TestRounds.state(2)
        T.start_round(self.state)
        TestRounds.agree(self.state, "t1")
        self.t1 = T.topic_for(self.state, "t1")
        self.t1.update(diff_url="https://gl/g/p/-/merge_requests/1/diffs?diff_id=7")
        self.post = mock.patch.object(T, "api_post", return_value={"id": 9}).start()
        mock.patch.object(T, "working_diff", return_value="").start()

    def set_reply(self, body):
        argv = ["threads.py", "set", "t1", "--reply", "-", "--iid", "1"]
        with mock.patch.object(sys, "argv", argv), mock.patch.object(T, "load", return_value=self.state), \
                mock.patch.object(T, "resolve_state", return_value=({}, 1, "/s", self.state)), \
                mock.patch.object(T, "save"), mock.patch.object(T, "hold"), \
                mock.patch("sys.stdin", io.StringIO(body)):
            T.main()

    def test_it_ends_on_the_fixed_line(self):
        self.set_reply("Took option B, since it keeps the cache warm.\n")
        self.assertTrue(self.t1["reply"].endswith(f"Fixed: {self.t1['diff_url']}\n"))

    def test_the_fixed_line_is_drawn_as_a_short_link_and_posted_whole(self):
        self.set_reply("Took option B.\n")
        shown = T._quote_draft(self.t1["reply"])
        self.assertIn(f"> Fixed: [the diff {T.LINK_ICON}]({self.t1['diff_url']})", shown)
        self.assertIn(f"Fixed: {self.t1['diff_url']}", self.t1["reply"])

    def test_a_reply_that_links_the_fix_already_gets_no_second_line(self):
        self.set_reply(f"Done in {self.t1['diff_url']}\n")
        self.assertEqual(self.t1["reply"].count(self.t1["diff_url"]), 1)

    def test_it_cannot_be_posted_with_resolve(self):
        self.t1["reply"] = "Took option B.\n"
        with mock.patch("sys.stderr", new_callable=io.StringIO), self.assertRaises(SystemExit):
            T.post_reply(self.state, {"enc": "g%2Fp", "web": "https://gl/g/p"}, 1, "t1", None, resolve=True)
        self.post.assert_not_called()

    def test_fixed_alone_still_resolves(self):
        T.post_reply(self.state, {"enc": "g%2Fp", "web": "https://gl/g/p"}, 1, "t1", None, fixed=True, resolve=True)
        self.assertEqual(self.post.call_count, 2)                     # the note, the resolve


class TestPostedByHandDiffers(unittest.TestCase):
    """A reply the user posted by hand that no longer holds: the agent stores its verdict and
    a corrected version, shown as a card; the user's press alone edits the note on GitLab."""

    def setUp(self):
        mock.patch.dict(os.environ, {"REVIEW_UI_DRAWN": "1", T.PRESS_ENV: ""}).start()
        self.addCleanup(mock.patch.stopall)
        T._WORKING.clear()
        self.addCleanup(T._WORKING.clear)
        self.state = TestRounds.state(2)
        self.state["threads"]["d1"]["notes"].append(
            {"author": "Me", "body": "Option A.", "mine": True, "url": "https://gl/g/p/-/merge_requests/1#note_8"})
        self.t1 = T.topic_for(self.state, "t1")
        self.t1["reply"] = "Option B, since it keeps the cache warm.\n"
        self.api = mock.patch.object(T, "api_post", return_value={}).start()

    def run_posted(self, *argv, stdin=""):
        argv = ["threads.py", "posted", "t1", *argv, "--iid", "1"]
        with mock.patch.object(sys, "argv", argv), \
                mock.patch.object(T, "resolve_state", return_value=({"enc": "g%2Fp", "web": "https://gl/g/p"}, 1, "/s", self.state)), \
                mock.patch.object(T, "save"), mock.patch.object(T, "hold"), \
                mock.patch("sys.stdin", io.StringIO(stdin)), \
                mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            T.main()
        return out.getvalue()

    def test_differs_stores_the_correction_and_shows_it_word_by_word(self):
        out = self.run_posted("--differs", "It drops which option.", "--fix", "-", stdin="Option B.\n")
        self.assertEqual(self.t1["post_differs"]["note"], "8")
        self.assertIn("```worddiff\nOption [-A-]{+B+}.\n```", out)
        self.assertTrue(T.state_json(self.state)["topics"][0]["post_differs"])

    def test_the_card_is_the_correction_s_while_it_waits(self):
        self.run_posted("--differs", "It drops the reason.", "--fix", "-", stdin="Option B.\n")
        self.assertIn("Your posted reply differs", "\n".join(T._step_parts(self.state, self.t1)))

    def test_a_correction_ends_once_its_draft_is_posted_from_the_card(self):
        self.run_posted("--differs", "v", "--fix", "-", stdin="Option B.\n")
        os.environ[T.PRESS_ENV] = "1"
        with mock.patch.object(T, "working_diff", return_value=""):
            T.post_reply(self.state, {"enc": "g%2Fp", "web": "https://gl/g/p"}, 1, "t1", None)
        self.assertNotIn("post_differs", self.t1)
        self.assertFalse(T.state_json(self.state)["topics"][0]["post_differs"])

    def test_a_correction_whose_draft_is_gone_no_longer_counts(self):
        self.run_posted("--differs", "v", "--fix", "-", stdin="Option B.\n")
        self.t1.pop("reply")                       # stored before a fix that dropped it
        self.assertFalse(T.state_json(self.state)["topics"][0]["post_differs"])

    def test_apply_is_the_user_s_press_alone(self):
        self.run_posted("--differs", "v", "--fix", "-", stdin="Option B.\n")
        with mock.patch("sys.stderr", new_callable=io.StringIO), self.assertRaises(SystemExit):
            self.run_posted("--apply")
        self.api.assert_not_called()

    def test_apply_edits_the_note_and_records_the_reply_posted(self):
        self.run_posted("--differs", "v", "--fix", "-", stdin="Option B.\n")
        os.environ[T.PRESS_ENV] = "1"
        self.run_posted("--apply")
        self.api.assert_called_once_with("projects/g%2Fp/merge_requests/1/discussions/d1/notes/8",
                                         {"body": "Option B.\n"}, method="PUT")
        self.assertNotIn("post_differs", self.t1)
        self.assertEqual(self.t1["state"], "waiting")


class TestReplyAheadOfTheFix(unittest.TestCase):
    """A reply posted before the fix (which option the plan took, that the fix follows):
    the plan stands, the topic stays the user's, and the round goes on."""

    def setUp(self):
        mock.patch.dict(os.environ, {"REVIEW_UI_DRAWN": "1", T.PRESS_ENV: "1"}).start()
        self.addCleanup(mock.patch.stopall)
        T._WORKING.clear()
        self.addCleanup(T._WORKING.clear)
        self.state = TestRounds.state(2)
        T.start_round(self.state)
        TestRounds.agree(self.state, "t1")
        self.t1 = T.topic_for(self.state, "t1")
        self.t1.update(reply="Wir nehmen Option 1, der Fix folgt.\n", reply_early=True)
        self.post = mock.patch.object(T, "api_post", return_value={"id": 9}).start()
        mock.patch.object(T, "working_diff", return_value="").start()

    def test_posting_it_keeps_the_plan_and_resolves_nothing(self):
        T.post_reply(self.state, {"enc": "g%2Fp", "web": "https://gl/g/p"}, 1, "t1", None)
        self.assertEqual(self.post.call_count, 1)                     # the note, no resolve
        self.assertNotIn("reply", self.t1)
        self.assertTrue(self.t1["early_posted"])
        self.assertEqual(T.step_of(self.state, self.t1), "fix")

    def test_it_cannot_be_posted_with_resolve(self):
        with mock.patch("sys.stderr", new_callable=io.StringIO), self.assertRaises(SystemExit):
            T.post_reply(self.state, {"enc": "g%2Fp", "web": "https://gl/g/p"}, 1, "t1", None, resolve=True)
        self.post.assert_not_called()

    def test_a_reply_ahead_of_the_fix_posted_by_hand_keeps_the_plan_too(self):
        argv = ["threads.py", "posted", "t1", "--ok", "--iid", "1"]
        with mock.patch.object(sys, "argv", argv), mock.patch.object(T, "load", return_value=self.state), \
                mock.patch.object(T, "save"), mock.patch.object(T, "hold"), \
                mock.patch.object(T, "state_file", return_value="/state"), \
                mock.patch.object(T, "context", return_value={"slug": "g-p", "enc": "g%2Fp", "web": "https://gl/g/p"}), \
                mock.patch("sys.stdout", new_callable=io.StringIO):
            T.main()
        self.assertTrue(self.t1["early_posted"])
        self.assertNotIn("reply", self.t1)
        self.assertEqual(T.step_of(self.state, self.t1), "fix")

    def test_on_a_plan_that_only_replies_it_is_the_reply_and_waits_on_the_reviewer(self):
        """Stored with `--early` on a push-back, it was taken for one ahead of a fix that
        never comes: posted, the topic stayed "to reply" until set waiting by hand."""
        for how in ("press", "by hand"):
            with self.subTest(how):
                self.t1.update(outcome="push-back", reply="Bewusst so.\n", reply_early=True)
                self.t1.pop("state", None)
                if how == "press":
                    T.post_reply(self.state, {"enc": "g%2Fp", "web": "https://gl/g/p"}, 1, "t1", None)
                else:
                    argv = ["threads.py", "posted", "t1", "--ok", "--iid", "1"]
                    with mock.patch.object(sys, "argv", argv), \
                            mock.patch.object(T, "load", return_value=self.state), \
                            mock.patch.object(T, "save"), mock.patch.object(T, "hold"), \
                            mock.patch.object(T, "state_file", return_value="/state"), \
                            mock.patch.object(T, "context", return_value={"slug": "g-p", "enc": "g%2Fp", "web": "https://gl/g/p"}), \
                            mock.patch("sys.stdout", new_callable=io.StringIO):
                        T.main()
                self.assertEqual(self.t1.get("state"), "waiting")
                self.assertNotIn("early_posted", self.t1)
                self.assertNotIn("reply_early", self.t1)

    def test_a_reply_stored_without_early_is_the_ordinary_one(self):
        argv = ["threads.py", "set", "t1", "--reply", "Fixed.", "--iid", "1"]
        with mock.patch.object(sys, "argv", argv), mock.patch.object(T, "load", return_value=self.state), \
                mock.patch.object(T, "save"), mock.patch.object(T, "hold"), \
                mock.patch.object(T, "state_file", return_value="/state"), \
                mock.patch.object(T, "context", return_value={"slug": "g-p", "enc": "g%2Fp", "web": "https://gl/g/p"}):
            T.main()
        self.assertEqual(self.t1["reply"], "Fixed.\n")
        self.assertNotIn("reply_early", self.t1)


class TestKeepPlan(unittest.TestCase):
    """A reviewer's answer to an agreed plan ("sounds good") is judged in the background:
    one that only acknowledges it keeps the plan, so the topic is not planned again."""

    def setUp(self):
        mock.patch.dict(os.environ, {"REVIEW_UI_DRAWN": "1"}).start()
        self.addCleanup(mock.patch.stopall)
        T._WORKING.clear()
        self.addCleanup(T._WORKING.clear)
        self.state = TestRounds.state(1)
        TestRounds.agree(self.state, "t1")
        self.t1 = T.topic_for(self.state, "t1")
        self.t1["plan"] = "Refetch the detail key."
        self.state["threads"]["d1"]["notes"].append(
            {"author": "Robin", "body": "Sounds good!", "at": "2026-02-01T00:00:00Z", "mine": False})
        mock.patch.object(T, "working_diff", return_value="").start()

    def run_cli(self, *argv):
        with mock.patch.object(sys, "argv", ["threads.py", *argv, "--iid", "1"]), \
                mock.patch.object(T, "load", return_value=self.state), mock.patch.object(T, "save"), \
                mock.patch.object(T, "hold"), mock.patch.object(T, "state_file", return_value="/state"), \
                mock.patch.object(T, "context", return_value={"slug": "g-p", "enc": "g%2Fp", "web": "https://gl/g/p"}), \
                mock.patch("sys.stdout", new_callable=io.StringIO):
            T.main()

    def test_an_acknowledgement_keeps_the_plan_and_the_card_says_so(self):
        self.assertEqual(T.step_of(self.state, self.t1), "plan")            # the reviewer wrote
        self.assertEqual(T.judge_list(self.state)["topics"], ["t1"])
        self.assertIn("t1 (plan agreed)", T.judge_brief(self.state, 1))
        self.assertIn("keep-plan <t>", T.judge_brief(self.state, 1))
        self.assertEqual(T.judge_brief(self.state, 1, skip=["t1"]), "")   # an agent failed on it
        self.run_cli("keep-plan", "t1", "--why", "Robin agreed with the plan.")
        self.assertEqual(T.step_of(self.state, self.t1), "fix")
        self.assertEqual(T.judge_list(self.state)["topics"], [])
        self.assertIn("_Robin agreed with the plan: the plan holds._", T.render_quote(self.state, "t1"))

    def test_a_topic_with_no_agreed_plan_cannot_be_kept(self):
        del self.t1["decision"]
        with mock.patch("sys.stderr", new_callable=io.StringIO), self.assertRaises(SystemExit):
            self.run_cli("keep-plan", "t1", "--why", "x")


class TestFixAgainstThePlan(unittest.TestCase):
    """Whether a fix carries out the plan agreed is the agent's judgment, stated when it shows
    the fix: the card says it, and a fix that differs is reviewed before Push."""

    def setUp(self):
        mock.patch.dict(os.environ, {"REVIEW_UI_DRAWN": "1"}).start()
        self.addCleanup(mock.patch.stopall)
        T._WORKING.clear()
        self.addCleanup(T._WORKING.clear)
        self.state = TestRounds.state(1)
        TestRounds.agree(self.state, "t1")
        mock.patch.object(T, "local_state", return_value=("/state", self.state)).start()
        mock.patch.object(T, "load", return_value=self.state).start()
        for name in ("save", "hold"):
            mock.patch.object(T, name).start()
        mock.patch.object(T, "working_diff", return_value="@@ -1 +1 @@\n-a\n+b\n").start()
        # No viewer open, unless a test opens one: nothing here reaches a real hunk.
        self.hunk = mock.patch.object(T, "hunk").start()
        self.hunk.find_session.return_value = None
        self.hunk.MAX_NOTES = 3

    def show(self, *flags):
        argv = ["threads.py", "diff-view", "t1", "--did", "Renamed it.", *flags]
        with mock.patch.object(sys, "argv", argv), \
                mock.patch.object(sys, "stdin", io.StringIO("@@ -1 +1 @@\n-a\n+b\n")), \
                mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            T.main()
        return out.getvalue()

    def test_code_changed_on_a_plan_to_reply_makes_it_a_fix_to_push(self):
        """A push-back that also adds a comment: left a reply, its card offered no push and
        `push` refused the fix the user had already checked."""
        TestRounds.agree(self.state, "t1", outcome="push-back")
        t1 = T.topic_for(self.state, "t1")
        self.assertEqual(T.step_of(self.state, t1), "reply")
        self.show("--as-agreed")
        self.assertEqual((t1["outcome"], T.step_of(self.state, t1)), ("fix", "fixed"))

    def test_a_fix_as_agreed_says_so(self):
        out = self.show("--as-agreed")
        self.assertRegex(out, r"Changes\*\*  \S+ \d+ files? \+\d+ −\d+\n\nRenamed it\.\n\nChecks")
        self.assertNotIn("Differs", out)
        self.assertFalse(T.state_json(self.state)["topics"][0]["differs"])

    def test_a_fix_that_differs_says_how_and_the_reply_after_it_explains(self):
        out = self.show("--differs", "It also drops the cache.")
        self.assertIn("Differs from the plan you agreed**\n\nIt also drops the cache.", out)
        self.assertLess(out.index("Differs from the plan"), out.index("Changes**"))
        self.assertTrue(T.state_json(self.state)["topics"][0]["differs"])

    def test_a_fix_made_while_another_topic_was_current_says_so_first(self):
        """Real runs: on t2's card, the model went on to t1's fix, said nothing of it, and
        the card switched to t1 as if it had been asked for. The current topic had moved on
        to t1 already, as the last plan was agreed: only the card shown tells."""
        two = TestRounds.state(2)
        self.state["threads"]["d2"] = two["threads"]["d2"]
        self.state["topics"].append(two["topics"][1])
        self.state["focus"] = "t1"
        self.state["shown"] = "t2"
        out = self.show("--as-agreed")
        self.assertIn("Made while you were on t2**", out)
        self.assertLess(out.index("Made while you were on"), out.index("Changes**"))
        self.assertEqual(T.state_json(self.state)["topics"][0]["made_on"], "t2")
        # A look at another topic is research, not a card: it changes nothing.
        with mock.patch("sys.stdout", new_callable=io.StringIO):
            T.emit(self.state, "a view", "t2")
        self.assertEqual(self.state["shown"], "t1")
        # Shown again from its own card (notes read, a change asked for): nothing to say.
        self.assertEqual(self.state["shown"], "t1")
        self.assertNotIn("Made while", self.show("--as-agreed"))
        self.assertIsNone(T.state_json(self.state)["topics"][0]["made_on"])

    def test_the_summary_names_its_commits_and_leaves_passed_checks_unsaid(self):
        """The plan is above it and the thread in the sidebar: the card says what changed,
        how big, and which commits it rewrites or adds; checks only where they did not pass."""
        argv = ["--as-agreed", "--fixup", "455d841 extract keys", "--new-commit", "test: cover the keys"]
        out = self.show(*argv)
        # The heading says how many, dim on the card; the commits follow the summary, apart.
        self.assertIn("  (1 new commit, 1 commit rewritten)\n", out)
        self.assertIn(f"Renamed it.\n\n{T.ui.REWRITTEN_ICON} `455d841` extract keys", out)
        self.assertIn(f"{T.ui.NEW_COMMIT_ICON} test: cover the keys", out)
        self.assertNotIn("asked", out.split("Changes**")[1])
        with mock.patch.object(T, "checks_of", return_value="passed"):
            self.assertNotIn("Checks", T.render_fix(self.state, "t1"))
        with mock.patch.object(T, "checks_of", return_value="failed"):
            self.assertIn("Checks failed", T.render_fix(self.state, "t1"))

    def test_a_fix_shown_again_reloads_the_open_viewer_with_this_showing_s_notes(self):
        """A fix changed after the user's notes in the viewer is shown again: the window
        must show the fix the card is about, not the one before it."""
        self.hunk.find_session.return_value = "sid"
        self.hunk.reload_working_diff.return_value = 1
        self.show("--as-agreed", "--note", "src/a.ts:3:why")
        self.hunk.clear_agent_notes.assert_called_once_with("sid")
        self.hunk.reload_working_diff.assert_called_once_with("sid")
        self.hunk.add_notes.assert_called_once_with("sid", [{"filePath": "src/a.ts", "newLine": 3, "summary": "why"}])
        self.hunk.focus_window.assert_not_called()          # moved to only on a press

    def test_the_viewer_opened_on_a_press_gets_the_notes_the_fix_was_shown_with(self):
        self.show("--as-agreed", "--note", "src/a.ts:3:why")
        self.hunk.show_working_diff.return_value = ("sid", "9 (x)")
        with mock.patch.object(sys, "argv", ["threads.py", "hunk-show", "t1"]), redirect_stdout(io.StringIO()), \
                mock.patch.object(T, "resolve_state", return_value=({}, 1, "/state", self.state)):
            T.main()
        self.hunk.add_notes.assert_called_once_with("sid", [{"filePath": "src/a.ts", "newLine": 3, "summary": "why"}])

    def test_a_fix_shown_with_no_viewer_open_opens_none(self):
        self.show("--as-agreed")
        self.hunk.reload_working_diff.assert_not_called()
        self.hunk.show_working_diff.assert_not_called()

    def test_a_fix_shown_without_the_judgment_is_refused(self):
        with mock.patch("sys.stderr", new_callable=io.StringIO) as err, self.assertRaises(SystemExit):
            self.show()
        self.assertIn("--as-agreed", err.getvalue())

    def test_plans_shows_the_change_agreed_to_judge_against(self):
        self.state["topics"][0]["agreed_change"] = "```diff\n-a\n+b\n```"
        self.assertIn("change agreed:\n    -a\n    +b", T.render_plans(self.state))


class TestFixInTheSidebar(unittest.TestCase):
    """A fix in several files: the sidebar gets the comment's own file, every file of the fix
    with its size, and the file picked from that list where the person picked one."""

    def run_code(self, *extra):
        state = TestRounds.state(1)
        state["threads"]["d1"].update(file="b.ts", line=2)
        stat = [("a.ts", 4, 4), ("b.ts", 1, 1), ("c.ts", 12, 3)]
        seen = {}

        def view(root, path, line, *a, **k):
            seen.update(path=path, line=line)
            return {"file": path, "rows": [["+", None, 1, "x"]], "at": [1, 1]}
        with mock.patch.object(sys, "argv", ["threads.py", "code", "t1", "--fix", "--iid", "1", *extra]), \
                mock.patch.object(T, "load", return_value=state), mock.patch.object(T, "save"), \
                mock.patch.object(T, "state_file", return_value="/state"), \
                mock.patch.object(T, "context", return_value={"slug": "g-p", "enc": "g%2Fp", "web": "https://gl/g/p"}), \
                mock.patch.object(T, "working_diff", return_value="diff"), \
                mock.patch.object(T, "diff_stat", return_value=stat), \
                mock.patch.object(T.code_view, "view", side_effect=view), \
                mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            T.main()
        return json.loads(out.getvalue()), seen

    def test_the_comment_s_file_and_every_file_of_the_fix_sized(self):
        got, seen = self.run_code()
        self.assertEqual((seen["path"], seen["line"]), ("b.ts", 2))
        self.assertEqual(got["fix_files"], [["a.ts", 4, 4], ["b.ts", 1, 1], ["c.ts", 12, 3]])
        self.assertEqual((got["commented"], got["mode"]), ("b.ts", "fix"))

    def test_a_file_picked_from_the_list_is_shown_with_no_comment_on_it(self):
        got, seen = self.run_code("--file", "c.ts")
        self.assertEqual((seen["path"], seen["line"]), ("c.ts", None))
        self.assertEqual(got["commented"], "b.ts")
        # A name that is not the fix's shows the comment's file, as with no pick.
        self.assertEqual(self.run_code("--file", "elsewhere.ts")[1]["path"], "b.ts")


class TestChangeBlocks(unittest.TestCase):
    """A proposal's change on its card: one block per file the mod heads with its place."""

    DIFF = ("diff --git a/src/x.ts b/src/x.ts\nindex 1..2 100644\n--- a/src/x.ts\n+++ b/src/x.ts\n"
            "@@ -60,2 +60,2 @@\n a\n-b\n+B\n--- a/src/y.ts\n+++ b/src/y.ts\n@@ -3 +3 @@\n-c\n+C\n")

    def test_drawn_each_file_is_a_block_of_its_own_without_its_headers(self):
        with mock.patch.dict(os.environ, {"REVIEW_UI_DRAWN": "1"}):
            out = "\n".join(T._change_blocks(self.DIFF))
        self.assertEqual(out, "```diff path=src/x.ts loc\n@@ -60,2 +60,2 @@\n a\n-b\n+B\n```\n\n"
                              "```diff path=src/y.ts loc\n@@ -3 +3 @@\n-c\n+C\n```")

    def test_without_the_mod_or_without_file_headers_the_diff_stays_as_it_is(self):
        with mock.patch.dict(os.environ, {"REVIEW_UI_DRAWN": "0"}):
            self.assertIn("+++ b/src/x.ts", "\n".join(T._change_blocks(self.DIFF)))
        with mock.patch.dict(os.environ, {"REVIEW_UI_DRAWN": "1"}):
            self.assertEqual(T._change_blocks("@@ -1 +1 @@\n-a\n+b\n"), ["```diff\n@@ -1 +1 @@\n-a\n+b\n```"])


class TestPushFix(unittest.TestCase):
    """`push` and `verify` around lib/checks.py: what is the script's to finish, and what
    goes back to the agent."""

    def setUp(self):
        T._WORKING.clear()
        self.addCleanup(T._WORKING.clear)
        self.state = TestRounds.state(2)
        for tid in ("t1", "t2"):
            TestRounds.agree(self.state, tid)
        self.t1 = T.topic_for(self.state, "t1")
        self.t1["fix"] = {"did": "d", "fixup": ["abc123 feat: a"], "stat": [], "compare": {}}
        self.t1["checked"] = {"ok": True, "tree": "T1", "record": "R", "ran": ["frontend checks"], "new": []}
        for target, kw in (("load", {"side_effect": lambda p: self.state}),
                           ("save", {}), ("hold", {}),
                           ("repo_root", {"return_value": "/repo"}),
                           ("versions", {"return_value": [{"id": 7, "head_commit_sha": "NEW"}]}),
                           ("working_diff", {"return_value": "diff"})):
            patcher = mock.patch.object(T, target, **kw)
            setattr(self, target, patcher.start())
            self.addCleanup(patcher.stop)
        self.checks = mock.patch.object(T, "checks").start()
        self.addCleanup(mock.patch.stopall)
        self.checks.status.return_value = ({"hooks": []}, None)
        self.checks.digest.return_value = "R"
        self.checks.snapshot.return_value = "T1"
        self.checks._out.side_effect = lambda root, *a: "HEADTREE" if a[-1] == "HEAD^{tree}" else "NEW"
        self.checks.Stop = type("Stop", (Exception,), {})
        mock.patch.object(T.hunk, "installed", return_value=False).start()
        # The push waits for GitLab's new version a second at a time; a mock that never shows
        # it would make a test sit the whole wait out.
        mock.patch.object(T.time, "sleep").start()
        self.ctx = {"web": "https://gitlab.example/g/p"}

    def push(self, tid="t1", committed=False):
        return T.push_fix(self.ctx, 1, "/state", tid, committed)

    def test_a_checked_fix_is_committed_squashed_pushed_and_linked(self):
        _, url = self.push()
        self.checks.commit_fixup.assert_called_once()
        self.assertEqual(self.checks.commit_fixup.call_args.args[3], "abc123")
        self.checks.autosquash.assert_called_once_with("/repo", "abc123", "T1")
        self.checks.push.assert_called_once()
        self.checks.trust.assert_called_once_with("/repo")
        self.assertEqual(url, "https://gitlab.example/g/p/-/merge_requests/1/diffs?diff_id=7&start_sha=NEW")
        self.assertEqual(self.t1["diff_url"], url)
        self.assertEqual(T.step_of(self.state, self.t1), "reply")

    def test_a_fix_without_passed_checks_goes_back_to_the_agent(self):
        self.t1["checked"] = {"ok": False, "failed": "frontend checks"}
        with self.assertRaisesRegex(T.Handoff, "verify t1"):
            self.push()
        self.checks.commit_fixup.assert_not_called()

    def test_files_changed_since_the_checks_go_back_to_the_agent(self):
        self.checks.snapshot.return_value = "OTHER"
        with self.assertRaisesRegex(T.Handoff, "Files changed since the checks ran"):
            self.push()
        self.checks.commit_fixup.assert_not_called()

    def test_changed_checks_go_back_to_the_agent(self):
        self.checks.status.return_value = ({"hooks": []}, "lefthook.yml changed")
        with self.assertRaisesRegex(T.Handoff, "lefthook.yml changed"):
            self.push()

    def test_a_fix_for_two_commits_is_the_agent_s_to_commit(self):
        self.t1["fix"]["fixup"] = ["abc123 feat: a", "def456 feat: b"]
        with self.assertRaisesRegex(T.Handoff, "--committed --token [0-9a-f]{8}"):
            self.push()
        self.checks.commit_fixup.assert_not_called()
        # The token the press handed over is the one stored, and new checks drop it.
        self.assertRegex(self.t1["push_token"], "^[0-9a-f]{8}$")

    def test_the_hand_off_names_the_button_pressed(self):
        self.t1["fix"]["fixup"] = ["abc123 feat: a", "def456 feat: b"]
        with self.assertRaisesRegex(T.Handoff, "pressed Ack and push for t1"):
            self.push()

    def test_a_push_cut_short_after_its_commit_goes_on_from_there(self):
        self.checks._out.side_effect = lambda root, *a: "T1" if a[-1] == "HEAD^{tree}" else "NEW"
        self.push()
        self.checks.commit_fixup.assert_not_called()
        self.checks.autosquash.assert_called_once_with("/repo", "abc123", "T1")
        self.checks.push.assert_called_once()

    def test_a_push_retried_checks_the_remote_against_the_branch_before_the_first_try(self):
        self.checks.push.side_effect = self.checks.Stop("rejected")
        with self.assertRaises(T.Handoff):
            self.push()
        self.assertEqual(self.t1["push_from"], "NEW")
        self.checks._out.side_effect = lambda root, *a: "T1" if a[-1] == "HEAD^{tree}" else "REWRITTEN"
        self.checks.push.side_effect = None
        self.push()
        self.assertEqual(self.checks.push.call_args.args[2], "NEW")
        self.assertNotIn("push_from", self.t1)

    def test_a_stop_while_pushing_goes_back_to_the_agent_in_its_words(self):
        self.checks.autosquash.side_effect = self.checks.Stop("squashing conflicts")
        with self.assertRaisesRegex(T.Handoff, "squashing conflicts"):
            self.push()
        self.checks.push.assert_not_called()

    def test_checks_are_refused_while_another_fix_waits_unpushed(self):
        mock.patch.object(T, "local_state", return_value=("/state", self.state)).start()
        with self.assertRaises(SystemExit), mock.patch("sys.stderr"):
            T.run_checks("/repo", "t2", [], 1)
        self.checks.run.assert_not_called()

    def test_unreadable_viewer_notes_stop_the_push(self):
        """`user_notes` is None when it cannot read them: a note left there may be the
        reason not to push."""
        mock.patch.object(T.hunk, "installed", return_value=True).start()
        mock.patch.object(T.hunk, "find_session", return_value="sid").start()
        mock.patch.object(T.hunk, "owner_id", return_value="me").start()
        notes = mock.patch.object(T.hunk, "user_notes", return_value=None).start()
        with self.assertRaisesRegex(T.Handoff, "could not be read"):
            self.push()
        self.checks.commit_fixup.assert_not_called()
        notes.return_value = [{"body": "why?"}]
        with self.assertRaisesRegex(T.Handoff, "answer them"):
            self.push()
        self.checks.commit_fixup.assert_not_called()
        notes.return_value = []
        mock.patch.object(T, "hunk_close_decision", return_value=("", False)).start()
        self.push()
        self.checks.commit_fixup.assert_called_once()

    def test_a_new_commit_on_the_card_is_the_agent_s_to_make_first(self):
        self.t1["fix"]["new"] = ["fix: guard the old helper"]
        with self.assertRaises(T.Handoff) as e:
            self.push()
        self.checks.commit_fixup.assert_not_called()
        msg = str(e.exception)
        self.assertLess(msg.index('-m "fix: guard the old helper"'), msg.index("--fixup=abc123"))

    def test_a_fix_asked_for_after_a_push_is_made_and_pushed_again(self):
        """The reviewer answers the pushed fix with more to do: the next fix agreed is made,
        checked and pushed, and its link is its own push's: the push before it is the
        topic's first fix, a fix of its own."""
        self.t1.update(diff_url="https://gitlab.example/g/p/-/merge_requests/1/diffs?diff_id=3&start_sha=S0",
                       start_sha="S0", reply="Fixed.")
        self.state["threads"]["d1"]["awaiting"] = "reviewer"
        self.assertEqual(T.step_of(self.state, self.t1), "reply")
        self.state["threads"]["d1"].update(awaiting="you", notes=self.state["threads"]["d1"]["notes"] + [
            {"author": "Robin", "body": "also the other call", "at": "2026-01-09T00:00:00Z", "mine": False}])
        self.assertEqual(T.step_of(self.state, self.t1), "plan")
        self.t1["proposal"] = {"on": T.reviewer_mark(self.state, self.t1), "says": "needs",
                               "why": "w", "plan": "fix the other call", "outcome": "fix"}
        with mock.patch.object(sys, "argv", ["threads.py", "agree", "t1"]), redirect_stdout(io.StringIO()), \
                mock.patch.object(T, "resolve_state", return_value=(self.ctx, 1, "/state", self.state)):
            T.main()
        self.assertEqual(T.step_of(self.state, self.t1), "fix")
        for gone in ("diff_url", "fix", "checked", "reply"):
            self.assertNotIn(gone, self.t1)
        self.t1["fix"] = {"did": "d", "fixup": ["abc123 feat: a"], "stat": [], "compare": {}}
        self.t1["checked"] = {"ok": True, "tree": "T1", "record": "R", "ran": [], "new": []}
        _, url = self.push()
        self.assertTrue(url.endswith("diff_id=7&start_sha=NEW"))
        self.assertEqual(T.step_of(self.state, self.t1), "reply")

    def test_the_card_says_where_the_checks_stand(self):
        self.assertEqual(T.checks_line(self.t1), ["Checks passed: frontend checks."])
        T._WORKING.clear()
        self.checks.snapshot.return_value = "OTHER"
        self.assertEqual(T.checks_line(self.t1), ["Files or checks changed since the checks ran."])
        self.t1["checked"] = {"ok": False, "failed": "frontend checks", "output": "boom"}
        self.assertEqual(T.checks_line(self.t1)[0], "Checks failed: frontend checks.")
        self.assertIn("boom", T.checks_line(self.t1))



class TestFixPerPush(unittest.TestCase):
    """A topic's pushes as its fixes, and the links its `Fixed:` reply gives."""

    @staticmethod
    def push(start, after, k, clean=True):
        return {"before": start, "start": start, "after": after, "clean": clean,
                "url": f"G/diffs?diff_id={k}&start_sha={start}"}

    def topic(self, *pushes, said=()):
        t = {"id": "t1", "thread_ids": ["d1"], "pushes": list(pushes), "diff_url": pushes[-1]["url"],
             "outcome": "fix"}
        notes = [{"body": "please", "mine": False}] + [{"body": b, "mine": True} for b in said]
        return {"topics": [t], "threads": {"d1": {"notes": notes}}}, t

    def test_pushes_with_nothing_between_are_one_fix_and_one_link(self):
        _, t = self.topic(self.push("A", "B", 2), self.push("B", "C", 3))
        self.assertEqual(T.fixes_of(t), [{"pushes": [1, 2], "before": "A", "start": "A", "after": "C",
                                          "url": "G/diffs?diff_id=3&start_sha=A", "mixed": False}])

    def test_a_push_between_keeps_two_fixes(self):
        _, t = self.topic(self.push("A", "B", 2), self.push("X", "C", 4))
        self.assertEqual([f["pushes"] for f in T.fixes_of(t)], [[1], [2]])

    def test_a_push_that_carried_more_than_its_fix_joins_none(self):
        _, t = self.topic(self.push("A", "B", 2), self.push("B", "C", 3, clean=False))
        fixes = T.fixes_of(t)
        self.assertEqual([f["pushes"] for f in fixes], [[1], [2]])
        self.assertEqual([f["mixed"] for f in fixes], [False, True])

    def test_nothing_linked_yet_lists_every_fix(self):
        state, t = self.topic(self.push("A", "B", 2), self.push("X", "C", 4))
        self.assertEqual(T.fixed_body(state, t),
                         "Fixed:\n- G/diffs?diff_id=2&start_sha=A\n- G/diffs?diff_id=4&start_sha=X\n")

    def test_one_fix_is_one_line(self):
        state, t = self.topic(self.push("A", "B", 2), self.push("B", "C", 3))
        self.assertEqual(T.fixed_body(state, t), "Fixed: G/diffs?diff_id=3&start_sha=A\n")

    def test_an_earlier_link_in_the_thread_leaves_only_what_came_after(self):
        # Linked after its first push: the second, consecutive as it is, is its own link.
        state, t = self.topic(self.push("A", "B", 2), self.push("B", "C", 3),
                              said=["Fixed: G/diffs?diff_id=2&start_sha=A"])
        self.assertEqual(T.fixed_body(state, t), "Fixed: G/diffs?diff_id=3&start_sha=B\n")
        # A reviewer quoting the link is not the user's having posted it.
        state["threads"]["d1"]["notes"][1]["mine"] = False
        self.assertEqual(T.fixed_body(state, t), "Fixed: G/diffs?diff_id=3&start_sha=A\n")

    def test_the_card_says_each_fix_and_marks_what_may_be_mixed(self):
        state, t = self.topic(self.push("A", "B", 2), self.push("X", "C", 4, clean=False))
        t["plan"] = "p"
        with mock.patch.object(T, "_git", return_value=""):
            out = T.pushed_steps(state, t)
        self.assertEqual(out[1:4], [f"{T.ui.STEP_DONE_ICON} Fix pushed · 2 pushes",
                                    f"\u00a0\u00a0{T.ui.PUSHED_ICON} push 1 · [the diff {T.LINK_ICON}](G/diffs?diff_id=2&start_sha=A)",
                                    f"\u00a0\u00a0{T.ui.PUSHED_ICON} push 2 · [the diff {T.LINK_ICON}](G/diffs?diff_id=4&start_sha=X)"
                                    " · may include other topics' changes"])


class TestStateByWord(unittest.TestCase):
    """Moving a topic by talking to the agent, along the workflows it has to carry: an
    answer and a wait, a pushed fix defended or redone, a fix dropped while it is made. Each
    move says where the topic stands after it, so one that changes nothing is noticed."""

    def setUp(self):
        mock.patch.dict(os.environ, {"REVIEW_UI_DRAWN": "1", T.PRESS_ENV: "1"}).start()
        self.addCleanup(mock.patch.stopall)
        T._WORKING.clear()
        self.addCleanup(T._WORKING.clear)
        self.state = TestRounds.state(1)
        TestRounds.agree(self.state, "t1")
        self.t1 = T.topic_for(self.state, "t1")
        self.diff = mock.patch.object(T, "working_diff", return_value="").start()

    def run_cmd(self, *argv, stdin=""):
        with mock.patch.object(sys, "argv", ["threads.py", *argv, "--iid", "1"]), \
                mock.patch.object(T, "load", return_value=self.state), \
                mock.patch.object(T, "save"), mock.patch.object(T, "hold"), \
                mock.patch.object(T, "state_file", return_value="/state"), \
                mock.patch.object(T, "context", return_value={"slug": "g-p", "enc": "g%2Fp", "web": "https://gl/g/p"}), \
                mock.patch("sys.stdin", io.StringIO(stdin)), \
                mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            T.main()
        return out.getvalue()

    def reviewer_writes(self, body):
        th = self.state["threads"]["d1"]
        th["notes"].append({"author": "Robin", "body": body, "at": "2026-02-01T00:00:00Z", "mine": False})
        th["awaiting"] = "you"
        self.t1.pop("state", None)

    def pushed(self):
        self.t1.update(diff_url="https://gl/g/p/-/merge_requests/1/diffs?diff_id=2",
                       pushes=[{"before": "A", "start": "A", "after": "B", "clean": True,
                                "url": "https://gl/g/p/-/merge_requests/1/diffs?diff_id=2"}])

    def test_an_outcome_alone_changes_the_plan_s_kind_and_says_where_it_stands(self):
        out = self.run_cmd("set", "t1", "--outcome", "push-back")
        self.assertEqual((self.t1["outcome"], self.t1["plan"]), ("push-back", "p"))
        self.assertIn('t1 is now to reply: the plan agreed is "Push back"', out)

    def test_a_move_that_changes_nothing_on_the_card_says_so(self):
        """`set --state open` on t1 once left the card as it was, and nothing said why."""
        self.assertIn("t1 is now to fix", self.run_cmd("set", "t1", "--state", "open"))

    def test_a_plan_to_reply_is_refused_while_the_fix_is_in_the_tree(self):
        self.t1["fix"] = {"did": "d"}
        self.diff.return_value = "@@ -1 +1 @@\n-a\n+b\n"
        with mock.patch("sys.stderr", new_callable=io.StringIO) as err, self.assertRaises(SystemExit):
            self.run_cmd("set", "t1", "--outcome", "reply")
        self.assertIn("drop-fix t1", err.getvalue())
        self.assertEqual(self.t1["outcome"], "fix")

    def test_a_question_back_waits_and_the_answer_brings_it_back_to_plan(self):
        """Workflow 1: the answer goes up, the topic waits; the reviewer's clarification
        reopens it, to be planned on the whole discussion."""
        self.t1.update(outcome="question", reply="Meinst du A oder B?\n")
        with mock.patch.object(T, "api_post", return_value={"id": 9}):
            T.post_reply(self.state, {"enc": "g%2Fp", "web": "https://gl/g/p"}, 1, "t1", None)
        self.assertEqual(T.step_of(self.state, self.t1), "none")
        self.reviewer_writes("B, bitte.")
        self.assertEqual(T.step_of(self.state, self.t1), "plan")

    def test_your_own_comment_last_on_gitlab_offers_to_wait(self):
        th = self.state["threads"]["d1"]
        th["notes"].append({"author": "Me", "body": "Meinst du A?", "at": "2026-02-01T00:00:00Z", "mine": True})
        th["awaiting"] = "reviewer"
        self.assertTrue(T.state_json(self.state)["topics"][0]["you_last"])
        self.run_cmd("wait", "t1")
        self.assertFalse(T.state_json(self.state)["topics"][0]["you_last"])
        self.t1.update(state=None, early_posted=True)     # a reply ahead of the fix: known
        self.assertFalse(T.state_json(self.state)["topics"][0]["you_last"])

    def test_a_pushed_fix_defended_is_a_reply_that_keeps_the_fix_and_links_no_fixed(self):
        """Workflow 2: the reviewer disagrees with the pushed fix, and the plan is to defend it."""
        self.pushed()
        self.reviewer_writes("Anders, bitte.")
        self.run_cmd("set", "t1", "--decision", "keep it", "--plan", "explain why", "--outcome", "push-back")
        self.assertEqual(T.step_of(self.state, self.t1), "reply")
        self.assertTrue(T.state_json(self.state)["topics"][0]["pushed"])
        with mock.patch.object(T, "pushed_steps", return_value=["PUSHED"]):
            parts = T._step_parts(self.state, self.t1)
        self.assertIn("PUSHED", parts)
        self.assertTrue(any("Agreed plan: Push back" in x for x in parts))
        self.run_cmd("set", "t1", "--reply", "-", stdin="Bewusst so, weil …\n")
        self.assertNotIn("Fixed", self.t1["reply"])

    def test_a_pushed_fix_redone_is_a_new_fix_round(self):
        self.pushed()
        self.reviewer_writes("Anders, bitte.")
        self.run_cmd("set", "t1", "--decision", "redo", "--plan", "do it as asked", "--outcome", "fix")
        self.assertEqual(T.step_of(self.state, self.t1), "fix")
        self.assertNotIn("diff_url", self.t1)
        self.assertEqual(len(self.t1["pushes"]), 1)                # the earlier push stays


class TestDropFix(unittest.TestCase):
    """Workflow 3 in a real checkout: a fix found not wanted while it is made is kept in git,
    the working tree put back, and the topic becomes the reply saying what was found."""

    @classmethod
    def setUpClass(cls):
        from lib.test_checks import Repo
        cls.Repo = Repo
        cls._template = tempfile.TemporaryDirectory()
        Repo.build(cls._template.name)

    @classmethod
    def tearDownClass(cls):
        cls._template.cleanup()

    def setUp(self):
        from lib.test_checks import sh
        self.sh = sh
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.r = self.Repo(self._tmp.name, self._template.name)
        cwd = os.getcwd()
        os.chdir(self.r.root)
        self.addCleanup(os.chdir, cwd)
        mock.patch.dict(os.environ, {"REVIEW_UI_DRAWN": "1"}).start()
        self.addCleanup(mock.patch.stopall)
        T._WORKING.clear()
        self.addCleanup(T._WORKING.clear)
        self.state = TestRounds.state(1)
        TestRounds.agree(self.state, "t1")
        self.t1 = T.topic_for(self.state, "t1")

    def git(self, *a):
        return self.sh(self.r.root, "git", *a)

    def test_the_fix_is_kept_the_tree_put_back_and_the_plan_a_reply(self):
        self.r.write("a.txt", "half a fix\n")
        self.r.write("added.txt", "new\n")
        self.r.write("notes.txt", "not the fix's\n")
        T.drop_fix(self.state, 1, self.t1, "it breaks the cache", "say why it is not done here",
                   "reply", new=["added.txt"])
        self.assertEqual(self.git("status", "--porcelain"), "?? notes.txt")   # only the fix left
        ref = "refs/rework-mr/mr1/t1/dropped1"
        self.assertIn("+half a fix", self.git("diff", f"{ref}/before", f"{ref}/after"))
        self.assertIn("added.txt", self.git("diff", "--stat", f"{ref}/before", f"{ref}/after"))
        self.assertEqual((self.t1["outcome"], self.t1["plan"]), ("reply", "say why it is not done here"))
        self.assertEqual(T.step_of(self.state, self.t1), "reply")
        self.assertIn(f"dropped fix kept: `git diff {ref}/before {ref}/after`", T.render_plans(self.state))
        self.assertTrue(T.state_json(self.state)["topics"][0]["dropped"])

    def test_a_fix_put_aside_goes_back_to_its_topic_and_returns_with_its_own(self):
        """A fix made while t2 was current, put aside: out of the working tree, kept in git,
        t2 current again; once t1 is current, the fix is back, ready to push, its commit
        found again after another push rewrote it."""
        two = TestRounds.state(2)
        self.state["threads"]["d2"] = two["threads"]["d2"]
        self.state["topics"].append(two["topics"][1])
        head = self.git("log", "-1", "--format=%h %s")
        self.t1.update(agreed_change="@@ a.txt:1-1\nfixed\n",
                       fix={"did": "d", "fixup": [head], "made_on": "t2"}, checked={"ok": True})
        self.r.write("a.txt", "an early fix\n")
        with mock.patch.object(T.CURRENT, "jump") as jump:
            T.drop_fix(self.state, 1, self.t1, "made while t2 was current", None, None, aside=True)
        jump.assert_called_once_with(self.state, "t2")
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertEqual((self.t1["outcome"], self.t1["plan"]), ("fix", "p"))
        self.assertIn("agreed_change", self.t1)
        self.assertEqual(T.step_of(self.state, self.t1), "fix")
        self.assertIn("fix put aside: `git diff refs/rework-mr/mr1/t1/dropped1/before",
                      T.render_plans(self.state))
        # Another topic pushed meanwhile: the commit the fix goes into has a new hash.
        self.git("commit", "-q", "--amend", "--no-verify", "--no-edit", "--date=2001-01-01")
        new = self.git("log", "-1", "--format=%h %s")
        self.assertNotEqual(new, head)
        self.assertTrue(T.restore_aside(self.state, 1, self.t1))
        self.assertEqual(self.git("status", "--porcelain"), "M a.txt")
        self.assertEqual(T.step_of(self.state, self.t1), "fixed")
        self.assertEqual(self.t1["fix"]["fixup"], [new])
        self.assertTrue(self.t1["fix"]["restored"])
        self.assertNotIn("made_on", self.t1["fix"])
        self.assertIn("The fix you put aside", T.render_fix(self.state, "t1"))

    def test_a_fix_put_aside_comes_back_when_its_card_comes_up(self):
        self.t1.update(fix={"did": "d"}, checked={"ok": True})
        self.r.write("a.txt", "an early fix\n")
        T.drop_fix(self.state, 1, self.t1, "not now", None, None, aside=True)
        self.assertEqual(self.git("status", "--porcelain"), "")
        with mock.patch.object(sys, "argv", ["threads.py", "quote", "t1", "--focus", "--iid", "1"]), \
                mock.patch.object(T, "load", return_value=self.state), \
                mock.patch.object(T, "save"), mock.patch.object(T, "hold"), \
                mock.patch.object(T, "state_file", return_value="/state"), \
                mock.patch.object(T, "context", return_value={"slug": "g-p", "enc": "g%2Fp", "web": "https://gl/g/p"}), \
                mock.patch.object(T, "refuse_leaving_fix"), mock.patch.object(T, "chat_opens"), \
                mock.patch.object(T.CURRENT, "view", return_value=None), \
                mock.patch.object(T, "current_topic", return_value="t1"), \
                mock.patch.object(T, "render_quote", return_value=""), mock.patch.object(T, "emit"), \
                mock.patch("sys.stdout", new_callable=io.StringIO):
            T.main()
        self.assertEqual(self.git("status", "--porcelain"), "M a.txt")
        self.assertEqual(T.step_of(self.state, self.t1), "fixed")

    def test_a_fix_put_aside_waits_while_another_is_in_the_tree(self):
        self.t1.update(fix={"did": "d"}, checked={"ok": True})
        self.r.write("a.txt", "an early fix\n")
        T.drop_fix(self.state, 1, self.t1, "not now", None, None, aside=True)
        self.r.write("b.txt", "another topic's fix\n")
        T._WORKING.clear()
        self.assertFalse(T.restore_aside(self.state, 1, self.t1))
        self.assertIn("aside", self.t1)

    def test_the_reviewer_s_answer_plans_again_and_a_new_fix_knows_the_kept_one(self):
        self.r.write("a.txt", "half a fix\n")
        T.drop_fix(self.state, 1, self.t1, "too big", "say it is too big for this MR", "reply")
        with mock.patch.object(T, "api_post", return_value={"id": 9}), mock.patch.dict(os.environ, {T.PRESS_ENV: "1"}):
            self.t1["reply"] = "Zu groß für diesen MR.\n"
            T.post_reply(self.state, {"enc": "g%2Fp", "web": "https://gl/g/p"}, 1, "t1", None)
        self.assertEqual(T.step_of(self.state, self.t1), "none")          # waits on the reviewer
        th = self.state["threads"]["d1"]
        th["notes"].append({"author": "Robin", "body": "Dann nur Teil A?", "at": "2026-02-01T00:00:00Z", "mine": False})
        th["awaiting"] = "you"
        self.t1.pop("state", None)
        self.assertEqual(T.step_of(self.state, self.t1), "plan")
        TestRounds.agree(self.state, "t1")                                 # a fix after all
        self.assertEqual(T.step_of(self.state, self.t1), "fix")
        self.assertTrue(T.state_json(self.state)["topics"][0]["dropped"])

    def test_a_pushed_fix_with_nothing_in_the_tree_is_not_dropped_here(self):
        self.t1["diff_url"] = "https://gl/x"
        with mock.patch("sys.stderr", new_callable=io.StringIO) as err, self.assertRaises(SystemExit):
            T.drop_fix(self.state, 1, self.t1, "w", "p", "reply")
        self.assertIn("pushed", err.getvalue())


class TestPushFixGit(unittest.TestCase):
    """`push` on a throwaway clone of a bare repository: the commits the card names are the
    commits the remote ends up with, and the hooks the record stands for are not skipped."""

    @classmethod
    def setUpClass(cls):
        from lib.test_checks import Repo
        cls.Repo = Repo
        cls._template = tempfile.TemporaryDirectory()
        Repo.build(cls._template.name)

    @classmethod
    def tearDownClass(cls):
        cls._template.cleanup()

    def setUp(self):
        from lib.test_checks import MSG, sh
        self.sh, self.MSG = sh, MSG
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.r = self.Repo(self._tmp.name, self._template.name)
        self.state = TestRounds.state(1)
        TestRounds.agree(self.state, "t1")
        self.t1 = T.topic_for(self.state, "t1")
        for target, kw in (("load", {"side_effect": lambda p: self.state}), ("save", {}), ("hold", {}),
                           ("repo_root", {"return_value": self.r.root}),
                           ("versions", {"return_value": [{"id": 7, "head_commit_sha": "S"}]}),
                           ("diff_url_for", {"return_value": "URL"})):
            mock.patch.object(T, target, **kw).start()
        mock.patch.object(T.hunk, "installed", return_value=False).start()
        self.addCleanup(mock.patch.stopall)

    def fix(self, fixup=(), new=(), files=("a.txt", "c.txt")):
        for f in files:
            self.r.write(f, "fixed\n")
        rec = T.checks.load(self.r.root) or self.r.record()
        stamp = T.checks.run(self.r.root, rec)
        self.assertTrue(stamp["ok"], stamp)
        self.t1["fix"] = {"did": "d", "fixup": list(fixup), "new": list(new), "stat": [], "compare": {}}
        self.t1["checked"] = stamp
        return stamp

    def push(self, committed=False):
        return T.push_fix({"web": "https://gitlab.example/g/p"}, 1, "/state", "t1", committed)

    def git(self, *a):
        return self.sh(self.r.root, "git", *a)

    def log(self, ref="origin/main"):
        return self.git("log", "--format=%s", ref).splitlines()

    def test_one_fixup_is_squashed_into_its_commit(self):
        stamp = self.fix(fixup=[f"{self.r.target} feat: a and b"], files=["a.txt"])
        self.push()
        self.assertEqual(self.log(), ["feat: c", "feat: a and b"])
        self.assertEqual(self.git("rev-parse", "origin/main^{tree}"), stamp["tree"])

    def test_the_push_keeps_the_fix_in_git_for_the_viewer(self):
        start = self.git("rev-parse", "HEAD")
        self.t1["start_sha"] = start
        self.fix(fixup=[f"{self.r.target} feat: a and b"], files=["a.txt"])
        self.push()
        head = self.git("rev-parse", "origin/main")
        self.assertEqual(T.fix_refs.kept(self.r.root, "rework-mr", 1), {"t1/push1": (start, head)})
        self.assertEqual([f for f, _a, _r in T.fix_refs.stat(self.r.root, (start, head))], ["a.txt"])

    def test_each_push_keeps_its_own_change_with_another_topic_s_between(self):
        """t1 pushed, then another topic's push, then t1 again: each of t1's pushes is kept
        with its own change only, and they stay two fixes, each with its own link."""
        heads = []

        def versions(*_a):
            head = self.git("rev-parse", "origin/main")
            if head not in heads:
                heads.append(head)
            return [{"id": heads.index(head) + 1, "head_commit_sha": head}]
        mock.patch.object(T, "versions", side_effect=versions).start()
        mock.patch.object(T, "diff_url_for", side_effect=lambda ctx, iid, start, head:
                          f"G/diffs?diff_id={versions()[0]['id']}&start_sha={start}").start()
        self.fix(fixup=[f"{self.r.target} feat: a and b"], files=["a.txt"])
        self.push()
        # Another topic's fix, pushed between.
        self.r.write("d.txt", "the other topic\n")
        self.git("add", "d.txt")
        self.git("commit", "-q", "--no-verify", "-m", "feat: d")
        self.git("push", "-q", "--no-verify")
        self.fix(fixup=[f"{self.r.target} feat: a and b"], files=["b.txt"])
        self.push()
        kept = T.fix_refs.kept(self.r.root, "rework-mr", 1)
        self.assertEqual(sorted(kept), ["t1/push1", "t1/push2"])
        self.assertEqual([f for f, _a, _r in T.fix_refs.stat(self.r.root, kept["t1/push1"])], ["a.txt"])
        self.assertEqual([f for f, _a, _r in T.fix_refs.stat(self.r.root, kept["t1/push2"])], ["b.txt"])
        fixes = T.fixes_of(self.t1)
        self.assertEqual([f["pushes"] for f in fixes], [[1], [2]])
        self.assertEqual([f["url"] for f in fixes], [f"G/diffs?diff_id=2&start_sha={heads[0]}",
                                                     f"G/diffs?diff_id=4&start_sha={heads[2]}"])
        self.assertFalse(any(f["mixed"] for f in fixes))

    def test_a_new_commit_alone_is_pushed_as_one(self):
        stamp = self.fix(new=["fix: the old helper"])
        with self.assertRaisesRegex(T.Handoff, 'git commit -m "fix: the old helper"'):
            self.push()
        self.git("reset", "-q")
        self.git("add", "a.txt", "c.txt")
        self.git("commit", "-q", "-m", "fix: the old helper")
        self.push(committed=True)
        self.assertEqual(self.log(), ["fix: the old helper", "feat: c", "feat: a and b"])
        self.assertEqual(self.git("rev-parse", "origin/main^{tree}"), stamp["tree"])
        # The agent's commits are not the push that tells whether the record stands.
        self.assertFalse(T.checks.load(self.r.root).get("trusted"))

    def test_a_new_commit_and_a_fixup_keep_the_new_one(self):
        stamp = self.fix(fixup=[f"{self.r.target} feat: a and b"], new=["fix: the old helper"])
        with self.assertRaises(T.Handoff):
            self.push()
        self.git("reset", "-q")
        self.git("add", "c.txt")
        self.git("commit", "-q", "-m", "fix: the old helper")
        self.git("add", "a.txt")
        self.git("commit", "-q", f"--fixup={self.r.target}")
        self.push(committed=True)
        self.assertEqual(self.log(), ["fix: the old helper", "feat: c", "feat: a and b"])
        self.assertEqual(self.git("show", "origin/main~2:a.txt"), "fixed")
        self.assertEqual(self.git("rev-parse", "origin/main^{tree}"), stamp["tree"])

    def test_a_new_commit_the_agent_did_not_make_stops_the_push(self):
        self.fix(fixup=[f"{self.r.target} feat: a and b"], new=["fix: the old helper"])
        with self.assertRaises(T.Handoff):
            self.push()
        self.git("commit", "-q", f"--fixup={self.r.target}")
        with self.assertRaisesRegex(T.Handoff, 'not on the branch: "fix: the old helper"'):
            self.push(committed=True)
        self.assertEqual(self.log(), ["feat: c", "feat: a and b"])

    def test_the_agent_s_commits_meet_the_commit_msg_hook(self):
        """An untrusted record: the hand-off has git run the hooks, and a message the hook
        refuses stops the push even where the agent skipped them."""
        self.r.hook("commit-msg", self.MSG)               # accepts `fixup!` messages only
        self.fix(new=["fix: the old helper"])
        with self.assertRaises(T.Handoff) as e:
            self.push()
        self.assertNotIn("--no-verify", str(e.exception))
        self.git("commit", "-q", "--no-verify", "-m", "fix: the old helper")
        with self.assertRaisesRegex(T.Handoff, "commit-msg hook refused"):
            self.push(committed=True)
        self.assertEqual(self.log(), ["feat: c", "feat: a and b"])
        self.assertFalse(T.checks.load(self.r.root).get("trusted"))


class TestFixStoredWithoutTheMod(unittest.TestCase):
    """`diff-view` stores the fix (its commits above all) card or no card, from the state as
    it is once held."""

    def run_view(self, stale, fresh):
        argv = ["threads.py", "diff-view", "t1", "--fixup", "abc123 feat: a", "--new-commit", "fix: b"]
        saved = []
        with mock.patch.dict(os.environ, {"REVIEW_UI_DRAWN": "0"}), \
                mock.patch.object(T, "local_state", return_value=("/state", stale)), \
                mock.patch.object(T, "load", return_value=fresh), mock.patch.object(T, "hold"), \
                mock.patch.object(T, "save", side_effect=lambda p, st: saved.append(json.loads(json.dumps(st)))), \
                mock.patch.object(T, "hunk") as hunk, mock.patch.object(sys, "argv", argv), \
                mock.patch.object(sys, "stdin", io.StringIO("@@ -1 +1 @@\n-a\n+b\n")), \
                redirect_stdout(io.StringIO()):
            hunk.MAX_NOTES = 3
            hunk.installed.return_value = False
            T.main()
        return saved

    def test_the_fix_s_commits_are_stored_and_a_write_since_the_read_survives(self):
        stale = TestRounds.state(1)
        fresh = TestRounds.state(1)
        fresh["topics"][0]["proposal"] = {"on": "x", "why": "judged meanwhile"}
        saved = self.run_view(stale, fresh)
        self.assertEqual(len(saved), 1)
        t = saved[0]["topics"][0]
        self.assertEqual((t["fix"]["fixup"], t["fix"]["new"]), (["abc123 feat: a"], ["fix: b"]))
        self.assertEqual(t["proposal"]["why"], "judged meanwhile")


if __name__ == "__main__":
    unittest.main(verbosity=2)
