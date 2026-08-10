#!/usr/bin/env python3
"""Tests for explain-diff's resolve-target.py.

Run: `python3 skills/explain-diff/scripts/test_resolve_target.py` (stdlib only).

Every git/glab call is mocked (subprocess.run is patched at the module level, since
resolve-target.py calls it directly rather than through one wrapper) — no real git repo
or glab auth is touched. The shlex.quote regression test guards a real fix (see
269e9f8): an MR title containing quotes/apostrophes used to be able to break sourcing
the script's output as shell.
"""
import importlib.util
import io
import os
import shlex
import sys
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "resolve-target.py")

spec = importlib.util.spec_from_file_location("resolve_target", SCRIPT)
RT = importlib.util.module_from_spec(spec)
spec.loader.exec_module(RT)


def fake_result(returncode=0, stdout="", stderr=""):
    class R:
        pass
    r = R()
    r.returncode, r.stdout, r.stderr = returncode, stdout, stderr
    return r


def dispatch(rules):
    """subprocess.run side_effect: `rules` maps a substring of the command to a
    fake_result; the first matching rule (in order) wins. Falls back to a plain
    success with empty output so calls the test doesn't care about don't blow up."""
    def run(args, **kw):
        cmd = " ".join(args)
        for needle, result in rules:
            if needle in cmd:
                return result
        return fake_result()
    return run


class TestFindMainRef(unittest.TestCase):
    def test_prefers_whichever_of_main_or_master_is_present(self):
        with patch("subprocess.run", side_effect=dispatch([
            ("branch -r", fake_result(stdout="  origin/main\n  origin/HEAD\n")),
        ])):
            self.assertEqual(RT.find_main_ref(), "origin/main")

    def test_finds_master_when_main_is_absent(self):
        with patch("subprocess.run", side_effect=dispatch([
            ("branch -r", fake_result(stdout="  origin/master\n")),
        ])):
            self.assertEqual(RT.find_main_ref(), "origin/master")

    def test_empty_when_neither_exists(self):
        with patch("subprocess.run", side_effect=dispatch([
            ("branch -r", fake_result(stdout="  origin/develop\n")),
        ])):
            self.assertEqual(RT.find_main_ref(), "")


class TestResolveBlank(unittest.TestCase):
    def test_normal_branch(self):
        with patch("subprocess.run", side_effect=dispatch([
            ("branch -r", fake_result(stdout="origin/main\n")),
            ("rev-parse HEAD", fake_result(stdout="abc1234\n")),
            ("merge-base", fake_result(stdout="base5678\n")),
            ("symbolic-ref", fake_result(stdout="feat/thing\n")),
        ])):
            out = RT.resolve_blank()
        self.assertEqual(out, {"BASE": "base5678", "HEAD_REF": "abc1234",
                               "LABEL": "feat/thing", "IS_SINGLE_COMMIT": "false"})

    def test_detached_head_with_a_tag_uses_the_tag_as_label(self):
        with patch("subprocess.run", side_effect=dispatch([
            ("branch -r", fake_result(stdout="origin/main\n")),
            ("rev-parse HEAD", fake_result(stdout="abc1234\n")),
            ("merge-base", fake_result(stdout="base5678\n")),
            ("symbolic-ref", fake_result(returncode=1)),
            ("describe", fake_result(stdout="v1.2.3\n")),
        ])):
            out = RT.resolve_blank()
        self.assertEqual(out["LABEL"], "v1.2.3")

    def test_detached_head_without_a_tag_uses_a_short_sha_label(self):
        with patch("subprocess.run", side_effect=dispatch([
            ("branch -r", fake_result(stdout="origin/main\n")),
            ("rev-parse HEAD", fake_result(stdout="abcdef1234567\n")),
            ("merge-base", fake_result(stdout="base5678\n")),
            ("symbolic-ref", fake_result(returncode=1)),
            ("describe", fake_result(returncode=1)),
        ])):
            out = RT.resolve_blank()
        self.assertEqual(out["LABEL"], "detached@abcdef1")

    def test_dies_when_no_main_ref(self):
        with patch("subprocess.run", side_effect=dispatch([
            ("branch -r", fake_result(stdout="origin/develop\n")),
        ])):
            with self.assertRaises(SystemExit):
                RT.resolve_blank()


class TestResolveBranch(unittest.TestCase):
    def test_dies_on_empty_value(self):
        with self.assertRaises(SystemExit):
            RT.resolve_branch("")

    def test_dies_when_no_main_ref(self):
        with patch("subprocess.run", side_effect=dispatch([
            ("branch -r", fake_result(stdout="")),
        ])):
            with self.assertRaises(SystemExit):
                RT.resolve_branch("feat/thing")

    def test_prefers_the_remote_branch(self):
        with patch("subprocess.run", side_effect=dispatch([
            ("branch -r", fake_result(stdout="origin/main\n")),
            ("show-ref --verify --quiet refs/remotes/origin/feat/thing", fake_result()),
            ("merge-base", fake_result(stdout="base123\n")),
        ])):
            out = RT.resolve_branch("feat/thing")
        self.assertEqual(out["HEAD_REF"], "origin/feat/thing")

    def test_falls_back_to_the_local_branch(self):
        with patch("subprocess.run", side_effect=dispatch([
            ("branch -r", fake_result(stdout="origin/main\n")),
            ("show-ref --verify --quiet refs/remotes/origin/feat/thing", fake_result(returncode=1)),
            ("show-ref --verify --quiet refs/heads/feat/thing", fake_result()),
            ("merge-base", fake_result(stdout="base123\n")),
        ])):
            out = RT.resolve_branch("feat/thing")
        self.assertEqual(out["HEAD_REF"], "feat/thing")

    def test_dies_when_branch_is_nowhere(self):
        with patch("subprocess.run", side_effect=dispatch([
            ("branch -r", fake_result(stdout="origin/main\n")),
            ("show-ref", fake_result(returncode=1)),
        ])):
            with self.assertRaises(SystemExit):
                RT.resolve_branch("nope")


class TestResolveMr(unittest.TestCase):
    def test_dies_when_spec_has_no_digits(self):
        with self.assertRaises(SystemExit):
            RT.resolve_mr("abc")

    def test_dies_when_glab_missing(self):
        with patch("shutil.which", return_value=None):
            with self.assertRaises(SystemExit):
                RT.resolve_mr("123")

    def test_dies_when_glab_fetch_fails(self):
        with patch("shutil.which", return_value="/usr/bin/glab"), \
             patch("subprocess.run", side_effect=dispatch([
                ("glab mr view", fake_result(returncode=1, stderr="not found")),
             ])):
            with self.assertRaises(SystemExit):
                RT.resolve_mr("123")

    def test_extracts_fields_and_builds_the_label(self):
        mr_json = ('{"source_branch": "feat/thing", "target_branch": "main", '
                  '"title": "Fix the retry loop", '
                  '"web_url": "https://example.com/merge_requests/123"}')
        with patch("shutil.which", return_value="/usr/bin/glab"), \
             patch("subprocess.run", side_effect=dispatch([
                ("glab mr view", fake_result(stdout=mr_json)),
                ("merge-base", fake_result(stdout="base123\n")),
             ])):
            out = RT.resolve_mr("123")
        self.assertEqual(out["MR_NUM"], "123")
        self.assertEqual(out["MR_URL"], "https://example.com/merge_requests/123")
        self.assertEqual(out["MR_TITLE"], "Fix the retry loop")
        self.assertEqual(out["HEAD_REF"], "origin/feat/thing")
        self.assertEqual(out["LABEL"], "mr-123-Fix the retry loop")

    def test_dies_when_source_or_target_branch_missing(self):
        with patch("shutil.which", return_value="/usr/bin/glab"), \
             patch("subprocess.run", side_effect=dispatch([
                ("glab mr view", fake_result(stdout='{"title": "x"}')),
             ])):
            with self.assertRaises(SystemExit):
                RT.resolve_mr("123")


class TestResolveCommit(unittest.TestCase):
    def test_dies_when_ref_not_found(self):
        with patch("subprocess.run", side_effect=dispatch([
            ("rev-parse --verify --quiet", fake_result(returncode=1)),
        ])):
            with self.assertRaises(SystemExit):
                RT.resolve_commit("deadbeef")

    def test_dies_when_commit_has_no_parent(self):
        with patch("subprocess.run", side_effect=dispatch([
            ("rev-parse --verify --quiet HEAD^{commit}", fake_result()),
            ("rev-parse HEAD", fake_result(stdout="abc1234\n")),
            ("rev-parse --verify --quiet HEAD^", fake_result(returncode=1)),
        ])):
            with self.assertRaises(SystemExit):
                RT.resolve_commit("HEAD")

    def test_defaults_to_head_and_resolves_the_parent(self):
        with patch("subprocess.run", side_effect=dispatch([
            ("rev-parse --verify --quiet HEAD^{commit}", fake_result()),
            ("rev-parse HEAD", fake_result(stdout="abcdef1234567\n")),
            ("rev-parse --verify --quiet HEAD^", fake_result(stdout="parent7654321\n")),
        ])):
            out = RT.resolve_commit("")
        self.assertEqual(out["HEAD_REF"], "abcdef1234567")
        self.assertEqual(out["BASE"], "parent7654321")
        self.assertEqual(out["LABEL"], "commit-abcdef1")
        self.assertEqual(out["IS_SINGLE_COMMIT"], "true")


class TestSlugify(unittest.TestCase):
    def test_replaces_non_alnum_runs_with_one_hyphen(self):
        self.assertEqual(RT.slugify("mr-123-Fix the retry loop"), "mr-123-Fix-the-retry-loop")

    def test_strips_leading_and_trailing_hyphens(self):
        self.assertEqual(RT.slugify("--detached@abc1234"), "detached-abc1234")


class TestMainOutputQuoting(unittest.TestCase):
    """269e9f8: MR JSON field extraction used to grep raw text and produced output that
    could break sourcing when a title contained quotes/apostrophes. The current
    implementation uses json.loads for parsing and shlex.quote for output — this proves
    the output side stays shell-safe for the adversarial titles that broke it."""

    def _run_main_with(self, mr_title):
        with patch.object(RT, "resolve_blank", side_effect=AssertionError("wrong resolver")), \
             patch.object(RT, "resolve_branch", side_effect=AssertionError("wrong resolver")), \
             patch.object(RT, "resolve_commit", side_effect=AssertionError("wrong resolver")), \
             patch.object(RT, "resolve_mr", return_value={
                "BASE": "base123", "HEAD_REF": "origin/feat", "LABEL": "irrelevant",
                "IS_SINGLE_COMMIT": "false", "MR_NUM": "123",
                "MR_URL": "https://example.com/123", "MR_TITLE": mr_title,
             }), \
             patch.object(sys, "argv", ["resolve-target.py", "mr:123"]):
            buf = io.StringIO()
            with redirect_stdout(buf):
                RT.main()
        return buf.getvalue()

    def _sourced_vars(self, output):
        """Parse `NAME=quoted-value` lines the way a shell sourcing this output would."""
        result = {}
        for line in output.splitlines():
            name, _, quoted = line.partition("=")
            result[name] = shlex.split(quoted)[0] if quoted.strip() else ""
        return result

    def test_title_with_double_quotes_round_trips(self):
        title = 'Fix "the retry loop"'
        out = self._run_main_with(title)
        self.assertEqual(self._sourced_vars(out)["MR_TITLE"], title)

    def test_title_with_apostrophe_round_trips(self):
        title = "Don't retry forever"
        out = self._run_main_with(title)
        self.assertEqual(self._sourced_vars(out)["MR_TITLE"], title)

    def test_title_with_dollar_and_backtick_round_trips(self):
        title = "Fix $HOME and `whoami` handling"
        out = self._run_main_with(title)
        self.assertEqual(self._sourced_vars(out)["MR_TITLE"], title)

    def test_unknown_spec_kind_dies(self):
        with patch.object(sys, "argv", ["resolve-target.py", "bogus:1"]):
            with self.assertRaises(SystemExit):
                RT.main()


if __name__ == "__main__":
    unittest.main(verbosity=2)
