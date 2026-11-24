#!/usr/bin/env python3
"""Tests for `hooks/last_session.py`: the note a quitting session leaves its terminal's shell.

Run: `python3 hooks/test_last_session.py` (stdlib only). ps is a fake process table; how the
hook calls this at quit, in tmux and outside it, is in lib/test_tab.py's TestNote.
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import last_session  # noqa: E402


def table(rows):
    """A fake ps over {pid: (ppid, tty)}; a pid not in it is gone, as ps prints nothing."""
    calls = []

    def ps(pid):
        calls.append(pid)
        return rows.get(pid, (0, ""))
    ps.calls = calls
    return ps


class TestTerminal(unittest.TestCase):
    def test_claude_codes_own_terminal_in_one_lookup(self):
        ps = table({500: (400, "ttys014"), 400: (1, "ttys014")})
        self.assertEqual(last_session.terminal({"CLAUDE_PID": "500"}, ps), "ttys014")
        self.assertEqual(ps.calls, [500])

    def test_never_the_terminal_claude_code_was_only_started_from(self):
        # An IDE's or a script's Claude Code, started without a terminal from a shell that has one.
        ps = table({500: (400, "??"), 400: (1, "ttys014")})
        self.assertEqual(last_session.terminal({"CLAUDE_PID": "500"}, ps), "")
        ps = table({500: (400, "?"), 400: (1, "pts/3")})
        self.assertEqual(last_session.terminal({"CLAUDE_PID": "500"}, ps), "")

    def test_without_claude_pid_the_nearest_ancestor_with_a_terminal(self):
        # hook (python) -> sh -c (no terminal: Claude Code starts its hooks without one) -> claude
        ps = table({300: (500, "??"), 500: (400, "pts/3"), 400: (1, "pts/9")})
        with mock.patch.object(last_session.os, "getppid", return_value=300):
            self.assertEqual(last_session.terminal({}, ps), "pts/3")
        self.assertEqual(ps.calls, [300, 500])

    def test_without_claude_pid_and_no_terminal_up_the_chain(self):
        ps = table({300: (500, "??"), 500: (1, "??")})
        with mock.patch.object(last_session.os, "getppid", return_value=300):
            self.assertEqual(last_session.terminal({}, ps), "")
        self.assertEqual(ps.calls, [300, 500])                         # stops at init's child
        ps = table({n: (n - 1, "??") for n in range(2, 300)})
        with mock.patch.object(last_session.os, "getppid", return_value=299):
            self.assertEqual(last_session.terminal({}, ps), "")
        self.assertEqual(len(ps.calls), 3)                             # a few steps, no more

    def test_a_ps_that_fails_is_no_terminal(self):
        def ps(pid):
            raise OSError("no ps")
        self.assertEqual(last_session.terminal({"CLAUDE_PID": "500"}, ps), "")

    def test_the_real_ps_on_this_process(self):
        # Whatever the test runs in, a terminal or none, ps answers in the form the hook reads.
        ppid, tty = last_session._ps(os.getpid())
        self.assertEqual(ppid, os.getppid())
        self.assertTrue(tty)


class TestNoteName(unittest.TestCase):
    def test_both_sides_names_for_a_terminal_give_one_file(self):
        for shell, ps in (("/dev/ttys014", "ttys014"), ("/dev/pts/3", "pts/3"), ("/dev/tty1", "tty1")):
            self.assertEqual(last_session.note_name(shell), last_session.note_name(ps))
        self.assertEqual(last_session.note_name("/dev/pts/3"), "pts-3")
        self.assertEqual(last_session.note_name("ttys014"), "ttys014")

    def test_nothing_but_a_plain_file_name(self):
        self.assertEqual(last_session.note_name("../x y/ü"), "..-x-y--")
        self.assertNotIn("/", last_session.note_name("/dev/../../etc/passwd"))


class TestNote(unittest.TestCase):
    SID = "0d8d3b78-aaaa-bbbb-cccc-000000000001"

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.repo = os.path.join(self.tmp, "repo")
        self.write(os.path.join(self.repo, ".git", "HEAD"), "ref: refs/heads/feat/offline-flag\n")
        self.env = {"XDG_CACHE_HOME": self.tmp, "CLAUDE_PID": "500"}
        self.ps = table({500: (1, "pts/3")})

    def write(self, path, text):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write(text)

    def note(self, tty="pts-3"):
        path = os.path.join(self.tmp, "agent-skills", "last-session", tty)
        if not os.path.exists(path):
            return None
        with open(path) as fh:
            return fh.read()

    def transcript(self, *entries):
        path = os.path.join(self.tmp, "t.jsonl")
        self.write(path, "\n".join(json.dumps(e) for e in entries) + "\n")
        return path

    def quit(self, env=None, ps=None, **data):
        data = {"session_id": self.SID, "cwd": self.repo, **data}
        return last_session.leave_note({**self.env, **(env or {})}, data, ps or self.ps)

    def test_keyed_by_the_terminal_with_the_title_and_the_branch(self):
        self.assertTrue(self.quit(transcript_path=self.transcript(
            {"type": "ai-title", "aiTitle": "Offline viewing flag"})))
        self.assertEqual(self.note(), f"Offline viewing flag · feat/offline-flag\n{self.SID}\n")

    def test_no_terminal_no_note(self):
        t = self.transcript({"type": "ai-title", "aiTitle": "Offline viewing flag"})
        self.assertFalse(self.quit(ps=table({500: (1, "??")}), transcript_path=t))
        self.assertEqual(sorted(os.listdir(self.tmp)), ["repo", "t.jsonl"])

    def test_no_title_no_note_and_no_lookup(self):
        # A `claude -p` in the session's shell: it never overwrites what the session leaves.
        self.assertFalse(self.quit())
        self.assertIsNone(self.note())
        self.assertEqual(self.ps.calls, [])

    def test_in_tmux_the_title_the_status_line_handed_over_for_the_pane(self):
        self.write(os.path.join(self.tmp, "agent-skills", "tab-seen", f"{self.SID}-999999"),
                   "\U000f127b !1234 Offline viewing flag")
        t = self.transcript({"type": "ai-title", "aiTitle": "Generated title"})
        self.quit(env={"TMUX_PANE": "%999999"}, transcript_path=t)
        self.assertTrue(self.note().startswith("\U000f127b !1234 Offline viewing flag · "))
        self.quit(transcript_path=t)                      # outside tmux: no pane to look up
        self.assertTrue(self.note().startswith("Generated title · "))

    def test_a_rename_in_the_transcript_over_the_generated_title(self):
        self.quit(transcript_path=self.transcript(
            {"type": "ai-title", "aiTitle": "Generated title"},
            {"type": "custom-title", "customTitle": "My own name"},
            {"type": "ai-title", "aiTitle": "Generated title"},
            {"type": "user", "message": "x" * 100}))
        self.assertTrue(self.note().startswith("My own name · feat/offline-flag\n"))

    def test_outside_a_checkout_the_title_alone(self):
        t = self.transcript({"type": "ai-title", "aiTitle": "tab names"})
        self.quit(cwd=os.path.join(self.tmp, "nowhere"), transcript_path=t)
        self.assertTrue(self.note().startswith("tab names\n"))

    def test_a_worktree_and_a_detached_head(self):
        wt = os.path.join(self.tmp, "wt")
        gitdir = os.path.join(self.repo, ".git", "worktrees", "wt")
        self.write(os.path.join(gitdir, "HEAD"), "0123456789abcdef\n")
        self.write(os.path.join(wt, ".git"), f"gitdir: {gitdir}\n")
        os.makedirs(os.path.join(wt, "src"))
        self.assertEqual(last_session.branch(os.path.join(wt, "src")), "0123456")
        self.write(os.path.join(gitdir, "HEAD"), "ref: refs/heads/topic\n")
        self.assertEqual(last_session.branch(wt), "topic")


if __name__ == "__main__":
    unittest.main()
