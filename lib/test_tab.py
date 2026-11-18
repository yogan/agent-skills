#!/usr/bin/env python3
"""Tests for `lib/tab.py` and `hooks/tmux-tab.py`: agent windows' names.

Run: `python3 lib/test_tab.py` (stdlib only). tmux is a fake holding one window's name and
options; the model is never asked.
"""
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from lib import tab  # noqa: E402

_spec = importlib.util.spec_from_file_location("tmux_tab", os.path.join(ROOT, "hooks", "tmux-tab.py"))
hook = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hook)

R = tab.AGENT_ICON


class Tmux:
    """One window, as far as the module reads and writes it."""

    def __init__(self, name="fish", auto=True):
        self.opts = {"window_name": name, "automatic-rename": "1" if auto else "0"}

    def __call__(self, argv):
        cmd = argv[1]
        if cmd == "display-message":
            fmt = argv[-1]
            out = fmt
            for k in sorted(self.opts, key=len, reverse=True):
                out = out.replace("#{" + k + "}", self.opts[k])
            import re
            out = re.sub(r"#\{[^}]*\}", "", out)
            return 0, out + "\n"
        if cmd == "rename-window":
            self.opts.update({"window_name": argv[-1], "automatic-rename": "0"})
            return 0, ""
        if cmd == "set-option":
            if "-wu" in argv:
                self.opts.pop(argv[-1], None)
            else:
                self.opts[argv[-2]] = "1" if argv[-1] == "on" else argv[-1]
            return 0, ""
        return 1, ""

    @property
    def name(self):
        return self.opts["window_name"]


class TestCut(unittest.TestCase):
    def test_a_short_title_stays(self):
        self.assertEqual(tab.cut("kept plan"), "kept plan")

    def test_whole_words_from_the_end_go(self):
        self.assertEqual(tab.cut("Add feature flag for offline viewing mode"), "Add feature flag")

    def test_a_commit_type_and_a_ticket_go_first(self):
        self.assertEqual(tab.cut("feat(ui): ABC-123 offline flag"), "offline flag")
        self.assertEqual(tab.cut("[ABC-12] Retry backoff"), "Retry backoff")

    def test_one_long_word_ends_in_an_ellipsis(self):
        self.assertEqual(tab.cut("Review-ui-b5-rest-two handoff"), "Review-ui-b5-rest…")
        self.assertEqual(len(tab.cut("x" * 40)), tab.MAX)

    def test_never_ends_on_a_word_that_is_only_a_symbol(self):
        self.assertEqual(tab.cut("Threads / Comment Design Review"), "Threads / Comment")
        self.assertEqual(tab.cut("Threads / Commentary Design"), "Threads")
        self.assertEqual(tab.cut("Sidebar layout & comments"), "Sidebar layout")


class TestNaming(unittest.TestCase):
    def test_a_window_tmux_names_is_free_and_then_follows_the_title(self):
        t = Tmux()
        self.assertTrue(tab.name("%999999", R, "agent-skills", run=t))
        self.assertEqual(t.name, f"{R} agent-skills")
        self.assertTrue(tab.name("%999999", R, "tab names", run=t))     # its own name, so still free
        self.assertEqual(t.name, f"{R} tab names")

    def test_a_window_the_user_named_is_left_alone(self):
        t = Tmux("srv", auto=False)
        self.assertFalse(tab.name("%999999", R, "tab names", run=t))
        self.assertEqual(t.name, "srv")
        t = Tmux()
        tab.name("%999999", R, "tab names", run=t)
        t("tmux rename-window -t %1 mine".split())                 # renamed by hand since
        self.assertFalse(tab.name("%999999", R, "other", run=t))
        self.assertEqual(t.name, "mine")

    def test_a_skill_holds_the_tab_and_gives_it_back(self):
        t = Tmux("srv", auto=False)
        self.assertTrue(tab.hold("%999999", tab.REVIEW_ICON, "retry backoff", run=t))
        self.assertEqual(t.name, f"{tab.REVIEW_ICON} retry backoff")
        self.assertFalse(tab.name("%999999", R, "a new title", run=t))  # the title waits
        tab.hold("%999999", tab.REVIEW_ICON, "retry backoff again", run=t)
        self.assertTrue(tab.release("%999999", run=t))
        self.assertEqual(t.name, "srv")                             # the first hold's
        self.assertFalse(tab.release("%999999", run=t))

    def test_a_window_tmux_named_gets_tmux_naming_back(self):
        t = Tmux()
        tab.hold("%999999", tab.REWORK_ICON, "rounding fix", run=t)
        tab.release("%999999", run=t)
        self.assertEqual(t.opts["automatic-rename"], "1")

    def test_no_pane_no_tmux(self):
        self.assertFalse(tab.name("", R, "x", run=Tmux()))
        self.assertFalse(tab.name("%999999", R, "x", run=lambda argv: (1, "")))


class Rig(unittest.TestCase):
    """The hook and the skills' calls against the fake window: every tmux call of lib/tab.py
    goes through `tab._run`, so none reaches the tmux the tests run in."""

    def setUp(self):
        self.t = Tmux()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for patch in (mock.patch.object(tab, "_run", self.t),
                      mock.patch.object(tab, "CACHE", os.path.join(self.tmp.name, "c.json")),
                      mock.patch.object(tab, "SEEN", os.path.join(self.tmp.name, "seen")),
                      mock.patch.object(tab, "NAMES", os.path.join(self.tmp.name, "names"))):
            patch.start()
            self.addCleanup(patch.stop)
        patch = mock.patch.dict(os.environ)          # the runner's AGENT_TAB_OFF: these
        patch.start()                                   # tests are the one place it is off
        self.addCleanup(patch.stop)
        os.environ.pop(tab.OFF_ENV, None)
        self.later = mock.patch.object(tab, "spawn").start()
        self.addCleanup(mock.patch.stopall)

    # The session's Claude Code process, and a `claude` run inside its pane: both still run.
    OUTER, NESTED = {"CLAUDE_PID": str(os.getpid())}, {"CLAUDE_PID": str(os.getppid())}

    def run_hook(self, *argv, data=None, env=None):
        env = {"TMUX_PANE": "%999999", **self.OUTER, **(env or {})}
        return hook.main(list(argv), env, io.StringIO(json.dumps(data or {})))


class TestHook(Rig):
    LONG = "Add feature flag for offline viewing mode"

    def shorten(self, *argv, says="offline flag"):
        with mock.patch.object(tab, "ask_model", return_value=says):
            self.run_hook("shorten", *argv)

    def test_startup_names_the_window_after_the_repository(self):
        self.run_hook("start", data={"source": "startup", "cwd": ROOT})
        self.assertEqual(self.t.name, f"{R} {os.path.basename(ROOT)}")
        self.later.assert_not_called()

    def test_a_long_title_waits_for_the_model_never_shown_cut(self):
        self.run_hook("start", data={"source": "startup", "session_title": self.LONG})
        self.assertEqual(self.t.name, "fish")                      # not "Add feature flag"
        self.later.assert_called_once()
        self.shorten(self.LONG)
        self.assertEqual(self.t.name, f"{R} offline flag")

    def test_where_the_model_fails_the_cut_after_all(self):
        self.run_hook("title", self.LONG)
        self.shorten(self.LONG, says=None)
        self.assertEqual(self.t.name, f"{R} Add feature flag")

    def test_clear_and_compact_change_nothing(self):
        self.run_hook("start", data={"source": "compact", "cwd": ROOT})
        self.assertEqual(self.t.name, "fish")

    def test_the_shortener_and_a_session_outside_tmux_change_nothing(self):
        self.run_hook("start", data={"source": "startup", "cwd": ROOT}, env={tab.SHORTENER_ENV: "1"})
        self.assertEqual(self.t.name, "fish")
        hook.main(["title", "x"], {}, io.StringIO("{}"))
        self.assertEqual(self.t.name, "fish")

    def test_a_short_title_needs_no_model(self):
        self.run_hook("title", "tab names")
        self.assertEqual(self.t.name, f"{R} tab names")
        self.later.assert_not_called()

    def test_the_models_title_is_kept_for_the_next_time(self):
        self.run_hook("title", self.LONG)
        self.shorten(self.LONG)
        self.later.reset_mock()
        self.run_hook("title", "tab names")
        self.run_hook("title", self.LONG)                          # e.g. back after a /rename
        self.assertEqual(self.t.name, f"{R} offline flag")
        self.later.assert_not_called()

    def test_the_model_late_for_an_older_title_changes_nothing(self):
        self.run_hook("title", self.LONG)
        self.run_hook("title", "tab names")
        self.shorten(self.LONG)
        self.assertEqual(self.t.name, f"{R} tab names")


class TestQuit(Rig):
    """When the session ends, a window it named gets back what it had before."""

    def test_a_fresh_window_is_tmuxs_to_name_again(self):
        self.run_hook("start", data={"source": "startup", "cwd": ROOT})
        self.run_hook("title", "tab names")
        self.run_hook("end", data={"reason": "prompt_input_exit"})
        self.assertEqual(self.t.opts["automatic-rename"], "1")
        self.assertNotIn(tab.SET_OPT, self.t.opts)

    def test_a_window_renamed_by_hand_since_keeps_that_name(self):
        self.run_hook("start", data={"source": "startup", "cwd": ROOT})
        self.t("tmux rename-window -t %999999 mine".split())
        self.run_hook("end", data={"reason": "prompt_input_exit"})
        self.assertEqual((self.t.name, self.t.opts["automatic-rename"]), ("mine", "0"))

    def test_a_clear_goes_on_in_the_same_window(self):
        self.run_hook("start", data={"source": "startup", "cwd": ROOT})
        self.run_hook("end", data={"reason": "clear"})
        self.assertEqual(self.t.name, f"{R} {os.path.basename(ROOT)}")

    def test_a_clear_and_then_the_end_of_the_session_after_it(self):
        self.run_hook("start", data={"source": "startup", "cwd": ROOT})
        self.run_hook("end", data={"reason": "clear"})
        self.run_hook("start", data={"source": "clear", "cwd": ROOT})
        self.run_hook("end", data={"reason": "prompt_input_exit"})
        self.assertEqual(self.t.opts["automatic-rename"], "1")

    def test_a_claude_run_inside_the_sessions_pane_leaves_its_tab_alone(self):
        # What `claude mcp list` fires there: SessionEnd alone; `claude -p` a SessionStart first.
        self.run_hook("start", data={"source": "startup", "cwd": ROOT})
        self.run_hook("title", "tab names")
        self.run_hook("end", data={"reason": "other"}, env=self.NESTED)
        self.assertEqual(self.t.name, f"{R} tab names")
        self.run_hook("start", data={"source": "startup", "cwd": "/elsewhere"}, env=self.NESTED)
        self.run_hook("end", data={"reason": "other"}, env=self.NESTED)
        self.assertEqual(self.t.name, f"{R} tab names")
        self.run_hook("end", data={"reason": "prompt_input_exit"})  # the session's own end
        self.assertEqual(self.t.opts["automatic-rename"], "1")

    def test_a_claude_after_the_last_one_quit_or_crashed_names_the_window(self):
        self.run_hook("start", data={"source": "startup", "cwd": ROOT})
        self.run_hook("end", data={"reason": "prompt_input_exit"})
        self.run_hook("start", data={"source": "resume", "session_title": "tab names"}, env=self.NESTED)
        self.assertEqual(self.t.name, f"{R} tab names")
        with mock.patch.object(tab, "_alive", return_value=False):  # ended without its hook
            self.run_hook("start", data={"source": "startup", "session_title": "kept plan"})
        self.assertEqual(self.t.name, f"{R} kept plan")

    def test_the_hook_at_quit_returns_before_any_tmux_call_or_process(self):
        # Claude Code cancels a SessionEnd hook still running 1.5s into its quit, and on a
        # loaded Mac one process started (a tmux call, another Python) costs a large part
        # of that: the hook forks before any, and its copy gives the window back.
        self.run_hook("start", data={"source": "startup", "cwd": ROOT})
        calls = []
        tmux = mock.Mock(side_effect=lambda argv: calls.append(argv) or self.t(argv))
        env = {"TMUX_PANE": "%999999", **self.OUTER}
        end = json.dumps({"reason": "prompt_input_exit"})
        # The test's own stdout and stderr are not given up: os's calls are recorded instead.
        setsid = mock.patch.object(hook.os, "setsid").start()
        mock.patch.object(hook.os, "open", return_value=99).start()
        dup2 = mock.patch.object(hook.os, "dup2").start()
        with mock.patch.object(tab, "_run", tmux), mock.patch.object(hook.os, "fork", return_value=4242):
            self.assertEqual(hook.main(["end"], env, io.StringIO(end), detach=True), 0)
        self.assertEqual(calls, [])
        self.later.assert_not_called()
        self.assertEqual(self.t.name, f"{R} {os.path.basename(ROOT)}")
        self.assertEqual(sorted(c.args[1] for c in dup2.call_args_list), [0, 1, 2])
        with mock.patch.object(hook.os, "fork", return_value=0):
            hook.main(["end"], env, io.StringIO(end), detach=True)
        setsid.assert_called_once()
        self.assertEqual(self.t.opts["automatic-rename"], "1")
        self.assertNotIn(tab.OWNER_OPT, self.t.opts)

    def test_a_window_the_user_named_is_untouched_from_start_to_end(self):
        self.t.opts.update(window_name="srv", **{"automatic-rename": "0"})
        self.run_hook("start", data={"source": "startup", "cwd": ROOT})
        self.run_hook("end", data={"reason": "prompt_input_exit"})
        self.assertEqual(self.t.name, "srv")


class TestSkills(Rig):
    """review-mr and rework-mr take the session's tab over for an MR and give it back."""
    TITLE = "Retry the payment call with backoff"

    def setUp(self):
        super().setUp()
        self.run_hook("start", data={"source": "startup", "cwd": ROOT})
        self.run_hook("title", "kept plan")                        # the session's own, before

    def test_the_tab_shows_the_mr_once_short_and_the_session_its_full_name_on_the_next_prompt(self):
        tab.take(tab.REVIEW_ICON, self.TITLE, pane="%999999",
                 session=f"{tab.REVIEW_ICON} !12 {self.TITLE}")
        self.assertEqual(self.t.name, f"{R} kept plan")             # not "Retry the"
        with mock.patch.object(tab, "ask_model", return_value="payment retry"):
            self.run_hook("shorten", "--icon", tab.REVIEW_ICON, "--hold", self.TITLE)
        self.assertEqual(self.t.name, f"{tab.REVIEW_ICON} payment retry")
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            self.run_hook("prompt")
        said = json.loads(out.getvalue())["hookSpecificOutput"]
        self.assertEqual(said, {"hookEventName": "UserPromptSubmit",
                                "sessionTitle": f"{tab.REVIEW_ICON} !12 {self.TITLE}"})
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            self.run_hook("prompt")                                # once only
        self.assertEqual(out.getvalue(), "")

    def test_a_held_tab_ignores_the_sessions_title(self):
        tab.take(tab.REWORK_ICON, "rounding", pane="%999999")
        self.assertEqual(self.t.name, f"{tab.REWORK_ICON} rounding")
        self.run_hook("title", "Some new session title")
        self.assertEqual(self.t.name, f"{tab.REWORK_ICON} rounding")

    def test_the_session_end_gives_the_tab_back_all_the_way(self):
        tab.take(tab.REWORK_ICON, "rounding", pane="%999999")
        self.run_hook("end", data={"reason": "prompt_input_exit"})
        self.assertNotIn(tab.SKILL_OPT, self.t.opts)
        self.assertEqual(self.t.opts["automatic-rename"], "1")     # the fresh window it was

    def test_a_claude_run_inside_the_sessions_pane_keeps_the_skills_hold(self):
        tab.take(tab.REWORK_ICON, "rounding", pane="%999999")
        self.run_hook("end", data={"reason": "other"}, env=self.NESTED)
        self.assertEqual(self.t.name, f"{tab.REWORK_ICON} rounding")

    def test_given_back_the_session_title_follows_again(self):
        tab.take(tab.REWORK_ICON, "rounding", pane="%999999")
        tab.give_back(pane="%999999")
        self.assertEqual(self.t.name, f"{R} kept plan")
        self.run_hook("title", "tab names")
        self.assertEqual(self.t.name, f"{R} tab names")

    def test_a_viewer_is_named_at_once_its_number_until_a_short_title_is_known(self):
        self.assertEqual(tab.viewer_label(self.TITLE, fallback="!12"), f"{tab.VIEWER_ICON} !12")
        self.later.assert_called_once()
        tab.remember(self.TITLE, "payment retry")
        self.assertEqual(tab.viewer_label(self.TITLE, fallback="!12"), f"{tab.VIEWER_ICON} payment retry")

    def test_off_in_tests(self):
        with mock.patch.dict(os.environ, {tab.OFF_ENV: "1"}):
            tab.take(tab.REVIEW_ICON, "x", pane="%999999")
            tab.viewer_label(self.TITLE, fallback="!12")
        self.assertEqual(self.t.name, f"{R} kept plan")
        self.later.assert_not_called()


class TestCache(unittest.TestCase):
    def test_two_titles_remembered_at_once_are_both_kept(self):
        """Each run wrote the file as it had read it: the one that read first and wrote last
        dropped the other's title."""
        import threading
        import time
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        real, read = tab._load, threading.Event()

        def slow_load():
            got = real()
            if not read.is_set():            # the first run: holds on to what it read a while
                read.set()
                time.sleep(0.3)
            return got
        with mock.patch.object(tab, "CACHE", os.path.join(tmp.name, "c.json")), \
                mock.patch.object(tab, "_load", slow_load):
            first = threading.Thread(target=tab.remember, args=("one title", "one"))
            first.start()
            read.wait(5)
            tab.remember("two title", "two")
            first.join(5)
            self.assertEqual(real(), {"one title": "one", "two title": "two"})


class TestModel(unittest.TestCase):
    def run_model(self, *outs, rc=0):
        """The model answering each of `outs` in turn; what came back and every prompt sent."""
        got = {"prompts": []}
        answers = iter(outs)

        def run(argv, **kw):
            got.update(argv=argv, env=kw["env"])
            got["prompts"].append(argv[-1])
            return mock.Mock(returncode=rc, stdout=next(answers))
        return tab.ask_model("Some long title", run=run), got

    def test_its_answer_as_it_said_it_and_none_where_it_failed(self):
        short, got = self.run_model("'review-ui handoff'\n")
        self.assertEqual(short, "review-ui handoff")
        self.assertEqual(got["env"][tab.SHORTENER_ENV], "1")
        self.assertIn("--setting-sources", got["argv"])            # no user hooks
        self.assertIsNone(self.run_model("x", rc=1)[0])
        self.assertIsNone(self.run_model("")[0])

    def test_an_answer_too_long_or_with_a_symbol_is_asked_again_saying_why(self):
        short, got = self.run_model("Threads / Comment Design", "threads comment")
        self.assertEqual(short, "threads comment")
        self.assertEqual(len(got["prompts"]), 2)
        self.assertIn("'Threads / Comment Design'", got["prompts"][1])
        self.assertEqual(self.run_model("threads / design", "threads design")[0], "threads design")

    def test_an_answer_after_thinking_aloud_is_its_last_line(self):
        said = ('Threads / Comment Design → "thread comments"? Checking length: "thread comments" '
                'is 15 characters, within the limit.\n\nthread comments\n')
        self.assertEqual(self.run_model(said)[0], "thread comments")

    def test_wrong_twice_is_a_failure_never_a_cut(self):
        # A failure leaves the tab the cut for now, and nothing is kept for the next time.
        self.assertIsNone(self.run_model("Threads / Comment Design", "Threads / Comment Design")[0])


if __name__ == "__main__":
    unittest.main()
