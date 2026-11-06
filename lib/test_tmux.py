#!/usr/bin/env python3
"""Tests for `lib/tmux.py` — finding the pane a background session is shown in.

Run: `python3 lib/test_tmux.py` (stdlib only). tmux, ps and Claude Code's session records
are never really read: the records are passed in, the two commands faked.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib import tmux  # noqa: E402

# The measured shape after ← : the agent's shell (900) runs under its session (800), under
# the session's host (700), under the shared daemon (600), which detached to init. The
# pane's own process (400) still runs the `claude` that parked the session (500). Nothing
# links the two trees but the records.
TREE = {900: 800, 800: 700, 700: 600, 600: 1, 500: 400, 400: 1}
# What the agent's shell really carries there: no TMUX, no TMUX_PANE, and no
# CLAUDE_CODE_SESSION_KIND either (that one stops at the host process).
BG_ENV = {"CLAUDE_PID": "800", "CLAUDE_CODE_SESSION_ID": "8b2c5945-0000-4000-8000-000000000000"}


def bg(job="8b2c5945", pid=800):
    return {"pid": pid, "kind": "bg", "jobId": job,
            "sessionId": "8b2c5945-0000-4000-8000-000000000000"}


def viewer(parked="8b2c5945", pid=500, where="main:@3.%7"):
    return {"pid": pid, "kind": "interactive", "tmux": where, "parkedJobId": parked,
            "sessionId": "f37a2a54-0000-4000-8000-000000000000"}


def fake(pane_pids=None, tree=TREE, commands=None):
    """`pane_pids` maps pane id -> its process, as `tmux display-message` reports it;
    `commands` a process's command line, as `ps` shows it where asked for."""
    pane_pids = {"%7": 400} if pane_pids is None else pane_pids
    commands = commands or {}
    calls = []

    def run(argv):
        calls.append(argv)
        if argv[:2] == ["tmux", "display-message"]:
            pane = argv[argv.index("-t") + 1]
            return (0, f"{pane_pids[pane]}\n") if pane in pane_pids else (1, "")
        if argv[:2] == ["tmux", "list-panes"]:
            return 0, "".join(f"{pane} {pid}\n" for pane, pid in pane_pids.items())
        if argv[0] == "ps":
            if "command=" not in argv:
                return 0, "".join(f"{p:>6} {pp:>6}\n" for p, pp in tree.items())
            return 0, "".join(f"{p:>6} {pp:>6} {commands.get(p, '-fish')}\n" for p, pp in tree.items())
        return 1, ""
    return run, calls


class TestOwnPane(unittest.TestCase):
    def test_the_variable_wins_without_asking_anyone(self):
        run, calls = fake()
        self.assertEqual(tmux.own_pane({"TMUX_PANE": "%3"}, run, []), "%3")
        self.assertEqual(calls, [])

    def test_a_parked_session_is_shown_in_the_pane_that_parked_it(self):
        run, _ = fake()
        self.assertEqual(tmux.own_pane(BG_ENV, run, [bg(), viewer()]), "%7")

    def test_the_session_record_is_found_by_session_id_too(self):
        run, _ = fake()
        env = dict(BG_ENV, CLAUDE_PID="")
        self.assertEqual(tmux.own_pane(env, run, [bg(), viewer()]), "%7")

    def test_after_bg_no_viewer_is_left_so_no_pane(self):
        """`/bg` quits the `claude` in the pane: nothing names the session any more."""
        run, _ = fake()
        self.assertEqual(tmux.own_pane(BG_ENV, run, [bg()]), "")

    def test_a_session_attached_again_from_the_agents_view_is_shown_where_its_client_runs(self):
        """Opened again from the overview, the pane runs `claude attach <job>`, with no
        record: its command line is the link."""
        run, _ = fake(commands={500: "/opt/claude/2.1/claude attach 8b2c5945"})
        self.assertEqual(tmux.own_pane(BG_ENV, run, [bg()]), "%7")

    def test_a_client_attached_to_another_job_is_not_ours(self):
        run, _ = fake(commands={500: "/opt/claude/2.1/claude attach 5bf2674b"})
        self.assertEqual(tmux.own_pane(BG_ENV, run, [bg()]), "")

    def test_two_clients_of_the_job_leave_it_unknown(self):
        tree = {**TREE, 510: 410, 410: 1}
        run, _ = fake(pane_pids={"%7": 400, "%8": 410}, tree=tree,
                      commands={500: "claude attach 8b2c5945", 510: "claude attach 8b2c5945"})
        self.assertEqual(tmux.own_pane(BG_ENV, run, [bg()]), "")

    def test_a_viewer_of_another_session_is_not_ours(self):
        run, _ = fake()
        self.assertEqual(tmux.own_pane(BG_ENV, run, [bg(), viewer(parked="5bf2674b")]), "")

    def test_a_record_left_by_a_client_that_is_gone_is_not_believed(self):
        """The pane now runs something else: its process is not the record's client's."""
        run, _ = fake(tree={500: 1, 400: 1})
        self.assertEqual(tmux.own_pane(BG_ENV, run, [bg(), viewer()]), "")

    def test_a_pane_tmux_no_longer_knows_is_no_pane(self):
        run, _ = fake(pane_pids={})
        self.assertEqual(tmux.own_pane(BG_ENV, run, [bg(), viewer()]), "")

    def test_two_viewers_claiming_one_session_are_not_guessed_between(self):
        run, _ = fake()
        records = [bg(), viewer(), viewer(pid=501, where="main:@4.%8")]
        self.assertEqual(tmux.own_pane(BG_ENV, run, records), "")

    def test_a_foreground_session_without_the_variables_is_not_in_tmux(self):
        """A plain terminal: the session's own record says interactive, so no viewer is
        looked for."""
        run, calls = fake()
        own = dict(bg(), kind="interactive", jobId=None)
        self.assertEqual(tmux.own_pane(BG_ENV, run, [own, viewer()]), "")
        self.assertFalse(tmux.in_background(BG_ENV, [own]))
        self.assertEqual(calls, [])

    def test_the_sessions_own_record_says_it_runs_in_the_background(self):
        self.assertTrue(tmux.in_background(BG_ENV, [bg()]))
        self.assertFalse(tmux.in_background(BG_ENV, []))

    def test_malformed_records_are_no_pane(self):
        run, _ = fake()
        for broken in (viewer(where="no-pane-here"), dict(viewer(), pid="500")):
            self.assertEqual(tmux.own_pane(BG_ENV, run, [bg(), broken]), "")

    def test_a_loop_in_the_parent_table_ends(self):
        run, _ = fake(tree={500: 501, 501: 500})
        self.assertEqual(tmux.own_pane(BG_ENV, run, [bg(), viewer()]), "")


class TestHint(unittest.TestCase):
    def test_it_names_the_commands_that_bring_the_session_back(self):
        hint = tmux.background_hint(BG_ENV, [bg()])
        self.assertIn("`claude stop 8b2c5945`", hint)
        self.assertIn("`claude --resume 8b2c5945-0000-4000-8000-000000000000`", hint)


if __name__ == "__main__":
    unittest.main()
