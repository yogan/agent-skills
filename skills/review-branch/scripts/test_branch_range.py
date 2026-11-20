"""branch-range.sh: what it prints is sourced by a shell, so every value must come back as
itself, whatever the branch is called."""
import os
import subprocess
import tempfile
import unittest

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "branch-range.sh")
IDENT = ["-c", "user.email=t@example.com", "-c", "user.name=t"]


def git(cwd, *args):
    return subprocess.run(["git", *IDENT, *args], cwd=cwd, check=True, capture_output=True,
                          text=True).stdout.strip()


class TestBranchRange(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = os.path.realpath(tmp.name)
        origin, self.repo = os.path.join(self.tmp, "origin.git"), os.path.join(self.tmp, "app")
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", origin], check=True)
        subprocess.run(["git", "clone", "-q", origin, self.repo], check=True, capture_output=True)
        git(self.repo, "commit", "-q", "--allow-empty", "-m", "one")
        git(self.repo, "push", "-q", "origin", "main")
        self.base = git(self.repo, "rev-parse", "HEAD")

    def sourced(self):
        """The variables as a shell that sources the script's output sees them."""
        env = {**os.environ, "REVIEW_BRANCH_BASE": self.base}
        out = subprocess.run(["bash", SCRIPT], cwd=self.repo, env=env, check=True,
                             capture_output=True, text=True).stdout
        shown = subprocess.run(["bash", "-c", out + '\nprintf "%s\\n" "$BRANCH_NAME" "$COMMIT_COUNT"'],
                               cwd=self.tmp, capture_output=True, text=True)
        return shown.stdout.splitlines()

    def test_an_mr_branch_checked_out_detached_names_itself_and_runs_nothing(self):
        name = "x\"'\"$(touch${IFS}ran)\"'\""
        git(self.repo, "switch", "-q", "-c", name)
        git(self.repo, "commit", "-q", "--allow-empty", "-m", "two")
        git(self.repo, "push", "-q", "origin", name)
        git(self.repo, "switch", "-q", "--detach", f"origin/{name}")
        self.assertEqual(self.sourced(), [name, "1"])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "ran")))

    def test_a_detached_head_on_no_branch_says_so(self):
        git(self.repo, "commit", "-q", "--allow-empty", "-m", "two")
        git(self.repo, "switch", "-q", "--detach", "HEAD")
        sha = git(self.repo, "rev-parse", "--short=7", "HEAD")
        self.assertEqual(self.sourced(), [f"detached@{sha}", "1"])

    def test_standalone_without_origin_main_says_why_it_stops(self):
        """`set -e` exited on grep's empty match before the message, so it stopped silently."""
        git(self.repo, "remote", "remove", "origin")
        env = {**os.environ, "REVIEW_BRANCH_STANDALONE": "1"}
        env.pop("REVIEW_BRANCH_BASE", None)
        r = subprocess.run(["bash", SCRIPT], cwd=self.repo, env=env, capture_output=True,
                           text=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("Cannot find origin/main or origin/master", r.stderr)


if __name__ == "__main__":
    unittest.main()
