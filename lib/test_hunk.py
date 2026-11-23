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
import re
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib import hunk                                   # noqa: E402

REPO = "/w/acme-api"
# What a skill names its viewer after: the title of what it shows, and the plain fact to
# show until the title's short form is known (lib/tab.py `viewer`).
NAME = ("Retry the payment call with backoff", "t3")


KEEP_DIR = hunk.keep_dir
MARK_READ_DIR = hunk.mark_read_dir


def setUpModule():
    """Never read the machine's own Claude Code session records: in a background session
    they would decide which pane these tests see. Nor the notes the keep-notes extension
    saved on this machine: they would add notes to every read, and calls to the fakes. Nor
    write to the read marks hunk-mark-read keeps there."""
    for stub in (patch.object(hunk.tmux, "_records", lambda: []),
                 patch.object(hunk, "keep_dir", lambda: None),
                 patch.object(hunk, "mark_read_dir", lambda: os.devnull),
                 # No short title this machine's model wrote decides a window's name here.
                 patch.object(hunk.tab, "CACHE", os.devnull),
                 patch.object(hunk.tab, "spawn", lambda *a, **k: None)):
        stub.start()
        unittest.addModuleCleanup(stub.stop)


def stat_of(n):
    """A caller's [(path, added, removed)] for a diff of `n` files — the shape
    `show_working_diff` now compares against, rather than a bare count."""
    return [(f"f{i}.py", 1, 0) for i in range(n)]


def session(sid="s1", repo=REPO, pane="%4"):
    loc = [{"source": "tty", "tty": "/dev/ttys004"}]
    if pane:
        loc.append({"source": "tmux", "paneId": pane})
    return {"sessionId": sid, "repoRoot": repo, "terminal": {"locations": loc}}



# Also in skills/rework-mr/scripts/test_threads.py, where diff-view's tests use it.
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


class TestDiffStat(unittest.TestCase):
    """The per-file counts are what lets a small change be approved without leaving the
    chat, so they have to be right for the shapes git actually emits."""

    def test_counts_per_file_ignoring_the_headers(self):
        self.assertEqual(hunk.diff_stat(SAMPLE_DIFF)[0], ("src/a.ts", 2, 1))

    def test_a_rename_is_reported_under_its_new_path(self):
        self.assertEqual(hunk.diff_stat(SAMPLE_DIFF)[1], ("new/n.ts", 1, 1))

    def test_a_name_git_quotes_is_reported_as_the_file_is_named(self):
        """git C-quotes a non-ASCII or special name in the headers, and the quoted text
        was taken for the name, which then matched no file the viewer lists."""
        diff = ('diff --git "a/caf\\303\\251 \\"x\\".txt" "b/caf\\303\\251 \\"x\\".txt"\n'
                'index 1..2 100644\n'
                '--- "a/caf\\303\\251 \\"x\\".txt"\n'
                '+++ "b/caf\\303\\251 \\"x\\".txt"\n'
                '@@ -1 +1 @@\n-a\n+b\n')
        self.assertEqual(hunk.diff_stat(diff), [('café "x".txt', 1, 1)])

    def test_a_conflicted_path_is_still_counted_as_a_file(self):
        """`git diff` emits `diff --cc` for an unmerged path, which this skill produces
        itself the moment a `rebase --autosquash` fixup conflicts. Unrecognised, the stanza
        was not a file boundary: it disappeared from the summary and its own headers were
        charged to the previous file — '1 file, +3 −3' over a two-file change."""
        d = (
            "diff --git a/ok.ts b/ok.ts\n--- a/ok.ts\n+++ b/ok.ts\n@@ -1 +1 @@\n-a\n+b\n"
            "diff --cc conf.ts\n--- a/conf.ts\n+++ b/conf.ts\n@@@ -1,1 -1,1 +1,1 @@@\n"
        )
        self.assertEqual([p for p, _, _ in hunk.diff_stat(d)], ["ok.ts", "conf.ts"])
        self.assertEqual(hunk.diff_stat(d)[0], ("ok.ts", 1, 1))

    def test_a_deletion_keeps_the_old_path(self):
        """Its `+++` side is /dev/null, so the `---` name is all there is."""
        self.assertEqual(hunk.diff_stat(SAMPLE_DIFF)[2], ("gone.ts", 0, 2))

    def test_a_repo_with_noprefix_configured(self):
        """`diff.noprefix` drops the a//b/ prefixes; the path is still the header's."""
        d = "diff --git x.ts x.ts\n--- x.ts\n+++ x.ts\n@@ -1 +1 @@\n-a\n+b\n"
        self.assertEqual(hunk.diff_stat(d), [("x.ts", 1, 1)])

    def test_a_binary_file_counts_no_lines(self):
        d = "diff --git a/i.png b/i.png\nBinary files a/i.png and b/i.png differ\n"
        self.assertEqual(hunk.diff_stat(d), [("i.png", 0, 0)])

    def test_an_empty_diff_has_no_files(self):
        self.assertEqual(hunk.diff_stat(""), [])

    def test_content_that_looks_like_a_header_is_counted_not_obeyed(self):
        """Diffing a file that itself contains a patch — a fixture, this repo's own docs —
        produces body lines beginning `+++ ` and `--- `. They are CONTENT: they must count
        toward the totals and must not rename the file. Headers only exist before `@@`."""
        d = (
            "diff --git a/doc.md b/doc.md\n"
            "--- a/doc.md\n"
            "+++ b/doc.md\n"
            "@@ -1,2 +1,3 @@\n"
            " intro\n"
            "+--- a/not-a-header.py\n"
            "+++ b/not-a-header.py\n"
        )
        self.assertEqual(hunk.diff_stat(d), [("doc.md", 2, 0)])


class FakeRun:
    """Stands in for `hunk._run`, dispatching on the shape of the command.

    `owners` maps pane -> owner mark; a pane absent from it answers the way tmux does for
    an unset option (non-zero, no value), which is what "somebody else's window" looks
    like from here.
    """

    def __init__(self, sessions=(), owners=None, new_pane="%9", window="9 (!123)",
                 reload_files=1, reload_ok=True, list_ok=True, register=True,
                 window_ids=None, loaded=None, new_repo=REPO, named=False, opts=None):
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
        # What a spawned session reports as its repository: a patch window has none.
        self.new_repo = new_repo
        # pane -> its window's name and options (tmux's own and lib/tab.py's). `named`: the
        # window's name is read from there, for the tests about it; else every window
        # answers `window`.
        self.opts = {p: dict(o) for p, o in (opts or {}).items()}
        self.named = named
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
            if argv[-1] != hunk.OWNER_OPT:
                got = self.opts.get(pane, {}).get(argv[-1])
                return (0, got) if got is not None else (1, "")
            return (0, self.owners[pane]) if pane in self.owners else (1, "")
        if argv[:2] == ["tmux", "new-window"]:
            if self.register:
                self.sessions.append(session("new", self.new_repo, self.new_pane))
            # `-n` names the window and turns tmux's own naming of it off.
            self.opts[self.new_pane] = {"window_name": argv[argv.index("-n") + 1],
                                        "automatic-rename": "0"}
            return 0, self.new_pane + "\n"
        if argv[:2] == ["tmux", "set-option"]:
            pane = argv[argv.index("-t") + 1]
            if hunk.OWNER_OPT in argv:
                self.owners[pane] = argv[-1]
            elif "-wu" in argv:
                self.opts.get(pane, {}).pop(argv[-1], None)
            else:
                self.opts.setdefault(pane, {})[argv[-2]] = argv[-1]
            return 0, ""
        if argv[:2] == ["tmux", "rename-window"]:
            pane = argv[argv.index("-t") + 1]
            self.opts.setdefault(pane, {}).update(
                {"window_name": argv[-1], "automatic-rename": "0"})
            return 0, ""
        if argv[:2] == ["tmux", "kill-window"]:
            return 0, ""
        if argv[:2] == ["tmux", "display-message"]:
            # Two callers, told apart by their format: the window id `new-window -t` needs,
            # and the "9 (!123)" the user is pointed at.
            if argv[-1] == "#{window_id}":
                return (0, self.window_ids[argv[-2]] + "\n") \
                    if argv[-2] in self.window_ids else (1, "")
            pane = argv[argv.index("-t") + 1]
            if self.named and pane in self.opts:
                # A window as lib/tab.py reads it: its name and options, "9" its index.
                out = argv[-1].replace("#{window_index}", "9")
                for k, v in self.opts[pane].items():
                    out = out.replace("#{" + k + "}", v)
                return 0, re.sub(r"#\{[^}]*\}", "", out) + "\n"
            return 0, self.window + "\n"
        return 1, ""


class HunkCase(unittest.TestCase):
    def show(self, fake, expect_files=1, name=NAME, **kw):
        """`installed` is forced rather than left to the machine: the viewer is optional
        and most machines running this suite — CI, anyone who cloned the repo — will not
        have it, which would otherwise turn every success case here into a failure that
        says nothing about the code."""
        with patch.dict(os.environ, {"TMUX": "/tmp/x", "CLAUDE_CODE_SESSION_ID": "me"}), \
                patch.object(hunk, "installed", lambda: True), \
                patch.object(hunk, "_run", fake):
            return hunk.show_working_diff(REPO, name, stat_of(expect_files),
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
            self.assertIsNone(hunk.show_working_diff(REPO, NAME, 1))

    def test_no_agent_identity(self):
        fake = FakeRun()
        with patch.dict(os.environ, {"TMUX": "/tmp/x"}, clear=True), \
                patch.object(hunk, "_run", fake):
            self.assertIsNone(hunk.show_working_diff(REPO, NAME, 1))

    def test_viewer_not_installed(self):
        """Checked up front, never discovered by trying: spawning a window for a binary
        that is not there would sit through the whole spawn timeout on every topic, for
        everyone who has tmux and has not installed the viewer."""
        fake = FakeRun()
        with patch.dict(os.environ, {"TMUX": "/x", "CLAUDE_CODE_SESSION_ID": "me"}), \
                patch.object(hunk, "shutil") as sh, \
                patch.object(hunk, "_run", fake):
            sh.which.side_effect = lambda b: None if b == "hunk" else "/usr/bin/tmux"
            self.assertIsNone(hunk.show_working_diff(REPO, NAME, 1))
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

    def test_the_viewer_loads_the_change_against_head_staged_or_not(self):
        """The callers count `git diff HEAD`. A plain `hunk diff` is the unstaged changes
        alone: a fix the agent had staged loaded as no file at all."""
        fake = FakeRun([session("mine", pane="%4")], owners={"%4": "me"})
        self.show(fake)
        reload = [c for c in fake.calls if c[:3] == ["hunk", "session", "reload"]][0]
        self.assertEqual(reload[reload.index("--") + 1:reload.index("--") + 3], ["diff", "HEAD"])

    def test_addresses_the_session_by_id_never_by_repo(self):
        """Two agents in one repo is the whole reason: `--repo` cannot tell them apart."""
        fake = FakeRun([session("mine", pane="%4")], owners={"%4": "me"})
        self.show(fake)
        reloads = [c for c in fake.calls if c[:3] == ["hunk", "session", "reload"]]
        self.assertEqual(reloads[0][3], "mine")
        self.assertNotIn("--repo", reloads[0])


class TestWindowName(HunkCase):
    """The window is named after what each load put in it (lib/tab.py `viewer`): the short
    title where known, the topic's id until then, never the name of what it held before;
    and never a name the user gave it by hand."""
    V = hunk.tab.VIEWER_ICON
    T5 = ("Add feature flag for offline viewing mode", "t5")

    def setUp(self):
        env = patch.dict(os.environ)          # the runner's AGENT_TAB_OFF would keep the model
        env.start()                           # from being asked; what is asked is faked here
        self.addCleanup(env.stop)
        os.environ.pop(hunk.tab.OFF_ENV, None)
        self.later = patch.object(hunk.tab, "spawn").start()
        self.addCleanup(patch.stopall)

    def ours(self, name):
        """The agent's viewer, named `name` by an earlier load."""
        return {"window_name": name, "automatic-rename": "0", hunk.tab.SET_OPT: name}

    def test_a_new_window_opens_with_the_name_of_its_first_load(self):
        fake = FakeRun(new_pane="%9", named=True)
        self.assertEqual(self.show(fake, name=NAME), ("new", f"9 ({self.V} t3)"))
        spawn = next(c for c in fake.calls if c[:2] == ["tmux", "new-window"])
        self.assertEqual(spawn[spawn.index("-n") + 1], f"{self.V} t3")
        self.assertEqual(fake.opts["%9"][hunk.tab.WANT_OPT], NAME[0])  # the model is asked
        self.later.assert_called_once()

    def test_a_reload_to_another_topic_is_renamed_at_once(self):
        fake = FakeRun([session("mine", pane="%4")], owners={"%4": "me"}, named=True,
                       opts={"%4": self.ours(f"{self.V} payment retry")})
        self.assertEqual(self.show(fake, name=self.T5), ("mine", f"9 ({self.V} t5)"))
        self.later.assert_called_once()
        with patch.object(hunk.tab, "cached", lambda t: "offline flag" if t == self.T5[0] else None):
            self.assertEqual(self.show(fake, name=self.T5), ("mine", f"9 ({self.V} offline flag)"))

    def test_a_patch_window_is_renamed_the_same_way(self):
        loaded = hunk.diff_stat(SAMPLE_DIFF)
        fake = FakeRun([session("mine", repo=None, pane="%4")], owners={"%4": "me"}, named=True,
                       opts={"%4": self.ours(f"{self.V} payment retry")},
                       reload_files=len(loaded), loaded=loaded)
        with tempfile.NamedTemporaryFile("w", suffix=".patch", delete=False) as fh:
            fh.write(SAMPLE_DIFF)
        self.addCleanup(os.unlink, fh.name)
        with patch.dict(os.environ, {"TMUX": "/tmp/x", "CLAUDE_CODE_SESSION_ID": "me"}), \
                patch.object(hunk, "installed", lambda: True), patch.object(hunk, "_run", fake):
            self.assertEqual(hunk.show_view(["patch", fh.name], loaded, self.T5, "/w"),
                             ("mine", f"9 ({self.V} t5)"))

    def test_a_window_the_user_renamed_keeps_their_name(self):
        renamed = dict(self.ours(f"{self.V} payment retry"), window_name="logs")
        fake = FakeRun([session("mine", pane="%4")], owners={"%4": "me"}, named=True,
                       opts={"%4": renamed})
        self.assertEqual(self.show(fake, name=self.T5), ("mine", "9 (logs)"))
        self.later.assert_not_called()

    def test_a_reload_outside_a_show_renames_too(self):
        fake = FakeRun([session("mine", pane="%4")], owners={"%4": "me"}, named=True,
                       opts={"%4": self.ours(f"{self.V} payment retry")})
        with patch.object(hunk, "_run", fake):
            self.assertTrue(hunk.rename("mine", self.T5))
            self.assertFalse(hunk.rename("gone", self.T5))
        self.assertEqual(fake.opts["%4"]["window_name"], f"{self.V} t5")


class TestShowPatch(unittest.TestCase):
    """The view window holding a patch file, which has no repository."""

    PATCH = SAMPLE_DIFF

    def show(self, fake):
        with tempfile.NamedTemporaryFile("w", suffix=".patch", delete=False) as fh:
            fh.write(self.PATCH)
        self.addCleanup(os.unlink, fh.name)
        with patch.dict(os.environ, {"TMUX": "/tmp/x", "CLAUDE_CODE_SESSION_ID": "me"}), \
                patch.object(hunk, "installed", lambda: True), \
                patch.object(hunk, "_run", fake):
            return hunk.show_view(["patch", fh.name], self.loaded(), NAME, "/w",
                                  sleep=lambda _s: None), fh.name

    def loaded(self):
        return hunk.diff_stat(self.PATCH)

    def test_opens_a_patch_window_when_there_is_none(self):
        fake = FakeRun(new_repo=None, loaded=self.loaded())
        shown, path = self.show(fake)
        self.assertEqual(shown, ("new", "9 (!123)"))
        spawn = [c for c in fake.calls if c[:2] == ["tmux", "new-window"]][0]
        self.assertEqual(spawn[-1], f"hunk patch {path}")

    def test_a_refused_reload_opens_a_new_window(self):
        """The viewer reloads a patch only from inside the repository its window started in,
        and the skills keep their patches outside one."""
        fake = FakeRun([session("mine", repo=None, pane="%4")], owners={"%4": "me"},
                       reload_ok=False, new_repo=None, loaded=self.loaded())
        shown, _path = self.show(fake)
        self.assertEqual(shown, ("new", "9 (!123)"))
        self.assertIn(["tmux", "kill-window", "-t", "%4"], fake.calls)

    def test_a_refused_reload_of_a_window_not_its_own_opens_nothing(self):
        """Closing a window is checked against its owner mark again; one that fails it is
        neither closed nor replaced by a second window beside it."""
        fake = FakeRun([session("mine", repo=None, pane="%4")], owners={"%4": "me"},
                       reload_ok=False, new_repo=None, loaded=self.loaded())
        with patch.object(hunk, "close_session", lambda _sid, _owner: False):
            shown, _path = self.show(fake)
        self.assertIsNone(shown)
        self.assertFalse([c for c in fake.calls if c[:2] == ["tmux", "new-window"]])

    def test_reloads_its_own_patch_window_with_the_new_patch(self):
        fake = FakeRun([session("mine", repo=None, pane="%4")], owners={"%4": "me"},
                       reload_files=len(self.loaded()), loaded=self.loaded())
        shown, path = self.show(fake)
        self.assertEqual(shown, ("mine", "9 (!123)"))
        reload = [c for c in fake.calls if c[:3] == ["hunk", "session", "reload"]][0]
        self.assertEqual(reload[-2:], ["patch", path])

    def test_never_takes_over_a_working_diff_window(self):
        """rework-mr's window of the same agent has a repository; it is not this one."""
        fake = FakeRun([session("rework", repo=REPO, pane="%4")], owners={"%4": "me"},
                       new_repo=None, loaded=self.loaded())
        self.assertEqual(self.show(fake)[0], ("new", "9 (!123)"))

    def test_a_window_holding_anything_else_is_refused(self):
        fake = FakeRun(new_repo=None, loaded=[("src/a.ts", 9, 9)])
        self.assertIsNone(self.show(fake)[0])

    def test_outside_tmux_it_is_inline(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(hunk.show_view(["patch", "/nonexistent"], self.loaded(), NAME, "/w"))


class TestShowView(unittest.TestCase):
    """The agent's view window: a commit range of a repository, or a patch, one window per
    agent, reloaded in place where the viewer can take the new input there."""

    RANGE = ["diff", "a1..b2", "--", "src/a.ts"]
    STAT = [("src/a.ts", 2, 1)]

    def show(self, fake, args=RANGE, cwd=REPO):
        with patch.dict(os.environ, {"TMUX": "/tmp/x", "CLAUDE_CODE_SESSION_ID": "me"}), \
                patch.object(hunk, "installed", lambda: True), patch.object(hunk, "_run", fake):
            return hunk.show_view(args, self.STAT, NAME, cwd, sleep=lambda _s: None)

    @staticmethod
    def spawned(fake):
        return [c for c in fake.calls if c[:2] == ["tmux", "new-window"]]

    @staticmethod
    def reloads(fake):
        return [c for c in fake.calls if c[:3] == ["hunk", "session", "reload"]]

    def test_a_range_opens_in_the_repository_marked_as_a_view(self):
        fake = FakeRun(loaded=self.STAT)
        self.assertEqual(self.show(fake), ("new", "9 (!123)"))
        (spawn,) = self.spawned(fake)
        self.assertEqual(spawn[-3:], ["-c", REPO, "hunk diff a1..b2 -- src/a.ts"])
        self.assertIn(f"{hunk.KEEP_TAG_ENV}=me{hunk.VIEW_TAG}", spawn)
        self.assertEqual(fake.opts["%9"][hunk.VIEW_OPT], "1")

    def test_the_next_range_reloads_the_same_window(self):
        fake = FakeRun([session("mine", pane="%4")], owners={"%4": "me"},
                       opts={"%4": {hunk.VIEW_OPT: "1"}}, loaded=self.STAT)
        self.assertEqual(self.show(fake), ("mine", "9 (!123)"))
        self.assertEqual(self.reloads(fake)[0][-4:], self.RANGE)
        self.assertEqual(self.spawned(fake), [])

    def test_a_patch_window_gives_way_to_a_range_without_a_doomed_reload(self):
        """The viewer never reloads a patch window to repository input; it is closed and
        the range opens in a window of its own, so the agent never has two."""
        fake = FakeRun([session("mine", repo=None, pane="%4")], owners={"%4": "me"},
                       loaded=self.STAT)
        self.assertEqual(self.show(fake), ("new", "9 (!123)"))
        self.assertEqual(self.reloads(fake), [])
        self.assertIn(["tmux", "kill-window", "-t", "%4"], fake.calls)

    def test_a_range_window_gives_way_to_a_patch(self):
        fake = FakeRun([session("mine", pane="%4")], owners={"%4": "me"},
                       opts={"%4": {hunk.VIEW_OPT: "1"}}, new_repo=None, loaded=self.STAT)
        self.assertEqual(self.show(fake, args=["patch", "/s/diff-t2.patch"]), ("new", "9 (!123)"))
        self.assertIn(["tmux", "kill-window", "-t", "%4"], fake.calls)
        self.assertEqual(self.spawned(fake)[0][-1], "hunk patch /s/diff-t2.patch")

    def test_a_range_of_another_repository_opens_anew(self):
        fake = FakeRun([session("mine", repo="/w/elsewhere", pane="%4")], owners={"%4": "me"},
                       opts={"%4": {hunk.VIEW_OPT: "1"}}, loaded=self.STAT)
        self.assertEqual(self.show(fake), ("new", "9 (!123)"))
        self.assertEqual(self.reloads(fake), [])
        self.assertIn(["tmux", "kill-window", "-t", "%4"], fake.calls)

    def test_the_working_diff_window_of_the_same_checkout_is_left_alone(self):
        """rework-mr's working diff is in the same repository as its pushed fix: taking it
        over would swap the diff a push is about to be approved against."""
        fake = FakeRun([session("work", pane="%4")], owners={"%4": "me"}, loaded=self.STAT)
        self.assertEqual(self.show(fake), ("new", "9 (!123)"))
        self.assertFalse([c for c in fake.calls if "%4" in c and c[:2] != ["tmux", "show-options"]])

    def test_the_working_diff_lookup_never_finds_the_view(self):
        fake = FakeRun([session("view", pane="%4")], owners={"%4": "me"},
                       opts={"%4": {hunk.VIEW_OPT: "1"}})
        with patch.object(hunk, "_run", fake):
            self.assertIsNone(hunk.find_session(REPO, "me"))
            self.assertEqual(hunk.find_view("me"), ("view", REPO))

    def test_a_window_the_user_opened_in_the_worktree_is_never_touched(self):
        fake = FakeRun([session("theirs", pane="%4")], owners={},
                       opts={"%4": {hunk.VIEW_OPT: "1"}}, loaded=self.STAT)
        self.assertEqual(self.show(fake), ("new", "9 (!123)"))
        self.assertFalse([c for c in fake.calls if "%4" in c and c[:2] != ["tmux", "show-options"]])
        self.assertFalse([c for c in fake.calls if "theirs" in c])

    def test_a_window_holding_anything_else_after_the_reload_is_refused(self):
        fake = FakeRun([session("mine", pane="%4")], owners={"%4": "me"},
                       opts={"%4": {hunk.VIEW_OPT: "1"}}, loaded=[("src/a.ts", 3, 1)])
        self.assertIsNone(self.show(fake))

    def test_nothing_to_show_opens_nothing(self):
        fake = FakeRun()
        with patch.dict(os.environ, {"TMUX": "/tmp/x", "CLAUDE_CODE_SESSION_ID": "me"}), \
                patch.object(hunk, "installed", lambda: True), patch.object(hunk, "_run", fake):
            self.assertIsNone(hunk.show_view(self.RANGE, [], NAME, REPO))
        self.assertEqual(fake.calls, [])


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


class TestNotesOutliveTheirViewer(unittest.TestCase):
    """The viewer holds notes in memory and forgets them with its window: the copy each read
    leaves on disk is what a note closed over is read from."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = os.path.join(self._tmp.name, "kept", "notes.json")
        self.note = ("a.py", "new", 3, "stop, this is wrong", "n1")

    def test_a_note_seen_in_a_live_viewer_is_there_once_the_viewer_is_gone(self):
        hunk.keep_notes(self.path, "s1", [self.note])
        self.assertEqual(hunk.kept_notes(self.path, but="s1"), [])      # open: read live
        self.assertEqual(hunk.kept_notes(self.path), [self.note])       # closed: from the copy

    def test_the_copy_follows_the_viewer_while_it_is_open(self):
        hunk.keep_notes(self.path, "s1", [self.note])
        hunk.keep_notes(self.path, "s1", [])                            # the user deleted it
        self.assertEqual(hunk.kept_notes(self.path), [])
        self.assertFalse(os.path.exists(self.path))

    def test_a_new_viewer_does_not_replace_what_an_earlier_one_left(self):
        hunk.keep_notes(self.path, "s1", [self.note])
        hunk.keep_notes(self.path, "s2", [])
        self.assertEqual(hunk.kept_notes(self.path, but="s2"), [self.note])

    def test_handed_over_the_copy_goes(self):
        hunk.keep_notes(self.path, "s1", [self.note])
        hunk.forget_notes(self.path)
        self.assertEqual(hunk.kept_notes(self.path), [])
        hunk.forget_notes(self.path)                                    # nothing there: no error

    def test_a_copy_that_cannot_be_read_holds_nothing(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w") as fh:
            fh.write("{not json")
        self.assertEqual(hunk.kept_notes(self.path), [])


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


class TestBackgroundSession(unittest.TestCase):
    """A Claude Code background session has no `TMUX` or `TMUX_PANE`. `lib/tmux.py` finds
    the pane it is shown in when there is one; these pin what this module does with that
    answer, either way."""

    def show(self, fake, pane, **env):
        with patch.dict(os.environ, env, clear=True), \
                patch.object(hunk.tmux, "own_pane", lambda *a, **k: pane), \
                patch.object(hunk, "installed", lambda: True), \
                patch.object(hunk, "_run", fake):
            return hunk.show_working_diff(REPO, NAME, stat_of(1), sleep=lambda _s: None)

    def test_the_window_opens_beside_the_pane_the_session_is_shown_in(self):
        fake = FakeRun(new_pane="%9", window_ids={"%2": "@7"})
        self.assertIsNotNone(self.show(fake, "%2", CLAUDE_CODE_SESSION_ID="me"))
        argv = next(c for c in fake.calls if c[:2] == ["tmux", "new-window"])
        self.assertEqual(argv[argv.index("-a") + 1:argv.index("-a") + 3], ["-t", "@7"])

    def test_the_pane_is_the_identity_when_the_session_id_is_missing(self):
        fake = FakeRun(new_pane="%9", window_ids={"%2": "@7"})
        self.assertIsNotNone(self.show(fake, "%2"))
        self.assertEqual(fake.owners["%9"], "%2")

    def test_no_pane_is_outside_tmux(self):
        fake = FakeRun()
        self.assertIsNone(self.show(fake, "", CLAUDE_CODE_SESSION_ID="me"))
        self.assertEqual(fake.calls, [])


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


class TestOwnReview(HunkCase):
    """`show_review` and the note calls behind self-review, where the author's notes are
    the review itself and must outlive every reload of the window they are in."""

    def review(self, fake, args=("show", "abc")):
        with patch.dict(os.environ, {"TMUX": "/tmp/x", "CLAUDE_CODE_SESSION_ID": "me"}), \
                patch.object(hunk, "installed", lambda: True), \
                patch.object(hunk, "_run", fake):
            return hunk.show_review(REPO, NAME, list(args), stat_of(1),
                                    sleep=lambda _s: None)

    def test_the_review_command_is_passed_through_to_the_reload(self):
        fake = FakeRun([session("s1")], owners={"%4": "me"})
        self.assertEqual(self.review(fake), ("s1", "9 (!123)"))
        reload = [c for c in fake.calls if c[:3] == ["hunk", "session", "reload"]]
        self.assertEqual(reload[0][-3:], ["--", "show", "abc"])

    def test_a_reload_clears_no_notes(self):
        fake = FakeRun([session("s1")], owners={"%4": "me"})
        self.review(fake)
        self.assertFalse([c for c in fake.calls if c[3:5] == ["comment", "clear"]])

    def test_a_window_holding_something_else_is_no_pointer(self):
        fake = FakeRun([session("s1")], owners={"%4": "me"}, loaded=[("other.py", 1, 0)])
        self.assertIsNone(self.review(fake))

    # Captured from `hunk session comment list --type all --json`: a note typed in the
    # viewer, and an agent's reply to it.
    CAPTURED = {"comments": [
        {"noteId": "user:1792053957009-1", "source": "user", "filePath": "f.txt",
         "hunkIndex": 0, "newRange": [3, 3], "body": "why uppercase?", "author": "user",
         "createdAt": "2026-10-15T08:45:57.009Z", "editable": True},
        {"noteId": "mcp:c8c66a3d", "parentId": "user:1792053957009-1", "source": "agent",
         "filePath": "f.txt", "hunkIndex": 0, "newRange": [3, 3], "body": "agent reply",
         "author": "agent", "createdAt": "2026-10-15T08:53:05.339Z", "editable": False}]}

    def test_notes_carry_who_wrote_them_and_what_they_answer(self):
        with patch.object(hunk, "_run", return_value=(0, json.dumps(self.CAPTURED))):
            got = hunk.notes("s1")
        self.assertEqual([(n["id"], n["parent"], n["source"], n["line"]) for n in got],
                         [("user:1792053957009-1", None, "user", 3),
                          ("mcp:c8c66a3d", "user:1792053957009-1", "agent", 3)])

    def test_a_viewer_that_does_not_answer_gives_none_not_an_empty_list(self):
        with patch.object(hunk, "_run", return_value=(1, "")):
            self.assertIsNone(hunk.notes("s1"))

    def test_clearing_everything_includes_the_users_notes(self):
        with patch.object(hunk, "_run", return_value=(0, "")) as run:
            hunk.clear_all_notes("s1")
        self.assertIn("--all", run.call_args[0][0])

    def test_a_created_note_reports_its_id(self):
        made = {"result": {"commentId": "mcp:abc"}}
        with patch.object(hunk, "_run", return_value=(0, json.dumps(made))) as run:
            self.assertEqual(hunk.add_note("s1", "f.py", 7, "text", "you"), "mcp:abc")
        argv = run.call_args[0][0]
        self.assertEqual(argv[argv.index("--new-line") + 1], "7")
        with patch.object(hunk, "_run", return_value=(0, json.dumps(made))):
            self.assertEqual(hunk.reply("s1", "user:1", "text"), "mcp:abc")

    def test_a_note_goes_on_the_side_it_is_given(self):
        with patch.object(hunk, "_run", return_value=(0, "{}")) as run:
            hunk.add_note("s1", "f.py", 7, "text", "agent", side="old")
        self.assertIn("--old-line", run.call_args[0][0])

    def test_a_point_is_a_note_brought_into_view(self):
        """`--focus` moves the view to the note and switches the agent's notes on; a plain
        note (a moved thread) leaves the view where the person has it."""
        made = {"result": {"commentId": "mcp:abc"}}
        with patch.object(hunk, "_run", return_value=(0, json.dumps(made))) as run:
            self.assertEqual(hunk.point("s1", "f.py", 7, "here", "agent"), "mcp:abc")
            self.assertIn("--focus", run.call_args[0][0])
            hunk.add_note("s1", "f.py", 7, "text", "agent")
            self.assertNotIn("--focus", run.call_args[0][0])

    def test_a_point_on_a_file_that_stays_hidden_is_not_made(self):
        shown = []
        with patch.object(hunk, "show_unread", lambda sid, paths, args: shown.append(
                (paths, args)) or False), \
                patch.object(hunk, "_run", side_effect=AssertionError("note added")):
            self.assertIsNone(hunk.point("s1", "f.py", 7, "here", "agent",
                                         args=["patch", "/p.patch"]))
        self.assertEqual(shown, [(["f.py"], ["patch", "/p.patch"])])

    def test_a_refused_note_has_no_id(self):
        with patch.object(hunk, "_run", return_value=(1, "")):
            self.assertIsNone(hunk.add_note("s1", "f.py", 7, "text", "you"))
            self.assertIsNone(hunk.reply("s1", "user:1", "text"))

    def test_a_mark_lives_on_the_sessions_window(self):
        fake = FakeRun([session("s1", pane="%4")], owners={})
        with patch.object(hunk, "_run", fake):
            self.assertTrue(hunk.set_mark("s1", "@shown", "abc"))
            self.assertEqual(hunk.mark("s1", "@shown"), "abc")
            self.assertEqual(hunk.mark("gone", "@shown"), "")

    def test_what_the_window_holds(self):
        got = {"session": {"inputKind": "show", "review": {"revision": "abc"}}}
        with patch.object(hunk, "_run", return_value=(0, json.dumps(got))):
            self.assertEqual(hunk.loaded_input("s1"), ("show", "abc"))
        with patch.object(hunk, "_run", return_value=(0, json.dumps(
                {"session": {"inputKind": "vcs"}}))):
            self.assertEqual(hunk.loaded_input("s1"), ("vcs", None))


class KeptFilesCase(unittest.TestCase):
    """The keep-notes Hunk extension's files in a directory of the test's own. Fixtures
    shaped like what the extension wrote under Hunk 0.25.0, with invented paths and ids."""

    REVIEW = {"sourceLabel": REPO, "loadedAt": 1772359300000, "files": [
        {"path": "f0.py", "additions": 1, "deletions": 0, "hidden": False},
        {"path": "f1.py", "additions": 1, "deletions": 0, "hidden": True}]}

    LAUNCHED = "2026-03-01T10:00:00.400Z"

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        stub = patch.object(hunk, "keep_dir", lambda: self.dir)
        stub.start()
        self.addCleanup(stub.stop)
        # The machine's processes as the test says them: {pid: when it started}, none by
        # default, so a pid that happens to be in use on the machine running the suite is not.
        self.processes = {}
        for name, fake in (("_alive", lambda pid: pid in self.processes),
                           ("_process_start", lambda pid: self.processes.get(pid))):
            stub = patch.object(hunk, name, fake)
            stub.start()
            self.addCleanup(stub.stop)

    STARTED = hunk._seconds("2026-03-01T10:00:00.100Z")     # what `write` puts in a file

    def write(self, pid, notes, started="2026-03-01T10:00:00.100Z", repo=REPO, tag="me",
              review=None):
        path = os.path.join(self.dir, f"{pid}-1772359200100.json")
        with open(path, "w") as fh:
            json.dump({"version": 1, "pid": pid, "startedAt": started, "cwd": repo,
                       "repoRoot": repo, "tag": tag, "updatedAt": started,
                       **({"review": review} if review else {}), "notes": notes}, fh)
        return path

    @staticmethod
    def kept(nid, file="hidden.py", line=4, text="stop, this is wrong", side="new"):
        return {"id": nid, "file": file, "side": side, "line": line, "end": line,
                "text": text, "createdAt": "2026-03-01T10:01:00.000Z"}

    def run_with(self, listed, live=({"pid": 501},)):
        """A `_run` whose daemon knows `live` sessions and lists `listed` comments."""
        calls = []

        def run(argv, timeout=None, stdin=None):
            calls.append(argv[:4])
            if argv[:3] == ["hunk", "session", "list"]:
                return 0, json.dumps({"sessions": [
                    dict(session(f"s{i + 1}"), launchedAt=self.LAUNCHED, **s)
                    for i, s in enumerate(live)]})
            if argv[:4] == ["hunk", "session", "comment", "list"]:
                return 0, json.dumps({"comments": listed})
            return 1, ""
        return run, calls


class TestKeepNotesFiles(KeptFilesCase):
    """What a hidden file's notes and a closed viewer's notes are read from."""

    VISIBLE = {"noteId": "user:1", "source": "user", "filePath": "shown.py",
               "newRange": [2, 2], "body": "fine", "createdAt": "2026-03-01T10:00:30.000Z"}

    def test_a_note_on_a_hidden_file_is_read_with_the_listed_ones_once_each(self):
        self.write(501, [self.kept("user:1", "shown.py", 2, "fine"), self.kept("user:2")])
        run, _ = self.run_with([self.VISIBLE])
        with patch.object(hunk, "_run", run):
            self.assertEqual(hunk.user_notes("s1"), [
                ("shown.py", "new", 2, "fine", "user:1"),
                ("hidden.py", "new", 4, "stop, this is wrong", "user:2")])
            self.assertEqual([(n["id"], n["source"], n["file"], n["line"])
                              for n in hunk.notes("s1")],
                             [("user:1", "user", "shown.py", 2),
                              ("user:2", "user", "hidden.py", 4)])

    def test_only_the_sessions_own_viewer_file_counts(self):
        """Another viewer's file, and an earlier process's that had the same pid."""
        self.write(502, [self.kept("user:7")])
        os.rename(self.write(501, [self.kept("user:8")], started="2026-02-20T09:00:00.000Z"),
                  os.path.join(self.dir, "501-1771578000000.json"))
        run, _ = self.run_with([self.VISIBLE])
        with patch.object(hunk, "_run", run):
            self.assertEqual([n[4] for n in hunk.user_notes("s1")], ["user:1"])

    def test_without_the_extensions_files_nothing_more_is_asked(self):
        run, calls = self.run_with([self.VISIBLE])
        with open(os.path.join(self.dir, "notes.json"), "w") as fh:
            fh.write("{}")                                  # not a name the extension gives
        with patch.object(hunk, "_run", run):
            self.assertEqual([n[4] for n in hunk.user_notes("s1")], ["user:1"])
        self.assertEqual(calls, [["hunk", "session", "comment", "list"]])

    def test_an_unreadable_viewer_is_still_none_whatever_the_file_says(self):
        self.write(501, [self.kept("user:2")])
        with patch.object(hunk, "_run", lambda *a, **k: (1, "")):
            self.assertIsNone(hunk.user_notes("s1"))
            self.assertIsNone(hunk.notes("s1"))

    def test_a_closed_viewers_notes_are_found_for_its_repository_and_tag_only(self):
        closed = self.write(777, [self.kept("user:3")])
        self.write(501, [self.kept("user:4")])                         # still running
        self.write(778, [self.kept("user:5")], repo="/w/other")        # another repository
        self.write(779, [self.kept("user:6")], tag=None)               # opened by hand
        run, _ = self.run_with([])
        with patch.object(hunk, "_run", run):
            self.assertEqual(hunk.closed_viewer_notes(REPO, "me"), [
                (closed, [("hidden.py", "new", 4, "stop, this is wrong", "user:3")])])

    def test_with_no_daemon_a_file_is_closed_once_its_process_is_gone(self):
        gone = self.write(777, [self.kept("user:3")])
        self.write(501, [self.kept("user:4")])
        self.processes[501] = self.STARTED
        with patch.object(hunk, "_run", lambda *a, **k: (1, "")):
            self.assertEqual([p for p, _ in hunk.closed_viewer_notes(REPO, "me")], [gone])

    def test_a_running_viewer_the_daemon_does_not_list_is_not_closed(self):
        """Not registered yet, or not again after the daemon restarted: called closed, its
        notes were handed over while they stayed in the viewer, and were reported twice."""
        self.write(777, [self.kept("user:3")])
        self.processes[777] = self.STARTED + 0.4
        run, _ = self.run_with([])
        with patch.object(hunk, "_run", run):
            self.assertEqual(hunk.closed_viewer_notes(REPO, "me"), [])

    def test_a_closed_viewers_pid_now_used_by_another_process_is_still_closed(self):
        """A reused pid made the file look running, and its notes then counted nowhere."""
        gone = self.write(777, [self.kept("user:3")])
        self.processes[777] = self.STARTED + 3600
        run, _ = self.run_with([])
        for daemon in (run, lambda *a, **k: (1, "")):
            with patch.object(hunk, "_run", daemon):
                self.assertEqual([p for p, _ in hunk.closed_viewer_notes(REPO, "me")], [gone])

    AGENT = {"id": "mcp:9", "source": "agent", "author": "agent", "file": "hidden.py",
             "side": "new", "line": 4, "end": 4, "text": "because the cache is per user",
             "parentId": "user:2", "createdAt": "2026-03-01T10:02:00.000Z"}

    def test_the_agents_answers_on_a_hidden_file_are_read_with_the_users_note(self):
        """A thread on a hidden file reads whole, so it does not look unanswered; only the
        user's notes count as the user's."""
        self.write(501, [self.kept("user:2"), self.AGENT])
        run, _ = self.run_with([self.VISIBLE])
        with patch.object(hunk, "_run", run):
            self.assertEqual([n[4] for n in hunk.user_notes("s1")], ["user:1", "user:2"])
            self.assertEqual([(n["id"], n["source"], n["author"], n["parent"], n["file"])
                              for n in hunk.notes("s1")],
                             [("user:1", "user", "", None, "shown.py"),
                              ("user:2", "user", "user", None, "hidden.py"),
                              ("mcp:9", "agent", "agent", "user:2", "hidden.py")])

    def test_a_closed_viewers_agent_notes_are_not_the_users(self):
        self.write(777, [self.kept("user:3"), self.AGENT])
        run, _ = self.run_with([])
        with patch.object(hunk, "_run", run):
            self.assertEqual([n[4] for _, ns in hunk.closed_viewer_notes(REPO, "me")
                              for n in ns], ["user:3"])

    def confirm(self, review, shown, count, since=1772359299.0, expect=2):
        """`_confirm` for a window that reports `shown` files and a reload of `count`."""
        self.write(501, [], review=review)
        run, _ = self.run_with([])
        with patch.object(hunk, "_run", run), \
                patch.object(hunk, "loaded_stat", lambda _s: shown), \
                patch.object(hunk, "window_of", lambda _s, _name=None: "9 (!123)"):
            return hunk._confirm("s1", count, stat_of(expect), since)

    def test_a_window_hiding_read_files_holds_the_diff_its_record_says_it_loaded(self):
        self.assertEqual(self.confirm(self.REVIEW, stat_of(1), 1), ("s1", "9 (!123)"))

    def test_nothing_but_a_fresh_exact_record_lets_a_short_window_through(self):
        stale = dict(self.REVIEW, loadedAt=1772359200000)
        other = dict(self.REVIEW, files=[dict(self.REVIEW["files"][0]),
                                         dict(self.REVIEW["files"][1], additions=7)])
        shown_differs = dict(self.REVIEW, files=[dict(self.REVIEW["files"][0], hidden=True),
                                                 dict(self.REVIEW["files"][1])])
        for review, shown, count in [(None, stat_of(1), 1),            # no extension
                                     (stale, stat_of(1), 1),           # an earlier load's
                                     (other, stat_of(1), 1),           # not the card's diff
                                     (shown_differs, stat_of(1), 1),   # shows other files
                                     (self.REVIEW, stat_of(1), 2)]:    # reload said 2 shown
            with self.subTest(review=review, count=count):
                self.assertIsNone(self.confirm(review, shown, count))
        self.assertIsNone(self.confirm(self.REVIEW, stat_of(1), 1, since=None))

    def test_the_agents_window_is_shown_while_it_hides_read_files(self):
        """The rework card's diff and the self-review piece, each in a window that hides one
        of their two files as read: shown, not refused."""
        self.write(501, [], review=dict(self.REVIEW, loadedAt=4102444800000))
        for show in (lambda: hunk.show_working_diff(REPO, NAME, stat_of(2), sleep=None),
                     lambda: hunk.show_review(REPO, NAME, ["show", "abc"], stat_of(2))):
            fake = FakeRun([dict(session("mine", pane="%4"), pid=501, launchedAt=self.LAUNCHED)],
                           owners={"%4": "me"}, reload_files=1, loaded=stat_of(1))
            with patch.dict(os.environ, {"TMUX": "/tmp/x", "CLAUDE_CODE_SESSION_ID": "me"}), \
                    patch.object(hunk, "installed", lambda: True), \
                    patch.object(hunk, "_run", fake):
                self.assertEqual(show(), ("mine", "9 (!123)"))

    def test_whole_review_lists_hidden_files(self):
        self.write(501, [], review=self.REVIEW)
        run, _ = self.run_with([])
        with patch.object(hunk, "_run", run):
            self.assertEqual(hunk.whole_review("s1"),
                             (stat_of(2), stat_of(1), ["f1.py"]))

    def test_forgetting_removes_exactly_the_files_named(self):
        a, b = self.write(777, [self.kept("user:3")]), self.write(501, [self.kept("user:4")])
        hunk.forget_viewer_files([a])
        self.assertEqual(os.listdir(self.dir), [os.path.basename(b)])

    def test_the_directory_is_the_extensions(self):
        home = os.path.expanduser("~")
        for env, want in [({}, os.path.join(home, ".local/state/hunk-keep-notes")),
                          ({"XDG_STATE_HOME": "/st"}, "/st/hunk-keep-notes"),
                          ({"XDG_STATE_HOME": "rel"}, os.path.join(home, ".local/state/hunk-keep-notes")),
                          ({"HUNK_KEEP_NOTES_DIR": "/mine", "XDG_STATE_HOME": "/st"}, "/mine"),
                          ({"HUNK_KEEP_NOTES": "off", "HUNK_KEEP_NOTES_DIR": "/mine"}, None)]:
            with patch.dict(os.environ, env, clear=True), patch.dict(os.environ, {"HOME": home}):
                self.assertEqual(KEEP_DIR(), want, env)

    def test_the_agents_viewer_is_tagged_with_its_owner(self):
        fake = FakeRun(new_pane="%9")
        with patch.object(hunk, "_run", fake):
            hunk.spawn_session(REPO, "!123", "me", sleep=lambda _s: None)
        argv = next(c for c in fake.calls if c[:2] == ["tmux", "new-window"])
        self.assertEqual(argv[argv.index("-e") + 1], "HUNK_KEEP_NOTES_TAG=me")


class TestShowUnread(KeptFilesCase):
    """A file hidden as read is un-marked in hunk-mark-read's files and the window loaded
    again, so the agent's note can go on it. Event files shaped like those hunk-mark-read
    0.1.0 writes, with invented paths and digests."""

    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.marks = tmp.name
        stub = patch.object(hunk, "mark_read_dir", lambda: self.marks)
        stub.start()
        self.addCleanup(stub.stop)

    @staticmethod
    def key(path, digest):
        return f"{path}\0\0{digest}"

    def log(self, name, *events):
        with open(os.path.join(self.marks, name), "a") as fh:
            for t, key, read in events:
                fh.write(json.dumps({"t": t, "key": key, "read": read}) + "\n")

    def read_keys(self):
        """What hunk-mark-read's replay makes of the folder: the keys read now."""
        latest = {}
        for e in hunk._mark_events(self.marks):
            latest[e["key"]] = e["read"]
        return sorted(k for k, read in latest.items() if read)

    def test_every_mark_of_the_path_goes_and_no_other(self):
        now, later = self.key("f1.py", "aa"), self.key("f1.py", "bb")
        other = self.key("f0.py", "cc")
        self.log("2026-03-01T10-00-00_repo_HEAD_501.jsonl",
                 (100, now, True), (110, other, True), (120, later, True))
        # Another viewer's, written with a clock ahead of this one's.
        self.log("2026-03-01T10-05-00_repo_HEAD_502.jsonl", (2 ** 52, later, True))
        self.assertEqual(hunk.unmark_read({"f1.py"}), 2)
        self.assertEqual(self.read_keys(), [other])
        self.assertEqual(len(os.listdir(self.marks)), 3)

    def test_nothing_read_writes_nothing(self):
        self.log("a_501.jsonl", (100, self.key("f1.py", "aa"), True),
                 (200, self.key("f1.py", "aa"), False), (300, self.key("f0.py", "cc"), True))
        self.assertEqual(hunk.unmark_read({"f1.py"}), 0)
        self.assertEqual(os.listdir(self.marks), ["a_501.jsonl"])

    HIDDEN = KeptFilesCase.REVIEW

    def window(self, still_hidden=False):
        """A `_run` whose viewer, on a reload, records a new load in its keep-notes file."""
        run, calls = self.run_with([])

        def reload_aware(argv, timeout=None, stdin=None):
            if argv[:3] == ["hunk", "session", "reload"]:
                calls.append(argv)
                files = [dict(f, hidden=f["hidden"] and still_hidden)
                         for f in self.HIDDEN["files"]]
                self.write(501, [], review=dict(self.HIDDEN, files=files,
                                                loadedAt=4102444800000))
                return 0, json.dumps({"result": {"fileCount": len(files)}})
            if argv[:4] == ["hunk", "session", "comment", "apply"]:
                calls.append(argv)
                return 0, ""
            return run(argv, timeout, stdin)
        return reload_aware, calls

    def test_a_hidden_file_is_unmarked_and_shown_again(self):
        self.write(501, [], review=self.HIDDEN)
        self.log("a_501.jsonl", (100, self.key("f1.py", "aa"), True))
        run, calls = self.window()
        with patch.object(hunk, "_run", run):
            self.assertTrue(hunk.show_unread("s1", ["f1.py"], ["show", "abc"]))
        self.assertEqual(self.read_keys(), [])
        self.assertEqual([c[-2:] for c in calls if c[2] == "reload"], [["show", "abc"]])

    def test_a_file_that_stays_hidden_is_reported(self):
        self.write(501, [], review=self.HIDDEN)
        self.log("a_501.jsonl", (100, self.key("f1.py", "aa"), True))
        run, _ = self.window(still_hidden=True)
        with patch.object(hunk, "_run", run):
            self.assertFalse(hunk.show_unread("s1", ["f1.py"], ["show", "abc"]))

    def test_marks_not_on_disk_load_nothing_again(self):
        """hunk-mark-read with `persist = false` holds its marks in the viewer alone."""
        self.write(501, [], review=self.HIDDEN)
        run, calls = self.window()
        with patch.object(hunk, "_run", run):
            self.assertFalse(hunk.show_unread("s1", ["f1.py"], ["show", "abc"]))
        self.assertEqual([c for c in calls if c[2] == "reload"], [])

    def test_a_shown_file_needs_nothing(self):
        self.write(501, [], review=self.HIDDEN)
        run, calls = self.window()
        with patch.object(hunk, "_run", run):
            self.assertTrue(hunk.show_unread("s1", ["f0.py"], ["show", "abc"]))
        self.assertEqual([c for c in calls if c[2] == "reload"], [])

    def test_the_agents_notes_on_a_hidden_file_land_once_it_is_shown(self):
        """The rework card's notes: the working diff is loaded again before they go up."""
        self.write(501, [], review=self.HIDDEN)
        self.log("a_501.jsonl", (100, self.key("f1.py", "aa"), True))
        run, calls = self.window()
        with patch.object(hunk, "_run", run):
            self.assertEqual(hunk.add_notes("s1", [{"filePath": "f1.py", "newLine": 1,
                                                    "summary": "x"}]), 1)
        mine = [c for c in calls if c[2] in ("reload", "comment")]
        self.assertEqual([c[2:4] for c in mine], [["reload", "s1"], ["comment", "apply"]])
        self.assertEqual(mine[0][-3:], hunk.WORKING_DIFF)

    def test_the_folder_is_hunk_mark_reads(self):
        home = os.path.expanduser("~")
        for env, want in [({}, os.path.join(home, ".local/state/hunk-mark-read/sessions")),
                          ({"XDG_STATE_HOME": "/st"}, "/st/hunk-mark-read/sessions")]:
            with patch.dict(os.environ, env, clear=True), patch.dict(os.environ, {"HOME": home}):
                self.assertEqual(MARK_READ_DIR(), want, env)


if __name__ == "__main__":
    unittest.main(verbosity=2)
