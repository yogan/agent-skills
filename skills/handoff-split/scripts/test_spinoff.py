#!/usr/bin/env python3
"""Tests for handoff-split's spinoff script, against real throwaway git repositories.

Run: `python3 skills/handoff-split/scripts/test_spinoff.py` (stdlib only).

Each test builds a bare "remote" and a clone of it inside its own TemporaryDirectory, so
the worktrees the script creates (siblings of the clone) are removed with it. tmux is
never really called: `launch` takes the runner and the environment as parameters.
"""
import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import spinoff as S  # noqa: E402


def sh(*args, cwd):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True)


def out(*args, cwd):
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True,
                          text=True).stdout.strip()


class Repo(unittest.TestCase):
    """A clone of a bare remote whose main has one commit more than the clone's feature
    branch started from, so "from the default branch" and "from HEAD" differ."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = os.path.realpath(tmp.name)
        remote = os.path.join(self.tmp, "remote.git")
        seed = os.path.join(self.tmp, "seed")
        self.clone = os.path.join(self.tmp, "app")
        sh("git", "init", "-q", "--bare", "-b", "main", remote, cwd=self.tmp)
        sh("git", "init", "-q", "-b", "main", seed, cwd=self.tmp)
        for args in (("config", "user.email", "t@example.com"), ("config", "user.name", "t"),
                     ("commit", "-q", "--allow-empty", "-m", "one"),
                     ("remote", "add", "origin", remote), ("push", "-q", "origin", "main")):
            sh("git", *args, cwd=seed)
        sh("git", "clone", "-q", remote, self.clone, cwd=self.tmp)
        for args in (("config", "user.email", "t@example.com"), ("config", "user.name", "t"),
                     ("checkout", "-q", "-b", "feat/current"),
                     ("commit", "-q", "--allow-empty", "-m", "feature work")):
            sh("git", *args, cwd=self.clone)
        sh("git", "commit", "-q", "--allow-empty", "-m", "two", cwd=seed)
        sh("git", "push", "-q", "origin", "main", cwd=seed)
        self.handoffs = os.path.join(self.tmp, "handoffs")
        os.mkdir(self.handoffs)
        sh("git", "config", S.CONFIG_KEY, self.handoffs, cwd=self.clone)
        self.remote_main = out("git", "rev-parse", "main", cwd=remote)

    def write_handoff(self, name):
        with open(os.path.join(self.handoffs, f"{name}-handoff.md"), "w") as fh:
            fh.write("# handoff\n")


class TestPlan(Repo):
    def test_paths_sit_beside_the_main_checkout(self):
        p = S.plan("fix-x", "fix/x", cwd=self.clone)
        self.assertEqual(p["worktree"], f"{self.clone}.wt/fix-x")
        self.assertEqual(p["handoff"], os.path.join(self.handoffs, "fix-x-handoff.md"))
        self.assertEqual(p["base"], "origin/main")

    def test_from_a_linked_worktree_paths_still_hang_off_the_main_checkout(self):
        """A spin-off of a spin-off lands beside the others instead of nesting."""
        linked = f"{self.clone}.wt/first"
        sh("git", "worktree", "add", "-q", "-b", "fix/first", linked, cwd=self.clone)
        p = S.plan("second", "fix/second", cwd=linked)
        self.assertEqual(p["worktree"], f"{self.clone}.wt/second")

    def test_stacked_starts_from_head(self):
        head = out("git", "rev-parse", "--short", "HEAD", cwd=self.clone)
        self.assertEqual(S.plan("x", "fix/x", stacked=True, cwd=self.clone)["base"], head)

    def test_without_config_the_handoff_goes_to_the_temp_dir(self):
        sh("git", "config", "--unset", S.CONFIG_KEY, cwd=self.clone)
        p = S.plan("x", "fix/x", cwd=self.clone)
        self.assertEqual(os.path.dirname(p["handoff"]), tempfile.gettempdir())

    def test_refuses_what_would_collide(self):
        for name, branch in (("x", "feat/current"), ("Bad Name", "fix/x"), ("x", "fix..x")):
            with self.subTest(name=name, branch=branch), self.assertRaises(SystemExit):
                S.plan(name, branch, cwd=self.clone)
        self.write_handoff("taken")
        with self.assertRaises(SystemExit):
            S.plan("taken", "fix/taken", cwd=self.clone)

    def test_without_a_remote_names_the_way_out(self):
        sh("git", "remote", "remove", "origin", cwd=self.clone)
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit):
            S.plan("x", "fix/x", cwd=self.clone)
        self.assertIn("--dir", err.getvalue())
        self.assertTrue(S.plan("x", "fix/x", stacked=True, cwd=self.clone)["base"])


class TestDir(unittest.TestCase):
    """A side task that is no branch: the session starts in a directory, nothing in git."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = os.path.realpath(tmp.name)
        self.name = f"test-spinoff-{os.getpid()}-{id(self)}"
        self.handoff = os.path.join(tempfile.gettempdir(), f"{self.name}-handoff.md")

    def write_handoff(self):
        with open(self.handoff, "w") as fh:
            fh.write("# handoff\n")
        self.addCleanup(os.remove, self.handoff)

    def main(self, *args):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            S.main(list(args))
        return buf.getvalue()

    def test_plan_names_the_directory_and_a_handoff_outside_it(self):
        printed = self.main("plan", "--name", self.name, "--dir", self.dir)
        self.assertEqual(printed, f"name={self.name}\ndir={self.dir}\nhandoff={self.handoff}\n")

    def test_refuses_a_missing_directory_or_a_branch_option(self):
        for args in (("--dir", os.path.join(self.dir, "gone")),
                     ("--dir", self.dir, "--stacked"),
                     ("--dir", self.dir, "--branch", "fix/x")):
            with self.subTest(args=args), self.assertRaises(SystemExit), \
                    contextlib.redirect_stderr(io.StringIO()):
                self.main("plan", "--name", self.name, *args)

    def test_a_clean_checkout_is_worked_in_on_its_branch(self):
        sh("git", "init", "-q", "-b", "main", self.dir, cwd=self.dir)
        printed = self.main("plan", "--name", self.name, "--dir", self.dir)
        self.assertIn("\nbranch=main\n", printed)

    def test_refuses_a_checkout_with_uncommitted_changes(self):
        sh("git", "init", "-q", "-b", "main", self.dir, cwd=self.dir)
        with open(os.path.join(self.dir, "wip.txt"), "w") as fh:
            fh.write("half done\n")
        err = io.StringIO()
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(err):
            self.main("plan", "--name", self.name, "--dir", self.dir)
        self.assertIn("wip.txt", err.getvalue())

    def test_commit_pending_hands_the_changes_over(self):
        """Only when asked: the side task is to commit what is uncommitted there."""
        sh("git", "init", "-q", "-b", "main", self.dir, cwd=self.dir)
        with open(os.path.join(self.dir, "wip.txt"), "w") as fh:
            fh.write("half done\n")
        p = S.plan_dir(self.name, self.dir, commit_pending=True)
        self.assertEqual(p["pending"], "wip.txt")
        self.assertIn("handed to you to commit", S.prompt_for(p))

    def test_commit_pending_needs_a_checkout(self):
        for args in (("--dir", self.dir), ("--branch", "fix/x")):
            with self.subTest(args=args), self.assertRaises(SystemExit), \
                    contextlib.redirect_stderr(io.StringIO()):
                self.main("plan", "--name", self.name, "--commit-pending", *args)

    def test_start_launches_in_the_directory_without_touching_git(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            self.main("start", "--name", self.name, "--dir", self.dir)
        self.write_handoff()
        with unittest.mock.patch.dict(os.environ, clear=True):
            printed = self.main("start", "--name", self.name, "--dir", self.dir)
        self.assertIn(f"cd {self.dir} && claude", printed)
        self.assertIn("no worktree or branch", printed)
        self.assertEqual(os.listdir(self.dir), [])


class TestCli(Repo):
    def test_repo_points_at_another_repository(self):
        """Run from anywhere, the plan follows `--repo`, not the current directory."""
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            S.main(["plan", "--name", "x", "--branch", "fix/x", "--repo", self.clone])
        self.assertIn(f"worktree={self.clone}.wt/x", buf.getvalue())


class TestStart(Repo):
    def test_refuses_before_the_handoff_exists(self):
        with self.assertRaises(SystemExit):
            S.start("x", "fix/x", cwd=self.clone, env={})
        self.assertFalse(os.path.exists(f"{self.clone}.wt/x"))

    def test_worktree_starts_from_the_freshly_fetched_default_branch(self):
        self.write_handoff("x")
        p, outcome = S.start("x", "fix/x", cwd=self.clone, env={})
        self.assertEqual(out("git", "rev-parse", "HEAD", cwd=p["worktree"]), self.remote_main)
        self.assertEqual(out("git", "branch", "--show-current", cwd=p["worktree"]), "fix/x")
        # No upstream: the branch is new work, not a copy of origin/main to pull into.
        self.assertEqual(subprocess.run(["git", "rev-parse", "@{u}"], cwd=p["worktree"],
                                        capture_output=True).returncode, 128)
        self.assertIn("not in tmux", outcome)

    def test_stacked_worktree_carries_the_current_branch(self):
        self.write_handoff("x")
        p, _ = S.start("x", "fix/x", stacked=True, cwd=self.clone, env={})
        self.assertEqual(out("git", "log", "-1", "--format=%s", cwd=p["worktree"]),
                         "feature work")


class TestLaunch(unittest.TestCase):
    P = {"name": "x", "branch": "fix/x", "base": "origin/main", "worktree": "/w/app.wt/x",
         "handoff": "/h/x-handoff.md"}

    def runner(self, calls, stdout="3"):
        def run(argv, **_):
            calls.append(argv)
            return SimpleNamespace(returncode=0, stdout="@7" if "display-message" in argv
                                   else stdout, stderr="")
        return run

    def test_opens_a_window_beside_the_current_one_without_switching(self):
        calls = []
        outcome = S.launch(self.P, {"TMUX": "1", "TMUX_PANE": "%1"}, self.runner(calls))
        argv = calls[-1]
        self.assertEqual(argv[:3], ["tmux", "new-window", "-d"])
        self.assertEqual(argv[argv.index("-a") + 2], "@7")
        self.assertEqual(argv[argv.index("-c") + 1], "/w/app.wt/x")
        self.assertTrue(argv[-1].startswith("claude "))
        self.assertIn("/h/x-handoff.md", argv[-1])
        self.assertEqual(outcome, "started in tmux window 3 (x)")

    def test_model_is_passed_to_claude(self):
        self.assertEqual(S.agent_command("go", {}, "opus"), ["claude", "--model", "opus", "go"])
        self.assertEqual(S.agent_command("go", {}), ["claude", "go"])

    def test_opencode_starts_opencode(self):
        cmd = S.agent_command("go", {"OPENCODE": "1"})
        self.assertEqual(cmd, ["opencode", "--prompt", "go"])

    def test_a_checkout_task_is_told_it_shares_the_checkout(self):
        p = {"name": "x", "dir": "/src/skills", "branch": "main", "handoff": "/h/x-handoff.md"}
        prompt = S.prompt_for(p)
        self.assertIn("checkout /src/skills itself, on branch main", prompt)
        self.assertIn("another session", prompt)

    def test_a_dir_task_opens_its_window_in_that_directory(self):
        calls = []
        p = {"name": "x", "dir": "/src", "handoff": "/h/x-handoff.md"}
        S.launch(p, {"TMUX": "1", "TMUX_PANE": "%1"}, self.runner(calls))
        argv = calls[-1]
        self.assertEqual(argv[argv.index("-c") + 1], "/src")
        self.assertIn("runs in /src, with no worktree or branch", argv[-1])

    def test_outside_tmux_hands_the_command_over(self):
        calls = []
        outcome = S.launch(self.P, {}, self.runner(calls))
        self.assertEqual(calls, [])
        self.assertIn("cd /w/app.wt/x && claude", outcome)


if __name__ == "__main__":
    unittest.main()
