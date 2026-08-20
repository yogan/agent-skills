#!/usr/bin/env python3
"""Tests for review-mr's presentation and status layer — code snippets, the overview
table, topic status derivation, and thread reconciliation.

Run: `python3 skills/review-mr/scripts/test_findings.py` (stdlib only).
"""
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import findings as F                                  # noqa: E402
from lib import critical_manifest                      # noqa: E402


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


class TestSync(unittest.TestCase):
    def test_local_fields_survive_a_fetch(self):
        state = new_state(threads={"d1": {"gone": True, "awaiting": "you"}})
        F.sync(state, {"d1": {"awaiting": "them"}})
        self.assertEqual(state["threads"]["d1"], {"awaiting": "them", "gone": True})

    def test_a_thread_missing_from_live_is_marked_resolved_and_gone(self):
        state = new_state(threads={"d1": {"awaiting": "you", "resolved": False}})
        F.sync(state, {})
        self.assertTrue(state["threads"]["d1"]["resolved"])
        self.assertTrue(state["threads"]["d1"]["gone"])

    def test_a_new_thread_from_live_is_added_verbatim(self):
        state = new_state()
        F.sync(state, {"d1": {"awaiting": "you"}})
        self.assertEqual(state["threads"]["d1"], {"awaiting": "you"})


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
        with patch.object(F, "mr_object", return_value={"state": "merged"}), \
             patch("subprocess.run"):
            removed = F.prune_worktrees(self._ctx(slug))
        self.assertEqual(len(removed), 1)
        self.assertIn("!42", removed[0])
        self.assertIn("merged", removed[0])
        self.assertFalse(os.path.isdir(wt))
        self.assertIsNone(F.get_worktree(slug, 42))
        self.assertTrue(os.path.exists(findings_path))

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
