#!/usr/bin/env python3
"""Tests for news.py: a command checks everything before it changes anything, and its answer
goes into a thread it fits.

Run: `python3 e2e/test_news.py` (no GitLab, no git: both are stood in for, and the only files
written are in a temporary folder the test removes).
"""
import io
import os
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fixture  # noqa: E402
import news  # noqa: E402

AUTH = "src/testing/mocks/handlers/auth.ts"
DB = "src/testing/mocks/db.ts"
PROTECTED = "src/routes/protected.tsx"
LIST = "src/features/discussions/api/getDiscussions.ts"


def thread(tid, user, path, *bodies):
    """A thread as GitLab lists it: its first comment by `user` on `path`, then Alex's."""
    notes = [{"author": {"username": user}, "position": {"new_path": path}, "body": bodies[0]}]
    notes += [{"author": {"username": "author-bot"}, "body": b} for b in bodies[1:]]
    return {"id": tid, "notes": notes}


class Rig:
    """GitLab and git as news.py sees them: the files at each branch's tip, the threads, and
    every change made, in order (`done`)."""

    def __init__(self, build, files, threads=(), merge_base="OLD"):
        self.build, self.files, self.threads, self.merge_base = build, files, list(threads), merge_base
        self.done = []

    def git(self, *args, cwd=None, user=None, date=None, check=True):
        out = ""
        if args[0] == "rev-parse":
            out = "MAIN" if args[1].endswith("/main") else "TIP"
        elif args[0] == "show":
            out = self.files[args[1].split(":", 1)[1]]
        elif args[0] == "merge-base":
            out = self.merge_base
        elif args[0] == "checkout":
            for path, text in self.files.items():
                full = os.path.join(self.build, path)
                os.makedirs(os.path.dirname(full), exist_ok=True)
                with open(full, "w") as f:
                    f.write(text)
        elif args[0] in ("push", "commit", "rebase"):
            self.done.append(args[0])
        return types.SimpleNamespace(stdout=out + "\n", stderr="", returncode=0)

    def api(self, path, method="GET", body=None, user="root", **_kw):
        if path.endswith("/discussions?per_page=100"):
            return self.threads
        raise AssertionError(f"unexpected call: {method} {path}")

    def post_thread(self, iid, user, path, needle, body):
        self.done.append(("post", path))
        return {"id": "NEW"}

    def reply(self, iid, discussion_id, user, body):
        self.done.append(("reply", discussion_id))


class NewsTest(unittest.TestCase):
    def setUp(self):
        build = tempfile.TemporaryDirectory()
        self.addCleanup(build.cleanup)
        self.build = build.name

    def run_news(self, rig, *argv):
        """news.py's exit status for `argv` on `rig`, and what it printed."""
        patches = [mock.patch.object(fixture, name, getattr(rig, name))
                   for name in ("git", "api", "post_thread", "reply")]
        patches += [mock.patch.object(fixture, "BUILD", self.build),
                    mock.patch.object(fixture, "env", lambda: {"E2E_PROJECT": "demo/x", "E2E_GITLAB_HOST": "h",
                                                               "E2E_TOKEN_ROOT": "t", "E2E_TOKEN_AUTHOR_BOT": "t"}),
                    mock.patch.object(sys, "argv", ["news.py", *argv])]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(out):
            try:
                code = news.main()
            except SystemExit as e:
                code = e.code
        return code, out.getvalue()

    def files(self, **changed):
        """MR !1 and !2 as the fixture leaves them, but for `changed` ({file: text})."""
        base = {AUTH: "x\n" + news.AUTH_ME, DB: "x\n" + news.LOAD_DB,
                "src/testing/mocks/handlers/comments.ts": news.UNAWAITED,
                PROTECTED: news.CATCH, LIST: news.LIST_KEY, "README.md": "# demo\n"}
        return {**base, **changed}

    # The manual round: `both` after `push` posted its reply, then failed on its push.
    def test_a_refused_command_changes_nothing(self):
        rig = Rig(self.build, self.files(**{DB: "fixed already\n"}),
                  [thread("T", "frank", DB, "loadDb returns null here")])
        code, out = self.run_news(rig, "both")
        self.assertNotEqual(code, 0)
        self.assertEqual(rig.done, [])
        self.assertIn("Nothing was changed", out)

    def test_both_posts_your_thread_pushes_and_answers_it(self):
        rig = Rig(self.build, self.files())
        code, _ = self.run_news(rig, "both")
        self.assertEqual(code, 0)
        self.assertEqual(rig.done, [("post", DB), "commit", "push", ("reply", "NEW")])
        with open(os.path.join(self.build, DB)) as f:
            self.assertIn(news.LOAD_DB_FIXED, f.read())

    def test_reply_answers_your_thread_about_the_point(self):
        rig = Rig(self.build, self.files(),
                  [thread("OTHER", "frank", DB, "loadDb returns null"),
                   thread("MINE", "frank", AUTH, "Should /auth/me not answer 401 here?")])
        self.assertEqual(self.run_news(rig, "reply")[0], 0)
        self.assertEqual(rig.done, [("reply", "MINE")])

    # The manual round: a question about something else on the file got the canned answer.
    def test_reply_never_answers_a_thread_about_something_else(self):
        rig = Rig(self.build, self.files(),
                  [thread("ASKED", "frank", AUTH, "Why did we have to destructure here?")])
        self.assertEqual(self.run_news(rig, "reply")[0], 0)
        self.assertEqual(rig.done, [("post", AUTH), ("reply", "NEW")])

    def test_each_command_runs_once(self):
        rig = Rig(self.build, self.files(),
                  [thread("MINE", "frank", AUTH, "401 please", news.REPLY)])
        code, out = self.run_news(rig, "reply")
        self.assertNotEqual(code, 0)
        self.assertEqual(rig.done, [])
        self.assertIn("`reply` ran before", out)

    def test_reply_after_fix_refuses(self):
        rig = Rig(self.build, self.files(**{AUTH: news.AUTH_ME_FIXED}))
        code, out = self.run_news(rig, "reply")
        self.assertNotEqual(code, 0)
        self.assertEqual(rig.done, [])
        self.assertIn("`fix` ran", out)

    def test_rebase_fix_needs_main_to_have_moved(self):
        rig = Rig(self.build, self.files(), [thread("T2", "frank", PROTECTED, "What happens when the prefetch fails?")],
                  merge_base="MAIN")
        code, out = self.run_news(rig, "rebase-fix")
        self.assertNotEqual(code, 0)
        self.assertEqual(rig.done, [])
        self.assertIn("news.py main", out)

    def test_rebase_fix_rebases_pushes_and_answers_t2_alone(self):
        rig = Rig(self.build, self.files(), [thread("T4", "frank", LIST, "The key ['discussions'] now exists twice"),
                                             thread("T2", "frank", PROTECTED, "What happens when the prefetch fails?")])
        self.assertEqual(self.run_news(rig, "rebase-fix")[0], 0)
        self.assertEqual(rig.done, ["rebase", "commit", "push", ("reply", "T2")])

    def test_help_says_which_commands_exclude_each_other(self):
        code, out = self.run_news(Rig(self.build, {}), "help")
        self.assertEqual(code, 0)
        self.assertIn("exclude each other", out)
        self.assertIn("`rebase-fix` needs `main` first", out)


if __name__ == "__main__":
    unittest.main()
