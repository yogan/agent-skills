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


class TestNeedsTitle(unittest.TestCase):
    """adopt_inbound has no way to author an English one-liner for a thread it did not
    write — the only text available is the raw comment body, in whatever language the
    commenter used. `needs_title` makes that gap visible instead of letting the raw
    quote silently pass as a finished title (see `short_summary`'s docstring)."""

    def test_adopted_thread_is_flagged_with_no_authored_summary(self):
        state = new_state(threads={"d1": {"body": "Sollten wir hier nicht X machen?",
                                           "file": "a.py", "line": 3}})
        F.adopt_inbound(state, None, None)
        t = F.topic_for(state, "t1")
        self.assertTrue(t["needs_title"])
        self.assertIsNone(t["summary"])

    def test_render_table_flags_a_needs_title_topic(self):
        state = new_state(threads={"d1": {"body": "Sollten wir hier nicht X machen?",
                                           "file": "a.py", "line": 3}})
        F.adopt_inbound(state, None, None)
        self.assertIn("needs summary", F.render_table(state))

    def test_render_table_leaves_an_authored_summary_alone(self):
        state = new_state(threads={"d1": {"body": "irrelevant"}})
        add_linked_topic(state, "d1", summary="a real English title")
        self.assertNotIn("needs summary", F.render_table(state))

    def test_quote_warns_when_topic_needs_a_title(self):
        state = new_state(threads={"d1": {"body": "Sollten wir hier nicht X machen?",
                                           "file": "a.py", "line": 3}})
        F.adopt_inbound(state, None, None)
        self.assertIn("needs an English summary", F.render_quote(state, "t1"))

    def test_quote_is_silent_for_an_authored_summary(self):
        state = new_state(threads={"d1": {"body": "irrelevant"}})
        add_linked_topic(state, "d1", summary="a real English title")
        self.assertNotIn("needs an English summary", F.render_quote(state, "t1"))

    def test_setting_a_summary_clears_the_flag(self):
        state = new_state(threads={"d1": {"body": "Sollten wir hier nicht X machen?",
                                           "file": "a.py", "line": 3}})
        F.adopt_inbound(state, None, None)
        t = F.topic_for(state, "t1")
        t["summary"] = "Clarify the X behavior"
        t["needs_title"] = False              # what `set --summary` does, see cmd == "set"
        self.assertNotIn("needs summary", F.render_table(state))
        self.assertNotIn("needs an English summary", F.render_quote(state, "t1"))


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
