#!/usr/bin/env python3
"""Tests for `lib/hunk.py` — driving a Hunk viewer in a tmux window.

Run: `python3 lib/test_hunk.py` (stdlib only).

Everything goes through `hunk._run`, so these fake that one boundary and never start a
viewer, a daemon or a tmux window. What is actually under test is the decision logic: who
owns which window, and — far more important — every way the module has to conclude "I
could not put the diff there" so the caller falls back to printing it inline. A pointer at
a window that does not hold the diff is the one outcome worth writing tests against: the
user approves a force-push against something they never saw.
"""
import json
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib import hunk                                   # noqa: E402

REPO = "/w/acme-api"


def stat_of(n):
    """A caller's [(path, added, removed)] for a diff of `n` files — the shape
    `show_working_diff` now compares against, rather than a bare count."""
    return [(f"f{i}.py", 1, 0) for i in range(n)]


def session(sid="s1", repo=REPO, pane="%4"):
    loc = [{"source": "tty", "tty": "/dev/ttys004"}]
    if pane:
        loc.append({"source": "tmux", "paneId": pane})
    return {"sessionId": sid, "repoRoot": repo, "terminal": {"locations": loc}}


class FakeRun:
    """Stands in for `hunk._run`, dispatching on the shape of the command.

    `owners` maps pane -> owner mark; a pane absent from it answers the way tmux does for
    an unset option (non-zero, no value), which is what "somebody else's window" looks
    like from here.
    """

    def __init__(self, sessions=(), owners=None, new_pane="%9", window="9 (!123)",
                 reload_files=1, reload_ok=True, list_ok=True, register=True,
                 window_ids=None, loaded=None):
        self.sessions = list(sessions)
        self.owners = dict(owners or {})
        self.new_pane, self.window = new_pane, window
        # pane -> the window it sits in, for the `#{window_id}` lookup only.
        self.window_ids = dict(window_ids or {})
        # What `session review` says the viewer holds. Defaults to agreeing with
        # `reload_files`, so only a test that is ABOUT a disagreement has to say so.
        self.loaded = stat_of(reload_files) if loaded is None else list(loaded)
        self.reload_files, self.reload_ok, self.list_ok = reload_files, reload_ok, list_ok
        # `register=False` is a viewer that starts and never reaches the daemon: the
        # window exists, no session ever appears for its pane.
        self.register = register
        self.calls = []

    def __call__(self, argv, timeout=None, stdin=None):
        self.calls.append(argv)
        import json as _json
        if argv[:3] == ["hunk", "session", "list"]:
            if not self.list_ok:
                return 1, ""
            return 0, _json.dumps({"sessions": self.sessions})
        if argv[:3] == ["hunk", "session", "reload"]:
            if not self.reload_ok:
                return 1, ""
            return 0, _json.dumps({"result": {"fileCount": self.reload_files}})
        if argv[:3] == ["hunk", "session", "review"]:
            return 0, _json.dumps({"review": {"files": [
                {"path": p, "additions": a, "deletions": r} for p, a, r in self.loaded]}})
        if argv[:3] == ["hunk", "session", "comment"]:
            return 0, _json.dumps({"comments": []})
        if argv[:2] == ["tmux", "show-options"]:
            pane = argv[argv.index("-t") + 1]
            return (0, self.owners[pane]) if pane in self.owners else (1, "")
        if argv[:2] == ["tmux", "new-window"]:
            if self.register:
                self.sessions.append(session("new", REPO, self.new_pane))
            return 0, self.new_pane + "\n"
        if argv[:2] == ["tmux", "set-option"]:
            self.owners[argv[argv.index("-t") + 1]] = argv[-1]
            return 0, ""
        if argv[:2] == ["tmux", "kill-window"]:
            return 0, ""
        if argv[:2] == ["tmux", "display-message"]:
            # Two callers, told apart by their format: the window id `new-window -t` needs,
            # and the "9 (!123)" the user is pointed at.
            if argv[-1] == "#{window_id}":
                return (0, self.window_ids[argv[-2]] + "\n") \
                    if argv[-2] in self.window_ids else (1, "")
            return 0, self.window + "\n"
        return 1, ""


class HunkCase(unittest.TestCase):
    def show(self, fake, expect_files=1, **kw):
        """`installed` is forced rather than left to the machine: the viewer is optional
        and most machines running this suite — CI, anyone who cloned the repo — will not
        have it, which would otherwise turn every success case here into a failure that
        says nothing about the code."""
        with patch.dict(os.environ, {"TMUX": "/tmp/x", "CLAUDE_CODE_SESSION_ID": "me"}), \
                patch.object(hunk, "installed", lambda: True), \
                patch.object(hunk, "_run", fake):
            return hunk.show_working_diff(REPO, "!123", stat_of(expect_files),
                                          sleep=lambda _s: None, **kw)


class TestOwnership(HunkCase):
    def test_finds_only_a_window_this_agent_marked(self):
        fake = FakeRun([session("mine", pane="%4")], owners={"%4": "me"})
        with patch.object(hunk, "_run", fake):
            self.assertEqual(hunk.find_session(REPO, "me"), "mine")

    def test_ignores_another_agents_window_in_the_same_repo(self):
        """The case that makes `--repo` unusable: same checkout, different agent. Taking
        it over would swap the diff another session told its user to look at."""
        fake = FakeRun([session("theirs", pane="%4")], owners={"%4": "someone-else"})
        with patch.object(hunk, "_run", fake):
            self.assertIsNone(hunk.find_session(REPO, "me"))

    def test_ignores_a_window_the_user_opened_themselves(self):
        """An unmarked window is the user's own hunk, not ours."""
        fake = FakeRun([session("theirs", pane="%4")], owners={})
        with patch.object(hunk, "_run", fake):
            self.assertIsNone(hunk.find_session(REPO, "me"))

    def test_ignores_our_window_in_a_different_repo(self):
        fake = FakeRun([session("other", repo="/w/elsewhere", pane="%4")],
                       owners={"%4": "me"})
        with patch.object(hunk, "_run", fake):
            self.assertIsNone(hunk.find_session(REPO, "me"))

    def test_an_agent_with_no_identity_adopts_nothing(self):
        """An UNMARKED window — the user's own viewer — reads back as the empty owner too,
        so an empty owner must match nothing. Matching would reload the user's diff away
        and then read their comments as answers to a question they were never asked."""
        fake = FakeRun([session("theirs", pane="%4")], owners={})
        with patch.object(hunk, "_run", fake):
            self.assertIsNone(hunk.find_session(REPO, ""))

    def test_a_window_whose_viewer_never_started_is_closed_again(self):
        """It holds no diff anyone was pointed at, and having no session the next topic
        would not find it either — it would open another one beside it."""
        fake = FakeRun(new_pane="%9", register=False)
        with patch.object(hunk, "_run", fake):
            self.assertIsNone(hunk.spawn_session(REPO, "!123", "me",
                                                 sleep=lambda _s: None))
        self.assertIn(["tmux", "kill-window", "-t", "%9"], fake.calls)

    def test_a_window_that_cannot_be_marked_is_closed_again(self):
        """The mark is the window's whole identity. Left unmarked it still serves THIS
        pointer, then becomes invisible to `find_session` for ever — a new viewer every
        topic, and `hunk-close` unable to reach any of them."""
        fake = FakeRun(new_pane="%9")

        def refuse_marking(argv, timeout=None, stdin=None):
            if argv[:2] == ["tmux", "set-option"]:
                fake.calls.append(argv)
                return 1, ""
            return fake(argv, timeout, stdin)
        with patch.object(hunk, "_run", refuse_marking):
            self.assertIsNone(hunk.spawn_session(REPO, "!123", "me",
                                                 sleep=lambda _s: None))
        self.assertIn(["tmux", "kill-window", "-t", "%9"], fake.calls)

    def test_a_window_that_did_start_is_left_alone(self):
        fake = FakeRun(new_pane="%9")
        with patch.object(hunk, "_run", fake):
            hunk.spawn_session(REPO, "!123", "me", sleep=lambda _s: None)
        self.assertNotIn("kill-window", [c[1] for c in fake.calls if len(c) > 1])

    def test_spawn_marks_the_window_it_created(self):
        """Identified by the pane tmux just reported, not by "whichever session appeared"
        — two agents opening a window at the same moment would resolve that wrongly."""
        fake = FakeRun(new_pane="%9")
        with patch.object(hunk, "_run", fake):
            self.assertEqual(hunk.spawn_session(REPO, "!123", "me", sleep=lambda _s: None),
                             "new")
        self.assertEqual(fake.owners["%9"], "me")


class TestFallsBackInline(HunkCase):
    """Every route to None. Each is a case where pointing at the window would ask the user
    to approve a push against a diff they were never shown."""

    def test_outside_tmux(self):
        fake = FakeRun([session(pane="%4")], owners={"%4": "me"})
        with patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": "me"}, clear=True), \
                patch.object(hunk, "_run", fake):
            self.assertIsNone(hunk.show_working_diff(REPO, "!123", 1))

    def test_no_agent_identity(self):
        fake = FakeRun()
        with patch.dict(os.environ, {"TMUX": "/tmp/x"}, clear=True), \
                patch.object(hunk, "_run", fake):
            self.assertIsNone(hunk.show_working_diff(REPO, "!123", 1))

    def test_viewer_not_installed(self):
        """Checked up front, never discovered by trying: spawning a window for a binary
        that is not there would sit through the whole spawn timeout on every topic, for
        everyone who has tmux and has not installed the viewer."""
        fake = FakeRun()
        with patch.dict(os.environ, {"TMUX": "/x", "CLAUDE_CODE_SESSION_ID": "me"}), \
                patch.object(hunk, "shutil") as sh, \
                patch.object(hunk, "_run", fake):
            sh.which.side_effect = lambda b: None if b == "hunk" else "/usr/bin/tmux"
            self.assertIsNone(hunk.show_working_diff(REPO, "!123", 1))
        self.assertEqual(fake.calls, [], "must not spawn or query anything")

    def test_daemon_unreachable(self):
        self.assertIsNone(self.show(FakeRun(list_ok=False)))

    def test_reload_failed(self):
        fake = FakeRun([session("mine", pane="%4")], owners={"%4": "me"}, reload_ok=False)
        self.assertIsNone(self.show(fake))

    def test_reload_loaded_a_different_number_of_files(self):
        """The check that makes the pointer trustworthy: hunk reads the working tree
        itself, so its count and the caller's must agree or the window is not showing
        what the message says it is."""
        fake = FakeRun([session("mine", pane="%4")], owners={"%4": "me"}, reload_files=5)
        self.assertIsNone(self.show(fake, expect_files=2))

    def test_empty_diff_is_not_mistaken_for_a_loaded_one(self):
        """A clean tree reloads to zero files. Expecting two and getting none is the same
        failure as any other mismatch, and must not pass just because zero is falsy."""
        fake = FakeRun([session("mine", pane="%4")], owners={"%4": "me"}, reload_files=0)
        self.assertIsNone(self.show(fake, expect_files=2))

    def test_same_files_but_different_line_counts(self):
        """The hole the file count alone left open. Five files in the summary and five in
        the window agree whatever is in them, so a file saved between the two reads — or an
        edit that landed after the diff was captured — used to get a confident pointer."""
        fake = FakeRun([session("mine", pane="%4")], owners={"%4": "me"}, reload_files=2,
                       loaded=[("f0.py", 1, 0), ("f1.py", 9, 9)])
        self.assertIsNone(self.show(fake, expect_files=2))

    def test_same_counts_but_different_files(self):
        fake = FakeRun([session("mine", pane="%4")], owners={"%4": "me"}, reload_files=2,
                       loaded=[("f0.py", 1, 0), ("elsewhere.py", 1, 0)])
        self.assertIsNone(self.show(fake, expect_files=2))

    def test_the_two_sides_may_order_their_files_differently(self):
        """Ordering is the viewer's business, not evidence of a different diff — comparing
        unsorted would refuse a window holding exactly the right change."""
        fake = FakeRun([session("mine", pane="%4")], owners={"%4": "me"}, reload_files=2,
                       loaded=[("f1.py", 1, 0), ("f0.py", 1, 0)])
        self.assertEqual(self.show(fake, expect_files=2), ("mine", "9 (!123)"))

    def test_a_viewer_that_cannot_report_what_it_holds_is_refused(self):
        """No evidence is not the same as agreement: an older viewer without
        `session review` loses the window, which is a worse read, not a wrong one."""
        fake = FakeRun([session("mine", pane="%4")], owners={"%4": "me"})
        with patch.object(hunk, "loaded_stat", lambda _s: None):
            self.assertIsNone(self.show(fake))

    def test_window_vanished_between_reload_and_report(self):
        fake = FakeRun([session("mine", pane="%4")], owners={"%4": "me"}, window="")
        self.assertIsNone(self.show(fake))

    def test_viewer_never_registered_after_spawn(self):
        """The window opens, the viewer never reaches the daemon."""
        self.assertIsNone(self.show(FakeRun(new_pane="%9", register=False)))


class TestSucceeds(HunkCase):
    def test_reuses_the_existing_window_and_reports_where(self):
        fake = FakeRun([session("mine", pane="%4")], owners={"%4": "me"}, reload_files=2)
        self.assertEqual(self.show(fake, expect_files=2), ("mine", "9 (!123)"))
        self.assertNotIn(["tmux", "new-window"], [c[:2] for c in fake.calls])

    def test_opens_one_when_there_is_none(self):
        fake = FakeRun(new_pane="%9", reload_files=2)
        self.assertEqual(self.show(fake, expect_files=2), ("new", "9 (!123)"))

    def test_the_viewer_is_narrowed_to_gits_view_of_the_tree(self):
        """The viewer lists untracked files, `git diff` does not. Without this the counts
        disagreed the moment the tree held one new file — routine while reworking — and
        the mismatch check fell back to an inline diff every single time."""
        fake = FakeRun([session("mine", pane="%4")], owners={"%4": "me"})
        self.show(fake)
        reload = [c for c in fake.calls if c[:3] == ["hunk", "session", "reload"]][0]
        self.assertIn("--exclude-untracked", reload)

    def test_addresses_the_session_by_id_never_by_repo(self):
        """Two agents in one repo is the whole reason: `--repo` cannot tell them apart."""
        fake = FakeRun([session("mine", pane="%4")], owners={"%4": "me"})
        self.show(fake)
        reloads = [c for c in fake.calls if c[:3] == ["hunk", "session", "reload"]]
        self.assertEqual(reloads[0][3], "mine")
        self.assertNotIn("--repo", reloads[0])


class TestNotes(unittest.TestCase):
    def test_refuses_more_than_the_cap(self):
        notes = [{"filePath": "a.py", "newLine": i, "summary": "x"} for i in range(4)]
        with self.assertRaises(ValueError) as cm:
            hunk.add_notes("s1", notes)
        self.assertIn("at most 3", str(cm.exception))

    def test_the_cap_itself_is_allowed(self):
        notes = [{"filePath": "a.py", "newLine": i, "summary": "x"} for i in range(3)]
        with patch.object(hunk, "_run", return_value=(0, "")) as run:
            self.assertEqual(hunk.add_notes("s1", notes), 3)
        self.assertIn("--stdin", run.call_args[0][0])

    def test_notes_are_sent_as_one_batch_on_stdin(self):
        """One `comment apply` for all of them: the viewer validates the whole batch before
        it mutates anything, so a bad anchor cannot leave half the notes applied."""
        notes = [{"filePath": "a.py", "newLine": 1, "summary": "x"},
                 {"filePath": "a.py", "newLine": 2, "summary": "y"}]
        with patch.object(hunk, "_run", return_value=(0, "")) as run:
            hunk.add_notes("s1", notes)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(json.loads(run.call_args.kwargs["stdin"])["comments"], notes)

    def test_a_refused_batch_reports_nothing_applied(self):
        with patch.object(hunk, "_run", return_value=(1, "")):
            self.assertEqual(hunk.add_notes("s1", [{"filePath": "a.py", "newLine": 1,
                                                    "summary": "x"}]), 0)

    def test_no_notes_makes_no_call(self):
        with patch.object(hunk, "_run") as run:
            self.assertEqual(hunk.add_notes("s1", []), 0)
            run.assert_not_called()

    def test_user_notes_are_read_back_with_their_anchor(self):
        """The payload here is CAPTURED from `hunk session comment list --type all --json`,
        not written from the docs. An invented fixture is what hid the bug this replaces:
        `comment add` TAKES `newLine`/`summary`, so a hand-made fixture naturally used
        those names, the parser was written to match it, and both agreed with each other
        while disagreeing with the CLI — which returns `newRange`/`body`. Every comment
        came back with a line number of None, and the test passed."""
        import json as _json

        def run(argv, timeout=None, stdin=None):
            return 0, _json.dumps({"comments": [
                {"noteId": "mcp:abc", "source": "user", "filePath": "a.tsx",
                 "hunkIndex": 0, "newRange": [42, 42], "body": " why not keep the map? ",
                 "createdAt": "2026-09-17T08:06:20.015Z", "editable": True},
                {"noteId": "mcp:def", "source": "user", "filePath": "b.tsx",
                 "hunkIndex": 1, "oldRange": [7, 9], "body": "this one",
                 "createdAt": "2026-09-17T08:07:00.000Z", "editable": True}]})
        with patch.object(hunk, "_run", run):
            self.assertEqual(hunk.user_notes("s1"),
                             [("a.tsx", "new", 42, "why not keep the map?", "mcp:abc"),
                              ("b.tsx", "old", 7, "this one", "mcp:def")])

    def test_a_note_on_a_removed_line_keeps_which_side_it_is_on(self):
        """Flattened to a bare number it is indistinguishable from a new-side anchor, and
        the model is told that output is its only copy — so it edits that line number in
        the file AFTER the change, where the deletion has put unrelated code."""
        import json as _json
        with patch.object(hunk, "_run", lambda *a, **k: (0, _json.dumps({"comments": [
                {"noteId": "n1", "filePath": "b.tsx", "oldRange": [7, 9],
                 "body": "why drop this?"}]}))):
            self.assertEqual(hunk.user_notes("s1")[0][1], "old")

    def test_no_user_notes_reads_as_an_empty_list(self):
        import json as _json
        with patch.object(hunk, "_run", lambda *a, **k: (0, _json.dumps({"comments": []}))):
            self.assertEqual(hunk.user_notes("s1"), [])

    def test_a_viewer_that_cannot_be_asked_is_not_an_empty_window(self):
        """The one that matters. Callers read [] as "nothing to answer, push it", so a
        daemon that timed out, a mangled reply or a vanished session must come back as
        None — otherwise a user who wrote "don't push this yet" is overruled by a hiccup."""
        for bad in [(1, ""), (0, "not json"), (0, "{}"), (0, '{"comments": null}')]:
            with patch.object(hunk, "_run", lambda *a, **k: bad):
                self.assertIsNone(hunk.user_notes("s1"), bad)

    def test_dropping_a_note_is_a_call_of_its_own(self):
        """Separate from the read on purpose: the caller prints first and drops after, so
        a note is never destroyed before it has actually reached the model."""
        with patch.object(hunk, "_run", return_value=(0, "")) as run:
            self.assertTrue(hunk.drop_note("s1", "mcp:abc"))
        self.assertEqual(run.call_args[0][0],
                         ["hunk", "session", "comment", "rm", "s1", "mcp:abc", "--json"])

    def test_reading_notes_never_deletes_anything(self):
        """Mutation-proofed: the test this replaces passed even with the delete loop moved
        ahead of the parse, because it only watched the order of calls that did happen."""
        import json as _json
        calls = []

        def run(argv, timeout=None, stdin=None):
            calls.append(argv[3])
            return 0, _json.dumps({"comments": [
                {"noteId": "n1", "filePath": "a.py", "newRange": [1, 1], "body": "x"}]})
        with patch.object(hunk, "_run", run):
            hunk.user_notes("s1")
        self.assertEqual(calls, ["list"])

    def test_a_refused_drop_says_so(self):
        """Left in the window it is reported once more, which is the harmless direction."""
        with patch.object(hunk, "_run", return_value=(1, "")):
            self.assertFalse(hunk.drop_note("s1", "n1"))

    def test_a_note_with_no_id_is_not_dropped_blindly(self):
        with patch.object(hunk, "_run") as run:
            self.assertFalse(hunk.drop_note("s1", None))
            run.assert_not_called()


class TestAgentNotesAreResetPerRound(unittest.TestCase):
    """The window outlives the diff in it. Notes from the round before describe code that
    has been rewritten since, and the viewer re-anchors them by line number — so they end
    up pointing confidently at whatever now sits on that line."""

    def test_showing_a_diff_clears_the_agents_earlier_notes(self):
        fake = FakeRun([session("mine", REPO, "%3")], owners={"%3": "me"})
        with patch.object(hunk, "_run", fake):
            self.assertIsNotNone(HunkCase.show(self, fake))
        self.assertIn(["hunk", "session", "comment", "clear", "mine", "--yes", "--json"],
                      fake.calls)

    def test_the_users_own_notes_survive_it(self):
        """Cleared here they would take a note left while the model was working — after its
        last read, before this reload — with them, unseen."""
        fake = FakeRun([session("mine", REPO, "%3")], owners={"%3": "me"})
        with patch.object(hunk, "_run", fake):
            HunkCase.show(self, fake)
        clears = [c for c in fake.calls if c[3:4] == ["clear"]]
        self.assertTrue(clears)
        for c in clears:
            self.assertNotIn("--include-user", c)
            self.assertNotIn("--all", c)

    def test_the_clear_precedes_the_reload(self):
        """Notes are addressed by id, and a reload can drop one whose line no longer
        exists — leaving nothing to clear and the note on screen."""
        fake = FakeRun([session("mine", REPO, "%3")], owners={"%3": "me"})
        with patch.object(hunk, "_run", fake):
            HunkCase.show(self, fake)
        cleared = next(i for i, c in enumerate(fake.calls) if c[3:4] == ["clear"])
        reloaded = next(i for i, c in enumerate(fake.calls) if c[2:3] == ["reload"])
        self.assertLess(cleared, reloaded)


class TestWindowPlacement(unittest.TestCase):
    """Where the viewer opens. The two windows are read alternately — switch over, read the
    diff, switch back — so the far end of a busy window list is the wrong place for it."""

    def spawn(self, fake, **env):
        with patch.dict(os.environ, env, clear=False):
            with patch.object(hunk, "_run", fake):
                return hunk.spawn_session(REPO, "!123", "me", sleep=lambda _s: None)

    def new_window(self, fake):
        return next(c for c in fake.calls if c[:2] == ["tmux", "new-window"])

    def test_it_opens_directly_right_of_the_agents_own_window(self):
        """Targeted by WINDOW id, never by the agent's pane: `new-window -t` names the
        index to create at and refuses a pane outright ("can't specify pane here"), which
        every other `-t` in this module does accept."""
        fake = FakeRun(new_pane="%9", window_ids={"%2": "@7"})
        self.spawn(fake, TMUX_PANE="%2")
        argv = self.new_window(fake)
        self.assertEqual(argv[argv.index("-a") + 1:argv.index("-a") + 3], ["-t", "@7"])

    def test_an_unknown_pane_leaves_the_placement_to_tmux(self):
        """Better a window at the far end than one placed against a guessed target."""
        fake = FakeRun(new_pane="%9")
        with patch.dict(os.environ, {}, clear=True):
            with patch.object(hunk, "_run", fake):
                hunk.spawn_session(REPO, "!123", "me", sleep=lambda _s: None)
        self.assertNotIn("-a", self.new_window(fake))

    def test_a_pane_tmux_will_not_resolve_leaves_the_placement_to_tmux(self):
        """`-t ""` would be an error, and an error here costs the whole viewer."""
        fake = FakeRun(new_pane="%9", window_ids={})
        self.spawn(fake, TMUX_PANE="%2")
        self.assertNotIn("-a", self.new_window(fake))


class TestClosing(unittest.TestCase):
    """The window's life ends with the push: the rebase leaves the tree clean, so what is
    loaded in it is a diff that no longer exists anywhere."""

    def test_closes_the_window_of_a_session_this_agent_owns(self):
        fake = FakeRun([session("mine", REPO, "%3")], owners={"%3": "me"})
        with patch.object(hunk, "_run", fake):
            self.assertTrue(hunk.close_session("mine", "me"))
        self.assertIn(["tmux", "kill-window", "-t", "%3"], fake.calls)

    def test_refuses_a_window_this_agent_does_not_own(self):
        """The one operation here that destroys something, pointed by a session id that
        could be out of date — at the user's own viewer, or another agent's."""
        fake = FakeRun([session("theirs", REPO, "%3")], owners={"%3": "someone-else"})
        with patch.object(hunk, "_run", fake):
            self.assertFalse(hunk.close_session("theirs", "me"))
        self.assertNotIn("kill-window", [c[1] for c in fake.calls if len(c) > 1])

    def test_an_agent_with_no_identity_closes_nothing(self):
        fake = FakeRun([session("mine", REPO, "%3")], owners={"%3": ""})
        with patch.object(hunk, "_run", fake):
            self.assertFalse(hunk.close_session("mine", ""))
        self.assertNotIn("kill-window", [c[1] for c in fake.calls if len(c) > 1])

    def test_a_session_already_gone_is_not_an_error(self):
        fake = FakeRun([], owners={})
        with patch.object(hunk, "_run", fake):
            self.assertFalse(hunk.close_session("mine", "me"))


class TestDegradesQuietly(unittest.TestCase):
    """`_run` is the only place a missing binary or a hung daemon can surface, and it must
    never raise into a skill's render path — the diff still has to reach the user."""

    def test_missing_binary(self):
        self.assertEqual(hunk._run(["definitely-not-a-real-binary-xyz"]), (1, ""))

    def test_unparseable_json_is_not_a_crash(self):
        with patch.object(hunk, "_run", lambda *a, **k: (0, "not json")):
            self.assertEqual(hunk.sessions(), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
