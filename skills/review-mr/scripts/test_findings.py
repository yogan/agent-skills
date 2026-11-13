#!/usr/bin/env python3
"""Tests for review-mr's presentation and status layer — code snippets, the overview
table, topic status derivation, and thread reconciliation.

Run: `python3 skills/review-mr/scripts/test_findings.py` (stdlib only).
"""
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import findings as F                                  # noqa: E402
from lib import critical_manifest                      # noqa: E402
os.environ["REVIEW_UI_DRAWN"] = "0"   # the views as without the review-ui mod, whatever session runs this (lib/ui.py)


def gate_signature(key):
    """The marker strings the Stop hook looks for in a given gated command's output —
    read from the shipped spec so a render and its gate cannot drift apart silently.
    (rework-mr's test_threads.py has the same helper against its own spec.)"""
    with open(os.path.join(HERE, "paste-gates.json")) as fh:
        gates = json.load(fh)["gates"]
    return next(g["signature"] for g in gates if g["key"] == key)


# Mirrors hooks/paste-gate.py's `_FLAGS`, and the helper below mirrors
# skills/rework-mr/scripts/test_threads.py's `forbidden_rules`. Duplicated rather than
# shared because the point is that each test pins its OWN skill's shipped spec — the same
# arrangement `gate_signature` above already uses for the gate side.
_FLAGS = {"m": re.M, "i": re.I, "s": re.S}


def forbidden_rules():
    """{key: compiled pattern} the Stop hook refuses to let reach the user, from the same
    spec — compiled WITH each rule's declared flags, as the hook compiles them.

    Matching the raw pattern string with a flagless `assertRegex` silently diverges from
    the hook the moment a rule is line-anchored with `"flags": "m"`: the hook goes on
    matching a warning that sits on its own line, this test stops, and the failure reads
    as "the producer stopped emitting the warning" when the producer never changed.
    """
    with open(os.path.join(HERE, "paste-gates.json")) as fh:
        rules = json.load(fh)["forbidden"]
    return {r["key"]: re.compile(r["text"], sum(_FLAGS[c] for c in r.get("flags", "")))
            for r in rules}


def new_state(**overrides):
    state = {"iid": 1, "title": "x", "author": None, "lang": None,
             "threads": {}, "topics": []}
    state.update(overrides)
    return state


def add_linked_topic(state, thread_id, **fields):
    """`add_topic`, then link it to `thread_id` — `thread_ids` is in `add_topic`'s
    ADD_PROTECTED set (never settable from seed/import fields), so tests attach it
    the same way `attach_thread` does: after creation."""
    t = F.add_topic(state, **fields)
    t["thread_ids"] = [thread_id]
    return t

class TestAttachThread(unittest.TestCase):
    def test_first_posted_thread_consumes_the_initial_draft(self):
        state = new_state()
        t = F.add_topic(state, draft="The comment already posted to GitLab.")

        F.attach_thread(state, t, "d1", None, 1, start_sha="abc")

        self.assertIsNone(t["draft"])
        self.assertEqual(t["thread_ids"], ["d1"])

    def test_an_additional_thread_keeps_a_genuine_follow_up_draft(self):
        state = new_state()
        t = add_linked_topic(state, "d1", draft="A follow-up still waiting to be posted.")

        F.attach_thread(state, t, "d2", None, 1, start_sha="abc")

        self.assertEqual(t["draft"], "A follow-up still waiting to be posted.")
        self.assertEqual(t["thread_ids"], ["d1", "d2"])


class Throwaway:
    """A temp directory that removes itself when the test ends.

    These tests need a real directory on disk because `code_snippet` reads real files
    through a worktree path. `tempfile.mkdtemp()` gave them one and nothing ever removed it,
    which leaked three directories per suite run — 460 of them had accumulated before anyone
    counted.

    It deletes ONLY what it made: `mkdtemp` returns a freshly created, uniquely named
    directory that belongs to this test and cannot be anything else. Nothing here globs, and
    nothing here removes a path it did not create — a cleanup that swept a *pattern* would
    eventually match a file some real run had written, which is a far worse bug than the leak
    it fixed.
    """

    def _throwaway_dir(self):
        path = tempfile.mkdtemp(prefix="test-findings-")
        self.addCleanup(shutil.rmtree, path, ignore_errors=True)
        return path


class TestCodeSnippet(Throwaway, unittest.TestCase):
    """code_snippet's fixed line window has no notion of syntax state — see
    lib.snippet.open_construct, and threads.py's render_code_context, which has the
    same hazard and is covered by its own equivalent tests."""

    def _worktree_with(self, rel_path, content):
        wt = self._throwaway_dir()
        full = os.path.join(wt, rel_path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as f:
            f.write(content)
        original = F.get_worktree
        F.get_worktree = lambda slug, iid: wt
        self.addCleanup(setattr, F, "get_worktree", original)
        return wt

    def test_window_starting_mid_docstring_extends_back_to_the_opener(self):
        src = (
            'class Service:\n'
            '    """Application service for tags.\n'
            '\n'
            '    Tags are additive and carry no business rules of their own yet —\n'
            '    any tag can be set on any document by any user, at any time.\n'
            '    """\n'
            '\n'
            '    def __init__(self, repo) -> None:\n'
            '        self.repo = repo\n'
        )
        self._worktree_with("service.py", src)
        out = F.code_snippet(new_state(slug="x"),
                              {"file": "service.py", "line": 8})
        self.assertIn('2 |     """Application service for tags.', out)
        self.assertTrue(out.startswith("```python\n"))

    def test_window_that_never_enters_a_construct_is_unaffected(self):
        src = "\n".join(f"x{i} = {i}" for i in range(1, 20))
        self._worktree_with("plain.py", src)
        out = F.code_snippet(new_state(slug="x"), {"file": "plain.py", "line": 10})
        self.assertTrue(out.startswith("```python\n"))
        self.assertIn("► 10 | x10 = 10", out)

    def test_env_file_gets_a_highlightable_language(self):
        self._worktree_with("local.env", "A=1\nB=2\nC=3\n")
        out = F.code_snippet(new_state(slug="x"), {"file": "local.env", "line": 2})
        self.assertTrue(out.startswith("```bash\n"))


class TestAnchorWarning(Throwaway, unittest.TestCase):
    """The anchor check judges the snippet the reader sees, not only the marked line."""

    SRC = "\n".join([
        "function ShelfEntry({ label, gate }) {",      # 1
        "  const { data: me } = useViewer()",           # 2
        "",                                             # 3
        "  if (gate === 'staff' && !me?.staff) return null",  # 4
        "  return label",                               # 5
        "}",                                            # 6
    ] + [f"const filler{i} = {i}" for i in range(7, 80)])

    def setUp(self):
        wt = self._throwaway_dir()
        with open(os.path.join(wt, "Shelf.tsx"), "w") as f:
            f.write(self.SRC)
        original = F.get_worktree
        F.get_worktree = lambda slug, iid: wt
        self.addCleanup(setattr, F, "get_worktree", original)

    def _warn(self, line):
        return F.anchor_warning(new_state(slug="x"), {
            "id": "t1", "file": "Shelf.tsx", "line": line,
            "summary": "Gating moved into generic ShelfEntry (useViewer per entry)"})

    def test_name_a_few_lines_above_the_mark_is_in_view(self):
        self.assertIsNone(self._warn(4))

    def test_name_far_from_the_snippet_warns(self):
        out = self._warn(70)
        self.assertIn("anchor check", out)
        self.assertIn("**2**", out)

    def test_name_some_way_above_is_brought_into_view(self):
        # A finding deep inside the function it names: the snippet reaches back to the
        # name, every line between included, and there is nothing to warn about.
        self.assertIsNone(self._warn(20))
        snip = F.code_snippet(new_state(slug="x"), {
            "file": "Shelf.tsx", "line": 20,
            "summary": "Gating moved into generic ShelfEntry (useViewer per entry)"})
        numbers = [int(m) for m in re.findall(r"^[ ►] +(\d+) \|", snip, re.M)]
        self.assertEqual(numbers, list(range(2, 25)))

    def test_drawn_the_check_goes_to_the_agent_and_never_onto_the_card(self):
        state = new_state(slug="x")
        F.add_topic(state, summary="Gating moved into generic ShelfEntry", file="Shelf.tsx",
                    line=70)
        with patch.dict(os.environ, {"REVIEW_UI_DRAWN": "1"}):
            card = F.render_quote(state, "t1")
            err = io.StringIO()
            with redirect_stderr(err):
                F.anchor_notes(state, state["topics"])
        self.assertNotIn("anchor check", card)
        self.assertIn("anchor check", err.getvalue())
        self.assertIn("set t1 --line 1", err.getvalue())


class TestRefineRender(Throwaway, unittest.TestCase):
    """Re-showing a reworded draft repeats only what changed. The code was pasted when
    the topic came up and has not changed since; pasting it again every time a wording is
    adjusted buries the draft, which is the one thing that did."""

    def _drafted(self, shown=True):
        """A drafted topic whose context has already been rendered in full — which is what
        `--refine` is allowed to leave out. `shown=False` for the case where it has not."""
        wt = self._throwaway_dir()
        with open(os.path.join(wt, "worker.py"), "w") as f:
            f.write("try:\n    run()\nexcept Exception:\n    pass\n")
        original = F.get_worktree
        F.get_worktree = lambda slug, iid: wt
        self.addCleanup(setattr, F, "get_worktree", original)
        state = new_state(slug="x")
        F.add_topic(state, summary="swallowed exception hides real failures",
                    file="worker.py", line=3, draft="Kürzer geht es nicht.")
        if shown:
            F.render_quote(state, "t1")      # records the digest, as every full view does
        return state

    def test_refine_drops_the_code_and_keeps_the_draft(self):
        state = self._drafted(shown=False)
        full = F.render_quote(state, "t1")
        self.assertIn("except Exception:", full)          # the context, shown once
        out = F.render_quote(state, "t1", refine=True)
        self.assertNotIn("except Exception:", out)
        self.assertNotIn("Code currently in MR", out)
        for kept in ("◈ t1", "draft — not yet on GitLab", "Draft of comment to post",
                     "worker.py:3", "Kürzer geht es nicht."):
            self.assertIn(kept, out)

    def test_refine_keeps_the_gate_signature(self):
        """Read from the shipped spec, not retyped: a render that drifts out of its gate's
        signature stops being enforced, and a gate that never fires is invisible."""
        out = F.render_quote(self._drafted(), "t1", refine=True)
        for sig in gate_signature("quote"):
            self.assertIn(sig, out)

    def test_refine_is_refused_when_there_is_no_draft(self):
        """Everything such a topic renders IS context, so the short render would answer
        with a header and nothing else. Refusing says which command to run instead."""
        state = new_state(threads={"d1": {"body": "b", "author": "A"}})
        add_linked_topic(state, "d1", summary="an English title")
        with self.assertRaises(SystemExit):
            F.render_quote(state, "t1", refine=True)

    def _posted_with_follow_up(self, shown=True):
        state = new_state(threads={"d1": {
            "author": "Jane", "body": "Warum nicht einfach retry?", "file": "src/client.py",
            "line": 88, "url": "http://gl/1", "note_count": 2, "last_author": "Jane",
            "last_body": "Habe einen TODO ergänzt, reicht das?", "awaiting": "you"}})
        t = add_linked_topic(state, "d1", summary="unbounded retry loop",
                             file="src/client.py", line=88)
        t["draft"] = "Der TODO reicht nicht — ohne Obergrenze läuft das ewig."
        if shown:
            F.render_quote(state, "t1")      # records the digest, as every full view does
        return state

    def test_a_follow_up_draft_on_a_posted_topic_is_shown(self):
        """It used to be stored and copyable while no view rendered it: the user was asked
        to approve a comment the skill never displayed."""
        out = F.render_quote(self._posted_with_follow_up(shown=False), "t1")
        self.assertIn("Draft of follow-up reply to post (de)", out)
        self.assertIn("> Der TODO reicht nicht", out)
        self.assertIn("Habe einen TODO ergänzt", out)      # the thread, shown once

    def test_refine_on_a_posted_topic_drops_the_notes_and_keeps_the_follow_up(self):
        """Same rule as an unposted topic, with the thread in the role the code plays
        there: it is what the user is already looking at."""
        out = F.render_quote(self._posted_with_follow_up(), "t1", refine=True)
        for gone in ("Warum nicht einfach retry?", "Habe einen TODO ergänzt"):
            self.assertNotIn(gone, out)
        for kept in ("◈ t1", "http://gl/1", "Draft of follow-up reply to post (de)",
                     "> Der TODO reicht nicht"):
            self.assertIn(kept, out)
        for sig in gate_signature("quote"):
            self.assertIn(sig, out)

    def test_refine_falls_back_when_the_context_was_never_shown(self):
        """The short render must not be a topic's FIRST show — the user would be asked
        about a comment they have not seen. Nothing recorded, nothing to leave out."""
        out = F.render_quote(self._drafted(shown=False), "t1", refine=True)
        self.assertIn("has not been shown yet", out)
        self.assertIn("except Exception:", out)            # the code, in full

    def test_refine_falls_back_when_the_code_moved(self):
        """An anchor fix (`set --file/--line`) moves the code out from under the draft, so
        the lines the user saw are not the lines the comment is about any more."""
        state = self._drafted()
        F.topic_for(state, "t1")["line"] = 1
        out = F.render_quote(state, "t1", refine=True)
        self.assertIn("changed since this was last shown", out)
        self.assertIn("Code currently in MR", out)

    def test_refine_falls_back_when_the_author_added_a_note(self):
        """A `sync` mid-topic can bring an author reply. Leaving the thread out then would
        ask about a follow-up to a conversation that has moved on."""
        state = self._posted_with_follow_up()
        state["threads"]["d1"].update(note_count=3, last_author="Jane",
                                      last_body="Doch nicht, ich baue es ein.")
        out = F.render_quote(state, "t1", refine=True)
        self.assertIn("changed since this was last shown", out)
        self.assertIn("Doch nicht, ich baue es ein.", out)

    def test_rewording_the_draft_does_not_count_as_a_changed_context(self):
        """The whole point: the draft is not context, so iterating on it keeps the short
        render however many times it takes."""
        state = self._drafted()
        for body in ("Zweite Fassung.", "Dritte Fassung."):
            F.topic_for(state, "t1")["draft"] = body
            out = F.render_quote(state, "t1", refine=True)
            self.assertNotIn("in full", out)
            self.assertNotIn("except Exception:", out)
            self.assertIn(body, out)

    def test_the_comparison_leaves_no_critical_lines_behind(self):
        """The digest re-renders the context and throws it away. Its code lines must not
        reach the manifest — the Stop hook would demand lines that are nowhere in the
        message and block a correct reply."""
        state = self._drafted(shown=False)
        critical_manifest.reset()
        F.context_digest(state, "t1")
        self.assertEqual(critical_manifest.current(), [])

    def test_a_seeded_note_is_not_mistaken_for_a_follow_up(self):
        """`note` is review-branch's seed text for the comment that OPENED the thread —
        long since posted. Only an explicit draft is a pending follow-up."""
        state = self._posted_with_follow_up()
        t = F.topic_for(state, "t1")
        del t["draft"]
        t["note"] = "unbounded retry loop — no MAX_RETRIES"
        self.assertNotIn("follow-up", F.render_quote(state, "t1"))


class TestBodylessQuote(Throwaway, unittest.TestCase):
    """An unposted topic with no stored draft used to render header + code and stop.
    Read as a complete presentation it left the user with no comment and no sign one
    was missing — reported live as title, code fragment, "nothing else". The gap is
    the agent's to fill (analysis, then ask how to handle it), so the reminder is its
    own stderr note: anything in the returned block is pasted to the user verbatim."""

    def _topic(self, draft=None):
        wt = self._throwaway_dir()
        with open(os.path.join(wt, "worker.py"), "w") as f:
            f.write("try:\n    run()\nexcept Exception:\n    pass\n")
        original = F.get_worktree
        F.get_worktree = lambda slug, iid: wt
        self.addCleanup(setattr, F, "get_worktree", original)
        state = new_state(slug="x")
        F.add_topic(state, summary="swallowed exception hides real failures",
                    file="worker.py", line=3, draft=draft)
        return state

    def test_a_bodyless_topic_renders_clean_and_nudges_the_agent_on_stderr(self):
        state = self._topic()
        buf = io.StringIO()
        with redirect_stderr(buf):
            out = F.render_quote(state, "t1")
        self.assertIn("except Exception:", out)          # the context still shows
        self.assertNotIn("has no draft yet", out)        # nothing meta for the user
        note = buf.getvalue()
        self.assertIn("t1 has no draft yet", note)
        self.assertIn("ask how they want to handle it", note)
        self.assertIn('`set t1 --draft "…"`', note)
        self.assertIn("written in de", note)       # no language marker is on screen yet

    def test_a_stored_draft_suppresses_the_note(self):
        buf = io.StringIO()
        with redirect_stderr(buf):
            out = F.render_quote(self._topic(draft="Kürzer geht es nicht."), "t1")
        self.assertIn("Draft of comment to post", out)
        self.assertNotIn("has no draft yet", buf.getvalue())

    def test_a_posted_topic_without_a_follow_up_stays_silent(self):
        """The follow-up branch's silence is correct: a posted topic with no pending
        reply has nothing to say about a draft, and must not inherit the note."""
        state = new_state(threads={"d1": {"body": "b", "author": "A",
                                          "file": "a.py", "line": 3}})
        add_linked_topic(state, "d1", summary="an English title",
                         file="a.py", line=3)
        buf = io.StringIO()
        with redirect_stderr(buf):
            out = F.render_quote(state, "t1")
        self.assertNotIn("has no draft yet", out)
        self.assertNotIn("has no draft yet", buf.getvalue())


class TestSuggestionInThreadNotes(unittest.TestCase):
    """Another reviewer's ```suggestion in a thread `quote` shows. It used to be printed
    raw inside the note's blockquote, so pasting the tool's output verbatim — exactly what
    the gate asks for — tripped the gate's own raw-suggestion-fence rule."""

    SUGGESTION = ("```suggestion:-0+0\nRetries share one budget: a client that hits "
                  "`MAX_RETRIES` stops, whichever call it was in.\n```")

    def _state(self, **thread):
        th = {"author": "Robin", "body": self.SUGGESTION, "file": "docs/retries.md",
              "line": 21, "url": "http://gl/1", "note_count": 1, "resolved": True,
              "resolved_by": "Sam"}
        th.update(thread)
        state = new_state(threads={"d1": th})
        add_linked_topic(state, "d1", summary="rewording of the retry paragraph",
                         file="docs/retries.md", line=21)
        return state

    def test_quote_never_trips_the_raw_suggestion_gate(self):
        out = F.render_quote(self._state(), "t1")
        self.assertNotRegex(out, forbidden_rules()["raw-suggestion-fence"])
        self.assertIn("> ```markdown\n> Retries share one budget", out)

    def test_with_the_file_it_was_made_on_it_is_a_diff(self):
        """Read from the review worktree at the comment's own commit, not the tip."""
        with tempfile.TemporaryDirectory() as wt:
            git = lambda *a: subprocess.run(["git", "-C", wt, *a], check=True,  # noqa: E731
                                            capture_output=True, text=True)
            git("init", "-q")
            os.makedirs(os.path.join(wt, "docs"))
            with open(os.path.join(wt, "docs/retries.md"), "w") as fh:
                fh.write("".join(f"line {i}\n" for i in range(1, 21)) + "Old budget.\n")
            git("add", ".")
            git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "c")
            sha = git("rev-parse", "HEAD").stdout.strip()
            with open(os.path.join(wt, "docs/retries.md"), "w") as fh:
                fh.write("moved on\n")                   # the tip is not what it saw
            state = self._state(head_sha=sha, side="new")
            with patch.object(F, "get_worktree", return_value=wt):
                out = F.render_quote(state, "t1")
        self.assertIn("> ```diff path=docs/retries.md\n> @@ -21,1 +21,1 @@\n> -Old budget.\n> +Retries share one budget", out)

    def test_a_commit_gone_from_the_worktree_is_read_from_gitlab(self):
        """A force-push drops the version the comment was on; GitLab still has it."""
        state = self._state(head_sha="GONE", side="new")
        state["project"] = "g/r"
        with patch.object(F, "get_worktree", return_value=None), \
             patch.object(F, "file_at", return_value="".join(
                 f"line {i}\n" for i in range(1, 21)) + "Old budget.\n") as api:
            out = F.render_quote(state, "t1")
        api.assert_called_with("g/r", "GONE", "docs/retries.md")
        self.assertIn("> ```diff path=docs/retries.md\n> @@ -21,1 +21,1 @@\n> -Old budget.\n> +Retries share one budget", out)

    def test_the_last_note_is_re_fenced_too(self):
        out = F.render_quote(self._state(
            body="Kürzer, bitte.", note_count=2, last_author="Robin",
            last_body=self.SUGGESTION), "t1")
        self.assertNotRegex(out, forbidden_rules()["raw-suggestion-fence"])
        self.assertIn("> ```markdown\n> Retries share one budget", out)

    def test_a_one_line_preview_strips_the_suggestion(self):
        """`candidates` lists a comment in one line, so the code is dropped, not cut."""
        state = self._state(mine=True, body="Kürzer:\n\n" + self.SUGGESTION)
        state["topics"] = []
        out = F.render_candidates(state, None)
        self.assertNotIn("suggestion", out)
        self.assertIn("Kürzer:", out)


class TestCriticalManifest(Throwaway, unittest.TestCase):
    def setUp(self):
        critical_manifest.reset()

    def test_code_snippet_marks_every_body_line(self):
        wt = self._throwaway_dir()
        full = os.path.join(wt, "a.py")
        with open(full, "w") as f:
            f.write("a = 1\nb = 2\nc = 3\n")
        original = F.get_worktree
        F.get_worktree = lambda slug, iid: wt
        self.addCleanup(setattr, F, "get_worktree", original)
        F.code_snippet(new_state(slug="x"), {"file": "a.py", "line": 2})
        marked = " ".join(critical_manifest.current())
        self.assertIn("b = 2", marked)
        self.assertIn("a = 1", marked)

    def test_render_table_marks_rows_not_header(self):
        state = new_state()
        add_linked_topic(state, "d1", summary="fix the thing")
        out = F.render_table(state)
        row = next(ln for ln in out.splitlines() if "fix the thing" in ln)
        self.assertIn(row, critical_manifest.current())
        self.assertNotIn("| State | Topic | Kind | Src | Location | Summary |",
                          critical_manifest.current())


class TestTopicStatus(unittest.TestCase):
    def test_no_threads_yet_is_draft(self):
        state = new_state()
        t = F.add_topic(state)
        self.assertEqual(F.topic_status(state, t), "draft")

    def test_awaiting_you_is_needs_ack(self):
        state = new_state(threads={"d1": {"awaiting": "you", "resolved": False}})
        t = add_linked_topic(state, "d1")
        self.assertEqual(F.topic_status(state, t), "needs_ack")

    def test_acked_overlay_sticks_when_author_not_active(self):
        state = new_state(threads={"d1": {"awaiting": "them", "resolved": False}})
        t = add_linked_topic(state, "d1")
        t["state"] = "acked"
        self.assertEqual(F.topic_status(state, t), "acked")

    def test_fresh_author_reply_after_ack_reopens_to_needs_ack(self):
        """An ack doesn't stick forever — a NEW author note after the ack timestamp
        means the thread needs you again, not that the overlay silently wins."""
        state = new_state(threads={
            "d1": {"awaiting": "you", "resolved": False, "last_at": "2026-01-02T00:00:00Z"},
        })
        t = add_linked_topic(state, "d1")
        t["state"] = "acked"
        t["acked_at"] = "2026-01-01T00:00:00Z"
        self.assertEqual(F.topic_status(state, t), "needs_ack")

    def test_praise_topic_needs_no_ack_loop(self):
        state = new_state(threads={"d1": {"resolved": True}})
        t = add_linked_topic(state, "d1", kind="praise")
        self.assertEqual(F.topic_status(state, t), "acked")
        state["threads"]["d1"]["resolved"] = False
        self.assertEqual(F.topic_status(state, t), "open")


class TestRenderTable(unittest.TestCase):
    def test_mine_scope_hides_a_topic_that_is_only_open(self):
        state = new_state(threads={"d1": {"awaiting": "them", "resolved": False}})
        add_linked_topic(state, "d1", summary="waiting on author")
        out = F.render_table(state, scope="mine")
        self.assertIn("nothing needs you", out)

    def test_mine_scope_shows_a_topic_needing_ack(self):
        state = new_state(threads={"d1": {"awaiting": "you", "resolved": False}})
        add_linked_topic(state, "d1", summary="needs your ack")
        out = F.render_table(state, scope="mine")
        self.assertIn("needs your ack", out)

    def test_progress_footer_counts_by_status(self):
        state = new_state(threads={"d1": {"awaiting": "you", "resolved": False}})
        add_linked_topic(state, "d1")
        out = F.render_table(state)
        self.assertIn("1 need your ack", out)


class TestLocationlessTopic(unittest.TestCase):
    """A topic about the MR itself — its title, its description, a test nobody's diff
    adds — has no diff line to hang a comment on. Every view has to say that, not
    print the empty string where a `file:line` goes: in the table it came out as a
    visible pair of empty backticks, in `quote` as `None:`."""

    def test_table_names_the_mr_level_topic_instead_of_empty_backticks(self):
        state = new_state()
        F.add_topic(state, summary="the MR title is the generated branch name")
        row = next(x for x in F.render_table(state).splitlines() if "t1" in x)
        self.assertIn("MR-level", row)
        self.assertNotIn("``", row)

    def test_table_still_shows_a_real_location(self):
        state = new_state()
        F.add_topic(state, summary="x", file="src/deep/x.ts", line=42)
        row = next(x for x in F.render_table(state).splitlines() if "t1" in x)
        self.assertIn("`x.ts:42`", row)

    def test_draft_quote_says_where_an_mr_level_comment_goes(self):
        state = new_state()
        F.add_topic(state, summary="the MR title is the generated branch name",
                    draft="Titel/Beschreibung nachziehen?")
        out = F.render_quote(state, "t1")
        self.assertIn("MR-level", out)
        self.assertNotIn("thread on ``", out)

    def test_posted_quote_of_an_mr_level_thread_prints_no_none(self):
        state = new_state(threads={"d1": {"body": "some overview comment",
                                          "author": "Someone", "file": None,
                                          "line": None}})
        add_linked_topic(state, "d1", summary="an English title")
        out = F.render_quote(state, "t1")
        self.assertIn("MR-level", out)
        self.assertNotIn("None:", out)

    def test_bodies_and_candidates_name_it_too(self):
        state = new_state(threads={"d1": {"body": "b", "author": "A",
                                          "awaiting": "you", "mine": True}})
        add_linked_topic(state, "d1", summary="an English title")
        self.assertIn("MR-level", F.render_bodies(state))
        state["topics"] = []                       # now unlinked → a link candidate
        self.assertIn("MR-level", F.render_candidates(state, "me"))


class TestSummaryLanguage(unittest.TestCase):
    """The `drafts <lang>` marker governs the comment BODY only. Read as "this
    review is in <lang>", it produced a table whose ten summaries were all German —
    so a summary in the draft language is flagged where it is rendered, and the paste
    gate refuses the view."""

    def test_a_german_summary_is_flagged_in_the_table(self):
        state = new_state(lang="de")
        F.add_topic(state, summary="`zone_code` ist in keinem der Schemas required",
                    file="a.json", line=3)
        self.assertIn("summaries must be English", F.render_table(state))

    def test_an_english_summary_is_not(self):
        state = new_state(lang="de")
        F.add_topic(state, summary="`zone_code` is not required in any of the schemas",
                    file="a.json", line=3)
        self.assertNotIn("summaries must be English", F.render_table(state))

    def test_english_summary_naming_german_identifiers_is_not_flagged(self):
        """The one that would make the check unusable: an English line about German
        code. Code spans are stripped and two distinct function words are needed."""
        state = new_state(lang="de")
        F.add_topic(state, summary="`ledger-zz.json` drops `menge_und_stueck`",
                    file="a.json", line=3)
        self.assertNotIn("summaries must be English", F.render_table(state))

    def test_an_acronym_that_lowercases_into_the_word_list_is_ignored(self):
        """MIT, DES, AUS and DEM all become German function words in lower case."""
        state = new_state(lang="de")
        F.add_topic(state, summary="MIT-licensed file in an Apache-2.0 repo, and the "
                                   "DES fallback is still reachable", file="a.py")
        self.assertNotIn("summaries must be English", F.render_table(state))

    def test_an_english_summary_quoting_german_text_is_not_flagged(self):
        state = new_state(lang="de")
        F.add_topic(state, summary="the error string 'Bitte Feld ausfuellen' is "
                                   "hardcoded instead of a message key", file="a.py")
        self.assertNotIn("summaries must be English", F.render_table(state))

    def test_a_single_ambiguous_word_is_not_evidence(self):
        state = new_state(lang="de")
        F.add_topic(state, summary="worker processes die before the queue is drained",
                    file="a.py", line=3)
        self.assertNotIn("summaries must be English", F.render_table(state))

    def test_an_english_draft_language_disables_the_check(self):
        """No word list for the language, no check — it must not guess."""
        state = new_state(lang="en")
        F.add_topic(state, summary="ist in keinem der Schemas required", file="a.json")
        self.assertNotIn("summaries must be English", F.render_table(state))

    def test_a_needs_title_topic_is_never_flagged(self):
        """Its "summary" is a raw thread quote, foreign by design — two warnings about
        the same gap would contradict each other. The table now refuses instead of
        rendering anything (TestNeedsTitle), so the one-warning guarantee is the quote
        view's to keep."""
        state = new_state(lang="de",
                          threads={"d1": {"body": "Sollten wir das nicht in der "
                                                  "Fabrik machen?", "file": "a.py",
                                          "line": 3}})
        F.adopt_inbound(state, None, None)
        out = F.render_quote(state, "t1")
        self.assertIn("needs an English summary", out)
        self.assertNotIn("summaries must be English", out)

    def test_the_quote_view_flags_it_too(self):
        state = new_state(lang="de")
        F.add_topic(state, summary="ist in keinem der Schemas required",
                    file="a.json", line=3, draft="Bitte ergänzen.")
        self.assertIn("summaries must be English", F.render_quote(state, "t1"))

    def test_the_header_marker_names_what_the_language_applies_to(self):
        state = new_state(lang="de")
        self.assertEqual(F.lang_hint(state), "_drafts de · rest en_")

    def test_an_english_draft_language_needs_only_half_the_marker(self):
        """A second half reading `rest en` next to `drafts en` is noise: with English
        drafts there is nothing to keep apart, and this marker is unavoidably in the
        user's face on every view."""
        self.assertEqual(F.lang_hint(new_state(lang="en")), "_drafts en_")


class TestNeedsTitle(unittest.TestCase):
    """adopt_inbound has no way to author an English one-liner for a thread it did not
    write — the only text available is the raw comment body, in whatever language the
    commenter used. `needs_title` makes that gap visible instead of letting the raw
    quote silently pass as a finished title (see `short_summary`'s docstring): the
    table refuses to render while the flag stands, and `quote` warns under the heading."""

    def test_adopted_thread_is_flagged_with_no_authored_summary(self):
        state = new_state(threads={"d1": {"body": "Sollten wir hier nicht X machen?",
                                           "file": "a.py", "line": 3}})
        F.adopt_inbound(state, None, None)
        t = F.topic_for(state, "t1")
        self.assertTrue(t["needs_title"])
        self.assertIsNone(t["summary"])

    def test_the_table_refuses_instead_of_rendering_the_marker(self):
        """Reported on a live table: the ✍️ marker row was pasted through on OpenCode,
        wedged mid-cell between the raiser's name and their words, and read as content —
        nothing blocks a paste there. Refusal at the renderer makes the dirty row
        unreachable instead of merely discouraged, on every agent."""
        state = new_state(threads={"d1": {"body": "Sollten wir hier nicht X machen?",
                                           "file": "a.py", "line": 3,
                                           "author": "Vogel, Petra - X9"}})
        F.adopt_inbound(state, None, None)
        with self.assertRaises(SystemExit):
            F.render_table(state)

    def test_the_refusal_names_the_topic_and_the_exact_fix(self):
        """The message is the workflow: e2e's title_peer_topic parses the handle out
        of it, so `needs summary: tN` is a contract, not wording."""
        state = new_state(threads={"d1": {"body": "Sollten wir hier nicht X machen?",
                                           "file": "a.py", "line": 3}})
        F.adopt_inbound(state, None, None)
        buf = io.StringIO()
        with redirect_stderr(buf), self.assertRaises(SystemExit):
            F.render_table(state)
        msg = buf.getvalue()
        self.assertIn("needs summary: t1", msg)
        self.assertIn('`set t1 --summary "..."`', msg)
        self.assertIn("bodies", msg)

    def test_render_table_leaves_an_authored_summary_alone(self):
        state = new_state(threads={"d1": {"body": "irrelevant"}})
        add_linked_topic(state, "d1", summary="a real English title")
        out = F.render_table(state)
        self.assertIn("a real English title", out)

    def test_quote_warns_when_topic_needs_a_title(self):
        state = new_state(threads={"d1": {"body": "Sollten wir hier nicht X machen?",
                                           "file": "a.py", "line": 3}})
        F.adopt_inbound(state, None, None)
        self.assertIn("needs an English summary", F.render_quote(state, "t1"))

    def test_quote_is_silent_for_an_authored_summary(self):
        state = new_state(threads={"d1": {"body": "irrelevant"}})
        add_linked_topic(state, "d1", summary="a real English title")
        self.assertNotIn("needs an English summary", F.render_quote(state, "t1"))

    def test_both_warnings_match_the_stop_hook_rules(self):
        """The gate spec keys off these literals; rewording one silently disables it.
        Mirrors rework-mr's test of the same name with one inversion: review-mr's
        table REFUSES where rework-mr's still renders the marker row, so the table
        pattern can no longer be held to a rendered table. It is pinned both ways
        instead — the render must NOT be able to produce it (the refusal test above),
        and the pattern must still match a hand-built row of the old shape (synthetic
        here, engine-side in hooks/test_paste_gate_slow.py), which is all it can meet
        in a message now. The quote pattern stays anchored to its live render."""
        rules = forbidden_rules()
        adopted = new_state(threads={"d1": {"body": "Sollten wir hier nicht X machen?",
                                             "file": "a.py", "line": 3}})
        F.adopt_inbound(adopted, None, None)
        with self.assertRaises(SystemExit):
            F.render_table(adopted)
        self.assertRegex("| ● open | ✍️ _needs summary:_ raw quote |",
                         rules["unresolved-needs-title-table"])
        self.assertRegex(F.render_quote(adopted, "t1"),
                         rules["unresolved-needs-title-quote"])

    def test_a_closed_topic_needs_no_title(self):
        """Nothing acts on an acked topic any more, and the refusal (and, before it,
        the gate rule) would otherwise block every table on a finished review until
        somebody titled closed work. rework-mr's `needs_summary` draws the same line
        at `done`."""
        state = new_state(threads={"d1": {"body": "Sollten wir hier nicht X machen?",
                                           "file": "a.py", "line": 3, "mine": False,
                                           "awaiting": "you",
                                           "last_at": "2026-01-01T00:00:00Z"}})
        F.adopt_inbound(state, None, None)
        t = F.topic_for(state, "t1")
        with self.assertRaises(SystemExit):     # still open: the table refuses
            F.render_table(state)
        t["state"] = "acked"                       # what `set --state acked` does,
        t["acked_at"] = "2026-01-02T00:00:00Z"     # stamp included
        out = F.render_table(state)                # terminal: the exemption renders
        self.assertIn("**t1**", out)
        self.assertNotIn("needs summary", out)
        self.assertNotIn("needs an English summary", F.render_quote(state, "t1"))

    def test_setting_a_summary_clears_the_flag(self):
        state = new_state(threads={"d1": {"body": "Sollten wir hier nicht X machen?",
                                           "file": "a.py", "line": 3}})
        F.adopt_inbound(state, None, None)
        t = F.topic_for(state, "t1")
        with self.assertRaises(SystemExit):
            F.render_table(state)              # refuses until the summary exists
        t["summary"] = "Clarify the X behavior"
        t["needs_title"] = False              # what `set --summary` does, see cmd == "set"
        out = F.render_table(state)
        self.assertIn("Clarify the X behavior", out)
        self.assertNotIn("needs an English summary", F.render_quote(state, "t1"))


class TestCodeStaysCode(unittest.TestCase):
    """A code block the user is asked to judge must render as one — highlighted, whole."""

    def test_a_draft_is_one_quote_its_code_included(self):
        """A fence inside a quote still renders highlighted, and the draft reads as one."""
        out = F.render_draft("So:\n\n```python\ny = 2\n```\n\nOk?", "a.py")
        self.assertEqual(out, "> So:\n>\n> ```python\n> y = 2\n> ```\n>\n> Ok?")

    def test_a_suggestion_indented_under_a_list_item_is_re_fenced(self):
        out = F.render_draft("- so\n  ```suggestion:-0+0\n  x = 1\n  ```", "a.py", 20)
        self.assertIn("> ```python\n> 20 | x = 1\n> ```", out)
        self.assertNotRegex(out, forbidden_rules()["raw-suggestion-fence"])

    def test_an_untagged_block_is_shown_as_rework_mr_shows_it(self):
        """Both skills pick the language through lib/fences.py's `block_lang`."""
        out = F.render_draft("So:\n\n```\n-a = 1\n+a = 2\n```", "a.py")
        self.assertIn("> ```diff\n", out)

    def test_a_suggestions_lines_are_numbered_from_its_range(self):
        out = F.render_draft("```suggestion:-1+1\na\nb\nc\n```", "a.py", 20)
        self.assertIn("> 19 | a\n> 20 | b\n> 21 | c", out)

    def test_the_diff_of_a_markdown_file_stays_in_one_block(self):
        """Its own ``` closed a bare ```diff early and spilled the rest as prose."""
        state = new_state(threads={"d1": {"body": "x", "file": "docs/a.md", "line": 3,
                                          "url": "u"}},
                          last_reviewed_head="OLD1",
                          mr_web_url="https://gitlab.example.com/g/r/-/merge_requests/1")
        F.adopt_inbound(state, None, None)
        diff = " ```suggestion\n-old\n+new\n ```\n"
        with patch.object(F, "mr_head", return_value="NEW1"), \
             patch.object(F, "versions", return_value=[{"id": 2}]), \
             patch.object(F, "_compare", return_value=[
                 {"new_path": "docs/a.md", "old_path": "docs/a.md", "diff": diff}]):
            out = F.render_topic_diff(state, {"enc": "x"}, 1, "t1")
        self.assertIn("````diff\n--- docs/a.md\n ```suggestion\n-old\n+new\n ```\n````", out)


class TestCurrentTopic(unittest.TestCase):
    """The current topic is what the opener shows and what every view declares to the
    paste gate. The rules themselves are lib/test_focus.py's; this pins the wiring."""

    def state(self):
        state = new_state(threads={
            "d1": {"body": "a", "file": "a.py", "line": 1, "awaiting": "you",
                   "resolved": False, "last_at": "2026-01-01T00:00:00Z"},
            "d2": {"body": "b", "file": "b.py", "line": 2, "awaiting": "author",
                   "resolved": False}})
        for i, d in enumerate(("d1", "d2"), 1):
            add_linked_topic(state, d, summary=f"point {i}", file=f"{'ab'[i - 1]}.py",
                             line=i)
        return state

    def payload(self, state, block, topic=None):
        buf = io.StringIO()
        with patch("sys.stdout", buf), patch.dict(os.environ,
                                                  {"AGENT_SKILLS_PASTE_GATE": "1"}):
            F.emit(state, block, topic)
        return json.loads(buf.getvalue().split("<!-- paste-gate:critical\n")[1]
                          .rsplit("\n-->", 1)[0])

    def test_the_opener_shows_the_first_topic_that_needs_you(self):
        self.assertIn("point 1", F.render_present(self.state()).split("---")[-1])

    def test_a_jump_is_what_the_opener_shows_next_time(self):
        """"Let's do t2 first" survives into the next session's `resume`."""
        state = self.state()
        F.CURRENT.jump(state, "t2")
        self.assertIn("point 2", F.render_present(state).split("---")[-1])

    def test_a_view_declares_its_topic_and_the_current_one(self):
        p = self.payload(self.state(), "◈ t2", "t2")
        self.assertEqual((p["topic"], p["focus"]), ("t2", "t1"))

    def test_acking_the_current_topic_moves_it_on(self):
        state = self.state()
        F.current_topic(state)
        F.topic_for(state, "t1").update(state="acked", acked_at=F.now_iso())  # as `set`
        self.assertEqual(self.payload(state, "table")["focus"], None)  # t2 awaits the author


class TestRenamedFileDiff(unittest.TestCase):
    def test_a_pure_rename_says_so(self):
        """It printed the header and URL and then nothing, which read as "no change"."""
        state = new_state(threads={"d1": {"body": "x", "file": "tests/test_old.py",
                                          "line": 3, "url": "u"}},
                          last_reviewed_head="OLD1",
                          mr_web_url="https://gitlab.example.com/g/r/-/merge_requests/1")
        F.adopt_inbound(state, None, None)
        with patch.object(F, "mr_head", return_value="NEW1"), \
             patch.object(F, "versions", return_value=[{"id": 2}]), \
             patch.object(F, "_compare", return_value=[
                 {"old_path": "tests/test_old.py", "new_path": "tests/test_new.py",
                  "renamed_file": True, "diff": ""}]):
            out = F.render_topic_diff(state, {"enc": "x"}, 1, "t1")
        self.assertIn("_renamed `tests/test_old.py` → `tests/test_new.py` — no content "
                      "change_", out)


class TestTopicDiff(unittest.TestCase):
    """`diff <t>` is the agent's input; `diff <t> --show` is what the user asked to see."""

    BIG = "@@ -1,90 +1,90 @@\n" + "".join(f"-old {i}\n+new {i}\n" for i in range(45))

    def render(self, diffs, show=False, viewer=None):
        state = new_state(threads={"d1": {"body": "x", "file": "a.py", "line": 3,
                                          "url": "u"}},
                          last_reviewed_head="OLD1",
                          mr_web_url="https://gitlab.example.com/g/r/-/merge_requests/1")
        F.adopt_inbound(state, None, None)
        with patch.object(F, "mr_head", return_value="NEW1"), \
             patch.object(F, "versions", return_value=[{"id": 2}]), \
             patch.object(F, "_compare", return_value=diffs), \
             patch.object(F.hunk, "show_patch", return_value=viewer), \
             patch.object(F, "state_file", side_effect=self.scratch), \
             patch.object(F, "get_worktree", return_value=None):
            return F.render_topic_diff(state, {"enc": "x", "slug": "s"}, 1, "t1", show)

    def scratch(self, *_parts):
        fd, path = tempfile.mkstemp(suffix=".patch")
        os.close(fd)
        self.addCleanup(os.unlink, path)
        return path

    def file(self, body):
        return [{"old_path": "a.py", "new_path": "a.py", "diff": body}]

    def test_the_agents_input_is_the_whole_diff_however_long(self):
        out = self.render(self.file(self.BIG))
        self.assertIn("+new 44", out)
        self.assertNotIn("too big", out)

    def test_shown_without_a_viewer_it_is_inline_when_small(self):
        out = self.render(self.file("@@ -1 +1 @@\n-a = 1\n+a = 2\n"), show=True)
        self.assertIn("```diff\n--- a.py\n@@ -1 +1 @@\n-a = 1\n+a = 2\n```", out)

    def test_shown_without_a_viewer_a_big_one_is_just_the_url(self):
        out = self.render(self.file(self.BIG), show=True)
        self.assertIn("too big to inline; open the URL", out)
        self.assertNotIn("+new 44", out)

    def test_shown_in_the_viewer_it_names_the_window_and_the_counts(self):
        out = self.render(self.file("@@ -1 +1 @@\n-a = 1\n+a = 2\n"), show=True,
                          viewer=("sid", "9 (◈ !1)"))
        self.assertIn("_Full diff in tmux window 9 (◈ !1):_ \uf4d2 1 file +1 −1", out)
        self.assertIn("```yaml\na.py:  +1 −1\n```", out)
        self.assertNotIn("```diff", out)

    def test_the_patch_counts_what_the_diff_holds(self):
        """The viewer is refused unless it reports these same counts, so they must be the
        diff's own — a rename under its new name, a new and a deleted file."""
        patch_text = F.topic_patch([
            {"old_path": "old.py", "new_path": "new.py", "renamed_file": True,
             "diff": "@@ -1 +1 @@\n-a\n+b\n"},
            {"old_path": "add.py", "new_path": "add.py", "new_file": True,
             "diff": "@@ -0,0 +1,2 @@\n+x\n+y\n"},
            {"old_path": "gone.py", "new_path": "gone.py", "deleted_file": True,
             "diff": "@@ -1 +0,0 @@\n-z\n"}])
        self.assertEqual(F.hunk.diff_stat(patch_text),
                         [("new.py", 1, 1), ("add.py", 2, 0), ("gone.py", 0, 1)])

    def test_only_the_shown_form_is_a_gated_view(self):
        with open(os.path.join(HERE, "paste-gates.json")) as fh:
            gate = next(g for g in json.load(fh)["gates"] if g["key"] == "diff")
        self.assertNotRegex("python3 $SD/findings.py diff t3 --iid 7", gate["cmd"])
        self.assertRegex("python3 $SD/findings.py diff t3 --show --iid 7", gate["cmd"])


class TestSettledThreads(unittest.TestCase):
    """Someone else's thread already resolved when `sync` first sees it is not taken on.
    Each one used to cost a summary and an ack, and the table refused to render until all
    had a summary — so a merge request with an old discussion blocked the opener."""

    @staticmethod
    def peer(resolved):
        return {"author": "Sam", "body": "Warum nicht ein Timeout?", "file": "a.py",
                "line": 3, "resolved": resolved, "awaiting": "author"}

    def test_a_thread_resolved_at_first_sight_is_left_out(self):
        state = new_state()
        F.sync(state, {"d1": self.peer(True), "d2": self.peer(False)}, None, None)
        self.assertEqual([t["thread_ids"] for t in state["topics"]], [["d2"]])
        state["topics"][0].update(summary="timeout on the client", needs_title=False)
        F.render_table(state)               # would refuse over a d1 topic's missing summary

    def test_one_adopted_while_open_stays_when_it_resolves(self):
        state = new_state()
        F.sync(state, {"d1": self.peer(False)}, None, None)
        F.sync(state, {"d1": self.peer(True)}, None, None)
        self.assertEqual([t["thread_ids"] for t in state["topics"]], [["d1"]])

    def test_one_reopened_later_is_adopted_then(self):
        state = new_state()
        F.sync(state, {"d1": self.peer(True)}, None, None)
        F.sync(state, {"d1": self.peer(False)}, None, None)
        self.assertEqual([t["thread_ids"] for t in state["topics"]], [["d1"]])

    def test_your_own_resolved_thread_is_still_offered(self):
        """Yours may be a posted draft; that matching is `candidates`/`link`'s, not this."""
        state = new_state()
        F.sync(state, {"d1": {**self.peer(True), "mine": True}}, None, None)
        self.assertEqual([t["thread_ids"] for t in state["topics"]], [["d1"]])


class TestSync(unittest.TestCase):
    def test_local_fields_survive_a_fetch(self):
        state = new_state(threads={"d1": {"gone": True, "awaiting": "you"}})
        F.sync(state, {"d1": {"awaiting": "them"}}, None, None)
        self.assertEqual(state["threads"]["d1"], {"awaiting": "them", "gone": True})

    def test_a_thread_missing_from_live_is_marked_resolved_and_gone(self):
        state = new_state(threads={"d1": {"awaiting": "you", "resolved": False}})
        F.sync(state, {}, None, None)
        self.assertTrue(state["threads"]["d1"]["resolved"])
        self.assertTrue(state["threads"]["d1"]["gone"])

    def test_a_new_thread_from_live_is_added_verbatim(self):
        state = new_state()
        F.sync(state, {"d1": {"awaiting": "you"}}, None, None)
        self.assertEqual(state["threads"]["d1"], {"awaiting": "you"})

    def test_a_linked_draft_matching_the_posted_comment_is_repaired(self):
        state = new_state(threads={"d1": {"body": "already posted"}})
        t = add_linked_topic(state, "d1", draft="already posted")
        F.sync(state, {"d1": {"body": "already posted"}}, None, None)
        self.assertIsNone(t["draft"])

    def test_a_genuine_follow_up_draft_survives_sync(self):
        state = new_state(threads={"d1": {"body": "original comment"}})
        t = add_linked_topic(state, "d1", draft="new follow-up")
        F.sync(state, {"d1": {"body": "original comment"}}, None, None)
        self.assertEqual(t["draft"], "new follow-up")


class TestSyncBackfillsMissingBaseline(unittest.TestCase):
    """A topic linked before this fix existed has `start_sha=None` and is never
    revisited by `adopt_inbound` (its thread is already in `linked`) — without a
    backfill, it would defer to a live read of `last_reviewed_head` forever, exactly
    the bug the freeze-at-adoption fix closes, just for topics older than the fix."""

    def test_a_linked_topic_with_no_baseline_gets_one_backfilled(self):
        state = new_state(last_reviewed_head="OLD1")
        t = add_linked_topic(state, "d1", file="a.py")
        self.assertIsNone(t["start_sha"])
        F.sync(state, {"d1": {"awaiting": "you"}}, None, None)
        self.assertEqual(t["start_sha"], "OLD1")

    def test_backfill_falls_back_to_mr_head_when_no_baseline_exists_at_all(self):
        state = new_state()
        t = add_linked_topic(state, "d1", file="a.py")
        with patch.object(F, "mr_head", return_value="FRESH1"):
            F.sync(state, {"d1": {"awaiting": "you"}}, {"enc": "x"}, 1)
        self.assertEqual(t["start_sha"], "FRESH1")

    def test_a_topic_with_its_own_baseline_is_left_alone(self):
        state = new_state(last_reviewed_head="OLD1")
        t = add_linked_topic(state, "d1", file="a.py", start_sha="ALREADY_SET")
        F.sync(state, {"d1": {"awaiting": "you"}}, None, None)
        self.assertEqual(t["start_sha"], "ALREADY_SET")


class TestAdoptInboundBaseline(unittest.TestCase):
    """The bug this closes, observed on a real MR: a topic auto-adopted by
    `adopt_inbound` used to get no baseline of its own (`start_sha=None`) and defer to
    a LIVE read of `last_reviewed_head` at render time (`render_topic_diff`). Acking a
    DIFFERENT topic in the same session advances that shared value — so the deferred
    topic's own "changes since you posted" silently lost everything before the advance,
    with no sign anything was wrong. Freezing the baseline at adoption time closes it."""

    def test_adopted_topic_freezes_the_current_baseline(self):
        state = new_state(threads={"d1": {"body": "x", "file": "a.py", "line": 3}},
                           last_reviewed_head="OLD1")
        F.adopt_inbound(state, None, None)
        self.assertEqual(F.topic_for(state, "t1")["start_sha"], "OLD1")

    def test_a_later_baseline_advance_does_not_retroactively_move_it(self):
        """The regression itself: not whether `start_sha` (a plain stored value that
        can't spontaneously change) survives a later advance — it trivially always
        does — but whether `render_topic_diff`, which is what actually reads it, still
        compares from the FROZEN baseline once `last_reviewed_head` has moved on."""
        state = new_state(threads={"d1": {"body": "x", "file": "a.py", "line": 3,
                                          "url": "u"}},
                          last_reviewed_head="OLD1",
                          mr_web_url="https://gitlab.example.com/g/r/-/merge_requests/1")
        F.adopt_inbound(state, None, None)
        state["last_reviewed_head"] = "NEW1"      # another topic's ack + set-head
        with patch.object(F, "mr_head", return_value="NEW1"), \
             patch.object(F, "versions", return_value=[{"id": 2}]), \
             patch.object(F, "_compare", return_value=[
                 {"new_path": "a.py", "old_path": "a.py", "diff": "+fix\n"}]) as cp:
            out = F.render_topic_diff(state, {"enc": "x"}, 1, "t1")
        self.assertEqual(cp.call_args.args[1], "OLD1")   # not the advanced baseline
        self.assertIn("+fix", out)

    def test_no_baseline_yet_falls_back_to_the_current_head(self):
        """First-ever sync, before any `set-head` — `attach_thread` already falls
        back to `mr_head` for this exact case; this is that same fallback for an
        auto-adopted topic."""
        state = new_state(threads={"d1": {"body": "x", "file": "a.py", "line": 3}})
        with patch.object(F, "mr_head", return_value="FRESH1"):
            F.adopt_inbound(state, ctx={"enc": "x"}, iid=1)
        self.assertEqual(F.topic_for(state, "t1")["start_sha"], "FRESH1")

    def test_no_baseline_and_no_ctx_leaves_it_unset(self):
        """`ctx`/`iid` may be `None` — every real call site always has both, via
        `sync`; only a test with no live GitLab context to give passes `None, None`
        explicitly, and gets the pre-fix behavior for that one case."""
        state = new_state(threads={"d1": {"body": "x", "file": "a.py", "line": 3}})
        F.adopt_inbound(state, None, None)
        self.assertIsNone(F.topic_for(state, "t1")["start_sha"])


class TestRenderUpdatesRebaseContentCheck(unittest.TestCase):
    """`_rebase_kind`'s commit-message comparison misses exactly the common case of a
    folded-in fixup: `git commit --amend --no-edit` (or an interactive-rebase `fixup!`)
    keeps the target commit's message unchanged BY DESIGN. These pin the content-based
    check `render_updates` runs instead/first: does any topic's own tracked file
    actually differ between the two heads, regardless of what the messages say —
    reusing the same compare a normal (non-rebase) push already trusts."""

    def _ctx(self):
        return {"enc": "grp%2Frepo", "web": "https://gitlab.example.com/grp/repo"}

    def _versions(self):
        return [{"id": 2, "head_commit_sha": "NEW1", "base_commit_sha": "BASE2"},
                {"id": 1, "head_commit_sha": "OLD1", "base_commit_sha": "BASE1"}]

    def _fake_compare(self, pairs):
        """side_effect for `_compare`: `pairs` maps (frm, to) -> diffs list (or
        `None`, to simulate that one call failing). The check now makes TWO calls
        per rebased push (each version's own base->head patch), so a test has to
        distinguish them — a single blanket `return_value` can't."""
        def _fn(ctx, frm, to):
            return pairs.get((frm, to))
        return _fn

    def test_content_change_on_a_tracked_topic_overrides_unchanged_messages(self):
        """The actual production miss: a rebase whose commit messages are identical
        (a silent `--amend`) but whose AUTHOR'S OWN patch — base to head, for each
        version — did change a tracked topic's file."""
        state = new_state(last_reviewed_head="OLD1")
        add_linked_topic(state, "d1", file="a.py")
        compare = self._fake_compare({
            ("BASE1", "OLD1"): [{"new_path": "a.py", "old_path": "a.py",
                                  "diff": "+old placeholder\n"}],
            ("BASE2", "NEW1"): [{"new_path": "a.py", "old_path": "a.py",
                                  "diff": "+nested placeholder\n"}],
        })
        with patch.object(F, "mr_head", return_value="NEW1"), \
             patch.object(F, "versions", return_value=self._versions()), \
             patch.object(F, "_compare", side_effect=compare), \
             patch.object(F, "_version_commits") as vc:
            out = F.render_updates(state, self._ctx(), 1)
        vc.assert_not_called()          # content already answered it — no need to ask
        self.assertIn("⚠️", out)
        self.assertIn("t1", out)

    def test_pure_rebase_where_upstream_touches_a_tracked_file_does_not_false_alarm(self):
        """Caught in review: an earlier version compared OLD head directly to NEW
        head, which cannot tell "the target branch moved and happened to touch a.py"
        apart from "the author touched a.py" — so a PURE rebase where the author
        changed nothing, but upstream touched a tracked file, must NOT raise the
        file-level ⚠️. It must fall through to the (accurate, here) message
        classification instead."""
        state = new_state(last_reviewed_head="OLD1")
        add_linked_topic(state, "d1", file="a.py")
        compare = self._fake_compare({
            # the author's own patch is identical before/after and never touches a.py
            ("BASE1", "OLD1"): [{"new_path": "b.py", "old_path": "b.py", "diff": "+x\n"}],
            ("BASE2", "NEW1"): [{"new_path": "b.py", "old_path": "b.py", "diff": "+x\n"}],
            # a raw head-to-head compare WOULD show a.py changing (upstream drift) —
            # this pair must never be consulted for the file-level check.
            ("OLD1", "NEW1"): [{"new_path": "a.py", "old_path": "a.py",
                                 "diff": "+upstream edit\n"}],
        })
        with patch.object(F, "mr_head", return_value="NEW1"), \
             patch.object(F, "versions", return_value=self._versions()), \
             patch.object(F, "_compare", side_effect=compare), \
             patch.object(F, "_version_commits",
                          side_effect=lambda ctx, iid, vid: [{"message": "feat: x"}]):
            out = F.render_updates(state, self._ctx(), 1)
        self.assertIn("↻", out)
        self.assertNotIn("⚠️", out)

    def test_no_tracked_file_touched_and_unchanged_messages_reassures_plainly(self):
        state = new_state(last_reviewed_head="OLD1")
        add_linked_topic(state, "d1", file="a.py")
        with patch.object(F, "mr_head", return_value="NEW1"), \
             patch.object(F, "versions", return_value=self._versions()), \
             patch.object(F, "_compare",
                          return_value=[{"new_path": "unrelated.py",
                                         "old_path": "unrelated.py", "diff": "+x\n"}]), \
             patch.object(F, "_version_commits",
                          side_effect=lambda ctx, iid, vid: [{"message": "feat: x"}]):
            out = F.render_updates(state, self._ctx(), 1)
        self.assertIn("↻", out)
        self.assertIn("no tracked topic's file differs", out)

    def test_content_check_failure_does_not_overclaim_a_clean_result(self):
        """If the compare API call itself fails, `touch` comes back empty for a
        completely different reason than "checked and found nothing" — there's
        nothing to be confident about. Must fall back to the ORIGINAL, hedged
        message-only wording, not silently upgrade to the stronger claim."""
        state = new_state(last_reviewed_head="OLD1")
        add_linked_topic(state, "d1", file="a.py")
        with patch.object(F, "mr_head", return_value="NEW1"), \
             patch.object(F, "versions", return_value=self._versions()), \
             patch.object(F, "_compare", return_value=None), \
             patch.object(F, "_version_commits",
                          side_effect=lambda ctx, iid, vid: [{"message": "feat: x"}]):
            out = F.render_updates(state, self._ctx(), 1)
        self.assertIn("↻", out)
        self.assertNotIn("no tracked topic's file differs", out)
        self.assertIn("skim the URL if unsure", out)

    def test_content_check_failure_with_a_message_change_claims_no_scope(self):
        """The other half of the `cmp`-failed branch: a real message change too.
        Must still say "not verified" rather than the stronger, unearned "(none on a
        topic you're tracking yet)" — that claim requires the content check to have
        actually run, which it didn't here."""
        state = new_state(last_reviewed_head="OLD1")
        add_linked_topic(state, "d1", file="a.py")
        commits = {1: [{"message": "feat: x"}],
                   2: [{"message": "feat: x", "title": "feat: x"},
                       {"message": "fix: y", "title": "fix: y"}]}
        with patch.object(F, "mr_head", return_value="NEW1"), \
             patch.object(F, "versions", return_value=self._versions()), \
             patch.object(F, "_compare", return_value=None), \
             patch.object(F, "_version_commits",
                          side_effect=lambda ctx, iid, vid: commits[vid]):
            out = F.render_updates(state, self._ctx(), 1)
        self.assertIn("real change(s) folded in", out)
        self.assertNotIn("tracking yet", out)      # never verified — don't claim it

    def test_no_tracked_file_touched_but_a_real_message_change_still_flags_it(self):
        state = new_state(last_reviewed_head="OLD1")
        add_linked_topic(state, "d1", file="a.py")
        commits = {1: [{"message": "feat: x"}],
                   2: [{"message": "feat: x, plus a fix", "title": "feat: x, plus a fix"}]}
        with patch.object(F, "mr_head", return_value="NEW1"), \
             patch.object(F, "versions", return_value=self._versions()), \
             patch.object(F, "_compare",
                          return_value=[{"new_path": "unrelated.py",
                                         "old_path": "unrelated.py", "diff": "+x\n"}]), \
             patch.object(F, "_version_commits",
                          side_effect=lambda ctx, iid, vid: commits[vid]):
            out = F.render_updates(state, self._ctx(), 1)
        self.assertIn("⚠️", out)
        self.assertIn("tracking yet", out)


class TestPruneWorktrees(Throwaway, unittest.TestCase):
    """Each MR now gets its own worktree (state_root/<slug>--mr<iid>/worktree)
    rather than one shared per repo, so parallel reviews of different MRs stop
    clobbering each other's checkout — but that means cleanup has to sweep them
    one by one instead of relying on there only ever being one."""

    def setUp(self):
        self.orig_root = F.STATE_ROOT
        F.STATE_ROOT = self._throwaway_dir()
        self.addCleanup(setattr, F, "STATE_ROOT", self.orig_root)

    def _ctx(self, slug="acme-repo"):
        return {"slug": slug, "enc": slug, "path": slug, "web": f"https://x/{slug}"}

    def _with_worktree(self, slug, iid):
        wt = self._throwaway_dir()
        F.set_worktree(slug, iid, wt)
        return wt

    def test_removes_the_worktree_but_keeps_the_findings_file_for_a_merged_mr(self):
        slug = "acme-repo"
        wt = self._with_worktree(slug, 42)
        findings_path = F.state_file(F.STATE_ROOT, slug, 42, "findings.json")
        with open(findings_path, "w") as f:
            f.write("{}")
        old = time.time() - F.KEEP_WORKTREE_S - 60
        os.utime(findings_path, (old, old))
        with patch.object(F, "mr_object", return_value={"state": "merged"}), \
             patch("subprocess.run"):
            removed = F.prune_worktrees(self._ctx(slug))
        self.assertEqual(len(removed), 1)
        self.assertIn("!42", removed[0])
        self.assertIn("merged", removed[0])
        self.assertFalse(os.path.isdir(wt))
        self.assertIsNone(F.get_worktree(slug, 42))
        self.assertTrue(os.path.exists(findings_path))

    def test_a_merged_mr_reviewed_lately_keeps_its_worktree(self):
        """Its review may still be open in another session, whose sidebar reads its code
        from the worktree: a review of another MR once removed it from under it."""
        slug = "acme-repo"
        wt = self._with_worktree(slug, 44)
        with open(F.state_file(F.STATE_ROOT, slug, 44, "findings.json"), "w") as f:
            f.write("{}")
        with patch.object(F, "mr_object", return_value={"state": "merged"}), \
             patch("subprocess.run"):
            self.assertEqual(F.prune_worktrees(self._ctx(slug)), [])
        self.assertEqual(F.get_worktree(slug, 44), wt)

    def test_closed_mr_is_also_pruned(self):
        slug = "acme-repo"
        self._with_worktree(slug, 43)
        with patch.object(F, "mr_object", return_value={"state": "closed"}), \
             patch("subprocess.run"):
            removed = F.prune_worktrees(self._ctx(slug))
        self.assertEqual(len(removed), 1)
        self.assertIsNone(F.get_worktree(slug, 43))

    def test_a_still_open_mrs_worktree_is_left_alone(self):
        slug = "acme-repo"
        wt = self._with_worktree(slug, 7)
        with patch.object(F, "mr_object", return_value={"state": "opened"}), \
             patch("subprocess.run"):
            removed = F.prune_worktrees(self._ctx(slug))
        self.assertEqual(removed, [])
        self.assertTrue(os.path.isdir(wt))
        self.assertEqual(F.get_worktree(slug, 7), wt)

    def test_one_mr_erroring_out_of_the_api_does_not_abort_the_rest_of_the_sweep(self):
        """mr_object() dies (sys.exit) on any glab API failure, e.g. an old MR whose
        project access was later revoked. That must not crash the whole sweep and
        block the review this run actually came here to do."""
        slug = "acme-repo"
        self._with_worktree(slug, 10)
        wt_ok = self._with_worktree(slug, 11)

        def fake_mr_object(_ctx, iid):
            if iid == 10:
                raise SystemExit(1)
            return {"state": "merged"}

        with patch.object(F, "mr_object", side_effect=fake_mr_object), \
             patch("subprocess.run"):
            removed = F.prune_worktrees(self._ctx(slug))
        self.assertEqual(len(removed), 1)
        self.assertIn("!11", removed[0])
        self.assertFalse(os.path.isdir(wt_ok))
        # the erroring MR's worktree is untouched, not silently dropped either
        self.assertIsNotNone(F.get_worktree(slug, 10))

    def test_skip_iid_is_never_pruned_even_if_merged(self):
        slug = "acme-repo"
        wt = self._with_worktree(slug, 99)
        with patch.object(F, "mr_object", return_value={"state": "merged"}), \
             patch("subprocess.run"):
            removed = F.prune_worktrees(self._ctx(slug), skip_iid=99)
        self.assertEqual(removed, [])
        self.assertTrue(os.path.isdir(wt))

    def test_another_repos_worktree_is_never_touched(self):
        mine, theirs = "acme-repo", "other-repo"
        self._with_worktree(mine, 1)
        wt_theirs = self._with_worktree(theirs, 1)
        with patch.object(F, "mr_object", return_value={"state": "merged"}), \
             patch("subprocess.run"):
            F.prune_worktrees(self._ctx(mine))
        self.assertTrue(os.path.isdir(wt_theirs))
        self.assertEqual(F.get_worktree(theirs, 1), wt_theirs)


class TestPreparedInputs(Throwaway, unittest.TestCase):
    """A recorded explainer or seed may only be reused while the branch it was made
    from is still the branch being reviewed. Every case here is a way that can stop
    being true between two runs — and each one has to regenerate rather than reuse,
    because reusing silently shows the user an explanation of code nobody pushed."""

    def setUp(self):
        self.orig_root = F.STATE_ROOT
        F.STATE_ROOT = self._throwaway_dir()
        self.addCleanup(setattr, F, "STATE_ROOT", self.orig_root)
        self.slug = "acme-repo"

    def _a_file(self, name="explainer.html"):
        path = os.path.join(self._throwaway_dir(), name)
        with open(path, "w") as f:
            f.write("<h1>explained</h1>")
        return path

    def test_reused_while_the_tip_is_unchanged(self):
        src = self._a_file()
        F.set_prepared(self.slug, 1, "explainer", src, "abc123def456")
        got, why = F.get_prepared(self.slug, 1, "explainer", "abc123def456")
        self.assertEqual(got, src)
        self.assertIsNone(why)

    def test_a_relative_path_is_stored_absolute(self):
        # The skill records from inside the review worktree but reads back from
        # wherever the next shell call happens to land, which is rarely the same place.
        src = self._a_file()
        cwd = os.getcwd()
        self.addCleanup(os.chdir, cwd)
        os.chdir(os.path.dirname(src))
        rec = F.set_prepared(self.slug, 1, "explainer", os.path.basename(src), "abc123")
        self.assertTrue(os.path.isabs(rec["path"]))
        os.chdir(cwd)
        got, _ = F.get_prepared(self.slug, 1, "explainer", "abc123")
        self.assertEqual(got, rec["path"])

    def test_a_moved_tip_makes_it_stale_and_names_both_shas(self):
        F.set_prepared(self.slug, 1, "explainer", self._a_file(), "abc123def456")
        got, why = F.get_prepared(self.slug, 1, "explainer", "999888777666")
        self.assertIsNone(got)
        self.assertIn("abc123def456"[:12], why)
        self.assertIn("999888777666"[:12], why)

    def test_a_deleted_file_is_not_offered(self):
        src = self._a_file()
        F.set_prepared(self.slug, 1, "explainer", src, "abc123")
        os.remove(src)
        got, why = F.get_prepared(self.slug, 1, "explainer", "abc123")
        self.assertIsNone(got)
        self.assertIn("gone from disk", why)

    def test_nothing_recorded_says_nothing_at_all(self):
        """The ordinary case — no MR has a prepared anything until somebody records one
        — and it is silent on purpose. The lookup shares a shell call with the opener
        often enough that a sentence here reads to the Stop hook as a line the agent
        dropped from a block it was told to paste whole, which blocked a correct reply."""
        got, why = F.get_prepared(self.slug, 7, "seed", "abc123")
        self.assertIsNone(got)
        self.assertIsNone(why)

    def test_a_record_that_stopped_applying_always_says_why(self):
        """The opposite case: something IS recorded and is about to be regenerated at
        real cost, so the reason is never swallowed."""
        src = self._a_file()
        F.set_prepared(self.slug, 7, "seed", src, "abc123")
        os.remove(src)
        got, why = F.get_prepared(self.slug, 7, "seed", "abc123")
        self.assertIsNone(got)
        self.assertIn("!7", why)

    def test_explainer_and_seed_are_recorded_separately(self):
        ex, sd = self._a_file(), self._a_file("seed.json")
        F.set_prepared(self.slug, 1, "explainer", ex, "abc123")
        F.set_prepared(self.slug, 1, "seed", sd, "abc123")
        self.assertEqual(F.get_prepared(self.slug, 1, "explainer", "abc123")[0], ex)
        self.assertEqual(F.get_prepared(self.slug, 1, "seed", "abc123")[0], sd)

    def test_one_mrs_record_is_not_another_mrs(self):
        F.set_prepared(self.slug, 1, "explainer", self._a_file(), "abc123")
        self.assertIsNone(F.get_prepared(self.slug, 2, "explainer", "abc123")[0])


class TestWrongCheckoutWarning(Throwaway, unittest.TestCase):
    """The review worktree is shared between a repo's MRs and left detached on whichever
    was reviewed last, so a missed checkout reviews the previous MR's code with nothing
    failing. Observed: a session read the resulting file-not-found as a stale SEED and
    offered to discard it."""

    def setUp(self):
        self.orig_root = F.STATE_ROOT
        F.STATE_ROOT = self._throwaway_dir()
        self.addCleanup(setattr, F, "STATE_ROOT", self.orig_root)
        self.slug = "acme-repo"
        self.wt = self._throwaway_dir()
        F.set_worktree(self.slug, 1, self.wt)

    def _touch(self, rel):
        p = os.path.join(self.wt, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w").close()

    def test_warns_when_not_one_named_file_is_in_the_checkout(self):
        topics = [{"file": "mock-server.ts"}, {"file": "src/testing/mocks/db.ts"}]
        w = F.wrong_checkout_warning(self.slug, 1, topics)
        self.assertIn("another MR's", w)
        self.assertIn(self.wt, w)

    def test_silent_when_even_one_file_is_there(self):
        # Normal: an MR can delete a file a finding names, so a partial miss proves
        # nothing about the checkout.
        self._touch("mock-server.ts")
        topics = [{"file": "mock-server.ts"}, {"file": "src/testing/mocks/db.ts"}]
        self.assertIsNone(F.wrong_checkout_warning(self.slug, 1, topics))

    def test_silent_for_mr_level_topics_with_no_file(self):
        self.assertIsNone(F.wrong_checkout_warning(self.slug, 1, [{"summary": "x"}]))

    def test_silent_when_no_worktree_is_recorded(self):
        self.assertIsNone(
            F.wrong_checkout_warning(self.slug, 99, [{"file": "mock-server.ts"}]))


class TestExplainerLine(Throwaway, unittest.TestCase):
    """The link the opener carries. It is what the user clicks, so it is marked critical
    — and it must cost nothing on the MRs (nearly all of them) that have no explainer."""

    def setUp(self):
        self.orig_root = F.STATE_ROOT
        F.STATE_ROOT = self._throwaway_dir()
        self.addCleanup(setattr, F, "STATE_ROOT", self.orig_root)
        critical_manifest.reset()
        self.addCleanup(critical_manifest.reset)
        self.ctx = {"slug": "acme-repo"}

    def _record(self, sha="abc123def456"):
        path = os.path.join(self._throwaway_dir(), "explainer.html")
        with open(path, "w") as f:
            f.write("<h1>x</h1>")
        F.set_prepared(self.ctx["slug"], 1, "explainer", path, sha)
        return path

    def _line(self, opened=False):
        return F.explainer_line(F.explainer_path(self.ctx, 1), opened)

    def test_renders_a_clickable_file_url_and_marks_it_critical(self):
        path = self._record()
        with patch.object(F, "mr_head", return_value="abc123def456"):
            line = self._line()
        self.assertIn(f"file://{path}", line)
        self.assertIn(line, critical_manifest.current())

    def test_drawn_it_is_a_named_link_that_says_it_opened(self):
        # A card draws markdown, so the link reads as the file's name rather than a
        # path, and the person learns the browser already has it.
        path = self._record()
        with patch.object(F, "mr_head", return_value="abc123def456"), \
                patch.dict(os.environ, {"REVIEW_UI_DRAWN": "1"}):
            line = self._line(opened=True)
            again = self._line()
        self.assertIn(f"[explainer.html](file://{path})", line)
        self.assertIn("opened in your browser", line)
        self.assertNotIn("opened", again)

    def test_no_record_means_no_line_and_no_call_to_gitlab(self):
        # present/resume run on every check; an MR with no explainer must not pay a
        # round trip to discover that.
        with patch.object(F, "mr_head", side_effect=AssertionError("asked GitLab")) as h:
            self.assertIsNone(self._line())
            h.assert_not_called()
        self.assertEqual(critical_manifest.current(), [])

    def test_a_stale_record_yields_no_line(self):
        self._record(sha="000000000000")
        with patch.object(F, "mr_head", return_value="abc123def456"):
            self.assertIsNone(self._line())
        self.assertEqual(critical_manifest.current(), [])


class TestResumeWithNothingToShow(unittest.TestCase):
    def test_the_review_is_still_recorded_for_the_mod(self):
        # Nothing current and no explainer: no view to print, but the mod's sidebar, where
        # the inbox is, still needs to know whose review this is.
        state = {"iid": 2, "topics": [], "threads": {}}
        with patch.dict(os.environ, {"REVIEW_UI_DRAWN": "1"}), \
                patch.object(F, "resolve_state", return_value=(CTX, 2, "/s", state)), \
                patch.object(F, "current_user"), patch.object(F, "sync"), \
                patch.object(F, "fetch_threads"), patch.object(F, "save"), \
                patch.object(F, "explainer_path"), patch.object(F, "explainer_line", return_value=""), \
                patch.object(F, "render_present", return_value=""), \
                patch.object(F, "view_context", return_value={"review": {"iid": 2}}), \
                patch.object(F.ui, "record") as record, \
                patch.object(sys, "argv", ["findings.py", "resume", "--iid", "2"]), \
                redirect_stdout(io.StringIO()):
            F.main()
        self.assertEqual(record.call_args.args[0]["review"], {"iid": 2})


CTX = {"enc": "g%2Fp", "web": "http://gl/g/p"}


class TestGitLabCannotSayWhoYouAre(unittest.TestCase):
    """Mirrors test_threads.py's: one failed "who am I" must not make your comments the
    author's news."""

    def refresh(self, state, me):
        with patch.object(F, "resolve_state", return_value=(CTX, 2, "/s", state)), \
                patch.object(F, "current_user", return_value=me), patch.object(F, "sync"), \
                patch.object(F, "fetch_threads", return_value={}) as fetch, patch.object(F, "save"), \
                patch.object(F, "mr_base", return_value=None), \
                patch.object(sys, "argv", ["findings.py", "refresh", "--iid", "2"]), \
                redirect_stdout(io.StringIO()) as out:
            F.main()
        return fetch, out.getvalue()

    def test_the_last_known_answer_tells_your_comments(self):
        fetch, _ = self.refresh({"iid": 2, "topics": [], "threads": {}, "me": "kim"}, None)
        self.assertEqual(fetch.call_args.args[2], "kim")

    def test_never_known_leaves_the_threads_as_they_were(self):
        fetch, out = self.refresh({"iid": 2, "topics": [], "threads": {}}, None)
        fetch.assert_not_called()
        self.assertTrue(json.loads(out))

DIFF = """@@ -10,4 +10,5 @@ app.use(
 app.use(
   cors({
-    origin: 'http://localhost',
+    origin: '*',
+    credentials: true,
   }),
"""


class TestReviewBegun(unittest.TestCase):
    """Fresh or resumed: by whether the first pass got as far as the findings."""

    def test_a_first_pass_stopped_before_its_findings_starts_over(self):
        state = new_state()
        state["explainer_choice"] = {"recommend": "skip", "why": "x", "chosen": "generate"}
        state["last_reviewed_head"] = "abc"           # written before the findings
        self.assertFalse(F.review_begun(state))
        self.assertFalse(F.review_begun(None))

    def test_findings_imported_make_it_a_review_to_resume_even_none(self):
        state = new_state()
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            f.write("[]")
        self.addCleanup(os.remove, f.name)
        with patch.object(F, "resolve_state", return_value=({"slug": "x"}, 1, "/s", state)), \
                patch.object(F, "save"), patch.object(F, "wrong_checkout_warning", return_value=None), \
                patch.object(F, "current_user", return_value="ana"), \
                patch.object(sys, "argv", ["findings.py", "import", f.name, "--iid", "1"]), \
                redirect_stdout(io.StringIO()):
            F.main()
        self.assertTrue(F.review_begun(state))

    def test_findings_on_stdin_are_imported_and_kept_as_the_seed(self):
        """Written to a file with the Write tool, the findings were on screen before the
        user's own review; on stdin they never are, and a re-run still finds its seed."""
        state = new_state()
        seed = '[{"kind": "issue", "summary": "s", "why": "w", "file": "a.py", "line": 1}]'
        with tempfile.TemporaryDirectory() as d, \
                patch.object(F, "resolve_state", return_value=({"slug": "x"}, 1, "/s", state)), \
                patch.object(F, "save"), patch.object(F, "wrong_checkout_warning", return_value=None), \
                patch.object(F, "current_user", return_value="ana"), patch.object(F, "mr_head", return_value="abc"), \
                patch.object(F, "STATE_ROOT", d), patch.object(F, "set_prepared") as prepared, \
                patch.object(sys, "argv", ["findings.py", "import", "-", "--seed", "--iid", "1"]), \
                patch.object(sys, "stdin", io.StringIO(seed)), redirect_stdout(io.StringIO()):
            F.main()
            self.assertEqual([t["summary"] for t in state["topics"]], ["s"])
            slug, iid, what, path, head = prepared.call_args.args
            self.assertEqual((slug, iid, what, head), ("x", 1, "seed", "abc"))
            with open(path) as fh:
                self.assertEqual(fh.read(), seed)

    def test_importing_closes_an_explainer_question_answered_in_words(self):
        """Typed, not pressed, the answer was never recorded, and the question's buttons
        stayed on every card after it: the review moving on closes it."""
        state = new_state()
        state["explainer_choice"] = {"recommend": "skip", "why": "x", "chosen": None}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            f.write("[]")
        self.addCleanup(os.remove, f.name)
        with patch.object(F, "resolve_state", return_value=({"slug": "x"}, 1, "/s", state)), \
                patch.object(F, "save"), patch.object(F, "wrong_checkout_warning", return_value=None), \
                patch.object(F, "current_user", return_value="ana"), \
                patch.object(sys, "argv", ["findings.py", "import", f.name, "--iid", "1"]), \
                redirect_stdout(io.StringIO()):
            F.main()
        self.assertIsNotNone(state["explainer_choice"]["chosen"])
        self.assertIsNone(F.state_json(state)["explainer"])     # no question left to ask

    def test_topics_threads_or_an_own_review_from_before_the_mark_count_too(self):
        state = new_state()
        F.add_topic(state, summary="a")
        self.assertTrue(F.review_begun(state))
        self.assertTrue(F.review_begun({**new_state(), "threads": {"d1": {}}}))
        self.assertTrue(F.review_begun({**new_state(), "own_review": "pending"}))


class TestStateForTheMod(unittest.TestCase):
    def test_each_topic_with_its_status_and_draft_version(self):
        state = new_state()
        F.add_topic(state, summary="a", file="a.ts", line=3, draft="Text")
        F.add_topic(state, summary="b")
        got = F.state_json(state)
        self.assertEqual(got["current"], "t1")
        self.assertEqual([t["id"] for t in got["topics"]], ["t1", "t2"])
        self.assertEqual(got["topics"][0]["draft"], F.draft_version("Text"))
        self.assertIsNone(got["topics"][1]["draft"])
        self.assertEqual(got["topics"][0]["status"], "draft")
        self.assertIsNone(got["topics"][0]["thread_url"])        # not posted: no thread yet

    def test_a_posted_topic_names_its_thread_on_gitlab(self):
        state = new_state()
        F.add_topic(state, summary="a", file="a.ts", line=3)
        state["topics"][0]["thread_ids"] = ["d1"]
        state["threads"] = {"d1": {"url": "https://gl/x#note_1", "notes": []}}
        self.assertEqual(F.state_json(state)["topics"][0]["thread_url"], "https://gl/x#note_1")

    def test_the_author_s_push_on_a_topic_is_kept_in_git_and_offered_until_the_merge(self):
        with tempfile.TemporaryDirectory() as wt:
            git = lambda *a: subprocess.run(["git", "-C", wt, *a], check=True,  # noqa: E731
                                            capture_output=True, text=True).stdout.strip()
            git("init", "-q")
            for k, v in (("user.email", "t@example.invalid"), ("user.name", "t"), ("commit.gpgsign", "false")):
                git("config", k, v)
            with open(os.path.join(wt, "a.ts"), "w") as fh:
                fh.write("one\n")
            git("add", ".")
            git("commit", "-qm", "c")
            before = git("rev-parse", "HEAD")
            with open(os.path.join(wt, "a.ts"), "w") as fh:
                fh.write("two\n")
            git("commit", "-qam", "the author's fix")
            after = git("rev-parse", "HEAD")
            state = new_state()
            state["iid"] = 7
            F.add_topic(state, summary="a", file="a.ts", line=1)
            F.add_topic(state, summary="b", file="a.ts", line=1)
            state["topics"][0]["pushed"] = {"from": before, "to": after, "at": "x"}
            # A push git has not fetched yet keeps nothing: the next fetch tries again.
            state["topics"][1]["pushed"] = {"from": before, "to": "0" * 40, "at": "x"}
            with patch.object(F, "get_worktree", return_value=wt):
                F.keep_author_fixes(state, wt, 7)
                self.assertEqual([t["fix_kept"] for t in F.state_json(state)["topics"]], [True, False])
                self.assertEqual(F.kept_fix(state, state["topics"][0]), (before, after))
                state["mr_status"] = {"state": "closed"}
                F.keep_author_fixes(state, wt, 7)
                self.assertEqual([t["fix_kept"] for t in F.state_json(state)["topics"]], [False, False])

    def test_the_sidebar_lists_the_author_s_fix_and_shows_a_file_picked_from_it(self):
        state = new_state()
        state["iid"] = 7
        F.add_topic(state, summary="a", file="a.ts", line=1)
        state["topics"][0]["thread_ids"] = ["d1"]
        state["threads"] = {"d1": {"file": "a.ts", "line": 1, "side": "new", "head_sha": "B"}}
        files = [("a.ts", 1, 1), ("b.ts", 3, 0)]
        shown = []

        def view(repo, path, line, *a, **k):
            shown.append((path, line, k.get("commit")))
            return {"file": path, "line": line, "side": "new", "rows": [["+", None, 1, "x"]], "at": [1, 1]}

        def code(*extra):
            with patch.object(F, "resolve_state", return_value=(CTX, 7, "/s", state)), \
                    patch.object(F, "get_worktree", return_value="/wt"), \
                    patch.object(F, "kept_fix", return_value=("B", "A")), \
                    patch.object(F.fix_refs, "stat", return_value=files), \
                    patch.object(F.code_view, "view", side_effect=view), \
                    patch.object(F.code_view, "now_at", side_effect=lambda r, s, p, c=None: p), \
                    patch.object(sys, "argv", ["findings.py", "code", "t1", "--iid", "7", *extra]), \
                    redirect_stdout(io.StringIO()) as out:
                F.main()
            return json.loads(out.getvalue())
        got = code()
        self.assertEqual((got["fix_files"], got["commented"], got["mode"]), ([list(f) for f in files], "a.ts", "mr"))
        got = code("--file", "b.ts")
        self.assertEqual((got["file"], got["mode"], shown[-1]), ("b.ts", "pushed", ("b.ts", None, "A")))

    def test_a_view_names_its_review_and_the_draft_it_shows(self):
        state = new_state()
        F.add_topic(state, summary="a", draft="Text")
        extra = F.view_context(state, "t1")
        self.assertEqual(extra["review"]["iid"], 1)
        self.assertTrue(extra["review"]["script"].endswith("findings.py"))
        self.assertEqual(extra["draft"], F.draft_version("Text"))
        self.assertNotIn("draft", F.view_context(state))


class TestDiffPosition(unittest.TestCase):
    def position(self, line):
        with patch.object(F, "mr_object", return_value={"diff_refs": {
                "base_sha": "b", "start_sha": "s", "head_sha": "h"}}), \
             patch.object(F, "api", return_value=[{"new_path": "srv.ts", "old_path": "srv.ts",
                                                  "diff": DIFF}]):
            return F.diff_position(CTX, 1, "srv.ts", line)

    def test_an_added_line_takes_the_new_line_alone(self):
        pos = self.position(12)
        self.assertEqual(pos["new_line"], 12)
        self.assertNotIn("old_line", pos)
        self.assertEqual((pos["base_sha"], pos["head_sha"]), ("b", "h"))

    def test_an_unchanged_line_takes_both(self):
        pos = self.position(11)
        self.assertEqual((pos["old_line"], pos["new_line"]), (11, 11))
        pos = self.position(14)          # after one removal and two additions
        self.assertEqual((pos["old_line"], pos["new_line"]), (13, 14))

    def test_an_unchanged_line_outside_every_hunk_takes_both_shifted_by_the_hunks_above(self):
        pos = self.position(40)
        self.assertEqual(pos["new_line"], 40)
        self.assertEqual(pos["old_line"], 40 - 1)      # the hunk above added one line net

    def test_a_file_the_mr_does_not_change_has_none(self):
        with patch.object(F, "mr_object", return_value={"diff_refs": {}}), \
             patch.object(F, "api", return_value=[{"new_path": "other.ts", "diff": DIFF}]):
            self.assertIsNone(F.diff_position(CTX, 1, "srv.ts", 3))


class TestPost(unittest.TestCase):
    def setUp(self):
        self.state = new_state(threads={})
        F.add_topic(self.state, summary="a", file="srv.ts", line=12, draft="Bitte fixen.")
        patcher = patch.dict(os.environ, {F.PRESS_ENV: "1"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def post(self, expect=None, tid="t1"):
        with patch.object(F, "diff_position", return_value={"new_line": 12}), \
             patch.object(F, "mr_head", return_value="h"), \
             patch.object(F, "api_post", return_value={"id": "d1", "notes": [{"id": 7}]}) as p:
            url = F.post_draft(self.state, CTX, 1, tid, expect)
        return url, p

    def test_a_new_thread_is_posted_at_the_line_and_linked(self):
        url, p = self.post(F.draft_version("Bitte fixen."))
        endpoint, body = p.call_args[0]
        self.assertTrue(endpoint.endswith("/merge_requests/1/discussions"))
        self.assertEqual(body["body"], "Bitte fixen.")
        self.assertEqual(body["position"], {"new_line": 12})
        self.assertEqual(self.state["topics"][0]["thread_ids"], ["d1"])
        self.assertTrue(url.endswith("#note_7"))

    def test_a_posted_topic_gets_a_reply_in_its_thread(self):
        t = self.state["topics"][0]
        t["thread_ids"] = ["d9"]
        self.state["threads"]["d9"] = {"awaiting": "you"}
        _, p = self.post()
        self.assertTrue(p.call_args[0][0].endswith("/discussions/d9/notes"))
        self.assertIsNone(t["draft"])

    def test_a_changed_draft_is_refused(self):
        with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()) as err:
            self.post("000000000000")
        self.assertIn("changed", err.getvalue())

    def test_a_topic_handle_in_the_body_is_refused(self):
        self.state["topics"][0]["draft"] = "Siehe t3."
        with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
            self.post()

    def test_only_a_press_posts(self):
        with patch.dict(os.environ, {F.PRESS_ENV: ""}), self.assertRaises(SystemExit), \
             redirect_stderr(io.StringIO()) as err:
            self.post()
        self.assertIn("press", err.getvalue())


class TestWhereTheModDraws(unittest.TestCase):
    """lib/ui.py: in a session the review-ui mod draws, the views change for the person;
    everywhere else they stay byte for byte as they were."""

    def setUp(self):
        self.state = new_state(title="Add a widget cache")
        F.add_topic(self.state, summary="Cache key ignores locale", file="a.ts", line=3,
                    severity="high")
        F.add_topic(self.state, summary="Eviction never runs", draft="Bitte.", severity="low")
        F.add_topic(self.state, summary="Typo")["state"] = "dropped"

    def drawn(self, value):
        return patch.dict(os.environ, {"REVIEW_UI_DRAWN": value})

    def test_the_table_takes_the_shared_words_and_icons(self):
        with self.drawn("1"):
            table = F.render_table(self.state)
        self.assertIn(f"{F.ui.STATE_ICON['new']} new", table)
        self.assertIn(f"{F.ui.STATE_ICON['draft']} draft", table)
        self.assertIn(F.ui.SEVERITY_ICON["high"], table)
        self.assertNotIn("◈", table)
        self.assertNotIn("🟠", table)

    def test_without_the_mod_the_table_is_as_it_was(self):
        with self.drawn("0"):
            table = F.render_table(self.state)
        self.assertIn("✎ draft | ◈ **t1**", table)
        self.assertIn("🟠", table)

    def test_the_opener_is_the_current_topic_alone(self):
        with self.drawn("1"), patch.object(F, "_code_context", return_value=[]):
            opener = F.render_present(self.state)
        self.assertNotIn("| State |", opener)
        self.assertIn(f"**t1** {F.ui.SEVERITY_ICON['high']} **Cache key ignores locale**", opener)

    def test_a_card_carries_the_analysis_and_the_whole_title(self):
        # A press brings a card up with no model turn, so what the person decides on is
        # stored with the finding; and a card wraps a long title instead of cutting it.
        long = "Cache key ignores locale, so two shoppers in different markets share " \
               "one cached widget and each sees the other's prices"
        t = self.state["topics"][0]
        t.update(summary=long, why="Two markets share one entry. Raise it.")
        with self.drawn("1"), patch.object(F, "_code_context", return_value=[]):
            card = F.render_quote(self.state, "t1")
        self.assertIn(long, card)
        self.assertIn("Analysis**\n\nTwo markets share one entry. Raise it.", card)
        self.assertNotIn("not yet on GitLab", card)

    def test_a_follow_up_suggestion_counts_from_the_thread_it_replies_into(self):
        # The user's own comment on line 17 became the topic's thread; the topic's own
        # seeded line was 15. The reply's suggestion sits under line 17.
        state = new_state(threads={"d1": {"file": "a.ts", "line": 17, "body": "odd"}})
        add_linked_topic(state, "d1", summary="x", file="a.ts", line=15,
                         draft="Try:\n\n```suggestion:-0+0\norigin: true,\n```")
        with self.drawn("1"), patch.object(F, "_thread_context", return_value=[]):
            card = F.render_quote(state, "t1")
        self.assertIn("17 | origin: true,", card)
        self.assertNotIn("15 | origin", card)

    def test_a_dropped_topic_is_kept_counted_and_not_listed(self):
        dropped = self.state["topics"][2]
        self.assertEqual(F.topic_status(self.state, dropped), "dropped")
        with self.drawn("0"):
            table = F.render_table(self.state)
        self.assertNotIn("Typo", table)
        self.assertIn("1/3 acked/closed", table)
        self.assertNotEqual(F.current_topic(self.state), "t3")
        self.assertEqual(F.state_json(self.state)["topics"][2]["state"], "dropped")


class TestOwnReviewFirst(unittest.TestCase):
    """Where the mod draws, a fresh review waits for the user's own review: no topic is
    current, the opener says so, and their own comments are matched before the topics."""

    def setUp(self):
        self.state = new_state(threads={
            "d7": {"mine": True, "file": "a.ts", "line": 4, "body": "Same as the cache point"},
        })
        F.add_topic(self.state, summary="Cache key ignores locale", file="a.ts", line=3)
        F.add_topic(self.state, summary="Eviction never runs", file="b.ts", line=9)
        self.state["own_review"] = "pending"

    def test_no_topic_is_current_and_the_opener_shows_none(self):
        self.assertIsNone(F.current_topic(self.state))
        with patch.dict(os.environ, {"REVIEW_UI_DRAWN": "1"}):
            opener = F.render_present(self.state)
        self.assertIn("Agent review done", opener)
        self.assertNotIn("Cache key", opener)
        self.assertNotRegex(opener, r"\b\d+ topics?\b")

    def test_your_own_comments_wait_for_the_matching_instead_of_being_adopted(self):
        F.adopt_inbound(self.state, None, None)
        self.assertEqual(len(self.state["topics"]), 2)

    def test_a_merge_proposal_holds_the_topics_until_it_is_settled(self):
        self.state["own_review"] = "done"
        self.state["proposals"] = [{"thread": "d7", "into": "t1", "summary": "x"}]
        self.assertIsNone(F.current_topic(self.state))
        got = F.state_json(self.state)["proposals"][0]
        self.assertEqual((got["where"], got["comment"]), ("a.ts:4", "Same as the cache point"))
        self.state["proposals"] = []
        self.assertEqual(F.current_topic(self.state), "t1")

    def test_a_merged_comment_brings_its_topic_back_for_a_follow_up(self):
        t = self.state["topics"][0]
        t["thread_ids"] = ["d7"]
        t["follow_up"] = "offer"
        self.state["own_review"] = "done"
        self.assertEqual(F.topic_status(self.state, t), "needs_ack")
        self.assertEqual(F.current_topic(self.state), "t1")
        self.assertEqual(F.ui_state(self.state, t), "new")
        t.pop("follow_up")
        self.assertEqual(F.topic_status(self.state, t), "open")

    def test_with_no_comments_of_yours_the_first_topic_comes_up_saying_so(self):
        state = new_state()
        F.add_topic(state, summary="Cache key ignores locale", file="a.ts", line=3)
        state["own_review"] = "matching"
        with self.drawn("1"), patch.object(F, "anchor_notes"):
            out = F.close_own_review(state, "done")
        lead, card = out.split("\n\n", 1)
        self.assertEqual(lead, "_No comments of yours on !1 to match: on to the agent's topics._")
        self.assertIn("**t1**", card.split("\n")[0])
        self.assertIn("Cache key ignores locale", card)
        self.assertEqual((state["own_review"], F.current_topic(state)), ("done", "t1"))

    def test_with_your_comments_all_on_topics_it_says_that_instead(self):
        self.state["own_review"] = "matching"
        self.state["topics"][0]["thread_ids"] = ["d7"]
        with self.drawn("1"), patch.object(F, "anchor_notes"):
            out = F.close_own_review(self.state, "done")
        self.assertTrue(out.startswith("_Every comment of yours is on a topic already"))

    def drawn(self, value):
        return patch.dict(os.environ, {"REVIEW_UI_DRAWN": value})

    def test_a_review_seeded_without_the_mod_never_waits(self):
        state = new_state()
        F.add_topic(state, summary="a")
        self.assertEqual(F.current_topic(state), "t1")


class TestPostedByHand(unittest.TestCase):
    """A draft the user posted by their own hand, often reworded, is checked before the
    round moves on: the topic stays current until the check holds."""

    def test_the_topic_stays_current_until_its_posting_is_found_sound(self):
        state = new_state(threads={"d1": {"file": "a.ts", "line": 3, "mine": True, "awaiting": "author",
                                          "body": "Reworded by hand."}})
        with patch.dict(os.environ, {"REVIEW_UI_DRAWN": "1"}):
            for s in ("a", "b"):
                F.add_topic(state, summary=s)
            for t in state["topics"]:
                t["draft"] = f"draft of {t['id']}"
            state["own_review"] = "done"
            F.start_round(state)
            self.assertEqual(F.current_topic(state), "t1")
            t1 = state["topics"][0]
            t1["post_check"] = {"draft": t1["draft"]}
            F.attach_thread(state, t1, "d1", None, 1, start_sha="abc")
            self.assertEqual(F.current_topic(state), "t1")
            self.assertTrue(next(x for x in F.state_json(state)["topics"] if x["id"] == "t1")["post_check"])
            t1.pop("post_check")
            self.assertEqual(F.current_topic(state), "t2")


class TestMergedBeforeTheReviewIsThrough(unittest.TestCase):
    """An MR merged (or closed) mid-review: what still matters stays the user's, the rest
    closes, and the MR's own news reaches the inbox."""

    def state(self):
        threads = {f"d{i}": {"file": "a.ts", "line": i, "mine": True, "awaiting": "author",
                             "body": f"b{i}"} for i in (1, 2)}
        state = new_state(threads=threads, own_review="done")
        add_linked_topic(state, "d1", summary="posted, high", severity="high")
        add_linked_topic(state, "d2", summary="posted, low", severity="low")
        F.add_topic(state, summary="never posted, critical", severity="critical", draft="x")
        F.add_topic(state, summary="never posted, medium", severity="medium", draft="y")
        state["mr_status"] = {"state": "merged", "merged_at": "2026-11-09T15:20:00Z", "merged_by": "Doe, Jan"}
        return state

    def test_what_still_matters_is_the_round_and_the_rest_closes(self):
        state = self.state()
        self.assertEqual(F.merge_left(state), {"left": ["t1", "t3"], "drafts": ["t4"], "quiet": 1})
        with patch.dict(os.environ, {"REVIEW_UI_DRAWN": "1"}):
            F.start_round(state)
            self.assertEqual(F.current_topic(state), "t1")
            topics = {x["id"]: x for x in F.state_json(state)["topics"]}
            self.assertTrue(topics["t1"]["left"])
            self.assertFalse(topics["t2"]["left"])
            state["topics"][0]["state"] = "dropped"
            self.assertEqual(F.current_topic(state), "t3")
            state["topics"][2]["state"] = "dropped"
            self.assertIsNone(F.current_topic(state))
            self.assertEqual(F.state_json(state)["mr"]["merged_by"], "Jan")

    def test_a_closed_topic_opened_from_the_sidebar_lets_go_once_a_press_closes_it(self):
        """Every topic a merge closed reads the same, so the one the user opened held on
        through an ack, with nothing left on it to press and no way to the inbox."""
        state = self.state()
        with patch.dict(os.environ, {"REVIEW_UI_DRAWN": "1"}):
            for t in ("t1", "t3"):
                F.topic_for(state, t)["state"] = "dropped"
            self.assertIsNone(F.current_topic(state))
            F.focus_on(state, "t2")                       # a click on its row
            self.assertEqual(F.current_topic(state), "t2")
            F.topic_for(state, "t2")["state"] = "acked"   # Accept
            self.assertIsNone(F.current_topic(state))

    def test_news_that_still_asks_the_user_is_opened_from_the_box_after_a_merge(self):
        state = self.state()
        rec = [{"id": t["id"], "inbox": "yours" if t["id"] == "t2" else None, "news": ["resolved"],
                "news_at": "2026-11-09T15:00:00Z", "status": "open"} for t in state["topics"]]
        status = {"t2": "needs_ack"}
        with patch.dict(os.environ, {"REVIEW_UI_DRAWN": "1"}), \
                patch.object(F, "topic_status", side_effect=lambda st, t: status.get(t["id"], "acked")):
            self.assertEqual(F.inbox_json(state, rec)["first"], "t2")
            status["t2"] = "acked"
            self.assertIsNone(F.inbox_json(state, rec)["first"])

    def test_the_mr_s_own_news(self):
        at = "2026-11-09T15:00:00Z"
        opened = {"state": "opened", "auto_merge": False, "approved_by": []}
        self.assertEqual(F.mr_news(opened, {**opened, "auto_merge": True}, at),
                         [{"kind": "auto-merge", "at": at, "by": None}])
        approved = {**opened, "approved_by": [{"name": "Doe, Jan", "me": False}, {"name": "x", "me": True}]}
        self.assertEqual(F.mr_news(opened, approved, at)[0]["by"], "Jan, You")
        merged = {**opened, "state": "merged", "merged_at": "2026-11-09T15:20:00Z", "merged_by": "Doe, Jan"}
        # Merged is news even to a review that never checked before; nothing else is.
        self.assertEqual(F.mr_news(None, {**merged, "auto_merge": True}, at),
                         [{"kind": "merged", "at": "2026-11-09T15:20:00Z", "by": "Jan"}])
        self.assertEqual(F.mr_news(merged, merged, at), [])

    def test_the_mr_s_own_news_counts_for_since_when(self):
        state = new_state(mr_news=[{"kind": "approved", "at": "2026-11-09T12:11:00Z", "by": "You"}])
        self.assertEqual(F.inbox_json(state, [])["since"], "2026-11-09T12:11:00Z")

    def test_each_push_links_to_what_it_changed(self):
        state = new_state(pushes=[{"at": "t", "head": "H2", "from": "H0"}, {"at": "t", "head": "H3"}])
        vs = [{"id": 30, "head_commit_sha": "H3"}, {"id": 20, "head_commit_sha": "H2"}, {"id": 10, "head_commit_sha": "H1"}]
        with patch.object(F, "versions", return_value=vs):
            F.link_pushes(state, {"web": "https://gl/g/r"}, 5)
        self.assertEqual([p["url"] for p in state["pushes"]], [
            "https://gl/g/r/-/merge_requests/5/diffs?diff_id=20&start_sha=H0",
            "https://gl/g/r/-/merge_requests/5/diffs?diff_id=30&start_sha=H2"])


class TestVerdict(unittest.TestCase):
    """The agent's judgment of an author's answer is stored, so every card of an answered
    topic carries it, also one a press brings up; a new answer makes it stale."""

    def setUp(self):
        self.state = new_state(threads={"d1": {"file": "a.ts", "line": 3, "awaiting": "you",
                                               "last_at": "2026-11-04T10:00:00Z", "body": "b"}})
        add_linked_topic(self.state, "d1", summary="Cache key ignores locale")
        self.t = self.state["topics"][0]

    def drawn(self):
        return patch.dict(os.environ, {"REVIEW_UI_DRAWN": "1"})

    def _judge(self, says="more", text="Fixed for reads, not for writes. Ask for writes."):
        self.t["verdict"] = {"says": says, "text": text, "asked": "Asked for locale in the key.",
                             "on": F.answer_mark(self.state, self.t)}

    def test_the_background_agent_never_overwrites_a_verdict_stored_since_it_started(self):
        self.assertTrue(F.store_verdict(self.state, self.t, "more", "Push back.", asked="A."))
        self.assertFalse(F.store_verdict(self.state, self.t, "settled", "Fine.", unless_judged=True))
        self.assertEqual(self.t["verdict"]["says"], "more")
        self.state["threads"]["d1"]["last_at"] = "2026-11-04T11:00:00Z"     # a new answer
        self.assertTrue(F.store_verdict(self.state, self.t, "settled", "Fine.", unless_judged=True))

    def test_a_changed_verdict_shows_the_change_alone(self):
        F.store_verdict(self.state, self.t, "more", "Writes still skip it.", case="partly-fixed")
        before = F.verdict_of(self.state, self.t)
        F.store_verdict(self.state, self.t, "settled", "Holds on a deeper read.")
        card = F.render_verdict_change(self.state, self.t, before)
        self.assertIn("Verdict: Settled", card)
        self.assertIn("_was: Partly fixed_", card)
        self.assertNotIn("What you asked", card)

    def test_the_verdict_heading_names_what_is_wrong(self):
        F.store_verdict(self.state, self.t, "more", "Restore the 200.", case="comment-wrong")
        with self.drawn():
            self.assertIn("Verdict: The comment was wrong", F.render_quote(self.state, "t1"))
            self.state["threads"]["d1"]["mine"] = True
            self.assertIn("Verdict: Your comment was wrong", F.render_quote(self.state, "t1"))
        F.store_verdict(self.state, self.t, "settled", "Fine.", case="comment-wrong")
        self.assertNotIn("case", self.t["verdict"])

    def test_the_recap_stays_with_the_topic_across_verdicts(self):
        with self.drawn():
            self.assertIn("--asked", F.judge_brief(self.state, 1))
        F.store_verdict(self.state, self.t, "more", "x", asked="Asked for locale in the key.")
        F.store_verdict(self.state, self.t, "settled", "y")
        with self.drawn():
            self.assertIn("Asked for locale in the key.", F.render_quote(self.state, "t1"))
        self.state["threads"]["d1"]["last_at"] = "2026-11-04T11:00:00Z"
        with self.drawn():
            self.assertNotIn("--asked", F.judge_brief(self.state, 1))  # given once

    def test_an_unjudged_answer_is_the_background_agent_s_to_judge(self):
        with self.drawn():
            self.assertEqual(F.needs_verdict(self.state), ["t1"])
            brief = F.judge_brief(self.state, 1)
        self.assertIn("- t1 (Cache key ignores locale): the author answered.", brief)
        self.assertIn("`verdict t1 settled|more|wait --unless-judged --asked", brief)
        self._judge()
        with self.drawn():
            self.assertEqual(F.needs_verdict(self.state), [])
            self.assertEqual(F.judge_brief(self.state, 1), "")

    def test_the_judgment_reads_what_was_agreed_halfway_through_the_thread(self):
        """The first comment asks for more than was agreed later; the last says done. A
        verdict judged from those two alone would call the fix short."""
        self.state["threads"]["d1"]["notes"] = [
            {"author": "Ann", "body": "Implement the locale handling in full.", "mine": True},
            {"author": "Bob", "body": "Can a TODO do for now?", "mine": False},
            {"author": "Ann", "body": "Yes, a TODO with the ticket is enough.", "mine": True},
            {"author": "Bob", "body": "Done.", "mine": False}]
        with self.drawn():
            brief = F.judge_brief(self.state, 1)
        self.assertIn("`bodies t1`", brief)
        self.assertIn("a TODO with the ticket is enough", F.render_bodies(self.state, ["t1"]))

    def test_what_an_agent_failed_on_is_left_out_of_the_next_brief(self):
        with self.drawn():
            self.assertEqual(F.judge_brief(self.state, 1, skip=["t1"]), "")
            self.assertIn("- t1 ", F.judge_brief(self.state, 1))

    def test_the_card_carries_the_verdict(self):
        self._judge()
        with self.drawn(), patch.object(F, "_thread_context", return_value=[]):
            card = F.render_quote(self.state, "t1")
        self.assertIn("Verdict: Not settled", card)         # a verdict without its case
        self.assertIn("Fixed for reads, not for writes.", card)
        self.assertEqual(F.state_json(self.state)["topics"][0]["verdict"], "more")

    def test_an_answered_card_recaps_the_ask_then_what_was_done_then_the_verdict(self):
        self.t["why"] = "The old analysis. **Recommendation:** Raise it."
        self.state["author"] = "Alex Novak"
        self.state["threads"]["d1"].update(body="Missing await here.", mine=True)
        self.t["verdict"] = {"says": "settled", "text": "The write is awaited now.",
                             "asked": "The write was not awaited; asked for an await.",
                             "did": "Pushed the await; no reply.", "on": F.answer_mark(self.state, self.t)}
        with self.drawn():
            card = F.render_quote(self.state, "t1")
        order = [card.index(x) for x in ("What you asked", "asked for an await", "What Alex did",
                                         "Pushed the await; no reply.", "Verdict: Settled",
                                         "The write is awaited now.")]
        self.assertEqual(order, sorted(order))
        self.assertNotIn("Missing await here.", card)   # the thread is the sidebar's
        self.assertNotIn("Raise it", card)            # the finding's own advice is history now

    def test_a_verdict_without_a_recap_goes_straight_to_what_was_done(self):
        self.t["verdict"] = {"says": "settled", "text": "Done.", "did": "Pushed it.",
                             "on": F.answer_mark(self.state, self.t)}
        with self.drawn():
            card = F.render_quote(self.state, "t1")
        self.assertNotIn("asked", card)
        self.assertIn("Pushed it.", card)

    def test_waiting_on_the_author_takes_the_topic_out_until_they_answer_again(self):
        self.t["verdict"] = {"says": "wait", "text": "Agreed; the fix is to come.",
                             "on": F.answer_mark(self.state, self.t)}
        with self.drawn():
            self.assertEqual(F.topic_status(self.state, self.t), "open")
            self.assertIsNone(F.inbox_of(self.state, self.t))
            self.assertEqual(F.needs_verdict(self.state), [])
            self.state["threads"]["d1"]["last_at"] = "2026-11-05T11:00:00Z"
            self.assertEqual(F.topic_status(self.state, self.t), "needs_ack")

    def test_a_new_answer_makes_the_verdict_stale(self):
        self._judge("settled", "Fixed.")
        self.state["threads"]["d1"]["last_at"] = "2026-11-04T11:00:00Z"
        self.assertIsNone(F.verdict_of(self.state, self.t))
        self.assertIsNone(F.state_json(self.state)["topics"][0]["verdict"])


class TestDraftLanguage(unittest.TestCase):
    def test_a_draft_in_another_language_than_the_draft_language_is_named(self):
        state = new_state(lang="en")
        self.assertIn("reads as de", F.draft_language_warning(
            state, "Die Tests sind damit sauber, aber der Client bleibt ein Singleton."))
        self.assertIsNone(F.draft_language_warning(state, "The tests are clean now, thanks."))
        state["lang"] = "de"
        self.assertIsNone(F.draft_language_warning(
            state, "Die Tests sind damit sauber, aber der Client bleibt ein Singleton."))


class TestStaleDrafts(unittest.TestCase):
    """A follow-up draft answers the thread as it stood when it was written: a newer answer,
    or choosing to wait, leaves it nothing to say, and its Post would contradict the verdict."""

    def setUp(self):
        self.state = new_state(threads={"d1": {"file": "a.ts", "line": 3, "awaiting": "you",
                                               "last_at": "2026-11-04T10:00:00Z", "body": "b"}})
        add_linked_topic(self.state, "d1", summary="Cache key ignores locale")
        self.t = self.state["topics"][0]

    def _draft(self, on=True):
        self.t["draft"] = "Please also cover writes."
        if on:
            self.t["draft_on"] = F.answer_mark(self.state, self.t)

    def test_a_draft_on_the_standing_answer_stays(self):
        self._draft()
        F.drop_stale_drafts(self.state)
        self.assertEqual(self.t["draft"], "Please also cover writes.")

    def test_a_newer_answer_drops_the_draft(self):
        self._draft()
        self.state["threads"]["d1"]["last_at"] = "2026-11-04T11:00:00Z"
        F.drop_stale_drafts(self.state)
        self.assertIsNone(self.t["draft"])
        self.assertNotIn("draft_on", self.t)

    def test_an_unmarked_draft_goes_where_the_verdict_leaves_nothing_to_ask(self):
        for says, kept in (("more", True), ("settled", False), ("wait", False)):
            self._draft(on=False)
            self.t["verdict"] = {"says": says, "text": "x", "on": F.answer_mark(self.state, self.t)}
            F.drop_stale_drafts(self.state)
            self.assertEqual(bool(self.t["draft"]), kept, says)


class TestRounds(unittest.TestCase):
    """Where the mod draws, news waits in the inbox until the user opens it; a round then
    runs through everything that needs them, in list order from where they started."""

    def setUp(self):
        env = patch.dict(os.environ, {"REVIEW_UI_DRAWN": "1"})
        env.start()
        self.addCleanup(env.stop)
        threads = {f"d{i}": {"file": "a.ts", "line": i, "side": "new", "awaiting": "author",
                             "last_at": "2026-11-04T10:00:00Z", "body": "b", "head_sha": "c0"}
                   for i in range(1, 5)}
        self.state = new_state(threads=threads, own_review="done")
        for i in range(1, 5):
            add_linked_topic(self.state, f"d{i}", summary=f"topic {i}")

    def answer(self, i, at="2026-11-05T09:00:00Z"):
        self.state["threads"][f"d{i}"].update(awaiting="you", last_at=at)

    def ack(self, tid):
        t = F.topic_for(self.state, tid)
        t.update(state="acked", acked_at="2026-11-05T12:00:00Z")
        F.settle(self.state)

    def test_news_between_rounds_is_marked_and_never_becomes_current(self):
        self.answer(2)
        F.settle(self.state)
        self.assertIsNone(F.current_topic(self.state))
        j = F.state_json(self.state)
        self.assertEqual(j["topics"][1]["inbox"], "new")
        self.assertEqual(j["topics"][1]["news"], ["reply"])
        self.assertEqual(j["inbox"]["replies"], ["t2"])
        self.assertEqual(j["inbox"]["first"], "t2")

    def test_a_round_runs_on_from_the_topic_opened_and_wraps_round(self):
        for i in (1, 2, 4):
            self.answer(i)
        F.focus_on(self.state, "t2")
        F.settle(self.state)
        self.assertEqual(F.state_json(self.state)["topics"][1]["inbox"], "yours")
        self.ack("t2")
        self.assertEqual(F.current_topic(self.state), "t4")
        self.ack("t4")
        self.assertEqual(F.current_topic(self.state), "t1")
        self.ack("t1")
        self.assertIsNone(F.current_topic(self.state))
        self.assertFalse(self.state["in_round"])

    def test_news_after_a_round_ended_waits_for_the_next(self):
        self.answer(1)
        F.start_round(self.state)
        self.ack("t1")
        self.answer(3)
        F.settle(self.state)
        self.assertIsNone(F.current_topic(self.state))

    def test_news_during_a_round_joins_it(self):
        self.answer(1)
        F.start_round(self.state)
        self.answer(3)
        self.ack("t1")
        self.assertEqual(F.current_topic(self.state), "t3")

    def test_the_round_s_order_is_what_comes_next(self):
        for i in (1, 2, 3):
            self.answer(i)
        F.start_round(self.state, "t2")
        # From the current one on in list order, round to the top again.
        self.assertEqual(F.round_order(self.state), ["t2", "t3", "t1"])

    def test_a_view_between_rounds_is_research_and_claims_nothing(self):
        self.answer(1)
        self.assertIsNone(F.CURRENT.view(self.state, "t1", False, "quote t1 --focus"))
        self.assertIsNone(F.current_topic(self.state))


class TestSaveBesideTheState(unittest.TestCase):
    """The records written beside the review's state (a prepared seed, the explainer) have no
    topics, and are saved as they are where the mod draws, too."""

    def test_a_prepared_record_is_saved_where_the_mod_draws(self):
        d = tempfile.mkdtemp(prefix="test-findings-save-")
        self.addCleanup(shutil.rmtree, d)
        with patch.dict(os.environ, {"REVIEW_UI_DRAWN": "1"}), \
                patch.object(F, "prepared_path", return_value=os.path.join(d, "seed.json")):
            F.set_prepared("slug", 1, "seed", "/tmp/seed.json", "abc")
        self.assertEqual(json.load(open(os.path.join(d, "seed.json")))["sha"], "abc")


class TestAnswersInCode(unittest.TestCase):
    """A push that changes the lines a waiting comment is on makes the topic the user's,
    with no reply in the thread; one that only moves them keeps it waiting."""

    def setUp(self):
        env = patch.dict(os.environ, {"REVIEW_UI_DRAWN": "1"})
        env.start()
        self.addCleanup(env.stop)
        self.state = new_state(threads={"d1": {"file": "a.ts", "line": 11, "side": "new",
                                               "awaiting": "author", "head_sha": "c0",
                                               "last_at": "2026-11-04T10:00:00Z"}},
                               own_review="done", seen_head="c0", diff_base="b0")
        self.t = add_linked_topic(self.state, "d1", summary="Cache key")

    def push(self, diff, head="c1"):
        files = [{"new_path": "a.ts", "old_path": "a.ts", "diff": diff}]
        with patch.object(F, "mr_head", return_value=head), \
                patch.object(F, "_compare", return_value=files), \
                patch.object(F, "_gl_compare", return_value={"stat": "`+2/−1` · 1 file"}):
            F.check_pushes(self.state, None, 1, "b0")

    def test_a_push_on_the_comment_s_lines_is_news(self):
        self.push(TestTouchedDiff.REWRITE)
        self.assertEqual(F.topic_status(self.state, self.t), "needs_ack")
        self.assertEqual(F.news_of(self.state, self.t)[0], ["push"])
        self.assertEqual(F.needs_verdict(self.state), ["t1"])
        self.assertEqual(self.state["pushes"][0]["topics"], ["t1"])
        self.assertIn("pushed a change to the lines", F.judge_brief(self.state, 1))

    def test_a_push_is_noted_once_however_often_the_check_runs(self):
        self.push(TestTouchedDiff.REWRITE)
        at = self.t["pushed"]["at"]
        with patch.object(F, "now_iso", return_value="2099-01-01T00:00:00+00:00"):
            self.push(TestTouchedDiff.REWRITE)
        self.assertEqual(self.t["pushed"]["at"], at)

    def test_a_review_the_check_never_saw_compares_from_the_last_review(self):
        del self.state["seen_head"]
        self.state["last_reviewed_head"] = "c0"
        self.push(TestTouchedDiff.REWRITE)
        self.assertEqual(len(self.state["pushes"]), 1)
        self.assertEqual(self.state["pushes"][0]["topics"], ["t1"])

    def test_a_decision_after_seeing_the_code_answers_the_push_up_to_it(self):
        # The round showed c1; the user replied after it; c1's change to the lines is not
        # news again, though the check never noted it as a push of its own.
        self.state["seen_head"] = "c1"
        self.t["code_seen"] = {"sha": "c1", "mark": F.decision_mark(self.state, self.t)}
        self.state["threads"]["d1"]["last_at"] = "2026-11-05T10:00:00Z"     # their reply
        self.push(TestTouchedDiff.REWRITE)
        self.assertEqual(F.topic_status(self.state, self.t), "open")
        self.assertEqual(self.t["watch"]["sha"], "c1")
        self.assertNotIn("pushed", self.t)

    def test_a_push_that_only_moves_the_lines_keeps_the_topic_waiting(self):
        self.push("@@ -2,0 +3,2 @@\n+x\n+y\n")
        self.assertEqual(F.topic_status(self.state, self.t), "open")
        self.assertEqual(self.t["watch"], {"sha": "c1", "first": 13, "last": 13})
        self.assertEqual(self.state["pushes"][0]["topics"], [])

    def test_a_rebase_that_moved_the_lines_without_the_author_is_no_news(self):
        own = [{"new_path": "a.ts", "old_path": "a.ts", "diff": "@@ -1 +1 @@\n-x\n+y\n"}]
        moved = [{"new_path": "a.ts", "old_path": "a.ts", "diff": TestTouchedDiff.REWRITE}]

        def compare(ctx, frm, to):
            return own if (frm, to) in (("b0", "c0"), ("b1", "c1")) else moved
        with patch.object(F, "mr_head", return_value="c1"), patch.object(F, "_compare", compare), \
                patch.object(F, "_gl_compare", return_value=None):
            F.check_pushes(self.state, None, 1, "b1")
        self.assertEqual(F.topic_status(self.state, self.t), "open")
        self.assertEqual(self.state["pushes"][0], {**self.state["pushes"][0], "rebase": True, "topics": []})

    def test_a_push_that_left_the_code_as_it_was_is_said_as_such(self):
        """Squashed or reworded, the code as before: GitLab's diff of it is empty. The inbox
        says so, dim, as it explains the new commits GitLab shows while nothing moved."""
        with patch.object(F, "mr_head", return_value="c1"), patch.object(F, "_compare", return_value=[]), \
                patch.object(F, "_gl_compare", return_value={"paths": [], "stat": None}):
            F.check_pushes(self.state, None, 1, "b0")
        self.assertEqual([(p["same"], p["topics"]) for p in self.state["pushes"]], [(True, [])])
        self.assertEqual(self.state["seen_head"], "c1")

    def test_an_ack_answers_the_push_and_the_next_one_is_checked_from_it(self):
        self.push(TestTouchedDiff.REWRITE)
        self.t.update(state="acked", acked_at="2026-11-05T12:00:00Z")
        self.assertEqual(F.topic_status(self.state, self.t), "acked")
        self.push("", head="c2")
        self.assertNotIn("pushed", self.t)
        self.assertEqual(self.t["watch"]["sha"], "c1")


class TestTouchedDiff:
    REWRITE = "@@ -8,7 +8,8 @@\n a8\n a9\n a10\n-a11\n+b11\n+b12\n a12\n a13\n a14\n"


class TestPeerThreads(unittest.TestCase):
    """Another reviewer's thread waits on you where the mod draws, until you say something."""

    def test_waits_on_you_until_you_speak(self):
        state = new_state(threads={"d1": {"author": "Kim", "awaiting": "author", "body": "b"}})
        add_linked_topic(state, "d1", source="peer", summary="x")
        t = state["topics"][0]
        with patch.dict(os.environ, {"REVIEW_UI_DRAWN": "1"}):
            self.assertEqual(F.topic_status(state, t), "needs_ack")
            self.assertTrue(F.state_json(state)["topics"][0]["peer"])
            state["threads"]["d1"]["you_spoke"] = True
            self.assertEqual(F.topic_status(state, t), "open")
        state["threads"]["d1"]["you_spoke"] = False
        with patch.dict(os.environ, {"REVIEW_UI_DRAWN": "0"}):
            self.assertEqual(F.topic_status(state, t), "open")      # without the mod, as before


class TestWatchedThreads(unittest.TestCase):
    """Another reviewer's thread is theirs to settle: the user watches it, and it joins their
    round only where the agent judged it needs them."""

    def setUp(self):
        env = patch.dict(os.environ, {"REVIEW_UI_DRAWN": "1"})
        env.start()
        self.addCleanup(env.stop)
        self.state = new_state(threads={"d1": {"author": "Kim", "awaiting": "author", "body": "b",
                                               "last_at": "2026-11-05T09:00:00Z"}},
                               own_review="done")
        self.t = add_linked_topic(self.state, "d1", source="peer", summary="x")

    def view(self, says, **kw):
        self.t["peer_view"] = {"says": says, "advice": kw.get("advice", "seen"),
                               "into": kw.get("into"), "on": F.answer_mark(self.state, self.t)}

    def test_unjudged_it_is_news_for_the_background_agent(self):
        self.assertEqual(F.needs_verdict(self.state), ["t1"])
        self.assertIn("`peer t1 fyi|needs --unless-judged", F.judge_brief(self.state, 1))

    def test_for_your_information_it_is_listed_but_never_in_a_round(self):
        self.view("fyi")
        self.assertEqual(F.topic_status(self.state, self.t), "acked")
        self.assertEqual(F.inbox_of(self.state, self.t), "fyi")
        self.assertIsNone(F.round_pick(self.state))
        self.assertEqual(F.needs_verdict(self.state), [])
        thr = F.state_json(self.state)["inbox"]["threads"]
        self.assertEqual(thr, [{"id": "t1", "by": None, "fyi": True}])
        self.t["seen"] = F.answer_mark(self.state, self.t)          # opened once
        self.assertIsNone(F.inbox_of(self.state, self.t))

    def test_needs_you_joins_the_round_until_seen(self):
        self.view("needs", advice="merge", into="t9")
        self.assertEqual(F.topic_status(self.state, self.t), "needs_ack")
        self.assertEqual(F.state_json(self.state)["topics"][0]["peer_view"],
                         {"says": "needs", "advice": "merge", "into": "t9"})
        self.t["state"] = "acked"
        self.assertEqual(F.topic_status(self.state, self.t), "acked")

    def test_one_closed_before_views_existed_stays_closed_until_someone_writes(self):
        self.t.update(state="acked", acked_at="2026-11-05T09:30:00Z")
        self.assertEqual(F.topic_status(self.state, self.t), "acked")
        self.assertEqual(F.needs_verdict(self.state), [])
        self.assertIsNone(F.inbox_of(self.state, self.t))
        self.state["threads"]["d1"]["last_at"] = "2026-11-05T10:00:00Z"
        self.assertEqual(F.needs_verdict(self.state), ["t1"])

    def test_a_new_comment_asks_for_a_new_view(self):
        self.view("fyi")
        self.t["state"] = "acked"
        self.state["threads"]["d1"]["last_at"] = "2026-11-05T10:00:00Z"
        self.assertEqual(F.needs_verdict(self.state), ["t1"])


class TestOwnThreads(unittest.TestCase):
    def test_every_own_thread_needs_settling(self):
        state = new_state(threads={"d1": {"mine": True, "file": "a.ts", "line": 3, "body": "odd"},
                                   "d2": {"mine": True, "file": "b.ts", "line": 1, "body": "x"},
                                   "d3": {"mine": False, "body": "theirs"}})
        add_linked_topic(state, "d2", summary="x")
        state["proposals"] = []
        self.assertEqual(F.unsettled_own(state), ["d1"])
        state["proposals"] = [{"thread": "d1", "into": "t1", "summary": "s"}]
        self.assertEqual(F.unsettled_own(state), [])
        own = {x["id"]: x for x in F.own_threads(state)}
        self.assertEqual((own["d1"]["proposal"], own["d2"]["topic"]), ("t1", "t1"))


class TestProposalCard(unittest.TestCase):
    def test_the_card_shows_the_comment_the_topic_the_reason_and_the_advice(self):
        state = new_state(threads={"d1": {"mine": True, "file": "a.ts", "line": 3,
                                          "body": "this is not secure!", "url": "http://gl/n1"}})
        F.add_topic(state, summary="CORS origin is rejected", severity="critical")
        state["proposals"] = [{"thread": "d1", "into": "t1", "summary": "s",
                               "why": "Both flag `cors()`.", "recommend": "merge"}]
        with patch.dict(os.environ, {"REVIEW_UI_DRAWN": "1"}):
            card = F.render_proposal(state)
        self.assertIn("**Your comment** · `a.ts:3`", card)
        self.assertNotIn("**You**", card)              # nobody else answered: no signature
        self.assertIn(") this is not secure!", card)
        self.assertIn("**Possible match:** t1 — “CORS origin is rejected”", card)
        self.assertIn("Both flag `cors()`.", card)
        self.assertIn("Recommendation**\n\nMerge it into t1.", card)

    def test_signed_where_someone_else_answered(self):
        state = new_state(threads={"d1": {"mine": True, "file": "a.ts", "line": 3, "body": "odd",
                                          "notes": [{"author": "Sam Rivers", "body": "odd", "mine": True},
                                                    {"author": "Kim Lee", "body": "fixed", "mine": False}]}})
        F.add_topic(state, summary="CORS")
        state["proposals"] = [{"thread": "d1", "into": "t1", "summary": "s", "recommend": "separate"}]
        with patch.dict(os.environ, {"REVIEW_UI_DRAWN": "1"}):
            card = F.render_proposal(state)
        self.assertIn("> **You**\n> odd", card)
        self.assertIn("> **Kim**\n> fixed", card)
        self.assertIn("Recommendation**\n\nKeep it apart from t1, as a topic of its own.", card)


class TestDraftSuggestion(unittest.TestCase):
    def test_a_draft_s_suggestion_is_a_diff_against_the_lines_it_replaces(self):
        out = F.render_draft("Use this:\n\n```suggestion\nnew\n```", "a.ts", 2, ["l1", "old", "l3"])
        self.assertIn("> ```diff path=a.ts\n> @@ -2,1 +2,1 @@\n> -old\n> +new\n> ```", out)

    def test_a_suggestion_keeps_its_indentation(self):
        out = F.render_draft("```suggestion\n    await x();\n```", "a.ts", 2, ["l1", "    x();", "l3"])
        self.assertIn("> +    await x();", out)

    def test_without_the_file_its_lines_are_numbered(self):
        out = F.render_draft("```suggestion\nnew\n```", "a.ts", 2)
        self.assertIn("> 2 | new", out)


class TestExplainerAssessment(unittest.TestCase):
    """Whether an explainer is worth it is the agent's call; this measures what it decides
    on, so a refactor that only moves code reads as one however large it is."""

    def repo(self, d):
        def git(*a):
            subprocess.run(["git", "-C", d, *a], check=True, capture_output=True)
        git("init", "-q")
        git("config", "user.email", "t@example.com")
        git("config", "user.name", "T")
        body = "".join(f"export function f{i}(x: number): number {{\n  return x * {i} + {i};\n}}\n\n"
                       for i in range(20))
        with open(os.path.join(d, "a.ts"), "w") as fh:
            fh.write(body)
        with open(os.path.join(d, "a.test.ts"), "w") as fh:
            fh.write("test('x', () => {});\n")
        git("add", ".")
        git("commit", "-qm", "base")
        return git, body

    def test_code_that_only_moved_reads_as_moved(self):
        with tempfile.TemporaryDirectory() as d:
            git, body = self.repo(d)
            os.remove(os.path.join(d, "a.ts"))
            with open(os.path.join(d, "b.ts"), "w") as fh:
                fh.write("// moved\n" + body)
            with open(os.path.join(d, "a.test.ts"), "a") as fh:
                fh.write("test('y', () => {});\n")
            git("add", "-A")
            git("commit", "-qm", "move")
            a = F.assess_diff(d, "HEAD~1")
        self.assertEqual(a["files"], 2)                           # the test file left out
        self.assertGreater(a["moved"], 0.9 * (a["added"] + a["removed"]))
        self.assertIn("only moved", F.assess_line(a))
        self.assertTrue(F.assess_line(a).startswith("\uf4d2 2 files +"))

    def test_a_share_that_moved_too_little_to_matter_goes_unsaid(self):
        self.assertNotIn("moved", F.assess_line({"files": 43, "added": 182, "removed": 3, "moved": 0}))
        self.assertNotIn("moved", F.assess_line({"files": 3, "added": 90, "removed": 10, "moved": 19}))
        self.assertIn("20% of those lines only moved", F.assess_line({"files": 3, "added": 90, "removed": 10, "moved": 20}))

    def test_new_code_reads_as_changed(self):
        with tempfile.TemporaryDirectory() as d:
            git, _ = self.repo(d)
            with open(os.path.join(d, "c.ts"), "w") as fh:
                fh.write("".join(f"export const k{i} = fetch('/x/{i}');\n" for i in range(30)))
            git("add", "-A")
            git("commit", "-qm", "new")
            a = F.assess_diff(d, "HEAD~1")
        self.assertEqual((a["added"], a["moved"]), (30, 0))

    def test_the_choice_is_asked_until_answered(self):
        state = new_state()
        state["explainer_choice"] = {"recommend": "skip", "why": "A refactor that moves code.",
                                     "stats": {"files": 3, "added": 10, "removed": 10, "moved": 18},
                                     "chosen": None}
        self.assertEqual(F.state_json(state)["explainer"],
                         {"recommend": "skip", "why": "A refactor that moves code."})
        card = F.render_explainer_choice(state)
        self.assertIn("90% of those lines only moved", card)
        self.assertIn("Skip it. A refactor that moves code.", card)
        state["explainer_choice"]["chosen"] = "skip"
        self.assertIsNone(F.state_json(state)["explainer"])



class TestSuggestionWarning(unittest.TestCase):
    """A suggestion that replaces too little leaves the old lines beside the new once
    applied: storing the draft says so, with the lines around it to pick the range from."""

    SRC = ["## Decision", "", "### No auth on the socket", "", "The socket needs no auth.", "", "## Next"]

    def warn(self, body):
        t = {"file": "docs/a.md", "line": 4, "thread_ids": []}
        with patch.object(F, "_suggestion_source", return_value=self.SRC):
            return F.suggestion_warning({"threads": {}}, t, body)

    def test_a_blank_line_replaced_by_the_section_s_rewrite_is_flagged_with_its_lines(self):
        w = self.warn("Nit:\n\n```suggestion\n### Auth on the socket\n\nThe socket needs a token.\n```")
        self.assertIn("lines 3–5 at least", w)
        self.assertIn("    7 | ## Next", w)

    def test_a_range_that_covers_it_says_nothing(self):
        self.assertIsNone(self.warn("```suggestion:-1+1\n### Auth on the socket\n\nThe socket needs a token.\n```"))


class TestFollowUpPostedByHand(unittest.TestCase):
    """A follow-up the user posted by hand is checked against its draft like a comment: the
    note it compares and corrects is the user's newest in the thread, not the first."""

    def setUp(self):
        url = "http://gl/g/p/-/merge_requests/2#note_"
        self.state = {"iid": 2, "topics": [{"id": "t1", "thread_ids": ["d1"], "draft": "Still null on a read error?"}],
                      "threads": {"d1": {"notes": [{"body": "loadDb returns null.", "mine": True, "url": url + "1"},
                                                   {"body": "Fixed.", "mine": False, "url": url + "2"},
                                                   {"body": "Still null?", "mine": True, "url": url + "3"}]}}}
        self.t1 = self.state["topics"][0]

    def run_posted(self, *argv, stdin=""):
        with patch.dict(os.environ, {"REVIEW_UI_DRAWN": "1"}), \
                patch.object(F, "resolve_state", return_value=(CTX, 2, "/s", self.state)), \
                patch.object(F, "current_user"), patch.object(F, "sync"), \
                patch.object(F, "fetch_threads"), patch.object(F, "save"), patch.object(F.ui, "record"), \
                patch.object(sys, "argv", ["findings.py", "posted", "t1", *argv, "--iid", "2"]), \
                patch("sys.stdin", io.StringIO(stdin)), redirect_stdout(io.StringIO()) as out:
            F.main()
        return out.getvalue()

    def test_the_check_compares_the_newest_note_with_the_draft(self):
        out = self.run_posted()
        self.assertIn("Drafted:\n\nStill null on a read error?", out)
        self.assertIn("Posted:\n\nStill null?", out)
        self.assertTrue(self.t1["post_check"]["follow_up"])

    def test_its_correction_edits_the_newest_note(self):
        self.run_posted()
        self.run_posted("--differs", "It drops the case.", "--fix", "-", stdin="Still null on a read error?\n")
        self.assertEqual(self.t1["post_differs"]["note"], "3")

    def test_kept_as_posted_leaves_no_draft(self):
        self.run_posted()
        with patch.object(F, "CURRENT") as cur:
            cur.get.return_value = None
            self.run_posted("--ok")
        self.assertIsNone(self.t1["draft"])


class TestDraftHeading(unittest.TestCase):
    """Where the mod draws, a draft is headed by what it is alone, as in rework-mr, its Copy
    beside it; the language stays with the heading only where no mod draws."""

    def test_drawn_it_names_the_draft_alone(self):
        t = {"id": "t1", "thread_ids": [], "file": "a.ts", "line": 2}
        with patch.dict(os.environ, {"REVIEW_UI_DRAWN": "1"}), \
                patch.object(F, "_suggestion_source", return_value=None):
            out = "\n".join(F._draft_block({"threads": {}, "lang": "de"}, t, "Nit: x"))
        self.assertIn(f"**{F.ui.DRAFT_ICON} Draft comment**", out)
        self.assertNotIn("(de)", out)


class TestFollowUpReopens(unittest.TestCase):
    def test_a_follow_up_into_a_resolved_thread_reopens_it(self):
        state = {"iid": 2, "threads": {"d1": {"mine": True, "resolved": True, "notes": []}},
                 "topics": [{"id": "t2", "thread_ids": ["d1"], "draft": "Did you forget the ADR?"}]}
        with patch.dict(os.environ, {F.PRESS_ENV: "1"}), \
                patch.object(F, "api_post", return_value={"id": 9}) as api, \
                patch.object(F, "_reply_thread", return_value="d1"):
            F.post_draft(state, CTX, 2, "t2", None)
        self.assertEqual([c.args[0] for c in api.call_args_list],
                         ["projects/g%2Fp/merge_requests/2/discussions/d1/notes",
                          "projects/g%2Fp/merge_requests/2/discussions/d1?resolved=false"])
        self.assertFalse(state["threads"]["d1"]["resolved"])


class TestPostedCommentCard(unittest.TestCase):
    """Where the mod draws, a comment of the user's own already posted ends its card on that,
    then on who resolved its thread; the advice that led to it is not given again."""

    def test_it_ends_on_the_posting_and_the_resolve_without_the_recommendation(self):
        url = "http://gl/g/p/-/merge_requests/2#note_1"
        state = {"iid": 2, "threads": {"d1": {"mine": True, "resolved": True, "resolved_by": "Jonas Keller",
                                              "file": "a.md", "line": 3, "url": url,
                                              "notes": [{"body": "Nit: stale.", "mine": True, "url": url,
                                                         "at": "2026-11-10T16:39:00Z"}]}},
                 "topics": [{"id": "t2", "thread_ids": ["d1"], "summary": "stale section", "state": None,
                             "why": "The section is stale. **Recommendation:** raise it."}]}
        with patch.dict(os.environ, {"REVIEW_UI_DRAWN": "1"}), patch.object(F, "_thread_context", return_value=[]), \
                patch.object(F, "verdict_of", return_value=None), patch.object(F, "topic_status", return_value="open"):
            out = F._render_quote(state, "t2")
        self.assertNotIn("Recommendation", out)
        self.assertIn("The section is stale.", out)
        posted, resolved = out.index("Your comment is posted"), out.index("Resolved on GitLab by Jonas")
        self.assertLess(out.index("The section is stale."), posted)
        self.assertLess(posted, resolved)
        self.assertIn(f"[its thread ↗]({url})", out)

if __name__ == "__main__":
    unittest.main(verbosity=2)
