#!/usr/bin/env python3
"""Tests for the shared GitLab/glab helpers — see gitlab.py's module docstring for why
this, unlike the two skills' own rendering/state logic, moved here wholesale.

Run: `python3 lib/test_gitlab.py` (stdlib only).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib import gitlab as G


def fake_result(returncode=0, stdout="", stderr=""):
    class R:
        pass
    r = R()
    r.returncode, r.stdout, r.stderr = returncode, stdout, stderr
    return r


class TestParseRemote(unittest.TestCase):
    def test_ssh_style(self):
        self.assertEqual(G.parse_remote("git@gitlab.com:group/proj.git"),
                          ("gitlab.com", "group/proj"))

    def test_https_style(self):
        self.assertEqual(G.parse_remote("https://gitlab.com/group/proj.git"),
                          ("gitlab.com", "group/proj"))

    def test_https_with_embedded_credentials(self):
        self.assertEqual(G.parse_remote("https://user:tok@gitlab.example.com/g/p.git"),
                          ("gitlab.example.com", "g/p"))

    def test_without_dot_git_suffix(self):
        self.assertEqual(G.parse_remote("https://gitlab.com/group/proj"),
                          ("gitlab.com", "group/proj"))


class TestWebBase(unittest.TestCase):
    def test_extracts_the_project_base(self):
        self.assertEqual(
            G.web_base("https://gitlab.example.com/g/p/-/merge_requests/12"),
            "https://gitlab.example.com/g/p")

    def test_none_when_unusable(self):
        self.assertIsNone(G.web_base(None))
        self.assertIsNone(G.web_base("not a merge request url"))


class TestApi(unittest.TestCase):
    def test_parses_a_plain_json_response(self):
        G.run = lambda cmd: fake_result(stdout='{"iid": 1}')
        self.assertEqual(G.api("whatever"), {"iid": 1})

    def test_empty_response_is_an_empty_list(self):
        G.run = lambda cmd: fake_result(stdout="")
        self.assertEqual(G.api("whatever"), [])

    def test_paginated_response_joins_json_arrays(self):
        """--paginate concatenates one JSON array per page: "][" glues two pages'
        arrays together without a separating comma."""
        G.run = lambda cmd: fake_result(stdout="[1,2][3,4]")
        self.assertEqual(G.api("whatever", paginate=True), [1, 2, 3, 4])

    def test_failure_dies(self):
        G.run = lambda cmd: fake_result(returncode=1, stderr="boom")
        with self.assertRaises(SystemExit):
            G.api("whatever")


class TestMrView(unittest.TestCase):
    def test_parses_json_on_success(self):
        G.run = lambda cmd: fake_result(stdout='{"iid": 7}')
        self.assertEqual(G.mr_view(), {"iid": 7})

    def test_dies_on_failure(self):
        G.run = lambda cmd: fake_result(returncode=1)
        with self.assertRaises(SystemExit):
            G.mr_view()


class TestCurrentUser(unittest.TestCase):
    def test_returns_username(self):
        G.run = lambda cmd: fake_result(stdout='{"username": "jdoe"}')
        self.assertEqual(G.current_user(), "jdoe")

    def test_none_on_failure(self):
        G.run = lambda cmd: fake_result(returncode=1)
        self.assertIsNone(G.current_user())

    def test_none_on_malformed_json(self):
        G.run = lambda cmd: fake_result(stdout="not json")
        self.assertIsNone(G.current_user())


class TestMrHead(unittest.TestCase):
    def test_prefers_diff_refs_head_sha(self):
        G.mr_object = lambda ctx, iid: {"diff_refs": {"head_sha": "abc123"}, "sha": "old"}
        self.assertEqual(G.mr_head({}, 1), "abc123")

    def test_falls_back_to_bare_sha(self):
        G.mr_object = lambda ctx, iid: {"sha": "old"}
        self.assertEqual(G.mr_head({}, 1), "old")


class TestVersions(unittest.TestCase):
    def test_hits_the_versions_endpoint_with_pagination(self):
        seen = {}

        def fake_run(cmd):
            seen["cmd"] = cmd
            return fake_result(stdout='[{"id": 2}, {"id": 1}]')

        G.run = fake_run
        out = G.versions({"enc": "g%2Fp"}, 7)
        self.assertEqual(out, [{"id": 2}, {"id": 1}])
        self.assertIn("--paginate", seen["cmd"])
        self.assertTrue(any("merge_requests/7/versions" in c for c in seen["cmd"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
