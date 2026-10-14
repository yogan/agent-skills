#!/usr/bin/env python3
"""Tests for self-review's script.

Run: `python3 skills/self-review/scripts/test_selfreview.py` (stdlib only).

The git side runs against real throwaway repositories, each in its own TemporaryDirectory:
which commits are under review, and what amending one of them does to the branch, are
facts about git, and a fake would only test the fake. The viewer is never started: every
call into `lib.hunk` is replaced, and the note fixtures are shaped like what `hunk session
comment list --type all --json` really returns.
"""
import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import selfreview as S  # noqa: E402


def sh(*args, cwd):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True)


def out(*args, cwd):
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True,
                          text=True).stdout.strip()


def commit(repo, name, text, subject):
    write(os.path.join(repo, name), text)
    sh("git", "add", ".", cwd=repo)
    sh("git", "commit", "-q", "-m", subject, cwd=repo)


def write(path, text):
    with open(path, "w") as fh:
        fh.write(text)


def read(path):
    with open(path) as fh:
        return fh.read()


class Repo(unittest.TestCase):
    """A feature branch of three commits — 1 edits f.txt, 2 adds g.txt, 3 edits f.txt
    again — whose default branch has moved on by one commit since it was branched.

    Built once per run and copied for each test: some twenty git calls per build were most
    of this file's run time, and a copy is a fresh, independent repository all the same.
    """

    @classmethod
    def setUpClass(cls):
        tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        cls.template = os.path.realpath(tmp.name)
        remote = os.path.join(cls.template, "remote.git")
        seed = os.path.join(cls.template, "seed")
        clone = os.path.join(cls.template, "app")
        sh("git", "init", "-q", "--bare", "-b", "main", remote, cwd=cls.template)
        sh("git", "init", "-q", "-b", "main", seed, cwd=cls.template)
        for args in (("config", "user.email", "t@example.com"), ("config", "user.name", "t")):
            sh("git", *args, cwd=seed)
        commit(seed, "f.txt", "a\nb\nc\n", "base")
        sh("git", "remote", "add", "origin", remote, cwd=seed)
        sh("git", "push", "-q", "origin", "main", cwd=seed)
        sh("git", "clone", "-q", remote, clone, cwd=cls.template)
        for args in (("config", "user.email", "t@example.com"), ("config", "user.name", "t"),
                     ("checkout", "-q", "-b", "feat/x")):
            sh("git", *args, cwd=clone)
        commit(clone, "f.txt", "a\nB\nc\n", "one")
        commit(clone, "g.txt", "g\n", "two")
        commit(clone, "f.txt", "a\nB\nc\nd\n", "three")
        commit(seed, "m.txt", "m\n", "moved on")
        sh("git", "push", "-q", "origin", "main", cwd=seed)
        sh("git", "fetch", "-q", "origin", cwd=clone)
        cls.base = out("git", "merge-base", "origin/main", "HEAD", cwd=clone)

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.clone = os.path.join(os.path.realpath(tmp.name), "app")
        shutil.copytree(os.path.join(self.template, "app"), self.clone, symlinks=True)

    def path(self, name):
        return os.path.join(self.clone, name)

    def subjects(self):
        return [s for _, s in S.commits(self.clone)]


class TestRange(Repo):
    def test_commits_run_from_the_merge_base_oldest_first(self):
        self.assertEqual(self.subjects(), ["one", "two", "three"])
        self.assertEqual(S.base(self.clone), self.base)

    def test_the_default_branch_moving_on_adds_nothing_to_a_commit(self):
        sha = S.commits(self.clone)[0][0]
        self.assertEqual(S.commit_stat(sha, self.clone), [("f.txt", 1, 1)])

    def test_the_uncommitted_change_counts_staged_unstaged_and_new_files(self):
        write(self.path("g.txt"), "g\nstaged\n")
        sh("git", "add", "g.txt", cwd=self.clone)
        write(self.path("f.txt"), "a\nB\nc\nd\ne\n")
        write(self.path("new.txt"), "1\n2\n")
        self.assertEqual(sorted(S.work_stat(self.clone)),
                         [("f.txt", 1, 0), ("g.txt", 1, 0), ("new.txt", 2, 0)])

    def test_a_position_past_the_branch_is_refused(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            S.commit_at(4, self.clone)


class TestShow(Repo):
    def show(self, target):
        shown = []
        with patch.object(S.hunk, "show_review",
                          lambda repo, label, args, stat: shown.append((args, stat))
                          or ("s1", "2 (self-review)")), \
                patch.object(S, "guard_switch", lambda *a, **k: None), \
                patch.object(S.hunk, "find_session", lambda repo, owner: None), \
                patch.object(S.hunk, "set_mark", lambda *a: True), \
                contextlib.redirect_stdout(io.StringIO()):
            S.show(target, self.clone)
        return shown

    def test_a_commit_is_shown_by_its_full_hash(self):
        sha = S.commits(self.clone)[1][0]
        self.assertEqual(self.show("2"), [(["show", sha], [("g.txt", 1, 0)])])

    def test_commits_wait_for_a_clean_tree(self):
        write(self.path("f.txt"), "dirty\n")
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.show("1")

    def test_the_uncommitted_change_is_shown_against_head(self):
        write(self.path("new.txt"), "n\n")
        self.assertEqual(self.show("work"), [(["diff", "HEAD"], [("new.txt", 1, 0)])])

    def test_there_is_no_uncommitted_change_to_show_in_a_clean_tree(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.show("work")


class TestAmend(Repo):
    def amend(self, n):
        shown = []
        with patch.object(S, "show", lambda target, cwd=None, after_amend=False:
                          shown.append((target, after_amend))), \
                contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            S.amend(n, self.clone)
        return shown

    def test_a_middle_commit_takes_the_fix_and_keeps_its_place(self):
        write(self.path("g.txt"), "g2\n")
        self.assertEqual(self.amend(2), [("2", True)])
        self.assertEqual(self.subjects(), ["one", "two", "three"])
        sha = S.commits(self.clone)[1][0]
        self.assertEqual(out("git", "show", f"{sha}:g.txt", cwd=self.clone), "g2")
        self.assertEqual(S.dirty(self.clone), [])

    def test_the_newest_commit_is_amended_in_place(self):
        write(self.path("f.txt"), "a\nB\nc\nD\n")
        self.amend(3)
        self.assertEqual(self.subjects(), ["one", "two", "three"])
        self.assertEqual(out("git", "show", "HEAD:f.txt", cwd=self.clone), "a\nB\nc\nD")

    def test_a_fix_that_conflicts_later_leaves_branch_and_fix_as_they_were(self):
        head = out("git", "rev-parse", "HEAD", cwd=self.clone)
        write(self.path("f.txt"), "a\nB\nC\n")
        with self.assertRaises(SystemExit):
            self.amend(1)
        self.assertEqual(out("git", "rev-parse", "HEAD", cwd=self.clone), head)
        self.assertEqual(read(self.path("f.txt")), "a\nB\nC\n")
        self.assertEqual(out("git", "status", "--porcelain", cwd=self.clone), "M f.txt")
        self.assertFalse(os.path.exists(os.path.join(self.clone, ".git", "rebase-merge")))

    def test_a_clean_tree_has_nothing_to_amend(self):
        with self.assertRaises(SystemExit):
            self.amend(1)


def note(nid, source, text, parent=None, at="1", line=3, side="new"):
    """One entry of `hunk session comment list --type all --json`, shaped as hunk emits
    it: a note typed in the viewer has a `user:` id and source "user"; a reply carries
    the note it answers as `parentId`."""
    c = {"noteId": nid, "source": source, "filePath": "f.txt", "hunkIndex": 0,
         f"{side}Range": [line, line], "body": text, "author": source,
         "createdAt": f"2026-01-01T00:00:0{at}.000Z", "editable": source == "user"}
    if parent:
        c["parentId"] = parent
    return c


def parsed(*raw):
    """`lib.hunk.notes`' reading of those entries."""
    with patch.object(S.hunk, "_run", lambda argv, **k: (0, __import__("json").dumps(
            {"comments": list(raw)}))):
        return S.hunk.notes("s1")


class TestOpenThreads(unittest.TestCase):
    def test_a_note_without_an_answer_awaits_one(self):
        threads = S.open_threads(parsed(note("user:1", "user", "why?")))
        self.assertEqual(threads, [(1, "user:1", "f.txt:3", ["why?"])])

    def test_an_answered_note_awaits_nothing(self):
        self.assertEqual(S.open_threads(parsed(
            note("user:1", "user", "why?"),
            note("mcp:2", "agent", "because", parent="user:1", at="2"))), [])

    def test_the_author_answering_back_reopens_the_thread_with_only_the_new_words(self):
        threads = S.open_threads(parsed(
            note("user:1", "user", "why?"),
            note("mcp:2", "agent", "because", parent="user:1", at="2"),
            note("user:3", "user", "then rename it", parent="mcp:2", at="3")))
        self.assertEqual(threads, [(1, "user:1", "f.txt:3", ["then rename it"])])

    def test_a_note_on_a_removed_line_says_so(self):
        threads = S.open_threads(parsed(note("user:1", "user", "gone?", side="old")))
        self.assertEqual(threads[0][2], "f.txt:3 (removed line)")


class TestReply(unittest.TestCase):
    """A reply has to reach the thread the agent means, from whatever reference it was
    given: ids are long, and the `user:` prefix is exactly what gets dropped in copying."""

    NOTES = parsed(note("user:1792059099183-1", "user", "why?"),
                   note("mcp:2", "agent", "because", parent="user:1792059099183-1", at="2"),
                   note("user:1792059099999-1", "user", "and this?", at="3", line=7))

    def test_a_thread_is_found_by_its_number(self):
        self.assertEqual(S.resolve("2", self.NOTES), "user:1792059099999-1")
        self.assertEqual(S.resolve("#1", self.NOTES), "user:1792059099183-1")

    def test_a_thread_is_found_by_any_of_its_ids_prefix_or_not(self):
        for ref in ("user:1792059099183-1", "1792059099183-1", "mcp:2"):
            self.assertEqual(S.resolve(ref, self.NOTES), "user:1792059099183-1", ref)

    def test_numbers_stay_put_when_a_thread_is_answered(self):
        self.assertEqual([(n, w) for n, _, w, _ in S.open_threads(self.NOTES)],
                         [(2, "f.txt:7")])

    def test_nothing_is_found_for_an_unknown_reference(self):
        self.assertIsNone(S.resolve("3", self.NOTES))
        self.assertIsNone(S.resolve("user:404", self.NOTES))

    def run_reply(self, ref, lands=True, posted=None):
        posted = [] if posted is None else posted
        state = {"notes": list(self.NOTES)}

        def post(sid, parent, text):
            posted.append(parent)
            if lands:
                state["notes"] = state["notes"] + [
                    {"id": "mcp:new", "parent": parent, "source": "agent", "file": "f.txt",
                     "side": "new", "line": 7, "text": text, "at": "9"}]
            return True

        with patch.object(S, "own_session", lambda cwd=None: "s1"), \
                patch.object(S.hunk, "notes", lambda sid: list(state["notes"])), \
                patch.object(S.hunk, "reply", post), \
                contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            S.reply(ref, "Renamed.")
        return posted

    def test_the_reply_goes_under_the_threads_first_note(self):
        self.assertEqual(self.run_reply("2"), ["user:1792059099999-1"])

    def test_an_answer_goes_under_the_authors_latest_note_in_it(self):
        """Under the first note it would be a sibling of the author's reply, and the viewer
        shows an agent's replies above the author's among siblings."""
        notes = parsed(note("mcp:1", "agent", "look here"),
                       note("user:2", "user", "why?", parent="mcp:1", at="2"))
        self.assertEqual(S.answering("mcp:1", notes), "user:2")
        self.NOTES = notes
        self.assertEqual(self.run_reply("1"), ["user:2"])

    def test_an_answer_to_a_lone_note_goes_under_it(self):
        self.assertEqual(S.answering("user:1792059099999-1", self.NOTES),
                         "user:1792059099999-1")

    def test_a_reply_missing_from_the_window_afterwards_is_an_error(self):
        with self.assertRaises(SystemExit):
            self.run_reply("2", lands=False)

    def test_an_unknown_thread_posts_nothing(self):
        posted = []
        with self.assertRaises(SystemExit):
            self.run_reply("5", posted=posted)
        self.assertEqual(posted, [])


class TestRemap(unittest.TestCase):
    """Where a note on lines start..end goes, given `git diff -U0` hunks (a, b, c, d): old
    lines a..a+b-1 became new lines c..c+d-1."""

    def test_untouched_code_below_every_change_stays(self):
        self.assertEqual(S.remap([(50, 2, 50, 2)], 10, 12), (12, "same"))

    def test_lines_added_above_shift_it(self):
        self.assertEqual(S.remap([(3, 0, 4, 2)], 10, 12), (14, "shifted"))

    def test_lines_removed_above_shift_it(self):
        self.assertEqual(S.remap([(3, 4, 2, 0)], 10, 12), (8, "shifted"))

    def test_a_replaced_block_puts_the_note_below_its_replacement(self):
        """The case that prompted this: a note on R356–R383, a 28-line block the fix
        replaced with a shorter one. On the old line numbers the thread split the new code
        down the middle; below the replacement it reads as a comment on it."""
        self.assertEqual(S.remap([(356, 28, 356, 17)], 356, 383), (372, "rewritten"))

    def test_a_partly_rewritten_range_ends_below_its_untouched_tail(self):
        self.assertEqual(S.remap([(10, 1, 10, 3)], 10, 12), (14, "rewritten"))

    def test_an_insertion_inside_the_range_is_a_rewrite(self):
        self.assertEqual(S.remap([(10, 0, 11, 2)], 10, 12), (14, "rewritten"))

    def test_an_insertion_right_after_the_range_leaves_it_alone(self):
        self.assertEqual(S.remap([(12, 0, 13, 2)], 10, 12), (12, "same"))

    def test_deleted_code_puts_the_note_where_it_used_to_be(self):
        self.assertEqual(S.remap([(10, 3, 9, 0)], 10, 12), (9, "deleted"))

    def test_deleting_the_first_lines_of_a_file_keeps_the_note_on_line_one(self):
        self.assertEqual(S.remap([(1, 2, 0, 0)], 1, 2), (1, "deleted"))


class TestChanges(Repo):
    def test_hunks_come_from_the_two_versions_of_the_file(self):
        one, three = S.commits(self.clone)[0][0], S.commits(self.clone)[2][0]
        self.assertEqual(S.changes(one, three, "f.txt", self.clone), [(3, 0, 4, 1)])

    def test_a_snapshot_holds_untracked_files_and_leaves_the_index_alone(self):
        write(self.path("new.txt"), "n\n")
        tree = S.snapshot(self.clone)
        self.assertEqual(out("git", "show", f"{tree}:new.txt", cwd=self.clone), "n")
        self.assertEqual(out("git", "status", "--porcelain", cwd=self.clone), "?? new.txt")


class FakeWindow:
    """The viewer's notes, for `move_notes`: adds, replies and removals act on a list."""

    def __init__(self, notes, fail_reply_at=None):
        self.notes = list(notes)
        self.fail_reply_at = fail_reply_at
        self.n = 0

    def _new(self, parent, path, line, text, author):
        self.n += 1
        nid = f"mcp:new{self.n}"
        self.notes.append({"id": nid, "parent": parent, "source": "agent",
                           "author": author, "file": path, "side": "new", "line": line,
                           "end": line, "text": text, "at": f"9{self.n}"})
        return nid

    def add_note(self, sid, path, line, text, author):
        return self._new(None, path, line, text, author)

    def reply(self, sid, parent, text, author="agent"):
        if self.fail_reply_at == self.n:
            return None
        root = next(n for n in self.notes if n["id"] == parent)
        return self._new(parent, root["file"], root["line"], text, author)

    def drop_note(self, sid, nid):
        self.notes = [n for n in self.notes if n["id"] != nid]
        return True

    def run(self, before, hunks, files=("f.txt",)):
        with patch.object(S.hunk, "add_note", self.add_note), \
                patch.object(S.hunk, "reply", self.reply), \
                patch.object(S.hunk, "drop_note", self.drop_note), \
                patch.object(S.hunk, "notes", lambda sid: list(self.notes)), \
                patch.object(S, "changes", lambda old, new, path, cwd=None: hunks):
            return S.move_notes("s1", before, "old", "new", set(files))


class TestMoveNotes(unittest.TestCase):
    THREAD = parsed(
        dict(note("user:1", "user", "why this block?", line=356), newRange=[356, 383]),
        note("mcp:2", "agent", "it can be shorter", parent="user:1", at="2", line=356),
        note("user:3", "user", "do it", parent="mcp:2", at="3", line=356))

    def test_a_rewritten_thread_moves_whole_and_stays_the_authors(self):
        w = FakeWindow(self.THREAD)
        report = w.run(self.THREAD, [(356, 28, 356, 17)])
        self.assertEqual(report, ["#1 f.txt:R356–383 rewritten: moved to R372"])
        self.assertEqual([(n["author"], n["line"], n["text"]) for n in w.notes],
                         [("you (moved from R356–383)", 372, "why this block?"),
                          ("agent", 372, "it can be shorter"),
                          ("you", 372, "do it")])
        self.assertEqual(S.open_threads(w.notes)[0][3], ["do it"])

    def test_a_moved_copy_keeps_each_reply_under_the_note_it_answered(self):
        w = FakeWindow(self.THREAD)
        w.run(self.THREAD, [(356, 28, 356, 17)])
        by = {n["id"]: n for n in w.notes}
        self.assertEqual([by[n["parent"]]["text"] if n["parent"] else None for n in w.notes],
                         [None, "why this block?", "it can be shorter"])

    def test_deleted_code_says_so_in_the_label(self):
        w = FakeWindow(self.THREAD)
        w.run(self.THREAD, [(356, 28, 355, 0)])
        self.assertEqual(w.notes[0]["author"], "you (moved from R356–383, code deleted)")

    def test_unchanged_code_leaves_the_thread_untouched(self):
        w = FakeWindow(self.THREAD)
        self.assertEqual(w.run(self.THREAD, [(400, 1, 400, 1)]), [])
        self.assertEqual(w.notes, list(self.THREAD))

    def test_a_copy_that_cannot_be_completed_is_removed_and_the_original_kept(self):
        w = FakeWindow(self.THREAD, fail_reply_at=2)
        report = w.run(self.THREAD, [(356, 28, 356, 17)])
        self.assertEqual(w.notes, list(self.THREAD))
        self.assertIn("could not be moved", report[0])

    def test_a_note_dropped_by_the_reload_is_re_created_from_the_copy(self):
        w = FakeWindow([])
        w.run(self.THREAD, [(356, 28, 356, 17)])
        self.assertEqual(len(w.notes), 3)

    def test_a_file_gone_from_the_change_leaves_its_thread_and_says_so(self):
        w = FakeWindow(self.THREAD)
        report = w.run(self.THREAD, [(356, 28, 356, 17)], files=())
        self.assertEqual(w.notes, list(self.THREAD))
        self.assertIn("no longer part of this change", report[0])

    def test_a_note_on_a_removed_line_does_not_move(self):
        removed = parsed(note("user:1", "user", "why removed?", side="old"))
        w = FakeWindow(removed)
        self.assertEqual(w.run(removed, [(1, 5, 1, 0)]), [])


class TestPoint(unittest.TestCase):
    """The agent's own diff note, started only because the author asked for a place to be
    pointed out."""

    def run_point(self, lands=True, side="new"):
        w = FakeWindow([])
        added = []

        def add(sid, path, line, text, author, side="new"):
            added.append((path, line, side, author))
            return w.add_note(sid, path, line, text, author) if lands else None

        with patch.object(S, "own_session", lambda cwd=None: "s1"), \
                patch.object(S.hunk, "notes", lambda sid: list(w.notes)), \
                patch.object(S.hunk, "add_note", add), \
                contextlib.redirect_stdout(io.StringIO()) as out, \
                contextlib.redirect_stderr(io.StringIO()):
            S.point("a.py", 32, "names[0] is read here", side=side)
        return added, out.getvalue(), w

    def test_a_pointed_line_gets_the_agents_note_and_a_number(self):
        added, printed, _ = self.run_point()
        self.assertEqual(added, [("a.py", 32, "new", "agent")])
        self.assertEqual(printed.strip(), "pointed out #1 a.py:32")

    def test_a_removed_line_is_pointed_at_on_the_old_side(self):
        self.assertEqual(self.run_point(side="old")[0][0][2], "old")

    def test_a_line_outside_the_piece_is_an_error(self):
        with self.assertRaises(SystemExit):
            self.run_point(lands=False)

    def test_a_pointed_note_awaits_nothing_until_the_author_replies(self):
        notes = parsed(note("mcp:1", "agent", "look here"))
        self.assertEqual(S.open_threads(notes), [])
        notes = parsed(note("mcp:1", "agent", "look here"),
                       note("user:2", "user", "why?", parent="mcp:1", at="2"))
        self.assertEqual(S.open_threads(notes)[0][3], ["why?"])

    def test_a_pointed_note_moves_as_the_agents(self):
        thread = parsed(note("mcp:1", "agent", "look here", line=10))
        w = FakeWindow(thread)
        w.run(thread, [(3, 0, 4, 2)])
        self.assertEqual(w.notes[0]["author"], "agent (moved from R10)")
        self.assertEqual(S.open_threads(w.notes), [])


class TestGuards(unittest.TestCase):
    """What stands between the author's notes and losing them: moving the window on, and
    clearing or closing it, each refuse while notes are in the way."""

    def run_with(self, fn, *args, notes=(), loaded=("show", "aaa"), notes_ok=True):
        cleared = []
        with patch.object(S.hunk, "installed", lambda: True), \
                patch.object(S.hunk, "find_session", lambda repo, owner: "s1"), \
                patch.object(S, "root", lambda cwd=None: "/w/app"), \
                patch.object(S.hunk, "notes", lambda sid: list(notes) if notes_ok else None), \
                patch.object(S.hunk, "loaded_input", lambda sid: loaded), \
                patch.object(S.hunk, "clear_all_notes", lambda sid: cleared.append(sid) or True), \
                patch.object(S.hunk, "close_session", lambda sid, owner: True), \
                contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            fn(*args)
        return cleared

    OPEN = parsed(note("user:1", "user", "why?"))
    ANSWERED = parsed(note("user:1", "user", "why?"),
                      note("mcp:2", "agent", "because", parent="user:1", at="2"))

    def test_an_empty_window_may_load_anything(self):
        self.run_with(S.guard_switch, "/w/app", ["show", "bbb"])

    def test_notes_keep_the_window_on_its_commit(self):
        with self.assertRaises(SystemExit):
            self.run_with(S.guard_switch, "/w/app", ["show", "bbb"], notes=self.ANSWERED)

    def test_the_same_commit_may_be_reloaded_under_its_notes(self):
        self.run_with(S.guard_switch, "/w/app", ["show", "aaa"], notes=self.ANSWERED)

    def test_the_uncommitted_change_may_be_reloaded_under_its_notes(self):
        self.run_with(S.guard_switch, "/w/app", ["diff", "HEAD"], notes=self.ANSWERED,
                      loaded=("vcs", None))

    def test_a_viewer_that_does_not_answer_loads_nothing(self):
        with self.assertRaises(SystemExit):
            self.run_with(S.guard_switch, "/w/app", ["show", "bbb"], notes_ok=False)

    def test_ack_waits_for_every_answer(self):
        cleared = []
        with self.assertRaises(SystemExit):
            cleared = self.run_with(S.ack, notes=self.OPEN)
        self.assertEqual(cleared, [])

    def test_ack_clears_an_answered_window(self):
        self.assertEqual(self.run_with(S.ack, notes=self.ANSWERED), ["s1"])

    def test_close_waits_for_every_answer(self):
        with self.assertRaises(SystemExit):
            self.run_with(S.close, notes=self.OPEN)


if __name__ == "__main__":
    unittest.main()
